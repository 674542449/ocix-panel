from fakes import FakeBackend
from ocix.oci_helpers import (
    is_capacity_available,
    probe_single_ad_capacity,
    scan_capacity_radar,
)


def test_probe_single_ad_capacity(monkeypatch):
    fake = FakeBackend(
        ads=[{"name": "AD-1"}, {"name": "AD-2"}, {"name": "AD-3"}],
        capacity_status="AVAILABLE",
    )
    monkeypatch.setattr("ocix.oci_helpers._b", lambda: fake)
    monkeypatch.setattr("ocix.oci_helpers.tenancy_of", lambda p: "cid1")
    monkeypatch.setattr("ocix.oci_helpers.region_of", lambda p: "us-ashburn-1")

    res = probe_single_ad_capacity("DEFAULT", "cid1", "AD-1", "VM.Standard.A1.Flex", 4.0, 24.0)
    assert res["has_capacity"] is True
    assert res["availability_domain"] == "AD-1"
    assert len(res["fault_domains"]) == 3
    assert res["fault_domains"][0]["status"] == "AVAILABLE"
    assert res["best_fault_domain"] == "FAULT-DOMAIN-1"


def test_scan_capacity_radar_current_region(monkeypatch):
    fake = FakeBackend(
        ads=[{"name": "AD-1"}, {"name": "AD-2"}, {"name": "AD-3"}],
        capacity_status="AVAILABLE",
    )
    monkeypatch.setattr("ocix.oci_helpers._b", lambda: fake)
    monkeypatch.setattr("ocix.oci_helpers.tenancy_of", lambda p: "cid1")
    monkeypatch.setattr("ocix.oci_helpers.region_of", lambda p: "us-ashburn-1")

    res = scan_capacity_radar(
        "DEFAULT", shape="VM.Standard.A1.Flex", ocpus=4, memory_in_gbs=24, all_regions=False
    )
    assert res["scanned_regions_count"] == 1
    assert res["has_any_capacity"] is True
    assert res["total_available_locations"] == 3
    assert len(fake.launched) == 0  # 绝对不创建实例！


def test_scan_capacity_radar_all_regions(monkeypatch):
    fake = FakeBackend(
        ads=[{"name": "AD-1"}, {"name": "AD-2"}],
        region_subscriptions=[
            {"region_name": "us-ashburn-1", "is_home_region": True},
            {"region_name": "ap-tokyo-1", "is_home_region": False},
        ],
        capacity_status="AVAILABLE",
    )
    monkeypatch.setattr("ocix.oci_helpers._b", lambda: fake)
    monkeypatch.setattr("ocix.oci_helpers.tenancy_of", lambda p: "cid1")
    monkeypatch.setattr("ocix.oci_helpers.region_of", lambda p: "us-ashburn-1")

    res = scan_capacity_radar(
        "DEFAULT", shape="VM.Standard.A1.Flex", ocpus=4, memory_in_gbs=24, all_regions=True
    )
    assert res["scanned_regions_count"] == 2
    assert res["has_any_capacity"] is True
    assert len(fake.launched) == 0  # 纯扫描，不建机


def test_is_capacity_available(monkeypatch):
    fake = FakeBackend(
        ads=[{"name": "AD-1"}],
        capacity_status="AVAILABLE",
    )
    monkeypatch.setattr("ocix.oci_helpers._b", lambda: fake)
    monkeypatch.setattr("ocix.oci_helpers.tenancy_of", lambda p: "cid1")
    monkeypatch.setattr("ocix.oci_helpers.region_of", lambda p: "us-ashburn-1")

    avail, best_fd = is_capacity_available("DEFAULT", "cid1", "AD-1", "VM.Standard.A1.Flex", 4, 24)
    assert avail is True
    assert best_fd == "FAULT-DOMAIN-1"

    fake.capacity_status = "OUT_OF_HOST_CAPACITY"
    avail_no, best_fd_no = is_capacity_available("DEFAULT", "cid1", "AD-1", "VM.Standard.A1.Flex", 4, 24)
    assert avail_no is False
    assert best_fd_no is None


def test_capacity_radar_api(app_client, monkeypatch):
    fake = FakeBackend(
        ads=[{"name": "AD-1"}],
        capacity_status="AVAILABLE",
    )
    monkeypatch.setattr("ocix.oci_helpers._b", lambda: fake)
    monkeypatch.setattr("ocix.oci_helpers.tenancy_of", lambda p: "cid1")
    monkeypatch.setattr("ocix.oci_helpers.region_of", lambda p: "us-ashburn-1")

    resp = app_client.post(
        "/api/provision/capacity-radar",
        json={
            "profile": "EXISTING",
            "shape": "VM.Standard.A1.Flex",
            "ocpus": 4,
            "memory_gb": 24,
            "all_regions": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "regions" in data
    assert data["shape"] == "VM.Standard.A1.Flex"
    assert data["has_any_capacity"] is True
