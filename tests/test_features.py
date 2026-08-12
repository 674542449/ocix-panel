"""账户等级、一键更新、密码有效期。"""

import json
import time

import pytest

from fakes import FakeBackend
from ocix import oci_helpers as H
from ocix.backends import set_backend
from ocix.common import OCIError


def fake_tier(_fake):
    """每次都要清缓存，否则第二个用例读到的是上一个的结论。"""
    H.invalidate_read_cache()
    return H.account_tier("P")


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
#
# 结论只看订阅记录。**服务限额不能用来判断**：Oracle 在纯免费账号上
# 也会给付费机型返回非零限额，早先拿它当证据，免费号被判成了已升级。

def _sub(payment_model="", tier="", promo_status=None):
    detail = {"subscription_tier": tier}
    if promo_status:
        detail["promotion"] = [{"status": promo_status}]
    return ([{"id": "sub1", "payment_model": payment_model}],
            {"sub1": detail})


def test_payg_payment_model_means_paid(fake):
    fake.subscriptions, fake.subscription_details = _sub(payment_model="PAY_AS_YOU_GO")
    res = fake_tier(fake)
    assert res["tier"] == "paid"
    assert "付费" in res["label"]


@pytest.mark.parametrize("model", [
    "Pay as you go",   # OCI 文档里的实际写法
    "PAY_AS_YOU_GO",   # 下划线变体也得认
    "Monthly", "ANNUAL", "COMMIT", "PayG",
])
def test_other_paid_payment_models(fake, model):
    fake.subscriptions, fake.subscription_details = _sub(payment_model=model)
    assert fake_tier(fake)["tier"] == "paid"


def test_free_tier_subscription(fake):
    fake.subscriptions, fake.subscription_details = _sub(payment_model="FREE_TIER")
    res = fake_tier(fake)
    assert res["tier"] == "free"
    assert "Always Free" in res["label"]


def test_promo_trial_counts_as_free(fake):
    fake.subscriptions, fake.subscription_details = _sub(tier="PROMOTIONAL_TRIAL")
    assert fake_tier(fake)["tier"] == "free"


def test_paid_limits_do_not_make_a_free_account_look_upgraded(fake):
    """这就是用户遇到的误判：免费账号上付费机型的限额本来就可能非零。"""
    fake.subscriptions, fake.subscription_details = _sub(payment_model="FREE_TIER")
    fake.limit_values = [
        {"name": "standard-e2-micro-core-count", "value": 2},
        {"name": "standard-a1-core-count", "value": 4},
        {"name": "standard-e4-core-count", "value": 20},     # 免费号上也可能有
        {"name": "standard3-core-count", "value": 16},
    ]
    res = fake_tier(fake)
    assert res["tier"] == "free", res["reasons"]
    # 限额仍然照常展示，只是不参与结论
    assert any(x["name"] == "standard-e4-core-count" for x in res["limits"])
    assert "不能作为付费依据" in res["limits_note"]


def test_limits_alone_never_decide_the_verdict(fake):
    """没有订阅记录时，哪怕限额再漂亮也只能说「无法确定」。"""
    fake.subscriptions, fake.subscription_details = [], {}
    fake.limit_values = [{"name": "standard-e4-core-count", "value": 64}]
    res = fake_tier(fake)
    assert res["tier"] == "unknown"
    assert any("inspect subscriptions" in r for r in res["reasons"])


def test_unknown_when_subscription_query_fails(fake):
    def boom(profile, compartment_id):
        raise OCIError("NotAuthorizedOrNotFound")
    fake.list_subscriptions = boom
    res = fake_tier(fake)
    assert res["tier"] == "unknown"
    assert any("订阅查询失败" in r for r in res["reasons"])


def test_verdict_survives_get_subscription_failure(fake):
    """详情拿不到不影响主判断——payment_model 在列表里就有了。"""
    fake.subscriptions, fake.subscription_details = _sub(payment_model="PAY_AS_YOU_GO")

    def boom(profile, subscription_id):
        raise OCIError("no permission")
    fake.get_subscription = boom
    assert fake_tier(fake)["tier"] == "paid"


def test_limit_failure_does_not_break_the_verdict(fake):
    fake.subscriptions, fake.subscription_details = _sub(payment_model="FREE_TIER")

    def boom(profile, compartment_id, service_name):
        raise OCIError("limits unreadable")
    fake.list_limit_values = boom
    res = fake_tier(fake)
    assert res["tier"] == "free"
    assert res["limit_error"]


def test_tier_endpoint(app_client, live_backend):
    live_backend.subscriptions = [{"id": "sub1", "payment_model": "FREE_TIER"}]
    live_backend.subscription_details = {"sub1": {"subscription_tier": ""}}
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


def test_list_view_skips_the_limits_call(fake):
    """列表只显示一个标签，不该为它多查一次限额。"""
    fake.subscriptions, fake.subscription_details = _sub(payment_model="Free Tier")
    H.invalidate_read_cache()
    res = H.account_tier("P", with_limits=False)
    assert res["tier"] == "free"
    assert res["limits"] == []
    assert fake.count("list_limit_values") == 0


def test_limits_and_no_limits_are_cached_separately(fake):
    """两种口径的结果不能串味：先要精简版，再要完整版得真去查。"""
    fake.subscriptions, fake.subscription_details = _sub(payment_model="Free Tier")
    H.invalidate_read_cache()
    H.account_tier("P", with_limits=False)
    full = H.account_tier("P", with_limits=True)
    assert full["limits"], "完整版应当带上限额"
    assert fake.count("list_limit_values") == 1


def test_tier_endpoint_honours_limits_flag(app_client, live_backend):
    live_backend.subscriptions = [{"id": "s1", "payment_model": "Free Tier"}]
    live_backend.subscription_details = {"s1": {}}
    body = app_client.get("/api/profiles/EXISTING/tier?limits=false").json()
    assert body["limits"] == []
    assert live_backend.count("list_limit_values") == 0
