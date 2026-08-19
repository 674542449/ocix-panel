import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import __version__, security
from ..config import CONTROL_DIR, GITHUB_REPO, INSTALL_DIR
from ..db import audit

router = APIRouter(prefix="/api/system", tags=["system"])

_CACHE_TTL = 600
_cache = {"ts": 0.0, "data": None}
_lock = threading.Lock()


def _version_tuple(v: str) -> tuple:
    parts = []
    for chunk in (v or "").lstrip("vV").split("."):
        num = "".join(c for c in chunk if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple((parts + [0, 0, 0])[:3])


def _fetch_latest() -> dict:
    """问 GitHub 最新 tag。拿不到就如实说，不猜。"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/tags?per_page=10"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"ocix/{__version__}",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 - 固定的 https 地址
        tags = json.loads(resp.read().decode("utf-8"))
    versions = [t.get("name", "") for t in tags if t.get("name", "").lstrip("vV")[:1].isdigit()]
    if not versions:
        return {"latest": None, "error": "远端还没有打过版本 tag"}
    latest = max(versions, key=_version_tuple)
    return {"latest": latest.lstrip("vV"), "error": None}


def _latest_cached(force: bool = False) -> dict:
    with _lock:
        fresh = _cache["data"] is not None and time.time() - _cache["ts"] < _CACHE_TTL
        if fresh and not force:
            return _cache["data"]
    try:
        data = _fetch_latest()
    except urllib.error.HTTPError as e:
        data = {"latest": None, "error": f"GitHub 返回 {e.code}（可能触发了匿名调用限流，过会儿再试）"}
    except Exception as e:  # noqa: BLE001 - 网络问题一律降级为「查不到」
        data = {"latest": None, "error": f"连不上 GitHub：{e}"}
    with _lock:
        _cache.update(ts=time.time(), data=data)
    return data


@router.get("/info")
def system_info(
    refresh: bool = Query(False, description="跳过缓存，强制重新查一次"),
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """版本信息与更新指引。

    面板容器本身不碰 docker——真正执行更新的是宿主机上的代理进程，
    面板只负责往交换目录里写一个「请求更新」的标记。
    """
    security.check_rate(request, security.API_RATE_LIMIT)
    remote = _latest_cached(force=refresh)
    latest = remote.get("latest")
    available = bool(latest) and _version_tuple(latest) > _version_tuple(__version__)
    return {
        "current": __version__,
        "latest": latest,
        "update_available": available,
        "check_error": remote.get("error"),
        "install_dir": INSTALL_DIR,
        "update_command": f"bash {INSTALL_DIR}/scripts/update.sh",
        "check_command": f"bash {INSTALL_DIR}/scripts/update.sh --check",
        "repo_url": f"https://github.com/{GITHUB_REPO}",
        "compare_url": (
            f"https://github.com/{GITHUB_REPO}/compare/v{__version__}...v{latest}"
            if available else None
        ),
        "agent": _agent_state(),
    }


# ---- 一键更新：面板只写请求，宿主机代理负责执行 ----
_REQUEST_FILE = "update.request"
_STATUS_FILE = "update.status"
_ALIVE_FILE = "agent.alive"
_LOG_FILE = "update.log"
# 更新日志里的 ANSI 颜色码，得剥掉才好在网页上显示。
# 必须带上开头的 ESC，否则会把日志里正常的 "[0-9]" 之类文本也吃掉。
_ANSI_RE = re.compile("\x1b\\[[0-9;]*[a-zA-Z]")
# 代理每 10 秒摸一次心跳文件，留三倍余量判定在线
_ALIVE_TIMEOUT = 30


def _control_path(name: str):
    return CONTROL_DIR / name


def _read_json(name: str) -> dict:
    try:
        with open(_control_path(name), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _agent_state() -> dict:
    """宿主机代理在不在。

    不在的话点更新只会让请求文件一直躺着没人管，
    所以要在界面上直接说清楚，而不是让用户对着转圈等。
    """
    alive = _control_path(_ALIVE_FILE)
    try:
        age = time.time() - os.path.getmtime(alive)
    except OSError:
        return {
            "online": False, "last_seen": None,
            # 用 update.sh 而不是 install.sh：它会顺手把代理装上，
            # 而且不会重问域名/密码那些安装期的问题。跑这一次之后就永久生效。
            "hint": f"在服务器上执行一次 sudo bash {INSTALL_DIR}/scripts/update.sh 即可装好更新代理，"
                    f"之后就能一直用网页更新，不用再登服务器。",
            "fix_command": f"sudo bash {INSTALL_DIR}/scripts/update.sh",
        }
    if age > _ALIVE_TIMEOUT:
        return {
            "online": False,
            "last_seen": int(time.time() - age),
            "hint": f"更新代理已 {int(age)} 秒未检测到心跳（服务未启动或已停止）",
            "fix_command": f"bash {INSTALL_DIR}/scripts/update.sh",
        }
    return {"online": True, "last_seen": int(time.time() - age), "hint": None, "fix_command": None}


def _read_log(limit: int = 4000) -> str | None:
    """读更新日志。

    日志是纯文本文件，不由代理往 JSON 里塞——shell 侧做 JSON 转义又脆又难验证。
    这里读进来交给 Python 的 json 编码器，任意字节都能安全处理。
    """
    try:
        with open(_control_path(_LOG_FILE), "rb") as f:
            try:
                f.seek(-limit, os.SEEK_END)
            except OSError:
                f.seek(0)
            raw = f.read()
    except OSError:
        return None
    # 日志里有 ANSI 颜色码，直接显示在网页上是一堆乱码
    text = _ANSI_RE.sub("", raw.decode("utf-8", errors="replace"))
    return text.strip() or None


@router.get("/update/status")
def update_status(user: str = Depends(security.get_current_user)):
    """更新进度。容器会在更新过程中被重启，前端断连后继续轮询即可。"""
    status = _read_json(_STATUS_FILE)
    pending = _control_path(_REQUEST_FILE).exists()
    return {
        "state": status.get("state") or ("pending" if pending else "idle"),
        "message": status.get("message"),
        "version": status.get("version"),
        "started_at": status.get("started_at"),
        "finished_at": status.get("finished_at"),
        "log": _read_log(),
        "queued": pending,
        "current": __version__,
        "agent": _agent_state(),
    }


@router.post("/update")
def trigger_update(request: Request, user: str = Depends(security.get_current_user)):
    """请求更新。

    这里只写一个标记文件，绝不执行任何命令——宿主机代理读到标记后跑的是
    固定的 update.sh，文件内容不会以任何形式进入命令行。
    """
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    agent = _agent_state()
    if not agent["online"]:
        raise HTTPException(status_code=409, detail=agent["hint"])

    running = _read_json(_STATUS_FILE).get("state")
    if running == "running":
        raise HTTPException(status_code=409, detail="已经有一次更新在进行中，请等它跑完")

    try:
        CONTROL_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"requested_by": user, "requested_at": int(time.time()), "from": __version__}
        with open(_control_path(_REQUEST_FILE), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"写入更新请求失败：{e}")

    audit(user, "system-update", result="ok",
          detail=f"从 v{__version__} 请求更新", ip=ip)
    return {"ok": True, "state": "pending",
            "msg": "更新请求已提交，宿主机代理会在几秒内开始执行"}


# ---- Telegram 通知设置 ----

def _mask_token(token: str) -> str:
    token = token.strip()
    if not token:
        return ""
    if len(token) <= 8:
        return "********"
    return token[:4] + "*" * (len(token) - 8) + token[-4:]


@router.get("/telegram")
def get_telegram_settings(
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    from .. import db
    enabled = db.get_setting("tg_enabled", "0") in ("1", "true", "True")
    token = db.get_setting("tg_bot_token", "")
    chat_id = db.get_setting("tg_chat_id", "")
    return {
        "enabled": enabled,
        "bot_token": token,
        "has_token": bool(token),
        "chat_id": chat_id,
    }


@router.post("/telegram")
def save_telegram_settings(
    req: dict,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    from .. import db
    enabled = req.get("enabled", True)
    bot_token = (req.get("bot_token") or "").strip()
    chat_id = (req.get("chat_id") or "").strip()

    if bot_token and not bot_token.startswith("****") and "*" not in bot_token:
        db.set_setting("tg_bot_token", bot_token)
    if chat_id:
        db.set_setting("tg_chat_id", chat_id)

    stored_token = db.get_setting("tg_bot_token", "")
    stored_cid = db.get_setting("tg_chat_id", "")
    if stored_token and stored_cid and enabled:
        db.set_setting("tg_enabled", "1")
    else:
        db.set_setting("tg_enabled", "1" if enabled else "0")

    audit(
        user,
        "update-telegram-settings",
        detail=f"enabled={enabled} chat_id={stored_cid}",
        result="ok",
        ip=ip,
    )
    return {
        "ok": True,
        "enabled": db.get_setting("tg_enabled", "0") in ("1", "true", "True"),
        "has_token": bool(stored_token),
        "chat_id": stored_cid,
    }


@router.post("/telegram/test")
def test_telegram_connection(
    req: dict,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    from .. import db, notifier
    bot_token = (req.get("bot_token") or "").strip()
    chat_id = (req.get("chat_id") or "").strip()

    # 如果传进来的是脱敏后的 token，从库里读真实 token
    if not bot_token or "*" in bot_token:
        bot_token = db.get_setting("tg_bot_token", "")
    if not chat_id:
        chat_id = db.get_setting("tg_chat_id", "")

    if not bot_token or not chat_id:
        raise HTTPException(status_code=400, detail="请填写完整的 Bot Token 和 Chat ID")

    ok, msg = notifier.test_telegram(bot_token, chat_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}

