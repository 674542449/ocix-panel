"""oci_helpers 的业务逻辑：镜像筛选、防火墙规则、版本排序、卷性能、调用次数。

这些测试打的是 Backend 接口，因此对 cli / sdk 两种实现同样成立。
后端各自的实现细节另见 test_backends.py。
"""

import pytest

from fakes import FakeBackend
from ocix import oci_helpers as H
from ocix.backends import set_backend


@pytest.fixture()
def fake():
    b = FakeBackend()
    set_backend(b)
    H.tenancy_of = lambda p: "root"  # 免去读配置文件
    yield b
    set_backend(None)


@pytest.fixture(autouse=True)
def _restore_tenancy():
    original = H.tenancy_of
    yield
    H.tenancy_of = original


# ── 镜像筛选 ──

def image(iid, version, created, state="AVAILABLE", minimal=False):
    name = f"Canonical-Ubuntu-{version}{'-Minimal' if minimal else ''}-aarch64-{created}-0"
    return {
        "id": iid,
        "display-name": name,
        "operating-system": "Canonical Ubuntu",
        "operating-system-version": f"{version}-Minimal" if minimal else version,
        "lifecycle-state": state,
        "time-created": created,
    }


IMAGES = [
    image("i2404", "24.04", "2025.01.15"),
    image("i2404min", "24.04", "2025.01.16", minimal=True),
    image("i2204", "22.04", "2025.01.10"),
    image("i2204old", "22.04", "2024.06.01"),
    image("i2004", "20.04", "2024.12.01"),
    image("i1804", "18.04", "2024.01.01"),
    image("idisabled", "24.10", "2025.01.01", state="DISABLED"),
]


def test_keeps_only_two_latest_major_versions(fake):
    fake.images = IMAGES
    versions = [i["os_version"] for i in H.list_images("P", "c", shape="VM.Standard.A1.Flex")]
    assert versions == ["24.04", "22.04"]


def test_minimal_variant_does_not_displace_a_real_version(fake):
    """回归：Minimal 的版本串是 '24.04-Minimal'，曾被当成独立大版本挤掉 22.04。"""
    fake.images = IMAGES
    assert [i["id"] for i in H.list_images("P", "c")] == ["i2404", "i2204"]


def test_each_version_keeps_its_newest_build(fake):
    fake.images = IMAGES
    imgs = H.list_images("P", "c")
    assert next(i["id"] for i in imgs if i["os_version"] == "22.04") == "i2204"


def test_disabled_images_are_skipped(fake):
    fake.images = IMAGES
    assert all(i["id"] != "idisabled" for i in H.list_images("P", "c"))


def test_keep_majors_is_configurable(fake):
    fake.images = IMAGES
    assert len(H.list_images("P", "c", keep_majors=3)) == 3


def test_minimal_is_still_offered_when_it_is_the_only_option(fake):
    fake.images = [image("only", "24.04", "2025.01.16", minimal=True)]
    imgs = H.list_images("P", "c")
    assert len(imgs) == 1 and imgs[0]["minimal"]


@pytest.mark.parametrize("newer,older", [
    ("24.04", "22.04"), ("24.10", "24.04"), ("24.04", "20.04"),
])
def test_version_ordering(newer, older):
    assert H._version_key(newer) > H._version_key(older)


def test_version_number_strips_variant_suffix():
    assert H._version_number("24.04-Minimal") == "24.04"
    assert H._version_number("24.04 Minimal") == "24.04"
    assert H._version_number("24.04") == "24.04"


# ── 防火墙规则 ──

DEFAULT_RULES = [
    {"protocol": "6", "source": "0.0.0.0/0", "sourceType": "CIDR_BLOCK", "isStateless": False,
     "tcpOptions": {"destinationPortRange": {"min": 22, "max": 22}}, "description": "Default SSH"},
    {"protocol": "1", "source": "0.0.0.0/0", "sourceType": "CIDR_BLOCK", "isStateless": False},
]


@pytest.fixture()
def fw(fake):
    fake.ingress_rules = [dict(r) for r in DEFAULT_RULES]
    fake.subnet = {"security_list_ids": ["sl1"], "ipv6_cidr_block": "2603::/64"}
    return fake


def test_open_all_replaces_existing_rules(fw):
    """按需求：先删掉 Oracle 预置的默认规则，再写入全放行，不做叠加。"""
    res = H.open_all_ports_on_subnet("P", "sub1", include_ipv6=True)
    assert res["removed"] == 2
    assert [r["source"] for r in fw.ingress_rules] == ["0.0.0.0/0", "::/0"]
    assert all(r["protocol"] == "all" for r in fw.ingress_rules)


