"""与 OCI 无关的公共设施：错误类型、并发、缓存、配置文件解析。

（早期版本这些放在 oci_cli.py 里，面板改用官方 Python SDK 后
 CLI 相关代码已全部移除，只留下这些通用件。）
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from configparser import ConfigParser
from contextlib import contextmanager

from .config import OCI_CONFIG_PATH, OCI_MAX_WORKERS


class OCIError(Exception):
    """所有 OCI 侧失败统一成这一种，携带可直接展示给用户的中文/原文消息。"""

    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class TTLCache:
    """极简 TTL 缓存。

    OCI 的每次请求都要走公网往返，同一份数据在短时间内被反复请求时缓存收益明显。
    """

    def __init__(self, ttl: float):
        self.ttl = ttl
        self._data: dict = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._data.get(key)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        return None

    def set(self, key, value):
        with self._lock:
            self._data[key] = (time.time(), value)
        return value

    def invalidate(self, profile: str = None):
        with self._lock:
            if profile is None:
                self._data.clear()
            else:
                for k in [k for k in self._data if isinstance(k, tuple) and k and k[0] == profile]:
                    self._data.pop(k, None)


# 同一时刻只允许一个账户在跟 OCI 通信。
# 多个账户并发打 OCI 容易触发对方限流，出错信息还难归因；
# 面板是单人自用的，串行带来的等待可以接受。
# 注意是「按账户」串行：同一个账户内部（比如并发查多个 compartment）不受影响，
# 否则页面会慢得离谱。
_account_gate = threading.Lock()
_gate_owner: list = [None]


@contextmanager
def account_gate(profile: str):
    """跨账户串行；同一账户重入直接放行，避免自己把自己锁死。"""
    if _gate_owner[0] == profile:
        yield
        return
    with _account_gate:
        _gate_owner[0] = profile
        try:
            yield
        finally:
            _gate_owner[0] = None


def gather(fn: Callable, items: Iterable) -> list[tuple[object, object, Exception]]:
    """并发执行 fn(item)，返回 [(item, result, error)]。

    每次调用都新建线程池：嵌套并发（账户 → compartment）共用一个有界池会自锁。
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
