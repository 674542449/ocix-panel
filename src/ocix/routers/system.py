import json
import threading
import time
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, Query, Request

from .. import __version__, security
from ..config import GITHUB_REPO, INSTALL_DIR

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

    面板不会自己去改宿主机上的代码——那需要把 docker socket 交给容器，
    风险远大于收益。这里只负责告诉你有没有新版本、以及该敲哪条命令。
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
    }