def test_open_all_skips_ipv6_when_disabled(fw):
    fw.subnet = {"security_list_ids": ["sl1"]}
    H.open_all_ports_on_subnet("P", "sub1", include_ipv6=False)
    assert [r["source"] for r in fw.ingress_rules] == ["0.0.0.0/0"]
    assert all(r["protocol"] == "all" for r in fw.ingress_rules)


def test_add_single_port_rule(fw):
    H.add_port_rule("P", "sub1", "TCP", 443, 443, "0.0.0.0/0")
    added = fw.ingress_rules[-1]
    assert added["protocol"] == "6"
    assert added["tcpOptions"]["destinationPortRange"] == {"min": 443, "max": 443}
    assert len(fw.ingress_rules) == 3


def test_add_port_rule_is_idempotent(fw):
    H.add_port_rule("P", "sub1", "TCP", 443, 443, "0.0.0.0/0")
    res = H.add_port_rule("P", "sub1", "TCP", 443, 443, "0.0.0.0/0")
    assert res["added"] is False
    assert len(fw.ingress_rules) == 3


def test_add_port_rule_rejects_unknown_protocol(fw):
    with pytest.raises(Exception, match="协议"):
        H.add_port_rule("P", "sub1", "SCTP", 1, 1, "0.0.0.0/0")


def test_delete_rule_by_index(fw):
    H.delete_port_rule("P", "sub1", 0)
    assert len(fw.ingress_rules) == 1
    assert fw.ingress_rules[0]["protocol"] == "1"


def test_delete_rule_rejects_bad_index(fw):
    with pytest.raises(Exception, match="序号"):
        H.delete_port_rule("P", "sub1", 99)


def test_clear_keeps_ssh_and_reports_real_count(fw):
    """removed 要报「删掉了几条」，不能报净差值——保留 SSH 时净差值会是 0。"""
    res = H.clear_ingress_rules("P", "sub1", keep_ssh=True)
    assert res["removed"] == 2
    assert res["kept_ssh"] is True
    assert len(fw.ingress_rules) == 1
    assert fw.ingress_rules[0]["tcpOptions"]["destinationPortRange"]["min"] == 22


def test_clear_without_ssh_leaves_nothing(fw):
    H.clear_ingress_rules("P", "sub1", keep_ssh=False)
    assert fw.ingress_rules == []


# ── 多安全列表的子网 ──
# OCI 的安全列表是「取并集」的：一个子网可以挂到 5 个，任何一个列表里有 allow
# 端口就是开的。所以放行写一个列表够用，收回必须遍历每一个。

@pytest.fixture()
def fw_multi(fake):
    """两个安全列表：sl1 只放 22，sl2 是 IPv4 全放行。"""
    fake.subnet = {"security_list_ids": ["sl1", "sl2"]}
    fake.ingress_by_list = {
        "sl1": [dict(DEFAULT_RULES[0])],
        "sl2": [{"protocol": "all", "source": "0.0.0.0/0", "sourceType": "CIDR_BLOCK",
                 "isStateless": False, "description": "wide open"}],
    }
    fake.vnic_attachments = [{"lifecycle_state": "ATTACHED", "instance_id": "i1",
                              "vnic_id": "v1"}]
    return fake


def test_clear_sweeps_every_security_list(fw_multi):
    """只清第一个列表的话，面板报「已清空」而 sl2 的全放行还在——端口实际仍然开着。"""
    res = H.clear_ingress_rules("P", "sub1", keep_ssh=True)
    assert res["removed"] == 2
    assert fw_multi.ingress_by_list["sl2"] == []
    assert len(fw_multi.ingress_by_list["sl1"]) == 1
    assert fw_multi.ingress_by_list["sl1"][0]["tcpOptions"][
        "destinationPortRange"]["min"] == 22


def test_revoke_all_ports_sweeps_every_security_list(fw_multi):
    res = H.revoke_all_ports("P", "i1", "root")
    assert res["removed"] == 1
    assert fw_multi.ingress_by_list["sl2"] == []
    assert res["status"]["all_open_v4"] is False


def test_firewall_status_reports_rule_ownership(fw_multi):
    """删除要靠 (security_list_id, index) 定位，聚合列表的下标不能当序号用。"""
    rules = H.firewall_status("P", "i1", "root")["ingress_rules"]
    assert [r["security_list_id"] for r in rules] == ["sl1", "sl2"]
    assert [r["index"] for r in rules] == [0, 0]


