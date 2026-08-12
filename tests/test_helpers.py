"""oci_helpers 里的纯逻辑：镜像筛选、防火墙规则、版本号排序、卷性能档位。"""

import json

import pytest

from ocix import oci_helpers as H


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


@pytest.fixture()
def stub_images(monkeypatch):
    def _apply(images):
        monkeypatch.setattr(H, "run_oci", lambda *a, **k: {"data": images})
        monkeypatch.setattr(H, "tenancy_of", lambda profile: "ocid1.tenancy.oc1..t")
    return _apply


def test_keeps_only_two_latest_major_versions(stub_images):
    stub_images(IMAGES)
    versions = [i["os_version"] for i in H.list_images("P", "c", shape="VM.Standard.A1.Flex")]
    assert versions == ["24.04", "22.04"]


def test_minimal_variant_does_not_displace_a_real_version(stub_images):
    """回归：Minimal 的版本串是 '24.04-Minimal'，曾被当成独立大版本挤掉 22.04。"""
    stub_images(IMAGES)
    imgs = H.list_images("P", "c")
    assert [i["id"] for i in imgs] == ["i2404", "i2204"]


def test_each_version_keeps_its_newest_build(stub_images):
    stub_images(IMAGES)
    imgs = H.list_images("P", "c")
    assert next(i["id"] for i in imgs if i["os_version"] == "22.04") == "i2204"


def test_disabled_images_are_skipped(stub_images):
    stub_images(IMAGES)
    assert all(i["id"] != "idisabled" for i in H.list_images("P", "c"))


def test_keep_majors_is_configurable(stub_images):
    stub_images(IMAGES)
    assert len(H.list_images("P", "c", keep_majors=3)) == 3


def test_minimal_is_still_offered_when_it_is_the_only_option(stub_images):
    stub_images([image("only", "24.04", "2025.01.16", minimal=True)])
    imgs = H.list_images("P", "c")
    assert len(imgs) == 1 and imgs[0]["minimal"]


@pytest.mark.parametrize("newer,older", [
    ("24.04", "22.04"),
    ("24.10", "24.04"),
    ("24.04", "20.04"),
])
def test_version_ordering(newer, older):
    assert H._version_key(newer) > H._version_key(older)


def test_version_number_strips_variant_suffix():
    assert H._version_number("24.04-Minimal") == "24.04"
    assert H._version_number("24.04 Minimal") == "24.04"
    assert H._version_number("24.04") == "24.04"


# ── 防火墙规则 ──

@pytest.fixture()
def fw_store(monkeypatch):
    """用一份内存里的安全列表模拟 OCI，观察规则被怎么改写。"""
    store = {"rules": [
        {"protocol": "6", "source": "0.0.0.0/0", "sourceType": "CIDR_BLOCK", "isStateless": False,
         "tcpOptions": {"destinationPortRange": {"min": 22, "max": 22}}, "description": "Default SSH"},
        {"protocol": "1", "source": "0.0.0.0/0", "sourceType": "CIDR_BLOCK", "isStateless": False},
    ]}

    def fake_run(profile, *args, **kwargs):
        joined = " ".join(args)
        if joined.startswith("network subnet get"):
            return {"data": {"security-list-ids": ["sl1"], "ipv6-cidr-block": "2603::/64"}}
        if joined.startswith("network security-list get"):
            return {"data": {"ingress-security-rules": store["rules"]}}
        if joined.startswith("network security-list update"):
            store["rules"] = json.loads(args[args.index("--ingress-security-rules") + 1])
            return {"data": {}}
        raise AssertionError("未预期的调用: " + joined)

    monkeypatch.setattr(H, "run_oci", fake_run)
    return store


