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




# ── 前端：单个账户的等级检测 ──

def test_single_account_tier_button_exists_and_guards_the_bulk_sweep():
    """账户列表的操作栏要能单独检测一个号的等级。

    整表重测是**串行**的（同一时刻只让一个号跟 OCI 通信），号一多就得等很久，
    只想确认某一个号时太亏。
    """
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1]
            / "src" / "ocix" / "web" / "index.html").read_text(encoding="utf-8")
    assert "async function loadOneTier(name)" in html, "缺少单账户检测"
    assert "isBusy('tier:' + row.name)" in html, "按钮要有自己的 loading 状态"
    # 整表在跑的时候不能再单独发起一个，否则两边同时往 tiers 里写
    assert ':disabled="tierBusy"' in html, "整表检测期间单行按钮要禁用"


def test_unknown_tier_is_not_reported_as_success():
    """判不出等级时不能弹绿色的「成功」。

    踩过：接口判不出来时返回的是 200 + tier:unknown（不是报错），
    于是走了成功分支，弹出「成功：无法确定」——荒唐，而且会让人以为查到了。
    """
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1]
            / "src" / "ocix" / "web" / "index.html").read_text(encoding="utf-8")
    i = html.index("async function loadOneTier(name)")
    body = html[i : html.index("async function loadAllTiers", i)]
    assert "data.tier === 'unknown'" in body, "要单独判 unknown"
    assert "ElMessage.warning" in body, "unknown 应当用警告色"



def test_tier_is_never_detected_automatically():
    """账户等级只在点击时检测，任何页面切换都不能触发。

    等级要往 OCI 打一次订阅查询。原来进「账户配置」会整表扫一遍、
    进「免费额度」会查一次、换账户还会再查一次——而这个面板承诺
    「不主动操作就不在后台请求 OCI」，这三处是自相矛盾的。

    判定方式：带 false 的调用就是当初那三处自动加载（手动入口一律传 true）。
    """
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1]
            / "src" / "ocix" / "web" / "index.html").read_text(encoding="utf-8")

    assert "loadTier(false)" not in html, "还有地方在自动查单账户等级"
    assert "loadAllTiers(false)" not in html, "还有地方在自动整表扫等级"
    # 手动入口必须还在
    assert '@click="loadTier(true)"' in html, "免费额度页的检测按钮不见了"
    assert '@click="loadAllTiers(true)"' in html, "账户配置页的整表检测按钮不见了"
    assert "loadOneTier(row.name)" in html, "单行检测按钮不见了"

# ── Oracle 账号（控制台登录）的密码有效期 ──
#
# 这是 Oracle 侧 Identity Domain 里的 passwordExpiresAfter，免费租户默认 120 天，
# 跟面板自己的登录密码是两码事。

def test_reads_console_password_policy(fake):
    H.invalidate_read_cache()
    res = H.console_password_policy("P")
    assert res["supported"] is True
    pol = res["policies"][0]
    assert pol["expires_after_days"] == 120
    assert pol["domain_name"] == "Default"


def test_set_console_password_never_expires(fake):
    H.invalidate_read_cache()
    res = H.set_console_password_expiry("P", 0)
    assert res["days"] == 0
    assert res["changed"]
    # 再读一次应当已经是「永不过期」
    H.invalidate_read_cache()
    assert H.console_password_policy("P")["policies"][0]["expires_after_days"] == 0


def test_set_console_password_specific_days(fake):
    H.invalidate_read_cache()
    H.set_console_password_expiry("P", 365)
    H.invalidate_read_cache()
    assert H.console_password_policy("P")["policies"][0]["expires_after_days"] == 365


def test_console_policy_reports_classic_iam_clearly(fake):
    """经典 IAM 租户没有 Identity Domain，要说清楚而不是报个空。"""
    fake.domains = []
    H.invalidate_read_cache()
    res = H.console_password_policy("P")
    assert res["supported"] is False
    assert "经典 IAM" in res["error"]


def test_console_policy_surfaces_permission_error(fake):
    def boom(profile, compartment_id):
        raise OCIError("NotAuthorizedOrNotFound")
    fake.list_domains = boom
    H.invalidate_read_cache()
    res = H.console_password_policy("P")
    assert res["supported"] is False
    assert "NotAuthorizedOrNotFound" in res["error"]


def test_console_policy_endpoint(app_client, live_backend):
    body = app_client.get("/api/profiles/EXISTING/console-password-policy").json()
    assert body["policies"][0]["expires_after_days"] == 120

    r = app_client.put("/api/profiles/EXISTING/console-password-policy", json={"days": 0})
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 0
    assert r.json()["policy"]["policies"][0]["expires_after_days"] == 0


