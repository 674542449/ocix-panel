"""接口层：鉴权、导入回滚、额度硬闸门、静态文件安全。

这里从不调用真实 oci CLI —— conftest 把 PATH 清空了，任何外呼都会以
「未找到 oci 命令」失败，正好用来验证错误处理。
"""

import pytest

GOOD_CONFIG = ("[NEW]\nuser=ocid1.user.oc1..ccc\nfingerprint=cc:dd\n"
               "tenancy=ocid1.tenancy.oc1..ddd\nregion=us-phoenix-1\n")
FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nZmFrZQ==\n-----END PRIVATE KEY-----"
PUB_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyForTests test@example"


# ── 健康检查 ──

def test_health_is_reachable_without_auth(anon_client):
    """回归：SPA 通配路由曾注册在前面，把 /api/health 吞成 404。"""
    r = anon_client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_health_reports_config_state(anon_client):
    assert anon_client.get("/api/health").json()["oci_config"]["profiles"] == 1


# ── 鉴权 ──

@pytest.mark.parametrize("path", [
    "/api/profiles", "/api/instances/all", "/api/audit",
    "/api/provision/storage?profile=EXISTING",
])
def test_protected_endpoints_require_a_token(anon_client, path):
    assert anon_client.get(path).status_code == 401


def test_wrong_password_is_rejected(anon_client):
    r = anon_client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_login_failure_is_audited(app_client):
    app_client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    logs = app_client.get("/api/audit?limit=100").json()["logs"]
    assert any(x["action"] == "login" and x["result"] == "fail" for x in logs)
    assert all(x["ip"] is not None for x in logs)


def test_password_change_invalidates_existing_tokens(app_client):
    old = dict(app_client.headers)
    r = app_client.post("/api/auth/change-password",
                        json={"old_password": "devpass123", "new_password": "newpass123"})
    assert r.status_code == 200
    assert app_client.get("/api/auth/me", headers=old).status_code == 401
    assert app_client.post("/api/auth/login",
                           json={"username": "admin", "password": "newpass123"}).status_code == 200


def test_short_password_is_rejected(app_client):
    r = app_client.post("/api/auth/change-password",
                        json={"old_password": "devpass123", "new_password": "short"})
    assert r.status_code == 422


def test_wrong_old_password_is_rejected(app_client):
    r = app_client.post("/api/auth/change-password",
                        json={"old_password": "wrong", "new_password": "newpass123"})
    assert r.status_code == 400


# ── profile 导入 ──

def test_config_text_arrives_as_form_data(app_client):
    """回归：config_text 曾漏写 Form(...)，被当成 query 参数，导入 100% 失败。"""
    r = app_client.post("/api/profiles/import", data={"config_text": ""})
    assert r.status_code == 400 and "不能为空" in r.text


def test_missing_required_fields_are_reported(app_client):
    partial = GOOD_CONFIG.replace("region=us-phoenix-1\n", "")
    r = app_client.post("/api/profiles/import", data={"config_text": partial})
    assert r.status_code == 400 and "必填字段" in r.text


def test_missing_key_gives_actionable_message(app_client):
    r = app_client.post("/api/profiles/import", data={"config_text": GOOD_CONFIG})
    assert r.status_code == 400 and "key_file" in r.text


@pytest.mark.parametrize("name", ["../evil", "a/b", "x" * 65, ""])
def test_profile_name_is_validated(app_client, name):
    r = app_client.post("/api/profiles/import",
                        data={"config_text": GOOD_CONFIG, "profile_name": name})
    assert r.status_code == 400


def test_failed_import_rolls_back_completely(app_client, workdir):
    r = app_client.post("/api/profiles/import",
                        data={"config_text": GOOD_CONFIG, "key_text": FAKE_KEY})
    assert r.status_code == 400 and "已回滚" in r.text

    body = workdir["config"].read_text(encoding="utf-8")
    assert "[NEW]" not in body
    assert "[EXISTING]" in body
    assert not (workdir["root"] / "data" / "keys" / "NEW.pem").exists()


def test_failed_overwrite_preserves_the_working_profile(app_client, workdir):
    """覆盖同名 profile 失败时，原来能用的配置不能被毁掉。"""
    app_client.post("/api/profiles/import", data={
        "config_text": "[EXISTING]\nuser=ocid1.user.oc1..zzz\nfingerprint=zz\n"
                       "tenancy=t\nregion=r\n",
        "key_text": FAKE_KEY,
    })
    body = workdir["config"].read_text(encoding="utf-8")
    assert "ocid1.user.oc1..aaa" in body
    assert "ocid1.user.oc1..zzz" not in body


# ── 免费额度硬闸门 ──

