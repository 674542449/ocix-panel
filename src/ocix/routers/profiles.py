import configparser
import os
import re
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)

from .. import security
from ..common import OCIError, account_gate, list_profiles_from_config, read_config_parser
from ..config import KEYS_DIR, OCI_CONFIG_PATH
from ..db import (
    audit,
    delete_profile_db,
    get_setting,
    list_profiles_db,
    set_setting,
    upsert_profile,
)
from ..oci_helpers import (
    account_tier,
    console_password_policy,
    get_user,
    invalidate_compartment_cache,
    read_profile_config,
    set_console_password_expiry,
)
from ..schemas import ConsolePasswordPolicyRequest

router = APIRouter(prefix="/api/profiles", tags=["profiles"])

_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_REQUIRED_FIELDS = ("user", "fingerprint", "tenancy", "region")


def _check_name(name: str) -> str:
    """profile 名会拼进私钥文件路径，必须严格校验。"""
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="Profile 名称仅允许字母、数字、下划线、点和短横线（1-64 位）")
    return name


def _load_main_config() -> configparser.ConfigParser:
    return read_config_parser()


def _save_main_config(cp: configparser.ConfigParser):
    OCI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(str(OCI_CONFIG_PATH), "w", encoding="utf-8") as f:
        cp.write(f)
    try:
        os.chmod(OCI_CONFIG_PATH, 0o600)
    except Exception:
        pass


def _key_path(name: str) -> Path:
    return KEYS_DIR / f"{name}.pem"


def _strip_bom_bytes(data: bytes) -> bytes:
    """去掉 UTF-8 BOM。

    记事本「另存为 UTF-8」会在文件开头加 EF BB BF，而 BOM 不是空白字符，
    strip() 去不掉——于是一个完全正常的私钥会被判成「不是有效的 PEM」。
    """
    return data[3:] if data.startswith(b"\xef\xbb\xbf") else data


def _write_key(name: str, content: bytes) -> Path:
    content = _strip_bom_bytes(content).lstrip()
    if not content.startswith(b"-----BEGIN"):
        raise HTTPException(status_code=400, detail="不是有效的 PEM 私钥（应以 -----BEGIN 开头）")
    path = _key_path(name)
    path.write_bytes(content)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return path


# OCI 配置里这些键名规范上是小写。有人手改过配置、或从别的工具拷出来，
# 可能是大写或混合大小写——按原样存进去 SDK 就找不到字段了。
_OCI_KEYS = ("user", "fingerprint", "tenancy", "region", "key_file",
             "pass_phrase", "security_token_file")

# 全角符号：中文输入法没切回来时会打出这些，configparser 直接报
# 「Source contains parsing errors」，看不出是哪儿的问题
_FULLWIDTH = {"＝": "=", "：": ":", "［": "[", "］": "]", "　": " "}


