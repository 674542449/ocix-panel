"""Always Free 额度模型与开机前预检。"""

import pytest

from ocix import freetier as ft

AMD = ft.AMD_FREE_SHAPE
ARM = ft.ARM_FREE_SHAPE


def inst(shape, ocpus=None, mem=None, state="RUNNING"):
    d = {"shape": shape, "lifecycle-state": state}
    if ocpus is not None:
        d["shape-config"] = {"ocpus": ocpus, "memory-in-gbs": mem}
    return d


def vol(gb):
    return {"size-in-gbs": gb, "lifecycle-state": "AVAILABLE"}


def usage(instances=(), block=(), boot=()):
    return ft.summarize(list(instances), list(block), list(boot))


EMPTY = usage()
TWO_AMD = usage([inst(AMD, 1, 1)] * 2, boot=[vol(50)] * 2)
FULL_ARM = usage([inst(ARM, 4, 24)], boot=[vol(50)])


# ── 满配目标：2×AMD(1C1G) + 1×ARM(4C24G)，引导卷 50+50+100 = 200GB ──

@pytest.fixture()
def target():
    return usage([inst(AMD, 1, 1), inst(AMD, 1, 1), inst(ARM, 4, 24)],
                 boot=[vol(50), vol(50), vol(100)])


def test_summarize_counts_target_layout(target):
    assert target["amd_micro_instances"] == 2
    assert target["arm_instances"] == 1
    assert target["arm_ocpu"] == 4
    assert target["arm_memory_gb"] == 24
    assert target["storage_gb"] == 200


def test_three_boot_volumes_is_not_over_quota(target):
    """回归：曾把「引导卷+块存储 ≤ 2 个」当成硬限制，3 台机器必然误报超额。"""
    items = ft.usage_items(target)
    assert [i["label"] for i in items if i["over"]] == []
    assert ft.usage_warnings(target, items) == []


def test_storage_item_reports_full(target):
    storage = next(i for i in ft.usage_items(target) if i["key"] == "storage_gb")
    assert storage["percent"] == 100.0
    assert storage["over"] is False


# ── AMD 实例数 ──

@pytest.mark.parametrize("current,allowed", [
    (EMPTY, True),
    (usage([inst(AMD, 1, 1)], boot=[vol(50)]), True),
    (TWO_AMD, False),
])
def test_amd_instance_limit(current, allowed):
    assert ft.preflight(current, {"shape": AMD, "boot_gb": 50})["allow"] is allowed


def test_third_amd_blocker_names_the_limit():
    result = ft.preflight(TWO_AMD, {"shape": AMD, "boot_gb": 50})
    assert any("AMD 微型实例" in b for b in result["blockers"])


# ── ARM 规格 ──

def test_arm_fits_alongside_two_amd():
    result = ft.preflight(TWO_AMD, {"shape": ARM, "ocpus": 4, "memory_gb": 24, "boot_gb": 100})
    assert result["allow"]
    storage = next(c for c in result["checks"] if c["key"] == "storage_gb")
    assert storage["after"] == 200


def test_arm_can_be_split_in_two():
    half = usage([inst(ARM, 2, 12)], boot=[vol(50)])
    assert ft.preflight(half, {"shape": ARM, "ocpus": 2, "memory_gb": 12, "boot_gb": 50})["allow"]
    assert not ft.preflight(half, {"shape": ARM, "ocpus": 3, "memory_gb": 18, "boot_gb": 50})["allow"]


def test_arm_exhausted_blocks_on_both_cpu_and_memory():
    result = ft.preflight(FULL_ARM, {"shape": ARM, "ocpus": 1, "memory_gb": 6, "boot_gb": 50})
    assert not result["allow"]
    assert any("OCPU" in b for b in result["blockers"])
    assert any("内存" in b for b in result["blockers"])


@pytest.mark.parametrize("ocpus,memory_gb", [
    (1, 12),   # 每 OCPU 超过 6GB
    (4, 25),   # 总内存超过 24GB
    (5, 24),   # OCPU 超过 4
    (2, 1),    # 内存少于 OCPU 数
])
def test_invalid_arm_shapes_are_rejected(ocpus, memory_gb):
    result = ft.preflight(EMPTY, {"shape": ARM, "ocpus": ocpus,
                                  "memory_gb": memory_gb, "boot_gb": 50})
    assert not result["allow"]


# ── 存储 ──

def test_storage_limit_is_exact():
    plan = {"shape": ARM, "ocpus": 4, "memory_gb": 24}
    assert ft.preflight(TWO_AMD, {**plan, "boot_gb": 100})["allow"]
    over = ft.preflight(TWO_AMD, {**plan, "boot_gb": 101})
    assert not over["allow"]
    assert any("存储" in b for b in over["blockers"])


@pytest.mark.parametrize("boot_gb", [49, 0, 201])
def test_boot_volume_size_bounds(boot_gb):
    assert not ft.preflight(EMPTY, {"shape": AMD, "boot_gb": boot_gb})["allow"]


def test_warns_when_remaining_quota_cannot_fit_another_box():
    result = ft.preflight(TWO_AMD, {"shape": ARM, "ocpus": 4, "memory_gb": 24, "boot_gb": 60})
    assert result["allow"]
    assert result["warnings"]


def test_orphan_boot_volumes_still_consume_quota():
    """实例删了但保留引导卷时，额度并不会自动还回来。"""
    orphan = usage(boot=[vol(50), vol(100)])
    assert orphan["storage_gb"] == 150
    assert not ft.preflight(orphan, {"shape": ARM, "ocpus": 4,
                                     "memory_gb": 24, "boot_gb": 100})["allow"]


# ── 非免费规格 ──

def test_paid_shape_is_rejected_with_readable_reason():
    result = ft.preflight(EMPTY, {"shape": "VM.Standard.E4.Flex", "ocpus": 1,
                                  "memory_gb": 8, "boot_gb": 50})
    assert not result["allow"]
    assert any("Always Free" in b for b in result["blockers"])


def test_existing_paid_instances_raise_a_warning():
    paid = usage([inst("VM.Standard.E4.Flex", 2, 16)], boot=[vol(50)])
    assert any("一定在计费" in w for w in ft.usage_warnings(paid, ft.usage_items(paid)))


@pytest.mark.parametrize("shape,family", [
    (AMD, "amd"),
    (ARM, "arm"),
    ("VM.Standard.A1.Flex", "arm"),
    ("VM.Standard.E4.Flex", "other"),
    ("", "other"),
])
def test_shape_family_classification(shape, family):
    assert ft.shape_family(shape) == family
