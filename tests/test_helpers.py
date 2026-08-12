"""oci_helpers 里的纯逻辑：镜像筛选、防火墙规则解析、版本号排序。"""

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


@pytest.mark.parametrize("rule,expected", [
    ({"tcp-options": {"destination-port-range": {"min": 22, "max": 22}}}, "22"),
    ({"tcp-options": {"destination-port-range": {"min": 80, "max": 443}}}, "80-443"),
    ({"udp-options": {"destination-port-range": {"min": 53, "max": 53}}}, "53"),
    ({"tcp-options": {}}, "全部端口"),
    ({"protocol": "all"}, "全部端口"),
])
def test_firewall_rule_port_rendering(rule, expected):
    assert H._rule_ports(rule) == expected
