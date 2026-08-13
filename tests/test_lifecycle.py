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