def _create_payload(**over):
    payload = {
        "profile": "EXISTING", "compartment_id": "ocid1.compartment.oc1..c",
        "display_name": "test-box", "availability_domain": "AD-1",
        "image_id": "ocid1.image.oc1..i", "subnet_id": "ocid1.subnet.oc1..s",
        "shape": "VM.Standard.E2.1.Micro", "boot_gb": 50,
        "ssh_public_key": PUB_KEY,
    }
    payload.update(over)
    return payload


@pytest.fixture()
def stub_usage(monkeypatch):
    """把「当前用量」固定住，好单独验证闸门逻辑。"""
    from ocix import freetier as ft
    from ocix.routers import provision

    def _apply(current):
        def fake(profile, plan, compartment_id=None):
            result = ft.preflight(current, plan)
            result["current"] = current
            return result
        monkeypatch.setattr(provision, "preflight_create", fake)
    return _apply


def test_over_quota_creation_is_blocked_server_side(app_client, stub_usage):
    """界面按钮置灰只是提示，真闸门必须在服务端。"""
    from ocix import freetier as ft
    stub_usage(ft.summarize([{"shape": ft.AMD_FREE_SHAPE, "lifecycle-state": "RUNNING"}] * 2,
                            [], [{"size-in-gbs": 50, "lifecycle-state": "AVAILABLE"}] * 2))
    r = app_client.post("/api/provision/instances", json=_create_payload())
    assert r.status_code == 400
    assert "blockers" in r.json()["detail"]


def test_oversized_boot_volume_is_blocked(app_client, stub_usage):
    from ocix import freetier as ft
    stub_usage(ft.summarize([], [], []))
    r = app_client.post("/api/provision/instances", json=_create_payload(boot_gb=300))
    assert r.status_code == 400
    assert any("存储" in b or "引导卷" in b for b in r.json()["detail"]["blockers"])


def test_paid_shape_is_blocked(app_client, stub_usage):
    from ocix import freetier as ft
    stub_usage(ft.summarize([], [], []))
    r = app_client.post("/api/provision/instances",
                        json=_create_payload(shape="VM.Standard.E4.Flex", ocpus=2, memory_gb=16))
    assert r.status_code == 400
    assert any("Always Free" in b for b in r.json()["detail"]["blockers"])


@pytest.mark.parametrize("key", [
    "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
    "not-a-key",
    "",
])
def test_bad_ssh_keys_are_rejected(app_client, key):
    r = app_client.post("/api/provision/instances", json=_create_payload(ssh_public_key=key))
    assert r.status_code == 422


# ── 子网自动化：前端不再让用户选子网 ──

@pytest.fixture()
def stub_launch(monkeypatch, stub_usage):
    """把用量清零并接管 launch，用来观察后端到底往下传了什么。"""
    from ocix import freetier as ft
    from ocix.routers import provision

    stub_usage(ft.summarize([], [], []))
    captured = {}

    def fake_launch(profile, params):
        captured.update(params)
        return {"id": "ocid1.instance.oc1..new", "display-name": params["display_name"],
                "lifecycle-state": "PROVISIONING"}

    monkeypatch.setattr(provision, "launch_instance", fake_launch)
    return captured


def test_subnet_is_resolved_when_client_omits_it(app_client, stub_launch, monkeypatch):
    from ocix.routers import provision
    monkeypatch.setattr(provision, "resolve_subnet",
                        lambda p, c, **kw: {"id": "ocid1.subnet.oc1..auto", "created": False})

    payload = _create_payload()
    payload.pop("subnet_id")
    r = app_client.post("/api/provision/instances", json=payload)
    assert r.status_code == 200, r.text
    assert stub_launch["subnet_id"] == "ocid1.subnet.oc1..auto"


def test_first_instance_creates_the_network(app_client, stub_launch, monkeypatch):
    """账户第一次开机时后台自动建 VCN + 子网，前端不用管。"""
    from ocix.routers import provision
    monkeypatch.setattr(provision, "resolve_subnet",
                        lambda p, c, **kw: {"id": "ocid1.subnet.oc1..fresh", "created": True})

    payload = _create_payload()
    payload.pop("subnet_id")
    r = app_client.post("/api/provision/instances", json=payload)
    assert r.status_code == 200
    assert r.json()["network_created"] is True


def test_network_failure_creates_nothing(app_client, stub_launch, monkeypatch):
    from ocix.oci_cli import OCICLIError
    from ocix.routers import provision

    def boom(*a, **kw):
        raise OCICLIError("配额不足，无法创建 VCN")

    monkeypatch.setattr(provision, "resolve_subnet", boom)
    r = app_client.post("/api/provision/instances", json=_create_payload())
    assert r.status_code == 400
    assert "未创建任何实例" in r.json()["detail"]
    assert not stub_launch


# ── IPv6 ──

