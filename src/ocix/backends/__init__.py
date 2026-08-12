"""与 OCI 通信的实现层。

面板通过 Oracle 官方 `oci` Python SDK 直接调用 OCI REST API：进程内完成、
复用 HTTPS 连接，不依赖 oci 命令行。

保留 ``Backend`` 抽象是为了测试——业务逻辑只依赖接口，
测试用 ``tests/fakes.FakeBackend`` 就能在不连 OCI 的情况下验证全部行为。
"""

from __future__ import annotations

import threading

from .base import Backend

_instance: Backend | None = None
_lock = threading.Lock()


def get_backend() -> Backend:
    """进程内单例：SDK 客户端要复用连接，不能每次新建。"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                from .sdk import SDKBackend
                _instance = SDKBackend()
    return _instance


def set_backend(backend: Backend | None) -> None:
    """仅供测试替换实现；传 None 表示恢复默认。"""
    global _instance
    with _lock:
        _instance = backend


__all__ = ["Backend", "get_backend", "set_backend"]
