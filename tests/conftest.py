import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


@contextmanager
def _fresh_ocix():
    """让 ocix 以当前环境变量重新导入，结束后把模块表恢复原样。

    config.py 在 import 时读环境变量，所以要拿到新配置只能重新导入。
    但若不恢复，测试模块在收集期绑定的类/函数就会和 sys.modules 里的不是同一个对象，
    monkeypatch 会打在「另一份」模块上——这坑踩过两次。
    """
    saved = {k: v for k, v in sys.modules.items() if k == "ocix" or k.startswith("ocix.")}
    for name in saved:
        del sys.modules[name]
    try:
        yield
    finally:
        for name in [k for k in list(sys.modules) if k == "ocix" or k.startswith("ocix.")]:
            del sys.modules[name]
        sys.modules.update(saved)


@pytest.fixture()
def workdir(tmp_path):
    """一个干净的数据目录 + 一份假 ~/.oci/config，绝不碰真实 OCI。"""
    cfg = tmp_path / "oci_config"
    cfg.write_text(
        "[EXISTING]\n"
        "user=ocid1.user.oc1..aaa\nfingerprint=aa:bb\nkey_file=/nope.pem\n"
        "tenancy=ocid1.tenancy.oc1..bbb\nregion=us-ashburn-1\n",
        encoding="utf-8",
    )
    return {"root": tmp_path, "config": cfg}


@pytest.fixture()
def app_client(workdir, monkeypatch):
    """带真实鉴权的 TestClient；配置里的 key_file 不存在，绝不会真的连上 OCI。"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OCIX_SESSION_SECRET", "t" * 40)
    monkeypatch.setenv("OCIX_ADMIN_USER", "admin")
    monkeypatch.setenv("OCIX_ADMIN_PASSWORD", "devpass123")
    monkeypatch.setenv("OCIX_DATA_DIR", str(workdir["root"] / "data"))
    monkeypatch.setenv("OCI_CONFIG_PATH", str(workdir["config"]))

    with _fresh_ocix():
        from ocix.main import app

        with TestClient(app) as client:
            r = client.post("/api/auth/login",
                            json={"username": "admin", "password": "devpass123"})
            assert r.status_code == 200, r.text
            client.headers.update({"Authorization": "Bearer " + r.json()["token"]})
            yield client

    shutil.rmtree(workdir["root"] / "data", ignore_errors=True)


@pytest.fixture()
def anon_client(workdir, monkeypatch):
    """未登录的 TestClient。"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OCIX_SESSION_SECRET", "t" * 40)
    monkeypatch.setenv("OCIX_ADMIN_PASSWORD", "devpass123")
    monkeypatch.setenv("OCIX_DATA_DIR", str(workdir["root"] / "data2"))
    monkeypatch.setenv("OCI_CONFIG_PATH", str(workdir["config"]))

    with _fresh_ocix():
        from ocix.main import app

        with TestClient(app) as client:
            yield client


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """避免宿主机上的 OCI_* 环境变量影响测试。"""
    for key in list(os.environ):
        if key.startswith("OCI_"):
            monkeypatch.delenv(key, raising=False)


# app_client / anon_client 会清空 sys.modules 重新导入 ocix，
# 于是同时存在多个 oci_helpers 模块对象：测试模块在收集期绑定的那个，
# 和 fixture 里新导入的那个。这里把收集期那个先抓住，清缓存时两边都清，
# 否则缓存会跨用例泄漏（曾导致镜像与子网 IPv6 的用例互相污染）。
try:
    from ocix import oci_helpers as _helpers_at_import
except Exception:  # pragma: no cover - 依赖缺失时由具体用例报错
    _helpers_at_import = None


@pytest.fixture(autouse=True)
def _clear_caches():
    """清空进程级读缓存。

    oci_helpers 为了压低 OCI 请求次数缓存了实例、卷、镜像、网卡等数据。
    """
    def _reset():
        mods = []
        if _helpers_at_import is not None:
            mods.append(_helpers_at_import)
        current = sys.modules.get("ocix.oci_helpers")
        if current is not None and current not in mods:
            mods.append(current)
        for m in mods:
            m.invalidate_read_cache()
            m.invalidate_compartment_cache()

    _reset()
    yield
    _reset()