def test_open_all_replaces_existing_rules(fw_store):
    """按需求：先删掉 Oracle 预置的默认规则，再写入全放行，不做叠加。"""
    res = H.open_all_ports_on_subnet("P", "sub1", include_ipv6=True)
    assert res["removed"] == 2
    sources = [r["source"] for r in fw_store["rules"]]
    assert sources == ["0.0.0.0/0", "::/0"]
    assert all(r["protocol"] == "all" for r in fw_store["rules"])


def test_open_all_skips_ipv6_when_subnet_has_none(fw_store, monkeypatch):
    real = H.run_oci

    def no_v6(profile, *args, **kwargs):
        if " ".join(args).startswith("network subnet get"):
            return {"data": {"security-list-ids": ["sl1"]}}
        return real(profile, *args, **kwargs)

    monkeypatch.setattr(H, "run_oci", no_v6)
    H.open_all_ports_on_subnet("P", "sub1", include_ipv6=True)
    assert [r["source"] for r in fw_store["rules"]] == ["0.0.0.0/0"]


def test_add_single_port_rule(fw_store):
    H.add_port_rule("P", "sub1", "TCP", 443, 443, "0.0.0.0/0")
    added = fw_store["rules"][-1]
    assert added["protocol"] == "6"
    assert added["tcpOptions"]["destinationPortRange"] == {"min": 443, "max": 443}
    assert len(fw_store["rules"]) == 3


def test_add_port_rule_is_idempotent(fw_store):
    H.add_port_rule("P", "sub1", "TCP", 443, 443, "0.0.0.0/0")
    res = H.add_port_rule("P", "sub1", "TCP", 443, 443, "0.0.0.0/0")
    assert res["added"] is False
    assert len(fw_store["rules"]) == 3


def test_add_port_rule_rejects_unknown_protocol(fw_store):
    with pytest.raises(Exception, match="协议"):
        H.add_port_rule("P", "sub1", "SCTP", 1, 1, "0.0.0.0/0")


def test_delete_rule_by_index(fw_store):
    H.delete_port_rule("P", "sub1", 0)
    assert len(fw_store["rules"]) == 1
    assert fw_store["rules"][0]["protocol"] == "1"


def test_delete_rule_rejects_bad_index(fw_store):
    with pytest.raises(Exception, match="序号"):
        H.delete_port_rule("P", "sub1", 99)


def test_clear_keeps_ssh_and_reports_real_count(fw_store):
    """removed 要报「删掉了几条」，不能报净差值——保留 SSH 时净差值会是 0。"""
    res = H.clear_ingress_rules("P", "sub1", keep_ssh=True)
    assert res["removed"] == 2
    assert res["kept_ssh"] is True
    assert len(fw_store["rules"]) == 1
    assert fw_store["rules"][0]["tcpOptions"]["destinationPortRange"]["min"] == 22


def test_clear_without_ssh_leaves_nothing(fw_store):
    H.clear_ingress_rules("P", "sub1", keep_ssh=False)
    assert fw_store["rules"] == []


# ── 调用次数：每次 oci CLI 调用固定约 1.1 秒进程开销，次数就是等待时间 ──

@pytest.fixture()
def count_calls(monkeypatch):
    """统计一次操作实际发出多少次 CLI 调用。"""
    calls = []

    def fake_run(profile, *args, **kwargs):
        j = " ".join(args)
        calls.append(" ".join(j.split()[:3]))
        if j.startswith("iam compartment list"):
            return {"data": [{"id": "c1", "name": "a", "compartment-id": "root",
                              "lifecycle-state": "ACTIVE"}]}
        if j.startswith("iam availability-domain list"):
            return {"data": [{"name": "AD-1"}, {"name": "AD-2"}]}
        if j.startswith("compute instance list"):
            return {"data": [{"id": "i1", "display-name": "box", "shape": "VM.Standard.A1.Flex",
                              "lifecycle-state": "RUNNING", "compartment-id": "root",
                              "shape-config": {"ocpus": 1, "memory-in-gbs": 6}}]}
        if j.startswith("compute vnic-attachment list"):
            return {"data": [{"lifecycle-state": "ATTACHED", "instance-id": "i1", "vnic-id": "v1"}]}
        if j.startswith("network vnic get"):
            return {"data": {"public-ip": "1.2.3.4", "private-ip": "10.0.0.1", "subnet-id": "sub1"}}
        if j.startswith("network subnet get"):
            return {"data": {"security-list-ids": ["sl1"], "display-name": "public"}}
        if j.startswith("network security-list get"):
            return {"data": {"ingress-security-rules": []}}
        return {"data": []}

    monkeypatch.setattr(H, "run_oci", fake_run)
    monkeypatch.setattr(H, "tenancy_of", lambda p: "root")
    return calls