def test_checking_ipv6_enables_the_subnet_automatically(app_client, stub_launch, monkeypatch):
    """勾一下就够了，不需要用户再点一次「开通 IPv6」。"""
    from ocix.routers import provision
    calls = []
    monkeypatch.setattr(provision, "resolve_subnet",
                        lambda p, c, **kw: {"id": "ocid1.subnet.oc1..auto", "created": False})
    monkeypatch.setattr(provision, "ensure_subnet_ipv6",
                        lambda p, s, c: calls.append(s) or {"enabled": True, "warnings": []})

    r = app_client.post("/api/provision/instances", json=_create_payload(assign_ipv6=True))
    assert r.status_code == 200
    assert calls == ["ocid1.subnet.oc1..auto"]
    assert stub_launch["assign_ipv6"] is True


def test_ipv6_is_not_touched_when_unchecked(app_client, stub_launch, monkeypatch):
    from ocix.routers import provision
    calls = []
    monkeypatch.setattr(provision, "resolve_subnet",
                        lambda p, c, **kw: {"id": "s", "created": False})
    monkeypatch.setattr(provision, "ensure_subnet_ipv6",
                        lambda p, s, c: calls.append(s) or {"enabled": True})

    app_client.post("/api/provision/instances", json=_create_payload(assign_ipv6=False))
    assert calls == []


def test_ipv6_failure_aborts_before_creating_the_instance(app_client, stub_launch, monkeypatch):
    from ocix.oci_cli import OCICLIError
    from ocix.routers import provision

    def boom(*a, **kw):
        raise OCICLIError("该区域不支持 IPv6")

    monkeypatch.setattr(provision, "resolve_subnet",
                        lambda p, c, **kw: {"id": "s", "created": False})
    monkeypatch.setattr(provision, "ensure_subnet_ipv6", boom)

    r = app_client.post("/api/provision/instances", json=_create_payload(assign_ipv6=True))
    assert r.status_code == 400
    assert "未创建实例" in r.json()["detail"]
    assert not stub_launch


def test_add_ipv6_to_existing_instance(app_client, monkeypatch):
    from ocix.routers import provision
    monkeypatch.setattr(provision, "add_ipv6_to_instance",
                        lambda p, i, c: {"ipv6": "2603:c020::1", "changed": True, "warnings": []})

    r = app_client.post("/api/provision/instances/add-ipv6",
                        json={"profile": "EXISTING", "instance_id": "ocid1.instance.oc1..x"})
    assert r.status_code == 200
    assert r.json()["ipv6"] == "2603:c020::1"


def test_add_ipv6_is_idempotent(app_client, monkeypatch):
    from ocix.routers import provision
    monkeypatch.setattr(provision, "add_ipv6_to_instance",
                        lambda p, i, c: {"ipv6": "2603:c020::1", "changed": False, "warnings": []})

    r = app_client.post("/api/provision/instances/add-ipv6",
                        json={"profile": "EXISTING", "instance_id": "ocid1.instance.oc1..x"})
    assert r.status_code == 200
    assert r.json()["changed"] is False


def test_add_ipv6_failure_is_400(app_client):
    """没有 oci CLI 时应报可读错误而不是 500。"""
    r = app_client.post("/api/provision/instances/add-ipv6",
                        json={"profile": "EXISTING", "instance_id": "ocid1.instance.oc1..x"})
    assert r.status_code == 400


def test_invalid_instance_action_is_rejected(app_client):
    r = app_client.post("/api/instances/action",
                        json={"profile": "EXISTING", "instance_id": "x", "action": "DROP"})
    assert r.status_code == 422


# ── 依赖 oci CLI 的接口要给出可读错误，而不是 500 ──

def test_missing_cli_surfaces_as_400(app_client):
    r = app_client.get("/api/instances?profile=EXISTING")
    assert r.status_code == 400 and "oci" in r.text


def test_multi_account_view_degrades_per_account(app_client):
    r = app_client.get("/api/instances/all")
    assert r.status_code == 200
    assert r.json()["accounts"][0]["error"]


def test_metrics_failure_is_400_not_500(app_client):
    r = app_client.get("/api/monitor/metrics?profile=EXISTING&instance_id=ocid1.instance.oc1..x")
    assert r.status_code == 400


# ── 静态文件与 SPA 回退 ──

def test_spa_root_is_served(anon_client):
    assert anon_client.get("/").status_code == 200


def test_client_side_routes_fall_back_to_index(anon_client):
    assert anon_client.get("/dashboard").status_code == 200


def test_missing_asset_404s_instead_of_returning_html(anon_client):
    """否则浏览器会把 index.html 当 JS 执行，报一堆莫名其妙的语法错。"""
    assert anon_client.get("/assets/vue.global.prod.js").status_code == 404


@pytest.mark.parametrize("path", [
    "/%2e%2e/pyproject.toml",
    "/..%2f..%2fetc%2fpasswd",
    "/%2e%2e%2f%2e%2e%2fVERSION",
])
def test_path_traversal_is_blocked(anon_client, path):
    r = anon_client.get(path)
    assert r.status_code == 404


def test_unknown_api_path_404s(anon_client):
    assert anon_client.get("/api/nope").status_code == 404
