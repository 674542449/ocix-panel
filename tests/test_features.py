"""账户等级、一键更新、密码有效期。"""

import json
import time

import pytest

from fakes import FakeBackend
from ocix import oci_helpers as H
from ocix.backends import set_backend


@pytest.fixture()
def fake():
    b = FakeBackend()
    set_backend(b)
    original = H.tenancy_of
    H.tenancy_of = lambda p: "root"
    yield b
    H.tenancy_of = original
    set_backend(None)


# ── 账户等级 ──

def test_pure_free_account_is_detected(fake):
    """免费号：除 micro / A1 外的机型配额全是 0。"""
    res = H.account_tier("P")
    assert res["tier"] == "free"
    assert "Always Free" in res["label"]
    assert not res["paid_shape_limits"]


def test_upgraded_account_is_detected(fake):
    """一旦有付费机型拿到非零配额，就不是纯免费号了。"""
    fake.limit_values = [
        {"name": "standard-e2-micro-core-count", "value": 2},
        {"name": "standard-a1-core-count", "value": 4},
        {"name": "standard-e4-core-count", "value": 20},
    ]
    res = H.account_tier("P")
    assert res["tier"] == "paid"
    assert any("standard-e4-core-count" in r for r in res["reasons"])


def test_subscription_tier_alone_marks_paid(fake):
    """限额看不出来，但订阅信息写着按量付费时也算已升级。"""
    fake.subscriptions = [{"service_name": "PIC", "subscription_tier": "PAYG",
                           "payment_model": "PAY_AS_YOU_GO"}]
    assert H.account_tier("P")["tier"] == "paid"


def test_free_shape_limits_are_not_counted_as_paid(fake):
    """A1 / micro 本来就是免费额度的一部分，不能当成升级证据。"""
    fake.limit_values = [
        {"name": "standard-a1-core-count", "value": 4},
        {"name": "standard-e2-micro-core-count", "value": 2},
    ]
    res = H.account_tier("P")
    assert res["tier"] == "free"
    assert {x["name"] for x in res["free_shape_limits"]} == {
        "standard-a1-core-count", "standard-e2-micro-core-count"}


def test_tier_is_unknown_when_nothing_can_be_read(fake):
    """两条线都没数据时如实说不确定，不要瞎猜一个结论。"""
    fake.limit_values = []
    fake.subscriptions = []
    assert H.account_tier("P")["tier"] == "unknown"


def test_non_count_limits_are_ignored(fake):
    """存储、带宽之类的限额跟账户等级无关，混进来会误判。"""
    fake.limit_values = [
        {"name": "standard-e2-micro-core-count", "value": 2},
        {"name": "total-storage-gb", "value": 200},
        # 这两个非零但跟机型无关，早先只判 -count 时会被当成「有付费配额」
        {"name": "vcn-count", "value": 5},
        {"name": "vnic-count", "value": 20},
    ]
    res = H.account_tier("P")
    assert res["tier"] == "free", res["reasons"]
    assert res["checked_limits"] == 1
    assert not res["paid_shape_limits"]


def test_tier_endpoint(app_client, live_backend):
    r = app_client.get("/api/profiles/EXISTING/tier")
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "free"


def test_tier_endpoint_rejects_bad_profile_name(app_client, live_backend):
    assert app_client.get("/api/profiles/..%2Fetc/tier").status_code in (400, 404)


# ── 密码有效期 ──

def _set_changed_at(seconds_ago: int):
    from ocix.db import set_setting
    set_setting("admin_password_changed_at", str(int(time.time()) - seconds_ago))


def test_password_policy_defaults_to_120_days(app_client):
    body = app_client.get("/api/auth/password-policy").json()
    assert body["max_age_days"] == 120
    assert body["expired"] is False


def test_zero_means_never_expires(app_client):
    app_client.put("/api/auth/password-policy", json={"max_age_days": 0})
    _set_changed_at(9999 * 86400)
    body = app_client.get("/api/auth/password-policy").json()
    assert body["max_age_days"] == 0
    assert body["expired"] is False
    assert body["days_left"] is None
    # 关掉有效期后业务接口照常可用
    assert app_client.get("/api/profiles").status_code == 200


def test_expired_password_blocks_business_endpoints(app_client):
    app_client.put("/api/auth/password-policy", json={"max_age_days": 1})
    _set_changed_at(2 * 86400)
    r = app_client.get("/api/profiles")
    assert r.status_code == 403
    assert "有效期" in r.json()["detail"]


def test_expired_password_still_allows_me_and_change_password(app_client):
    """过期后必须还能看状态和改密，否则就把自己锁死在门外了。"""
    app_client.put("/api/auth/password-policy", json={"max_age_days": 1})
    _set_changed_at(2 * 86400)

    me = app_client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["password"]["expired"] is True

    r = app_client.post("/api/auth/change-password",
                        json={"old_password": "devpass123", "new_password": "brand-new-pass-1"})
    assert r.status_code == 200, r.text