def test_availability_domains_are_fetched_once(count_calls):
    """回归：可用域每个 compartment 都重查一次，存储页因此多了好几秒。"""
    for _ in range(4):
        H._availability_domains("P")
    assert count_calls.count("iam availability-domain list") == 1


def test_primary_vnic_is_cached(count_calls):
    """一次防火墙操作要反复定位同一张网卡，每次重查都是 2 次调用。"""
    for _ in range(3):
        H.primary_vnic("P", "i1", "root")
    assert len(count_calls) == 2


def test_repeated_firewall_status_only_refetches_rules(count_calls):
    """规则会变，网卡和子网不会——第二次查状态不该把前置调用重跑一遍。"""
    H.firewall_status("P", "i1", "root")
    first = len(count_calls)
    H.firewall_status("P", "i1", "root")
    assert len(count_calls) - first == 1


def test_instance_list_is_cached_and_invalidated(count_calls):
    H.list_instances("P", "root")
    H.list_instances("P", "root")
    assert count_calls.count("compute instance list") == 1

    H.invalidate_read_cache("P")
    H.list_instances("P", "root")
    assert count_calls.count("compute instance list") == 2


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


# ── instance launch 的参数（这里出过一次线上报错）──

@pytest.fixture()
def capture_launch(monkeypatch):
    calls = []

    def fake_run(profile, *args, **kwargs):
        calls.append(list(args))
        return {"data": {"id": "ocid1.instance.oc1..x", "lifecycle-state": "PROVISIONING"}}

    monkeypatch.setattr(H, "run_oci", fake_run)
    return calls


LAUNCH_PARAMS = {
    "compartment_id": "ocid1.compartment.oc1..c",
    "availability_domain": "AD-1",
    "display_name": "box",
    "image_id": "ocid1.image.oc1..i",
    "subnet_id": "ocid1.subnet.oc1..s",
    "shape": "VM.Standard.E2.1.Micro",
    "boot_gb": 50,
    "ssh_public_key": "ssh-ed25519 AAAA test@x",
}


@pytest.mark.parametrize("assign_ipv6", [False, True])
def test_launch_never_uses_create_vnic_details(capture_launch, assign_ipv6):
    """回归：--create-vnic-details 在不少 oci CLI 版本里不存在，
    会直接报 "No such option: --create-vnic-details"。IPv6 改为建完之后单独挂。"""
    H.launch_instance("P", {**LAUNCH_PARAMS, "assign_ipv6": assign_ipv6})
    args = capture_launch[0]
    assert "--create-vnic-details" not in args
    assert "--subnet-id" in args
    assert "--assign-public-ip" in args


def test_launch_passes_shape_config_only_for_arm(capture_launch):
    H.launch_instance("P", {**LAUNCH_PARAMS, "shape": "VM.Standard.A1.Flex",
                            "ocpus": 4, "memory_gb": 24})
    assert "--shape-config" in capture_launch[0]

    capture_launch.clear()
    H.launch_instance("P", LAUNCH_PARAMS)
    assert "--shape-config" not in capture_launch[0]


