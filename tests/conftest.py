import os
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


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
    """带真实鉴权的 TestClient；PATH 清空以保证不会真的调到 oci CLI。"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("OCIX_SESSION_SECRET", "t" * 40)
    monkeypatch.setenv("OCIX_ADMIN_USER", "admin")
    monkeypatch.setenv("OCIX_ADMIN_PASSWORD", "devpass123")
    monkeypatch.setenv("OCIX_DATA_DIR", str(workdir["root"] / "data"))
    monkeypatch.setenv("OCI_CONFIG_PATH", str(workdir["config"]))
    monkeypatch.setenv("OCIX_CLI_TIMEOUT", "5")

    for mod in [m for m in list(sys.modules) if m == "ocix" or m.startswith("ocix.")]:
        del sys.modules[mod]

    from ocix.main import app

    with TestClient(app) as client:
        r = client.post("/api/auth/login", json={"username": "admin", "password": "devpass123"})
        assert r.status_code == 200, r.text
        client.headers.update({"Authorization": "Bearer " + r.json()["token"]})
        yield client

    shutil.rmtree(workdir["root"] / "data", ignore_errors=True)


@pytest.fixture()
def anon_client(workdir, monkeypatch):
    """未登录的 TestClient。"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("OCIX_SESSION_SECRET", "t" * 40)
    monkeypatch.setenv("OCIX_ADMIN_PASSWORD", "devpass123")
    monkeypatch.setenv("OCIX_DATA_DIR", str(workdir["root"] / "data2"))
    monkeypatch.setenv("OCI_CONFIG_PATH", str(workdir["config"]))

    for mod in [m for m in list(sys.modules) if m == "ocix" or m.startswith("ocix.")]:
        del sys.modules[mod]

    from ocix.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """避免宿主机上的 OCI_* 环境变量影响测试。"""
    for key in list(os.environ):
        if key.startswith("OCI_CLI"):
            monkeypatch.delenv(key, raising=False)
