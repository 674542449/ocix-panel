"""创建实例改成后台任务之后的行为。

背景：这条链路同步跑要一分多钟——建 VCN / 网关 / 子网各等一次
（``oci.wait_until`` 每次上限 180 秒），实例建出来还要等网卡挂好才能
分配 IPv6（再 90 秒）。而面板挂在 Cloudflare 后面时，对方 100 秒拿不到
响应就直接给访客一个 524「源站超时」页，**请求却没被取消**：实例照样
建出来，用户看到的是报错，然后重试，于是又建一台。

所以这里盯两件事：接口必须立刻返回，以及重复提交不能变成两台机器。
"""

import threading
import time

import pytest

from ocix import jobs
from ocix.common import OCIError
from ocix.jobs import JobError


@pytest.fixture(autouse=True)
def _clean():
    jobs.reset_for_tests()
    yield
    jobs.reset_for_tests()


def test_submit_returns_immediately_even_if_the_work_is_slow():
    """接口要立刻回，不能等活儿干完——这正是 524 的根源。"""
    release = threading.Event()

    def slow(job):
        job.step("正在磨蹭")
        release.wait(timeout=5)
        return {"ok": True}

    t0 = time.time()
    job, fresh = jobs.submit("create-instance", "P", ("k",), slow)
    submit_cost = time.time() - t0

    assert fresh is True
    assert submit_cost < 0.5, f"提交花了 {submit_cost:.2f}s，应当立刻返回"
    assert job.snapshot()["state"] == "running"
    release.set()


def test_same_key_does_not_start_a_second_job():
    """同名同账户重复提交拿回同一个任务，不能建出两台。"""
    release = threading.Event()
    started = []

    def slow(job):
        started.append(1)
        release.wait(timeout=5)
        return {"ok": True}

    a, fresh_a = jobs.submit("create-instance", "P", ("create", "P", "web-1"), slow)
    b, fresh_b = jobs.submit("create-instance", "P", ("create", "P", "web-1"), slow)

    assert fresh_a is True and fresh_b is False
    assert a.id == b.id, "重复提交应当拿回同一个任务号"
    release.set()
    _wait_done(a)
    assert len(started) == 1, "只应当真正跑一次"


def test_different_name_is_a_different_job():
    release = threading.Event()

    def slow(job):
        release.wait(timeout=5)
        return {}

    a, _ = jobs.submit("create-instance", "P", ("create", "P", "web-1"), slow)
    b, fresh = jobs.submit("create-instance", "P", ("create", "P", "web-2"), slow)
    assert fresh is True and a.id != b.id
    release.set()


def test_finished_job_no_longer_blocks_a_resubmit():
    """跑完之后再提交同名任务是允许的——用户可能真的要再建一台。"""
    first, _ = jobs.submit("create-instance", "P", ("k",), lambda j: {"n": 1})
    _wait_done(first)
    second, fresh = jobs.submit("create-instance", "P", ("k",), lambda j: {"n": 2})
    assert fresh is True and second.id != first.id


def test_steps_are_recorded_in_order():
    def work(job):
        job.step("核算免费额度")
        job.step("准备网络")
        job.step("完成")
        return {}

    job, _ = jobs.submit("create-instance", "P", ("k",), work)
    _wait_done(job)
    snap = job.snapshot()
    assert [s["text"] for s in snap["steps"]] == ["核算免费额度", "准备网络", "完成"]
    assert all("at" in s for s in snap["steps"]), "每步要带耗时，前端拿来显示进度"


def test_structured_failure_survives_to_the_snapshot():
    """额度被拦下时要能把 blockers 原样带回前端，而不是压成一句话。"""
    def work(job):
        raise JobError({"message": "超出 Always Free 额度，已阻止创建",
                        "blockers": ["ARM 超了 2 OCPU", "存储超了 30 GB"]})

    job, _ = jobs.submit("create-instance", "P", ("k",), work)
    _wait_done(job)
    snap = job.snapshot()
    assert snap["state"] == "failed"
    assert snap["error"]["blockers"] == ["ARM 超了 2 OCPU", "存储超了 30 GB"]


def test_oci_error_becomes_a_plain_message():
    def work(job):
        raise OCIError("Out of host capacity")

    job, _ = jobs.submit("create-instance", "P", ("k",), work)
    _wait_done(job)
    assert job.snapshot()["error"] == {"message": "Out of host capacity"}


def test_unexpected_error_does_not_leak_internals():
    """兜底分支不能把内部异常原文抛给前端——里面可能有路径或字段名。"""
    def work(job):
        raise ValueError("/opt/ocix/data/secrets.db 打不开")

    job, _ = jobs.submit("create-instance", "P", ("k",), work)
    _wait_done(job)
    snap = job.snapshot()
    assert snap["state"] == "failed"
    assert "secrets.db" not in snap["error"]["message"]
    assert "ValueError" in snap["error"]["message"]


def _wait_done(job, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job.snapshot()["state"] != "running":
            return
        time.sleep(0.02)
    raise AssertionError(f"任务没在 {timeout}s 内结束")
