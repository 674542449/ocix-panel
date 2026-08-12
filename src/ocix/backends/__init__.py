"""后端选择：``OCIX_BACKEND=cli``（默认）或 ``sdk``。

两者都是 Oracle 官方通道——oci CLI 本身就构建在 oci SDK 之上，
差别只在于「起子进程」还是「进程内直接调用」。
"""

from __future__ import annotations

import threading

from ..config import OCI_BACKEND
from .base import Backend

_instance: Backend | None = None
_lock = threading.Lock()


def _build(name: str) -> Backend:
    if name == "sdk":
        from .sdk import SDKBackend
        return SDKBackend()
    from .cli import CLIBackend
    return CLIBackend()


def get_backend() -> Backend:
    """进程内单例。SDK 后端会复用 HTTPS 连接，不能每次新建。"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = _build(OCI_BACKEND)
    return _instance


def set_backend(backend: Backend | None) -> None:
    """仅供测试切换实现。"""
    global _instance
    with _lock:
        _instance = backend


__all__ = ["Backend", "get_backend", "set_backend"]
