"""实例详情、改规格、串口控制台、引导卷备份与扩容。

这几个都会动真资源，额度闸门是重点：改规格和扩容都可能把 Always Free 撑爆。
"""

import pytest

from fakes import FakeBackend
from ocix import oci_helpers as H
from ocix.backends import set_backend
from ocix.common import OCIError


@pytest.fixture()
def fake():
    b = FakeBackend()
    set_backend(b)
    original = H.tenancy_of
    H.tenancy_of = lambda p: "root"
    H.invalidate_read_cache()
    yield b
    H.tenancy_of = original
    set_backend(None)


def _arm(iid="i-arm", ocpus=1, mem=6):
    return {"id": iid, "shape": "VM.Standard.A1.Flex", "lifecycle-state": "RUNNING",
            "display-name": iid, "compartment-id": "cid", "availability-domain": "AD-1",
            "time-created": "2026-08-01T00:00:00+00:00",
            "shape-config": {"ocpus": ocpus, "memory-in-gbs": mem}}


# ── 改规格 ──

def test_resize_arm_within_quota(fake):
    fake.instances = [_arm(ocpus=1, mem=6)]
    res = H.resize_instance_shape("P", "i-arm", 4, 24)
    assert res["ok"]
    assert fake.resized == {"instance_id": "i-arm", "ocpus": 4, "memory_gb": 24}
    assert "重启" in res["note"]


def test_resize_rejects_fixed_shape(fake):
    fake.instances = [{"id": "i-amd", "shape": "VM.Standard.E2.1.Micro",
                       "compartment-id": "cid", "lifecycle-state": "RUNNING"}]
    with pytest.raises(OCIError, match="固定规格"):
        H.resize_instance_shape("P", "i-amd", 2, 4)


def test_resize_rejects_over_arm_ocpu(fake):
    fake.instances = [_arm()]
    with pytest.raises(OCIError, match="1-4 OCPU"):
        H.resize_instance_shape("P", "i-arm", 8, 24)


def test_resize_rejects_too_much_memory_per_ocpu(fake):
    """每 OCPU 最多 6GB，2 核要 24G 不行。"""
    fake.instances = [_arm()]
    with pytest.raises(OCIError, match="最多配"):
        H.resize_instance_shape("P", "i-arm", 2, 24)


def test_resize_counts_other_arm_instances(fake):
    """回归：只看这一台的话，两台各改到 4 OCPU 都能过，合起来就超了。"""
    fake.instances = [_arm("i-arm", 1, 6), _arm("i-other", 3, 18)]
    with pytest.raises(OCIError, match="其它实例已占 3 OCPU"):
        H.resize_instance_shape("P", "i-arm", 2, 12)
    # 留在额度内就该放行
    assert H.resize_instance_shape("P", "i-arm", 1, 6)["ok"]


def test_resize_memory_also_counts_others(fake):
    fake.instances = [_arm("i-arm", 1, 6), _arm("i-other", 1, 20)]
    with pytest.raises(OCIError, match="内存额度"):
        H.resize_instance_shape("P", "i-arm", 1, 6)


# ── 串口控制台 ──

PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI test@host"


def test_create_console_returns_ssh_command(fake):
    fake.instances = [_arm()]
    res = H.create_console("P", "i-arm", PUBKEY, "cid")
    assert res["created"] is True
    assert res["connections"][0]["ssh_command"].startswith("ssh ")
    assert res["connections"][0]["vnc_command"]


def test_create_console_reuses_existing(fake):
    """已经有连接就别再建一个——OCI 每台实例只允许一个。"""
    fake.instances = [_arm()]
    H.create_console("P", "i-arm", PUBKEY, "cid")
    H.invalidate_read_cache()
    res = H.create_console("P", "i-arm", PUBKEY, "cid")
    assert res["created"] is False
    assert fake.count("create_console_connection") == 1


def test_deleted_connections_are_hidden(fake):
    fake.console = [{"id": "c1", "instance-id": "i-arm", "lifecycle-state": "DELETED"},
                    {"id": "c2", "instance-id": "i-arm", "lifecycle-state": "ACTIVE"}]
    rows = H.console_connections("P", "cid", "i-arm")
    assert [c["id"] for c in rows] == ["c2"]


