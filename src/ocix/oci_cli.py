import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from configparser import ConfigParser

from .config import OCI_CLI_TIMEOUT, OCI_CONFIG_PATH, OCI_MAX_WORKERS


class OCICLIError(Exception):
    def __init__(self, message, returncode=None, code=None):
        super().__init__(message)
        self.returncode = returncode
        self.code = code
        self.message = message


def _cli_env() -> dict:
    env = dict(os.environ)
    # 容器里 key 权限告警会污染 stderr，且交互式提示会让子进程挂到超时
    env["OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING"] = "True"
    env["SUPPRESS_LABEL_WARNING"] = "True"
    return env


def _extract_error(stdout: str, stderr: str, returncode: int) -> tuple[str, str]:
    """oci CLI 的 ServiceError 以 `ServiceError:\\n{json}` 形式打到 stderr。"""
    for text in (stderr, stdout):
        if not text:
            continue
        start = text.find("{")
        if start >= 0:
            try:
                j = json.loads(text[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(j, dict) and j.get("message"):
                return str(j["message"]), str(j.get("code") or "")
    msg = (stderr or stdout or "").strip()
    if not msg:
        msg = f"oci 命令执行失败 (code={returncode})"
    # 只保留最后几行，避免把整段 traceback 丢到前端
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    return "\n".join(lines[-4:]), ""


def run_oci(profile: str, *args: str, timeout: int = OCI_CLI_TIMEOUT):
    """调用官方 oci CLI，返回解析后的 JSON。所有操作均走官方命令行。"""
    if not OCI_CONFIG_PATH.exists():
        raise OCICLIError(f"OCI 配置文件不存在: {OCI_CONFIG_PATH}")

    exe = shutil.which("oci")
    if not exe:
        raise OCICLIError("未找到 oci 命令，请确认 oci-cli 已安装并在 PATH 中")

    cmd = [
        exe,
        "--config-file",
        str(OCI_CONFIG_PATH),
        "--profile",
        profile,
        *args,
        "--output",
        "json",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_cli_env(),
            stdin=subprocess.DEVNULL,  # 私钥带口令时 CLI 会交互提示，禁掉以免挂死
        )
    except FileNotFoundError:
        raise OCICLIError("未找到 oci 命令，请确认 oci-cli 已安装并在 PATH 中")
    except subprocess.TimeoutExpired:
        raise OCICLIError(f"oci 命令执行超时 ({timeout}s)")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()

    if proc.returncode != 0:
        msg, code = _extract_error(out, err, proc.returncode)
        raise OCICLIError(msg, proc.returncode, code)

    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"raw": out}


def gather(fn: Callable, items: Iterable) -> list[tuple[object, object, Exception]]:
    """并发执行 fn(item)，返回 [(item, result, error)]。

    每次调用都新建线程池：嵌套并发（账户 → compartment）共用一个有界池会自锁。
    oci CLI 每次调用都要起子进程，线程池创建开销可以忽略。
    """
    items = list(items)
    if not items:
        return []
    if len(items) == 1:
        it = items[0]
        try:
            return [(it, fn(it), None)]
        except Exception as e:  # noqa: BLE001 - 汇总给调用方决定如何呈现
            return [(it, None, e)]

    def _safe(it):
        try:
            return (it, fn(it), None)
        except Exception as e:  # noqa: BLE001
            return (it, None, e)

    with ThreadPoolExecutor(max_workers=min(OCI_MAX_WORKERS, len(items))) as ex:
        return list(ex.map(_safe, items))


def read_config_parser() -> ConfigParser:
    cp = ConfigParser()
    cp.optionxform = str  # 保留大小写
    if OCI_CONFIG_PATH.exists():
        cp.read(str(OCI_CONFIG_PATH), encoding="utf-8")
    return cp


def list_profiles_from_config() -> list:
    """解析 ~/.oci/config 中的 profile 名称（仅名称，不含任何密钥）。"""
    if not OCI_CONFIG_PATH.exists():
        return []
    cp = read_config_parser()
    names = []
    if cp.defaults():
        names.append("DEFAULT")
    names.extend(cp.sections())
    return names


def cli_version() -> str:
    exe = shutil.which("oci")
    if not exe:
        return ""
    try:
        proc = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=20,
            env=_cli_env(), stdin=subprocess.DEVNULL,
        )
        return (proc.stdout or "").strip()
    except Exception:
        return ""
