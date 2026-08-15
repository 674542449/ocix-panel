"""跨账户串行闸门。

README 承诺「同一时刻只有一个账户在跟 OCI 通信」——多账户并发容易触发
对方限流，出错也难归因。同一个账户内部则允许并发（一个页面要查好几样，
串起来会慢得离谱）。

这两条同时成立并不显然，之前就不成立过：闸门只记了 owner，谁先跑完谁就
把 owner 清掉并放锁，于是同账户的第二个线程还在里面时，另一个账户已经
拿到锁进来了。
"""

import threading
import time

from ocix.common import account_gate, gather


class Watcher:
    """记录每个线程什么时候在闸门里，用来找跨账户重叠。"""

    def __init__(self):
        self.inside: dict[int, str] = {}
        self.overlaps: list = []
        self.lock = threading.Lock()

    def enter(self, profile):
        with self.lock:
            self.inside[threading.get_ident()] = profile
            live = set(self.inside.values())
            if len(live) > 1:
                self.overlaps.append(sorted(live))

    def leave(self):
        with self.lock:
            self.inside.pop(threading.get_ident(), None)


def test_two_accounts_never_overlap_even_when_one_lingers():
    """回归：同账户的第二个线程还没走，另一个账户就不能进来。

    构造的就是当初出问题的时序：
      A-主   拿锁进入，干 0.30s
      A-并发 借同账户放行进入，干 0.60s（比主线程活得久）
      A-主   先结束
      B-主   此时若能进来，就和还在里面的 A-并发 撞上了
    """
    w = Watcher()

    def work(profile, hold):
        with account_gate(profile):
            w.enter(profile)
            time.sleep(hold)
            w.leave()

    a = threading.Thread(target=work, args=("A", 0.30))
    a.start()
    time.sleep(0.05)
    a_worker = threading.Thread(target=work, args=("A", 0.45))
    a_worker.start()
    time.sleep(0.05)
    b = threading.Thread(target=work, args=("B", 0.10))
    b.start()

    for t in (a, a_worker, b):
        t.join(timeout=10)
        assert not t.is_alive(), "线程没结束，闸门可能死锁了"

    assert not w.overlaps, f"这些账户同时在闸门里：{w.overlaps}"


def test_same_account_still_runs_concurrently():
    """同账户不能被串行化，否则页面会慢得离谱。"""
    w = Watcher()
    seen_together = threading.Event()

    def work():
        with account_gate("SAME"):
            w.enter("SAME")
            time.sleep(0.15)
            with w.lock:
                if len(w.inside) > 1:
                    seen_together.set()
            w.leave()

    ts = [threading.Thread(target=work) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
        assert not t.is_alive()

    assert seen_together.is_set(), "同一账户的多个线程应当能同时在闸门里"


def test_nested_gather_inside_the_gate_does_not_deadlock():
    """闸门内部再用 gather 开线程池——这是查多个 compartment 的真实形态。

    子线程靠「同账户放行」进来；如果闸门改成每个线程都要抢锁，这里会自锁。
    """
    done = []

    def outer():
        with account_gate("ACC"):
            def inner(i):
                with account_gate("ACC"):     # 同账户重入
                    time.sleep(0.02)
                    return i * 2
            results = gather(inner, list(range(6)))
            done.extend(r for _, r, _ in results)

    t = threading.Thread(target=outer)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "嵌套 gather 死锁了"
    assert sorted(done) == [0, 2, 4, 6, 8, 10]


def test_gate_is_released_after_everyone_leaves():
    """所有人走光之后，别的账户必须能立刻进来（锁没被漏掉）。"""
    with account_gate("X"):
        pass
    got_in = threading.Event()

    def other():
        with account_gate("Y"):
            got_in.set()

    t = threading.Thread(target=other)
    t.start()
    t.join(timeout=5)
    assert got_in.is_set(), "闸门没被释放，后面的账户进不来"


def test_many_accounts_stay_serialised_under_load():
    """压一压：多账户多线程混跑，既不能重叠也不能卡住。"""
    w = Watcher()

    def work(profile):
        with account_gate(profile):
            w.enter(profile)
            time.sleep(0.01)
            w.leave()

    threads = []
    for i in range(24):
        profile = f"ACC{i % 4}"
        threads.append(threading.Thread(target=work, args=(profile,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive(), "有线程没结束，闸门可能死锁"

    assert not w.overlaps, f"这些账户同时在闸门里：{w.overlaps}"