# ── 备份 ──

def test_create_and_list_backup(fake):
    res = H.create_backup("P", "bv1", "", "FULL")
    assert res["ok"]
    H.invalidate_read_cache()
    rows = H.boot_volume_backups("P", "cid")["backups"]
    assert rows[0]["type"] == "FULL"
    assert rows[0]["display_name"].startswith("ocix-")


def test_terminated_backups_are_hidden(fake):
    fake.backups = [{"id": "b1", "lifecycle-state": "TERMINATED", "size-in-gbs": 50},
                    {"id": "b2", "lifecycle-state": "AVAILABLE", "size-in-gbs": 50}]
    assert [b["id"] for b in H.boot_volume_backups("P", "cid")["backups"]] == ["b2"]


def test_restore_is_blocked_when_it_would_exceed_storage(fake):
    """还原会造一个新卷；额度不够时必须先拦下，不然还原完反而开不出机器。"""
    fake.backups = [{"id": "b1", "lifecycle-state": "AVAILABLE", "size-in-gbs": 100,
                     "display-name": "nightly"}]
    fake.boot_volumes = [{"id": "v1", "size-in-gbs": 150, "lifecycle-state": "AVAILABLE",
                          "display-name": "bv", "availability-domain": "AD-1"}]
    with pytest.raises(OCIError, match="超出存储额度"):
        H.restore_backup("P", "b1", "AD-1", "", "cid")


def test_restore_within_quota_creates_a_new_volume(fake):
    fake.backups = [{"id": "b1", "lifecycle-state": "AVAILABLE", "size-in-gbs": 50,
                     "display-name": "nightly"}]
    fake.boot_volumes = [{"id": "v1", "size-in-gbs": 50, "lifecycle-state": "AVAILABLE",
                          "display-name": "bv", "availability-domain": "AD-1"}]
    res = H.restore_backup("P", "b1", "AD-1", "", "cid")
    assert res["ok"]
    assert fake.restored["backup_id"] == "b1"
    # 必须说清楚不是原地恢复，否则用户以为点一下就回滚了
    assert "新的引导卷" in res["note"]
    assert "原实例没有任何变动" in res["note"]


def test_restore_unknown_backup_is_rejected(fake):
    with pytest.raises(OCIError, match="找不到这个备份"):
        H.restore_backup("P", "nope", "AD-1", "", "cid")


# ── 引导卷扩容 ──

def _vol(size):
    return [{"id": "v1", "size-in-gbs": size, "lifecycle-state": "AVAILABLE",
             "display-name": "bv", "availability-domain": "AD-1"}]


def test_boot_volume_grows(fake):
    fake.boot_volumes = _vol(50)
    res = H.resize_boot_volume("P", "v1", 100, "cid")
    assert res["changed"] is True
    assert fake.resized_volume == {"id": "v1", "size_gb": 100}
    # 云端变大了系统里还看不到，必须提示 growfs，否则用户以为没生效
    assert "oci-growfs" in res["note"]


def test_boot_volume_cannot_shrink(fake):
    fake.boot_volumes = _vol(100)
    with pytest.raises(OCIError, match="只能扩容不能缩容"):
        H.resize_boot_volume("P", "v1", 50, "cid")


def test_resize_to_same_size_is_a_noop(fake):
    fake.boot_volumes = _vol(50)
    res = H.resize_boot_volume("P", "v1", 50, "cid")
    assert res["changed"] is False
    assert fake.count("update_boot_volume_size") == 0


def test_boot_volume_resize_respects_the_200gb_cap(fake):
    """三块 50GB 的卷，把其中一块撑到 150 就超了。"""
    fake.boot_volumes = [
        {"id": "v1", "size-in-gbs": 50, "lifecycle-state": "AVAILABLE",
         "display-name": "a", "availability-domain": "AD-1"},
        {"id": "v2", "size-in-gbs": 50, "lifecycle-state": "AVAILABLE",
         "display-name": "b", "availability-domain": "AD-1"},
        {"id": "v3", "size-in-gbs": 50, "lifecycle-state": "AVAILABLE",
         "display-name": "c", "availability-domain": "AD-1"},
    ]
    with pytest.raises(OCIError, match="超出存储额度"):
        H.resize_boot_volume("P", "v1", 150, "cid")
    # 刚好用满 200 是允许的
    H.invalidate_read_cache()
    assert H.resize_boot_volume("P", "v1", 100, "cid")["changed"] is True


