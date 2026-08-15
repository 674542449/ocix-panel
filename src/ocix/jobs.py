"""把耗时的动作挪到后台线程，HTTP 请求立刻返回一个任务号。

**为什么非这么做不可。**

建实例这条链路上有好几处要等云端就绪：账户第一次开机要建 VCN、网关、
子网，每一步都调 ``oci.wait_until`` 等资源变成 AVAILABLE，各自上限 180 秒；
实例建出来之后还要等网卡挂好才能分配 IPv6，又是 90 秒。
静态上界加起来六百多秒，就算一切顺利，首次开机也常在一分钟以上。

而面板挂在 Cloudflare 后面时，**对方等 100 秒拿不到响应就直接给访客一个
524「源站超时」页面**。麻烦的地方在于请求并没有被取消：它还在服务器上跑，
实例照样会建出来，只是用户看到的是一个报错页。于是用户重试，于是又建一台。

所以创建改成：提交即返回任务号，前端轮询进度。同一个任务在跑的时候重复提交
会拿回同一个任务号，而不是再起一个——这样即使用户手快点了两次也只建一台。
"""

from __future__ import annotations

import threading
import time
import uuid

from .common import OCIError

# 完成的任务留多久。留着是为了让用户刷新页面后还能看到上一次的结果。
JOB_TTL = 3600
# 最多留多少条已完成的任务，防止长期运行的进程慢慢涨内存
MAX_JOBS = 60


class JobError(Exception):
    """任务失败，且带一个结构化的原因（比如额度预检的 blockers 列表）。

    普通的 OCIError 只有一句话，用不着这个。
    """

    def __init__(self, detail):
        super().__init__(str(detail))
        self.detail = detail


class Job:
    def __init__(self, kind: str, profile: str, key):
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.profile = profile
        self.key = key
        self.state = "running"          # running / ok / failed
        self.result = None
        self.error = None
        self.created = time.time()
        self.updated = self.created
        self._steps: list[dict] = []
        self._lock = threading.Lock()

    def step(self, text: str) -> None:
        """记一步进度。前端靠这个显示「在干什么」，而不是干等一个转圈。"""
        with self._lock:
            self._steps.append({"text": text, "at": round(time.time() - self.created, 1)})
            self.updated = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "state": self.state,
                "steps": list(self._steps),
                "result": self.result,
                "error": self.error,
                "elapsed": round(time.time() - self.created, 1),
            }


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def _prune() -> None:
    """调用方必须已经持有 _lock。"""
    now = time.time()
    for jid in [j.id for j in _jobs.values()
                if j.state != "running" and now - j.updated > JOB_TTL]:
        _jobs.pop(jid, None)
    if len(_jobs) > MAX_JOBS:
        finished = sorted((j for j in _jobs.values() if j.state != "running"),
                          key=lambda j: j.updated)
        for j in finished[: len(_jobs) - MAX_JOBS]:
            _jobs.pop(j.id, None)


def submit(kind: str, profile: str, key, fn) -> tuple[Job, bool]:
    """起一个后台任务。

    ``key`` 相同且还在跑的任务不会重复提交——直接把正在跑的那个还回去。
    返回 ``(job, 是不是新起的)``。
    """
    with _lock:
        _prune()
        for j in _jobs.values():
            if j.state == "running" and j.key == key:
                return j, False
        job = Job(kind, profile, key)
        _jobs[job.id] = job

    def run() -> None:
        try:
            job.result = fn(job)
            job.state = "ok"
        except JobError as e:
            job.error = e.detail
            job.state = "failed"
        except OCIError as e:
            job.error = {"message": e.message}
            job.state = "failed"
        except Exception as e:  # noqa: BLE001 - 后台线程里没人接，必须自己兜住
            # 不把内部异常原文抛给前端：可能带路径、凭据字段名之类的东西。
            # 真正的堆栈留在服务端日志里。
            job.error = {"message": f"内部错误（{type(e).__name__}），详情见服务端日志"}
            job.state = "failed"
            raise
        finally:
            job.updated = time.time()

    threading.Thread(target=run, daemon=True, name=f"ocix-job-{kind}").start()
    return job, True


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def reset_for_tests() -> None:
    with _lock:
        _jobs.clear()