def test_console_policy_rejects_bad_days(app_client, live_backend):
    assert app_client.put("/api/profiles/EXISTING/console-password-policy",
                          json={"days": -1}).status_code == 422


def test_login_and_me_both_carry_the_version(app_client):
    """侧栏要显示版本号。登录后不会再调 /auth/me，所以两个接口都得带上。"""
    me = app_client.get("/api/auth/me").json()
    assert me["version"]
    login = app_client.post("/api/auth/login",
                            json={"username": "admin", "password": "devpass123"}).json()
    assert login["version"] == me["version"]


def test_account_gate_serialises_across_accounts():
    """同一时刻只允许一个账户在跟 OCI 通信。"""
    import threading
    import time as _t

    from ocix.common import account_gate

    active, peak, lock = [0], [0], threading.Lock()

    def work(profile):
        with account_gate(profile):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            _t.sleep(0.05)
            with lock:
                active[0] -= 1

    threads = [threading.Thread(target=work, args=(f"P{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1, f"同时有 {peak[0]} 个账户在请求"


def test_account_gate_is_reentrant_for_the_same_account():
    """同一账户内部嵌套调用不能把自己锁死。"""
    from ocix.common import account_gate
    with account_gate("P"):
        with account_gate("P"):
            pass


# ── 锁定账户 ──

def test_lock_and_unlock_a_profile(app_client):
    assert app_client.get("/api/profiles/lock").json()["locked"] is None
    assert app_client.post("/api/profiles/EXISTING/lock").status_code == 200
    assert app_client.get("/api/profiles/lock").json()["locked"] == "EXISTING"

    # 回归：DELETE /lock 曾被 DELETE /{name} 抢先匹配，
    # 变成「删除一个叫 lock 的账户」，解锁永远不生效
    assert app_client.delete("/api/profiles/lock").status_code == 200
    assert app_client.get("/api/profiles/lock").json()["locked"] is None


def test_unlocking_does_not_delete_a_profile(app_client):
    """把上面那个路由顺序的坑单独钉死。"""
    app_client.post("/api/profiles/EXISTING/lock")
    app_client.delete("/api/profiles/lock")
    names = [p["name"] for p in app_client.get("/api/profiles").json()["profiles"]]
    assert "EXISTING" in names, "解锁把账户删了"


def test_locking_an_unknown_profile_is_rejected(app_client):
    assert app_client.post("/api/profiles/NOPE/lock").status_code == 404


def test_lock_clears_itself_when_the_profile_is_gone(app_client):
    """账户被删掉后锁定要自动失效，否则界面卡在一个不存在的账户上。"""
    from ocix.db import set_setting
    app_client.post("/api/profiles/EXISTING/lock")
    set_setting("locked_profile", "DELETED-ONE")
    assert app_client.get("/api/profiles/lock").json()["locked"] is None


# ── 当月流量 ──

def test_egress_sums_hourly_buckets(fake):
    """VnicToNetworkBytes 是每段区间的字节数，直接求和即可。"""
    gb = 1024 ** 3
    fake.metrics = [{"name": "VnicToNetworkBytes", "aggregated_datapoints": [
        {"timestamp": "2026-08-01T00:00:00Z", "value": 2 * gb},
        {"timestamp": "2026-08-01T01:00:00Z", "value": 3 * gb},
    ]}]
    H.invalidate_read_cache()
    res = H.egress_usage("P", "cid")
    assert res["egress_gb"] == 5.0
    assert res["limit_gb"] == 10 * 1024
    assert "上限估算" in res["note"]


def test_egress_ignores_negative_and_nan(fake):
    """计数器回绕或 NaN 会把总量算飞。"""
    gb = 1024 ** 3
    fake.metrics = [{"name": "VnicToNetworkBytes", "aggregated_datapoints": [
        {"timestamp": "t1", "value": 1 * gb},
        {"timestamp": "t2", "value": -5 * gb},
        {"timestamp": "t3", "value": float("nan")},
    ]}]
    H.invalidate_read_cache()
    assert H.egress_usage("P", "cid")["egress_gb"] == 1.0


def test_egress_failure_is_reported_not_raised(fake):
    def boom(*a, **kw):
        raise OCIError("no monitoring permission")
    fake.summarize_metrics = boom
    H.invalidate_read_cache()
    res = H.egress_usage("P", "cid")
    assert res["egress_gb"] == 0
    assert "no monitoring permission" in res["error"]


# ── 账单 ──

def _inv(**over):
    row = {"invoice_id": "inv1", "invoice_number": "OCI-001", "is_paid": False,
           "invoice_status": "OPEN", "invoice_type": "SUBSCRIPTION",
           "currency": {"currency_code": "USD"},
           "invoice_amount": 12.5, "invoice_amount_due": 12.5,
           "time_invoice": "2026-07-01T00:00:00+00:00",
           "time_invoice_due": "2026-07-31T00:00:00+00:00"}
    row.update(over)
    return row


def test_invoice_states_are_classified(fake):
    """待支付 / 已支付 / 已逾期 三种要分清。"""
    future = "2099-01-01T00:00:00+00:00"
    fake.invoices = [
        _inv(invoice_id="a", is_paid=True),                       # 已支付
        _inv(invoice_id="b", is_paid=False, time_invoice_due=future),   # 未到期 -> 待支付
        _inv(invoice_id="c", is_paid=False),                      # 已过期 -> 已逾期
    ]
    H.invalidate_read_cache()
    res = H.list_invoices("P")
    states = {x["invoice_id"]: x["state"] for x in res["invoices"]}
    assert states == {"a": "paid", "b": "unpaid", "c": "overdue"}
    assert res["summary"]["paid"] == 1
    assert res["summary"]["unpaid"] == 1
    assert res["summary"]["overdue"] == 1


def test_paid_invoice_wins_over_due_date(fake):
    """已付清的账单即使早过了到期日，也不能算逾期。"""
    fake.invoices = [_inv(is_paid=True, time_invoice_due="2020-01-01T00:00:00+00:00")]
    H.invalidate_read_cache()
    assert H.list_invoices("P")["invoices"][0]["state"] == "paid"


def test_free_account_has_no_invoices_and_that_is_fine(fake):
    """免费号没有订阅，查不到账单是正确答案，不该报成故障。"""
    def boom(*a, **kw):
        raise OCIError("NotAuthorizedOrNotFound", 404, "NotAuthorizedOrNotFound")
    fake.list_invoices = boom
    H.invalidate_read_cache()
    res = H.list_invoices("P")
    assert res["unavailable"] is True
    assert res["invoices"] == []
    assert "read invoices in tenancy" in res["note"]


def test_currency_object_is_flattened(fake):
    """currency 回的是对象，直接塞给前端会显示成 [object Object]。"""
    fake.invoices = [_inv()]
    H.invalidate_read_cache()
    assert H.list_invoices("P")["invoices"][0]["currency"] == "USD"


def test_month_cost_groups_by_service(fake):
    fake.usage_items = [
        {"service": "Compute", "computed_amount": 1.25, "currency": "USD",
         "time_usage_started": "2026-08-01T00:00:00+00:00"},
        {"service": "Compute", "computed_amount": 0.75, "currency": "USD",
         "time_usage_started": "2026-08-02T00:00:00+00:00"},
        # 故意和 Compute 合计（2.0）拉开差距，否则并列时排序就没有确定答案
        {"service": "Block Storage", "computed_amount": 3.0, "currency": "USD",
         "time_usage_started": "2026-08-02T00:00:00+00:00"},
    ]
    H.invalidate_read_cache()
    res = H.month_cost("P")
    assert res["total"] == 5.0
    assert res["currency"] == "USD"
    assert res["by_service"][0] == {"service": "Block Storage", "amount": 3.0}
    assert len(res["daily"]) == 2


def test_month_cost_failure_is_reported_not_raised(fake):
    def boom(*a, **kw):
        raise OCIError("no usage permission")
    fake.summarize_usage = boom
    H.invalidate_read_cache()
    res = H.month_cost("P")
    assert res["total"] == 0.0
    assert "no usage permission" in res["error"]


def test_billing_endpoints(app_client, live_backend):
    live_backend.invoices = [_inv(is_paid=True)]
    live_backend.usage_items = [{"service": "Compute", "computed_amount": 1.0,
                                 "currency": "USD",
                                 "time_usage_started": "2026-08-01T00:00:00+00:00"}]
    inv = app_client.get("/api/monitor/invoices?profile=EXISTING")
    assert inv.status_code == 200, inv.text
    assert inv.json()["summary"]["paid"] == 1

    cost = app_client.get("/api/monitor/cost?profile=EXISTING")
    assert cost.status_code == 200
    assert cost.json()["total"] == 1.0

    eg = app_client.get("/api/monitor/egress?profile=EXISTING")
    assert eg.status_code == 200
    assert eg.json()["limit_gb"] == 10 * 1024