# ── 实例详情 ──

def test_instance_detail_aggregates_everything(fake):
    fake.instances = [_arm(ocpus=4, mem=24)]
    fake.boot_volumes = _vol(50)
    fake.boot_volume_attachments = [{"instance-id": "i-arm", "boot-volume-id": "v1",
                                     "lifecycle-state": "ATTACHED"}]
    d = H.instance_detail("P", "i-arm", "cid")
    assert d["ocpus"] == 4 and d["memory_gb"] == 24
    assert d["is_flex"] is True
    assert d["family"] == "arm"
    assert d["boot_volume"]["size_gb"] == 50
    assert isinstance(d["console"], list)


def test_instance_detail_survives_boot_volume_failure(fake):
    """引导卷查不到不该让整个详情页 500。"""
    fake.instances = [_arm()]

    def boom(*a, **kw):
        raise OCIError("no permission")
    fake.list_boot_volume_attachments = boom
    d = H.instance_detail("P", "i-arm", "cid")
    assert d["boot_volume"] is None
    assert "no permission" in d["boot_volume_error"]


# ── 详情页的 IP（回归） ──

def test_instance_detail_shows_ip_addresses(fake):
    """回归：attach_ips 把结果写在 _public_ip 这种下划线键上，
    详情页当初直接读 public_ip，取不到也不报错，IP 永远是空的。"""
    fake.instances = [_arm()]
    fake.vnic_attachments = [{"instance-id": "i-arm", "vnic-id": "vnic1",
                              "lifecycle-state": "ATTACHED"}]
    fake.vnic = {"id": "vnic1", "public-ip": "203.0.113.10",
                 "private-ip": "10.0.0.5", "ipv6-addresses": ["2001:db8::1"]}
    d = H.instance_detail("P", "i-arm", "cid")
    assert d["public_ip"] == "203.0.113.10"
    assert d["private_ip"] == "10.0.0.5"
    assert d["ipv6"] == "2001:db8::1"


def test_detail_and_list_report_the_same_ip(fake):
    """详情和列表必须说同一件事，不能一个有一个没有。"""
    fake.instances = [_arm()]
    fake.vnic_attachments = [{"instance-id": "i-arm", "vnic-id": "vnic1",
                              "lifecycle-state": "ATTACHED"}]
    fake.vnic = {"id": "vnic1", "public-ip": "198.51.100.7", "private-ip": "10.0.0.9"}

    listed = H.attach_ips("P", H.list_instances("P", "cid"))[0]
    H.invalidate_read_cache()
    detail = H.instance_detail("P", "i-arm", "cid")
    assert detail["public_ip"] == listed["_public_ip"] == "198.51.100.7"
    assert detail["private_ip"] == listed["_private_ip"] == "10.0.0.9"


def test_detail_has_no_silently_empty_fields(fake):
    """详情页这些字段只要后端有数据就必须填上。

    这类 bug 不会报错，只会让界面上出现一个「—」，很容易漏。
    """
    fake.instances = [_arm(ocpus=4, mem=24)]
    fake.vnic_attachments = [{"instance-id": "i-arm", "vnic-id": "vnic1",
                              "lifecycle-state": "ATTACHED"}]
    fake.vnic = {"id": "vnic1", "public-ip": "203.0.113.10", "private-ip": "10.0.0.5"}
    fake.boot_volumes = _vol(50)
    fake.boot_volume_attachments = [{"instance-id": "i-arm", "boot-volume-id": "v1",
                                     "lifecycle-state": "ATTACHED"}]
    d = H.instance_detail("P", "i-arm", "cid")
    for field in ("id", "display_name", "state", "shape", "availability_domain",
                  "time_created", "ocpus", "memory_gb", "public_ip", "private_ip"):
        assert d.get(field), f"{field} 是空的"
    assert d["boot_volume"] and d["boot_volume"]["size_gb"] == 50