def test_delete_rule_uses_the_owning_security_list(fw_multi):
    """表格第 2 行属于 sl2 的第 0 条。不带归属就会去删 sl1 的第 1 条（并不存在）。"""
    H.delete_port_rule("P", "sub1", 0, security_list_id="sl2")
    assert fw_multi.ingress_by_list["sl2"] == []
    assert len(fw_multi.ingress_by_list["sl1"]) == 1


def test_delete_rule_rejects_a_foreign_security_list(fw_multi):
    with pytest.raises(Exception, match="不属于"):
        H.delete_port_rule("P", "sub1", 0, security_list_id="sl-other")


def test_icmpv6_is_an_accepted_protocol(fw):
    """界面的协议下拉里有 ICMPv6，执行层也认 58——不能被自家校验挡住。"""
    H.add_port_rule("P", "sub1", "ICMPv6", 1, 1, "::/0")
    assert fw.ingress_rules[-1]["protocol"] == "58"


# ── 调用次数：每次后端调用都对应一次网络往返（CLI 后端还要加 1.1 秒进程开销）──

def test_availability_domains_are_fetched_once(fake):
    """回归：可用域每个 compartment 都重查一次，存储页因此多了好几秒。"""
    for _ in range(4):
        H._availability_domains("P")
    assert fake.count("list_availability_domains") == 1


def test_primary_vnic_is_cached(fake):
    fake.vnic_attachments = [{"lifecycle_state": "ATTACHED", "instance_id": "i1",
                              "vnic_id": "v1"}]
    for _ in range(3):
        H.primary_vnic("P", "i1", "root")
    assert fake.count("list_vnic_attachments") == 1
    assert fake.count("get_vnic") == 1


def test_repeated_firewall_status_only_refetches_rules(fake):
    """规则会变，网卡和子网不会——第二次查状态不该把前置调用重跑一遍。"""
    fake.vnic_attachments = [{"lifecycle_state": "ATTACHED", "instance_id": "i1",
                              "vnic_id": "v1"}]
    H.firewall_status("P", "i1", "root")
    before = len(fake.calls)
    H.firewall_status("P", "i1", "root")
    assert len(fake.calls) - before == 1
    assert fake.calls[-1] == "get_security_list"


def test_instance_list_is_cached_and_invalidated(fake):
    fake.instances = [{"id": "i1", "lifecycle_state": "RUNNING", "compartment_id": "root"}]
    H.list_instances("P", "root")
    H.list_instances("P", "root")
    assert fake.count("list_instances") == 1

    H.invalidate_read_cache("P")
    H.list_instances("P", "root")
    assert fake.count("list_instances") == 2


def test_write_operations_invalidate_the_cache(fake):
    fake.instances = [{"id": "i1", "lifecycle_state": "RUNNING", "compartment_id": "root"}]
    H.list_instances("P", "root")
    H.instance_action("P", "i1", "START")
    H.list_instances("P", "root")
    assert fake.count("list_instances") == 2, "开关机之后必须重新拉列表，否则界面显示旧状态"


# ── launch 传给后端的规格 ──

LAUNCH_PARAMS = {
    "compartment_id": "c", "availability_domain": "AD-1", "display_name": "box",
    "image_id": "img", "subnet_id": "sub1", "shape": "VM.Standard.E2.1.Micro",
    "boot_gb": 50, "ssh_public_key": "ssh-ed25519 AAAA test@x",
}


def test_launch_passes_normalised_spec(fake):
    H.launch_instance("P", dict(LAUNCH_PARAMS))
    spec = fake.launched[0]
    assert spec["shape"] == "VM.Standard.E2.1.Micro"
    assert spec["boot_gb"] == 50
    assert spec["subnet_id"] == "sub1"
    assert "shape_config" not in spec, "固定规格不该带 shape_config"


def test_launch_includes_shape_config_for_arm(fake):
    H.launch_instance("P", {**LAUNCH_PARAMS, "shape": "VM.Standard.A1.Flex",
                            "ocpus": 4, "memory_gb": 24})
    sc = fake.launched[0]["shape_config"]
    assert sc == {"ocpus": 4.0, "memoryInGBs": 24.0}


def test_launch_forwards_ipv6_flag(fake):
    H.launch_instance("P", {**LAUNCH_PARAMS, "assign_ipv6": True})
    assert fake.launched[0]["assign_ipv6"] is True


# ── 卷性能档位 ──

@pytest.mark.parametrize("vpus,tier", [
    (0, "较低成本"), (10, "均衡"), (20, "较高性能"), (60, "超高性能"), (120, "超高性能"),
])
def test_vpu_tier_naming(vpus, tier):
    assert H.vpu_tier(vpus) == tier