def test_launch_sends_ssh_key_as_metadata(capture_launch):
    H.launch_instance("P", LAUNCH_PARAMS)
    args = capture_launch[0]
    meta = args[args.index("--metadata") + 1]
    assert "ssh_authorized_keys" in meta
    assert "ssh-ed25519" in meta


# ── 只使用真实存在的 oci CLI 子命令 ──
# 这些名字都对着 oci-cli 的 --help 核过；写错会在运行时报 "No such command/option"，
# 而那是只有真连了 OCI 才会暴露的错误，所以在这里锁住。

def test_change_public_ip_uses_existing_subcommands(monkeypatch):
    """回归：曾误用不存在的 get-public-ip-by-private-ip-id 子命令。"""
    calls = []

    def fake_run(profile, *args, **kwargs):
        calls.append(list(args))
        joined = " ".join(args)
        if "vnic-attachment" in joined:
            return {"data": [{"lifecycle-state": "ATTACHED", "vnic-id": "v1"}]}
        if "vnic get" in joined:
            return {"data": {"subnet-id": "s1", "public-ip": "1.2.3.4", "private-ip": "10.0.0.2"}}
        if "private-ip list" in joined:
            return {"data": [{"id": "pip1", "is-primary": True}]}
        if "public-ip get" in joined:
            return {"data": {"id": "pubid", "lifetime": "EPHEMERAL"}}
        if "public-ip create" in joined:
            return {"data": {"ip-address": "5.6.7.8"}}
        return {"data": {}}

    monkeypatch.setattr(H, "run_oci", fake_run)
    res = H.change_public_ip("P", "ocid1.instance.oc1..x", "ocid1.compartment.oc1..c")

    assert res["new_ip"] == "5.6.7.8"
    flat = [" ".join(c) for c in calls]
    assert not any("get-public-ip-by-private-ip-id" in f for f in flat)
    assert any(f.startswith("network public-ip get ") and "--private-ip-id" in f for f in flat)


def test_reserved_ip_is_refused(monkeypatch):
    """保留 IP 换掉就找不回来了，必须拒绝而不是照删。"""
    def fake_run(profile, *args, **kwargs):
        joined = " ".join(args)
        if "vnic-attachment" in joined:
            return {"data": [{"lifecycle-state": "ATTACHED", "vnic-id": "v1"}]}
        if "vnic get" in joined:
            return {"data": {"subnet-id": "s1", "public-ip": "1.2.3.4"}}
        if "private-ip list" in joined:
            return {"data": [{"id": "pip1", "is-primary": True}]}
        if "public-ip get" in joined:
            return {"data": {"id": "pubid", "lifetime": "RESERVED"}}
        raise AssertionError("保留 IP 不该走到删除/新建：" + joined)

    monkeypatch.setattr(H, "run_oci", fake_run)
    with pytest.raises(Exception, match="保留"):
        H.change_public_ip("P", "i", "c")


def test_vcn_ipv6_request_declares_oracle_allocation(monkeypatch):
    """AddVcnIpv6CidrDetails 必须给 isOracleGuaAllocationEnabled，否则服务端拒绝。"""
    calls = []
    monkeypatch.setattr(H, "run_oci", lambda p, *a, **k: calls.append(list(a)) or {"data": {}})
    H._add_vcn_ipv6("P", "vcn1")
    assert "--is-oracle-gua-allocation-enabled" in calls[0]


@pytest.mark.parametrize("rule,expected", [
    ({"tcp-options": {"destination-port-range": {"min": 22, "max": 22}}}, "22"),
    ({"tcp-options": {"destination-port-range": {"min": 80, "max": 443}}}, "80-443"),
    ({"udp-options": {"destination-port-range": {"min": 53, "max": 53}}}, "53"),
    ({"tcp-options": {}}, "全部端口"),
    ({"protocol": "all"}, "全部端口"),
])
def test_firewall_rule_port_rendering(rule, expected):
    assert H._rule_ports(rule) == expected