def _parse_config_text(text: str) -> configparser.ConfigParser:
    """把用户粘的那坨文本解析成配置。

    configparser 对粘贴内容的容错比人想象的差很多，下面每一条都是实测出来的：

      * **BOM**：记事本或网页复制会带 EF BB BF，于是第一行不是 `[DEFAULT]`
        而是 `\ufeff[DEFAULT]`，直接报「File contains no section headers」
      * **没写段名**：只粘了几行 `key=value` 也会报同样的错。
        这种粘法很常见（从控制台只抄了中间几行），补一个 [DEFAULT] 就好
      * **重复键**：粘重了会抛 DuplicateOptionError。strict=False 之后后者覆盖前者
      * **行尾注释**：默认不识别行内注释，`region=us-phoenix-1  # 东京` 会把
        整串连注释一起当成 region 的值。这个最阴——解析不报错，
        等到真去调 OCI 才失败，报错还看不出根因
      * **全角等号**：先换成半角再解析

    有一处刻意开了口子：**私钥口令不吃行内注释**。开了 inline_comment_prefixes
    之后，`pass_phrase=abc #d` 会被截成 `abc`——口令被悄悄改短，
    然后私钥解不开，报错还指向别处。所以口令那一项按原文取回来。
    （`abc#d` 这种中间没空格的本来就不算注释，configparser 要求注释符前有空白。）
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    for bad, good in _FULLWIDTH.items():
        text = text.replace(bad, good)

    def _new(strip_comments: bool):
        cp = configparser.ConfigParser(
            strict=False,                          # 重复键：后者覆盖前者
            inline_comment_prefixes=("#", ";") if strip_comments else None,
        )
        cp.optionxform = str                       # 大小写留给下面统一处理
        return cp

    def _read(body: str):
        src = _new(True)
        src.read_string(body)
        # 口令按原文再读一遍，盖回去
        raw = _new(False)
        try:
            raw.read_string(body)
        except Exception:      # noqa: BLE001 - 原文读不了就算了，主解析已经成功
            return src
        for sect in [None, *src.sections()]:
            items = raw.defaults() if sect is None else dict(raw.items(sect))
            for key, val in items.items():
                if key.strip().lower() != "pass_phrase":
                    continue
                if sect is None:
                    src.set("DEFAULT", key, val)
                elif src.has_section(sect):
                    src.set(sect, key, val)
        return src

    try:
        return _read(text)
    except configparser.MissingSectionHeaderError:
        # 没写段名，当作 [DEFAULT] 再来一遍
        try:
            return _read("[DEFAULT]\n" + text)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"配置文本解析失败: {e}") from None
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"配置文本解析失败: {e}。"
                   "常见原因：等号写成了全角＝、或者行首多了别的字符") from None


@router.get("")
def get_profiles(user: str = Depends(security.get_current_user)):
    file_profiles = list_profiles_from_config()
    db_by_name = {p["name"]: p for p in list_profiles_db()}
    merged = []
    for name in file_profiles:
        meta = db_by_name.get(name, {})
        try:
            cfg = read_profile_config(name)
        except OCIError:
            cfg = {}
        key_file = cfg.get("key_file") or meta.get("key_file") or ""
        merged.append({
            "name": name,
            "user_ocid": cfg.get("user") or meta.get("user_ocid"),
            "tenancy_ocid": cfg.get("tenancy") or meta.get("tenancy_ocid"),
            "region": cfg.get("region") or meta.get("region"),
            "fingerprint": cfg.get("fingerprint") or meta.get("fingerprint"),
            "key_file": key_file,
            "key_exists": bool(key_file) and Path(key_file).expanduser().exists(),
            "validated": bool(meta),
            "created_at": meta.get("created_at"),
        })
    return {"profiles": merged}


@router.post("/import")
async def import_profile(
    request: Request,
    config_text: str = Form(""),
    profile_name: str | None = Form(None),
    key_text: str | None = Form(None),
    pass_phrase: str | None = Form(None),
    key_file: UploadFile = File(None),
    user: str = Depends(security.get_current_user),
):
    """导入 OCI 配置：支持原格式粘贴 + 私钥文件上传或直接粘贴私钥内容。

    - config_text: 形如 [DEFAULT] / [PROFILENAME] 的原格式文本
    - key_file:    可选，PEM 私钥文件；落盘到容器卷并设置 600 权限
    - key_text:    可选，直接粘贴 PEM 私钥内容（与 key_file 二选一）
    - pass_phrase: 可选，私钥口令
    """
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)

    if not config_text.strip():
        raise HTTPException(status_code=400, detail="config_text 不能为空")

    src = _parse_config_text(config_text)
    sections = src.sections()
    if sections:
        section = sections[0]
        opts = {k: v for k, v in src.items(section)}
        name = _check_name(profile_name or section)
    else:
        # 只粘贴了 [DEFAULT] 段落，或压根没写段名
        opts = {k: v for k, v in src.defaults().items()}
        name = _check_name(profile_name or "DEFAULT")

    # 键名里的已知 OCI 字段统一转小写：SDK 只认小写，
    # 而粘过来的可能是 USER= / Region= 这种
    cleaned = {}
    for k, v in opts.items():
        k = k.strip()
        if not k:
            continue
        if k.lower() in _OCI_KEYS:
            k = k.lower()
        cleaned[k] = (v or "").strip().strip('"').strip("'")
    opts = cleaned
    if not opts:
        raise HTTPException(status_code=400, detail="未能从配置中解析出任何字段")

    missing = [f for f in _REQUIRED_FIELDS if not opts.get(f)]
    if missing:
        raise HTTPException(status_code=400, detail=f"配置缺少必填字段: {', '.join(missing)}")

    if pass_phrase:
        opts["pass_phrase"] = pass_phrase

    # ---- 处理私钥 ----
    key_backup = None
    key_path = _key_path(name)
    if key_path.exists():
        key_backup = key_path.read_bytes()

    uploaded = None
    if key_file is not None and key_file.filename:
        uploaded = await key_file.read()
    elif key_text and key_text.strip():
        uploaded = key_text.strip().encode("utf-8") + b"\n"

    if uploaded is not None:
        _write_key(name, uploaded)
        opts["key_file"] = str(key_path)
    else:
        raw = opts.get("key_file", "")
        if not raw:
            raise HTTPException(status_code=400, detail="配置里没有 key_file，且未上传/粘贴私钥")
        resolved = Path(raw).expanduser()
        if not resolved.exists():
            raise HTTPException(
                status_code=400,
                detail=f"私钥文件在服务端不存在: {raw}。请上传或粘贴私钥内容——"
                       f"面板运行在容器里，读不到你本机的 {raw}",
            )
        opts["key_file"] = str(resolved)

    # ---- 写入 ~/.oci/config（合并，同名覆盖，失败可回滚） ----
    cp = _load_main_config()
    if name == "DEFAULT":
        prev = dict(cp.defaults())
        for k, v in opts.items():
            cp.set("DEFAULT", k, v)
    else:
        # cp.items(section) 会混入 DEFAULT 继承来的键，这里只留本段真正拥有的
        _d = cp.defaults()
        prev = (
            {k: v for k, v in cp.items(name, raw=True) if k not in _d or _d.get(k) != v}
            if cp.has_section(name) else None
        )
        if not cp.has_section(name):
            cp.add_section(name)
        for k, v in opts.items():
            cp.set(name, k, v)
    _save_main_config(cp)
    invalidate_compartment_cache(name)

    # ---- 立刻用 oci iam user get 验证密钥可用性 ----
    try:
        get_user(name)
        cfg = read_profile_config(name)
        upsert_profile(
            name,
            user_ocid=cfg.get("user"),
            tenancy_ocid=cfg.get("tenancy"),
            region=cfg.get("region"),
            fingerprint=cfg.get("fingerprint"),
            key_file=cfg.get("key_file"),
        )
        audit(user, "import-profile", profile=name, target=name, result="ok", ip=ip)
        return {
            "ok": True,
            "profile": name,
            "user_ocid": cfg.get("user"),
            "tenancy_ocid": cfg.get("tenancy"),
            "region": cfg.get("region"),
            "key_saved": uploaded is not None,
        }
    except Exception as e:
        # 回滚到导入前的状态：原来能用的 profile 不能被一次失败的导入毁掉
        cp2 = _load_main_config()
        if name == "DEFAULT":
            for k in list(cp2.defaults().keys()):
                cp2.remove_option("DEFAULT", k)
            for k, v in (prev or {}).items():
                cp2.set("DEFAULT", k, v)
        else:
            if cp2.has_section(name):
                cp2.remove_section(name)
            if prev is not None:
                cp2.add_section(name)
                for k, v in prev.items():
                    cp2.set(name, k, v)
        _save_main_config(cp2)
        if uploaded is not None:
            if key_backup is not None:
                _write_key(name, key_backup)
            elif key_path.exists():
                try:
                    key_path.unlink()
                except Exception:
                    pass
        invalidate_compartment_cache(name)

        msg = e.message if isinstance(e, OCIError) else str(e)
        audit(user, "import-profile", profile=name, target=name, result="fail", detail=msg, ip=ip)
        raise HTTPException(status_code=400, detail=f"配置校验失败，已回滚: {msg}")


# ---- 锁定账户 ----
# 锁定后所有页面固定用这个账户，顶部不再能切换。
# 存库而不是存 localStorage：换个浏览器进来也该是同一个账户，
# 不然「锁定」只锁住了一台设备。
_LOCK_KEY = "locked_profile"


@router.get("/lock")
def get_lock(user: str = Depends(security.get_current_user)):
    name = get_setting(_LOCK_KEY) or ""
    # 账户被删掉后锁定要自动失效，否则界面会卡在一个不存在的账户上
    if name and name not in list_profiles_from_config():
        set_setting(_LOCK_KEY, "")
        name = ""
    return {"locked": name or None}


@router.post("/{name}/lock")
def lock_profile(
    name: str,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """把这个账户锁成当前账户。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    _check_name(name)
    if name not in list_profiles_from_config():
        raise HTTPException(status_code=404, detail=f"账户 {name} 不存在")
    set_setting(_LOCK_KEY, name)
    audit(user, "lock-profile", profile=name, target=name, result="ok",
          detail="已锁定为当前账户", ip=security.client_ip(request))
    return {"ok": True, "locked": name}