def test_vpu_range_covers_0_to_120_in_steps_of_10():
    values = [t["vpus"] for t in H.VPU_RANGE["tiers"]]
    assert values == list(range(0, 121, 10))
    assert [t["vpus"] for t in H.VPU_RANGE["tiers"] if t["free"]] == [0, 10]


# ── 换 IP ──

def test_reserved_ip_is_refused(fake):
    """保留 IP 换掉就无法恢复，必须拒绝而不是照删。"""
    fake.vnic_attachments = [{"lifecycle_state": "ATTACHED", "instance_id": "i1",
                              "vnic_id": "v1"}]
    fake.public_ip = {"id": "pub1", "lifetime": "RESERVED"}
    with pytest.raises(Exception, match="保留"):
        H.change_public_ip("P", "i1", "root")
    assert fake.count("delete_public_ip") == 0


def test_change_ephemeral_ip(fake):
    fake.vnic_attachments = [{"lifecycle_state": "ATTACHED", "instance_id": "i1",
                              "vnic_id": "v1"}]
    res = H.change_public_ip("P", "i1", "root")
    assert res["new_ip"] == "5.6.7.8"
    assert fake.count("delete_public_ip") == 1
    assert fake.count("create_ephemeral_public_ip") == 1


# ── SDK 迁移遗留的键名问题（读回来是 snake_case）──

def test_metrics_parse_sdk_snake_case_datapoints(fake):
    """回归：监控页一直空白。

    SDK 返回的是 aggregated_datapoints，而代码只找 kebab / camel 两种写法，
    永远取不到，图表和表格全是空的。
    """
    fake.metrics = [{
        "name": "CpuUtilization",
        "aggregated_datapoints": [
            {"timestamp": "2026-08-12T10:00:00Z", "value": 12.5},
            {"timestamp": "2026-08-12T10:01:00Z", "value": 20.0},
        ],
    }]
    out = H.get_metrics("P", "inst1", "cid", hours=1)
    cpu = next(m for m in out if m["metric"] == "CpuUtilization")
    assert cpu["count"] == 2
    assert cpu["latest"] == 20.0
    assert cpu["avg"] == 16.25


def test_adding_a_rule_keeps_existing_port_ranges(fw):
    """回归（重要）：加一条规则会把已有 TCP 规则放大成全端口。

    读回来的是 tcp_options（snake），代码只找 tcpOptions，取不到就等于没有，
    重写规则集时端口范围整个丢掉——等于悄悄把没打算开的端口全开了。
    """
    # fw 里本来就有一条 SSH 22 规则，它的端口范围同样不能在重写中丢掉
    H.add_port_rule("P", "sub1", "TCP", 80, 80, "0.0.0.0/0")
    H.invalidate_read_cache()
    H.add_port_rule("P", "sub1", "TCP", 8443, 8443, "0.0.0.0/0")

    ranges = sorted(
        (r["tcpOptions"]["destinationPortRange"]["min"],
         r["tcpOptions"]["destinationPortRange"]["max"])
        for r in fw.ingress_rules if r.get("tcpOptions"))
    assert ranges == [(22, 22), (80, 80), (8443, 8443)], fw.ingress_rules


def test_rule_dedup_still_works_after_round_trip(fw):
    """判重要拿读回来的形态和新建的比，键名不一致就会重复添加。"""
    before = len(fw.ingress_rules)
    H.add_port_rule("P", "sub1", "TCP", 443, 443, "0.0.0.0/0")
    H.invalidate_read_cache()
    res = H.add_port_rule("P", "sub1", "TCP", 443, 443, "0.0.0.0/0")
    assert res["added"] is False
    assert len(fw.ingress_rules) == before + 1


def test_enabling_ipv6_keeps_existing_route_targets(fake):
    """回归：开 IPv6 会重写整张路由表，已有规则的目标不能被清空。"""
    fake.route_rules_existing = [{
        "destination": "0.0.0.0/0",
        "destinationType": "CIDR_BLOCK",
        "networkEntityId": "ocid1.internetgateway.oc1..existing",
    }]
    H._ensure_ipv6_route("P", "vcn1", "cid", "rt1")

    old = next(r for r in fake.route_rules if r["destination"] == "0.0.0.0/0")
    assert old["networkEntityId"] == "ocid1.internetgateway.oc1..existing", \
        "已有路由的目标被清空了"
    assert any(r["destination"] == "::/0" for r in fake.route_rules)