# ── 挑一条可用的串口控制台连接 ──

def _conn(state="ACTIVE", iid="i-arm"):
    return {"id": "c1", "instance-id": iid, "lifecycle-state": state,
            "connection-string": "ssh -o ProxyCommand=... x@y",
            "vnc-connection-string": "ssh -L ... x@y"}


def test_pick_console_returns_the_active_one(fake):
    fake.instances = [_arm()]
    fake.console = [_conn("ACTIVE")]
    assert H.pick_console("P", "i-arm", "cid")["state"] == "ACTIVE"


def test_pick_console_falls_back_to_the_instance_compartment(fake):
    """回归：前端某些路径没带 compartment，传空串会一条都查不到，
    然后报「还没有连接」——可用户明明刚创建成功。"""
    fake.instances = [_arm()]
    fake.console = [_conn("ACTIVE")]
    # 不传 compartment，应当自己去实例上取
    assert H.pick_console("P", "i-arm")["state"] == "ACTIVE"
    assert H.pick_console("P", "i-arm", "")["state"] == "ACTIVE"
    assert H.pick_console("P", "i-arm", None)["state"] == "ACTIVE"


def test_creating_connection_says_wait_not_missing(fake):
    """还在创建 ≠ 不存在。说成「没有连接」会让人反复重建。"""
    fake.instances = [_arm()]
    fake.console = [_conn("CREATING")]
    with pytest.raises(OCIError, match="还在创建中"):
        H.pick_console("P", "i-arm", "cid")


def test_failed_connection_suggests_recreating(fake):
    fake.instances = [_arm()]
    fake.console = [_conn("FAILED")]
    with pytest.raises(OCIError) as exc:
        H.pick_console("P", "i-arm", "cid")
    assert "FAILED" in str(exc.value)


def test_no_connection_at_all(fake):
    fake.instances = [_arm()]
    fake.console = []
    with pytest.raises(OCIError, match="没有串口控制台连接"):
        H.pick_console("P", "i-arm", "cid")


def test_pick_console_ignores_other_instances(fake):
    fake.instances = [_arm()]
    fake.console = [_conn("ACTIVE", iid="i-other")]
    with pytest.raises(OCIError, match="没有串口控制台连接"):
        H.pick_console("P", "i-arm", "cid")


def test_error_names_the_actual_states(fake):
    """报错要说出实际看到的状态，否则没法判断下一步做什么。"""
    fake.instances = [_arm()]
    fake.console = [_conn("CREATING")]
    with pytest.raises(OCIError) as exc:
        H.pick_console("P", "i-arm", "cid")
    assert "CREATING" in str(exc.value)


def test_console_list_endpoint_is_cheap(app_client, live_backend):
    """详情页刷新整块要 5 次 OCI 调用；只看连接状态时应当只花 1 次。"""
    live_backend.instances = [_arm()]
    live_backend.console = [_conn("CREATING")]
    r = app_client.get("/api/provision/console",
                       params={"profile": "EXISTING", "instance_id": "i-arm",
                               "compartment_id": "cid"})
    assert r.status_code == 200, r.text
    assert r.json()["connections"][0]["state"] == "CREATING"
    assert live_backend.count("list_console_connections") == 1
    # 不该顺带去查引导卷、网卡这些详情页才需要的东西
    assert live_backend.count("list_boot_volume_attachments") == 0
    assert live_backend.count("list_vnic_attachments") == 0


def test_console_list_falls_back_to_instance_compartment(app_client, live_backend):
    """不传 compartment 时自己去实例上取，跟创建那条路径保持一致。"""
    live_backend.instances = [_arm()]
    live_backend.console = [_conn("ACTIVE")]
    r = app_client.get("/api/provision/console",
                       params={"profile": "EXISTING", "instance_id": "i-arm"})
    assert r.status_code == 200, r.text
    assert r.json()["connections"][0]["state"] == "ACTIVE"