def test_changing_password_clears_expiry(app_client):
    app_client.put("/api/auth/password-policy", json={"max_age_days": 1})
    _set_changed_at(2 * 86400)
    app_client.post("/api/auth/change-password",
                    json={"old_password": "devpass123", "new_password": "brand-new-pass-1"})
    # 改密后令牌失效，重新登录再看
    tok = app_client.post("/api/auth/login",
                          json={"username": "admin", "password": "brand-new-pass-1"})
    assert tok.status_code == 200
    assert tok.json()["password"]["expired"] is False
    app_client.headers.update({"Authorization": "Bearer " + tok.json()["token"]})
    assert app_client.get("/api/profiles").status_code == 200


def test_days_left_rounds_up(app_client):
    """还剩几小时也该显示「还有 1 天」，显示 0 会让人以为已经过期。"""
    app_client.put("/api/auth/password-policy", json={"max_age_days": 10})
    _set_changed_at(int(9.5 * 86400))
    assert app_client.get("/api/auth/password-policy").json()["days_left"] == 1


def test_policy_rejects_out_of_range(app_client):
    assert app_client.put("/api/auth/password-policy",
                          json={"max_age_days": -1}).status_code == 422
    assert app_client.put("/api/auth/password-policy",
                          json={"max_age_days": 99999}).status_code == 422


def test_upgrade_from_old_version_is_not_instantly_expired(app_client):
    """老库里没有改密时间字段，缺了不能一升级就判过期。"""
    from ocix import security
    from ocix.db import get_setting, set_setting
    set_setting("admin_password_changed_at", "")   # 老库里没有这个字段
    security.bootstrap_admin()
    assert get_setting("admin_password_changed_at")
    assert security.password_expired() is False


# ── 一键更新 ──

def _control(app_client):
    from ocix.config import CONTROL_DIR
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    return CONTROL_DIR


def _agent_alive(app_client, alive=True):
    d = _control(app_client)
    f = d / "agent.alive"
    f.write_text("", encoding="utf-8")
    if not alive:
        import os
        old = time.time() - 600
        os.utime(f, (old, old))
    return d


def test_update_refused_when_agent_is_offline(app_client):
    _agent_alive(app_client, alive=False)
    r = app_client.post("/api/system/update")
    assert r.status_code == 409
    assert "代理" in r.json()["detail"]


def test_update_writes_only_a_request_marker(app_client):
    """面板绝不执行命令，只写标记；代理跑的是固定的 update.sh。"""
    d = _agent_alive(app_client)
    r = app_client.post("/api/system/update")
    assert r.status_code == 200, r.text
    payload = json.loads((d / "update.request").read_text(encoding="utf-8"))
    assert payload["requested_by"] == "admin"
    assert "requested_at" in payload


def test_update_rejected_while_one_is_running(app_client):
    d = _agent_alive(app_client)
    (d / "update.status").write_text(json.dumps({"state": "running"}), encoding="utf-8")
    r = app_client.post("/api/system/update")
    assert r.status_code == 409
    assert "进行中" in r.json()["detail"]


def test_update_status_reports_agent_offline(app_client):
    _agent_alive(app_client, alive=False)
    body = app_client.get("/api/system/update/status").json()
    assert body["agent"]["online"] is False
    assert body["agent"]["hint"]


def test_update_status_survives_a_corrupt_status_file(app_client):
    """代理写到一半被打断也不能让接口 500。"""
    d = _agent_alive(app_client)
    (d / "update.status").write_text('{"state": "run', encoding="utf-8")
    r = app_client.get("/api/system/update/status")
    assert r.status_code == 200
    assert r.json()["state"] == "idle"


def test_update_log_is_returned_with_ansi_stripped(app_client):
    d = _agent_alive(app_client)
    (d / "update.log").write_text("\x1b[32mOK\x1b[0m 拉取完成\n保留 [0-9] 文本",
                                  encoding="utf-8")
    body = app_client.get("/api/system/update/status").json()
    assert "\x1b" not in body["log"]
    assert "OK 拉取完成" in body["log"]
    # 别把日志里正常的方括号内容也吃掉
    assert "[0-9]" in body["log"]


def test_update_log_tolerates_invalid_utf8(app_client):
    d = _agent_alive(app_client)
    (d / "update.log").write_bytes(b"\xff\xfe bad bytes \xc3\x28")
    r = app_client.get("/api/system/update/status")
    assert r.status_code == 200
    assert "bad bytes" in r.json()["log"]


def test_expired_response_carries_a_machine_readable_header(app_client):
    """前端靠这个头把用户送到密码页；匹配中文文案太脆，改个字就失灵。"""
    app_client.put("/api/auth/password-policy", json={"max_age_days": 1})
    _set_changed_at(2 * 86400)
    r = app_client.get("/api/profiles")
    assert r.status_code == 403
    assert r.headers.get("X-OCIX-Password-Expired") == "1"


def test_normal_403_has_no_expiry_header(app_client):
    """没过期时不该带这个头，否则前端会莫名其妙跳到密码页。"""
    r = app_client.get("/api/profiles")
    assert r.status_code == 200
    assert "X-OCIX-Password-Expired" not in r.headers