@router.delete("/lock")
def unlock_profile(
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    set_setting(_LOCK_KEY, "")
    audit(user, "lock-profile", result="ok", detail="已解除锁定",
          ip=security.client_ip(request))
    return {"ok": True, "locked": None}


@router.post("/{name}/test")
def test_profile(
    name: str,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """重新校验某个 profile 的密钥是否仍然可用。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    name = _check_name(name)
    try:
        u = get_user(name)
    except OCIError as e:
        audit(user, "test-profile", profile=name, target=name, result="fail",
              detail=e.message, ip=security.client_ip(request))
        raise HTTPException(status_code=400, detail=e.message)
    cfg = read_profile_config(name)
    upsert_profile(
        name,
        user_ocid=cfg.get("user"),
        tenancy_ocid=cfg.get("tenancy"),
        region=cfg.get("region"),
        fingerprint=cfg.get("fingerprint"),
        key_file=cfg.get("key_file"),
    )
    audit(user, "test-profile", profile=name, target=name, result="ok",
          ip=security.client_ip(request))
    return {
        "ok": True,
        "profile": name,
        "user_name": u.get("name"),
        "user_email": u.get("email"),
        "region": cfg.get("region"),
    }


@router.delete("/{name}")
def delete_profile(
    name: str,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    name = _check_name(name)
    cp = _load_main_config()
    removed = False
    if name == "DEFAULT":
        # 仅移除已知字段（不盲目清空 DEFAULT）
        for k in ["user", "fingerprint", "key_file", "tenancy", "region", "pass_phrase"]:
            if cp.remove_option("DEFAULT", k):
                removed = True
    elif cp.has_section(name):
        cp.remove_section(name)
        removed = True
    _save_main_config(cp)

    # 清理私钥
    key_path = _key_path(name)
    if key_path.exists():
        try:
            key_path.unlink()
        except Exception:
            pass
    delete_profile_db(name)
    invalidate_compartment_cache(name)
    audit(user, "delete-profile", profile=name, target=name,
          result="ok" if removed else "noop", ip=security.client_ip(request))
    return {"ok": True, "removed": removed}


@router.get("/{name}/tier")
def profile_tier(
    name: str,
    request: Request,
    limits: bool = Query(True, description="是否附带服务限额（列表页不需要，可关掉少一次请求）"),
    user: str = Depends(security.get_current_user),
):
    """账户等级：免费号还是已升级。

    Oracle 没有给普通租户一个直白的等级标志位，
    这里综合订阅信息与服务限额来判断，并把依据一并返回。
    """
    security.check_rate(request, security.API_RATE_LIMIT)
    _check_name(name)
    try:
        # 跨账户串行：多个号同时打 OCI 容易被对方限流
        with account_gate(name):
            return account_tier(name, with_limits=limits)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.get("/{name}/console-password-policy")
def get_console_password_policy(
    name: str,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """Oracle 账号（控制台登录）的密码有效期。

    注意这跟面板自己的登录密码不是一回事：这条是 Oracle 侧的策略，
    免费租户默认 120 天必须改一次。
    """
    security.check_rate(request, security.API_RATE_LIMIT)
    _check_name(name)
    try:
        with account_gate(name):
            return console_password_policy(name)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.put("/{name}/console-password-policy")
def put_console_password_policy(
    name: str,
    req: ConsolePasswordPolicyRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """改 Oracle 账号的密码有效期，0 = 永不过期。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    _check_name(name)
    ip = security.client_ip(request)
    try:
        res = set_console_password_expiry(name, req.days, req.policy_id)
    except OCIError as e:
        audit(user, "console-password-policy", profile=name, target=name,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "console-password-policy", profile=name, target=name,
          detail=("控制台密码设为永不过期" if req.days == 0
                  else f"控制台密码有效期设为 {req.days} 天"),
          result="ok", ip=ip)
    return res
