"""创建实例走后台任务这条路的接口行为。

为什么要改：这条链路同步跑一分多钟起步，挂在 Cloudflare 后面时对方
100 秒不回就给访客一个 524「源站超时」页，而请求并没有被取消——
实例照样建出来，用户看到的却是报错，然后重试，于是又建一台。
"""

import time

PUB_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyForTests test@example"


def _spec(name="web-1", **kw):
    body = {
        "profile": "EXISTING",
        "compartment_id": "cid",
        "display_name": name,
        "availability_domain": "AD-1",
        "image_id": "img-ubuntu",
        "shape": "VM.Standard.E2.1.Micro",
        "ocpus": 1,
        "memory_gb": 1,
        "boot_gb": 50,
        "ssh_public_key": PUB_KEY,
        "assign_ipv6": False,
        "open_all_ports": False,
    }
    body.update(kw)
    return body


def _drain(client, job_id, timeout=10):
    """轮询到任务结束，返回快照。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/provision/jobs/{job_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last["state"] != "running":
            return last
        time.sleep(0.05)
    raise AssertionError(f"任务没在 {timeout}s 内结束，最后一次：{last}")


def test_create_returns_a_job_id_immediately(app_client, live_backend):
    """接口必须马上回，不能同步等——这正是 Cloudflare 524 的根源。"""
    t0 = time.time()
    r = app_client.post("/api/provision/instances", json=_spec())
    cost = time.time() - t0

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"] and body["started"] is True
    assert cost < 2, f"提交花了 {cost:.2f}s，应当立刻返回任务号"

    snap = _drain(app_client, body["job_id"])
    assert snap["state"] == "ok", snap
    assert snap["result"]["ok"] is True
    assert [s["text"] for s in snap["steps"]][0] == "核算免费额度"
    assert snap["steps"][-1]["text"] == "完成"


def test_double_submit_while_one_is_running_creates_only_one(app_client, live_backend,
                                                             monkeypatch):
    """手快点两下、或者第一单还在跑时又提交一次，都不该变成两台机器。

    去重挡的是**同时在飞**的重复提交。所以这里得把第一单摁住不让它跑完，
    否则在快机器上它眨眼就结束了，第二次提交自然就不算重复——
    CI 上就是这么翻的（本地过、CI 挂），不是偶发。
    """
    import threading

    from ocix.routers import provision

    hold = threading.Event()
    real = provision.launch_instance

    def slow_launch(profile, params):
        hold.wait(timeout=10)          # 摁住，保证第二次提交时它还在跑
        return real(profile, params)

    monkeypatch.setattr(provision, "launch_instance", slow_launch)

    a = app_client.post("/api/provision/instances", json=_spec("dup-1"))
    b = app_client.post("/api/provision/instances", json=_spec("dup-1"))
    assert a.status_code == 202 and b.status_code == 202
    assert a.json()["job_id"] == b.json()["job_id"], "第二次提交应当拿回同一个任务号"
    assert b.json()["started"] is False

    hold.set()
    snap = _drain(app_client, a.json()["job_id"])
    assert snap["state"] == "ok", snap
    assert live_backend.count("launch_instance") == 1, "只应当下单一次"


def test_resubmit_after_it_finished_is_allowed(app_client, live_backend):
    """跑完之后再提交同名任务是允许的——用户可能真的要再建一台。

    也就是说去重只覆盖「还在飞」的那段，不是永久的。写清楚免得被误读。
    """
    a = app_client.post("/api/provision/instances", json=_spec("again-1"))
    _drain(app_client, a.json()["job_id"])
    b = app_client.post("/api/provision/instances", json=_spec("again-1"))
    assert b.json()["job_id"] != a.json()["job_id"]
    assert b.json()["started"] is True
    _drain(app_client, b.json()["job_id"])
    assert live_backend.count("launch_instance") == 2


def test_quota_blockers_come_back_through_the_job(app_client, live_backend):
    """额度闸门没松，只是判定结果改从任务里回。"""
    # 已经占掉 170GB，再要 50GB 就是 220 > 200，必然被拦
    live_backend.boot_volumes = [{
        "id": "bv-1", "size-in-gbs": 170, "lifecycle-state": "AVAILABLE",
        "display-name": "bv-1", "compartment-id": "cid", "availability-domain": "AD-1",
    }]
    r = app_client.post("/api/provision/instances", json=_spec("too-big"))
    assert r.status_code == 202, r.text
    snap = _drain(app_client, r.json()["job_id"])
    assert snap["state"] == "failed"
    assert snap["error"]["blockers"], snap
    assert live_backend.count("launch_instance") == 0, "被闸门拦下就不该下单"


def test_job_endpoint_requires_auth(anon_client):
    assert anon_client.get("/api/provision/jobs/whatever").status_code == 401


def test_unknown_job_is_404(app_client):
    assert app_client.get("/api/provision/jobs/nope").status_code == 404
