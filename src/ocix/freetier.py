"""Always Free 额度模型与开机前预检。

额度口径（对照 Oracle Always Free 公开文档）：
- AMD：2 台 VM.Standard.E2.1.Micro（1 OCPU / 1GB，规格固定）
- ARM：VM.Standard.A1.Flex 合计 4 OCPU + 24GB，最多拆成 4 台
- 存储：引导卷 + 块存储**合计 200GB**，单个引导卷最小 50GB
- 「2 个块存储卷」指的是引导卷之外**额外挂载**的块存储；
  每台实例自带的引导卷不算在这个数里（否则 3 台机器必然误报超额）
"""

AMD_FREE_SHAPE = "VM.Standard.E2.1.Micro"
ARM_FREE_SHAPE = "VM.Standard.A1.Flex"

MIN_BOOT_GB = 50
MAX_BOOT_GB = 200
ARM_MIN_OCPU = 1
ARM_MAX_OCPU = 4
# A1.Flex 每 OCPU 最多配 6GB 内存
ARM_GB_PER_OCPU = 6

LIMITS = {
    "amd_micro_instances": 2,
    "arm_instances": 4,
    "arm_ocpu": 4,
    "arm_memory_gb": 24,
    "storage_gb": 200,
    "extra_block_volumes": 2,
}

FREE_SHAPES = [
    {
        "shape": AMD_FREE_SHAPE,
        "family": "amd",
        "label": "AMD 微型（1 OCPU / 1GB）",
        "flex": False,
        "ocpus": 1.0,
        "memory_gb": 1.0,
    },
    {
        "shape": ARM_FREE_SHAPE,
        "family": "arm",
        "label": "ARM Ampere A1（可调 1-4 OCPU / 最多 24GB）",
        "flex": True,
        "ocpus": None,
        "memory_gb": None,
    },
]


def shape_family(shape: str) -> str:
    s = (shape or "").upper()
    if s == AMD_FREE_SHAPE.upper():
        return "amd"
    if "A1.FLEX" in s or "AMPERE" in s or ".A1." in s:
        return "arm"
    return "other"


def _num(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _g(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def summarize(instances: list, block_volumes: list, boot_volumes: list) -> dict:
    """把原始资源列表压成一份额度视角的用量。"""
    amd, arm = [], []
    for i in instances:
        fam = shape_family(i.get("shape"))
        (amd if fam == "amd" else arm if fam == "arm" else []).append(i)

    def sc(i, *keys):
        cfg = _g(i, "shape-config", "shape_config", default={}) or {}
        return _num(_g(cfg, *keys))

    boot_gb = sum(_num(_g(v, "size-in-gbs", "size_in_gbs")) for v in boot_volumes)
    block_gb = sum(_num(_g(v, "size-in-gbs", "size_in_gbs")) for v in block_volumes)

    return {
        "amd_micro_instances": len(amd),
        "arm_instances": len(arm),
        "arm_ocpu": round(sum(sc(i, "ocpus") for i in arm), 2),
        "arm_memory_gb": round(sum(sc(i, "memory-in-gbs", "memory_in_gbs") for i in arm), 2),
        "boot_volume_count": len(boot_volumes),
        "boot_volume_gb": round(boot_gb, 2),
        "block_volume_count": len(block_volumes),
        "block_volume_gb": round(block_gb, 2),
        "storage_gb": round(boot_gb + block_gb, 2),
        "total_instances": len(instances),
        "running_instances": sum(
            1 for i in instances if _g(i, "lifecycle-state", "lifecycle_state") == "RUNNING"),
        "stopped_instances": sum(
            1 for i in instances if _g(i, "lifecycle-state", "lifecycle_state") == "STOPPED"),
        "other_shape_instances": sum(
            1 for i in instances if shape_family(i.get("shape")) == "other"),
    }


_ITEM_SPECS = [
    ("amd_micro_instances", "AMD 微型实例", "台"),
    ("arm_instances", "ARM 实例数", "台"),
    ("arm_ocpu", "ARM OCPU", "核"),
    ("arm_memory_gb", "ARM 内存", "GB"),
    ("storage_gb", "存储合计（引导+块）", "GB"),
    ("block_volume_count", "额外块存储卷", "个"),
]


def usage_items(current: dict) -> list:
    items = []
    for key, label, unit in _ITEM_SPECS:
        limit = LIMITS["extra_block_volumes"] if key == "block_volume_count" else LIMITS[key]
        used = current.get(key, 0)
        items.append({
            "key": key,
            "label": label,
            "used": used,
            "limit": limit,
            "unit": unit,
            "percent": round(min(used / limit * 100, 999), 1) if limit else 0,
            "over": used > limit,
        })
    return items


def usage_warnings(current: dict, items: list) -> list:
    out = [
        f"{i['label']} 已超出免费额度（{i['used']}/{i['limit']}{i['unit']}），可能产生费用"
        for i in items if i["over"]
    ]
    if current.get("other_shape_instances"):
        out.append(
            f"有 {current['other_shape_instances']} 台实例不是 Always Free 规格，"
            f"这部分一定在计费，请到 OCI 控制台确认")
    return out


def normalize_plan(plan: dict) -> dict:
    """把前端传来的创建参数补全成规范形态（不做额度判断）。"""
    shape = (plan.get("shape") or "").strip()
    family = shape_family(shape)

    # 只有「没传」才套默认值；传了 0 就是 0，交给下面的校验拒掉。
    # 早先写成 `x or DEFAULT`，0 是假值会被悄悄改成合法值，等于绕过了闸门。
    raw_boot = plan.get("boot_gb")
    boot_gb = MIN_BOOT_GB if raw_boot is None else int(_num(raw_boot, 0))

    if family == "amd":
        ocpus, memory_gb = 1.0, 1.0
    else:
        raw_ocpus = plan.get("ocpus")
        ocpus = 1.0 if raw_ocpus is None else _num(raw_ocpus, 0)
        raw_mem = plan.get("memory_gb")
        memory_gb = (ocpus * ARM_GB_PER_OCPU) if raw_mem is None else _num(raw_mem, 0)
    return {
        "shape": shape,
        "family": family,
        "ocpus": round(ocpus, 2),
        "memory_gb": round(memory_gb, 2),
        "boot_gb": boot_gb,
    }


def preflight(current: dict, plan: dict) -> dict:
    """核算「这台机器建出来之后」是否仍在免费额度内。

    返回 allow=False 时调用方必须拒绝创建——这是本面板对免费额度的硬闸门。
    """
    p = normalize_plan(plan)
    blockers, checks = [], []

    def add(key, label, cur, adding, limit, unit):
        after = round(cur + adding, 2)
        ok = after <= limit
        checks.append({
            "key": key, "label": label, "current": cur, "adding": adding,
            "after": after, "limit": limit, "unit": unit, "ok": ok,
        })
        if not ok:
            blockers.append(f"{label}：创建后将达到 {after}{unit}，超出免费额度 {limit}{unit}")

    if p["family"] == "other":
        blockers.append(
            f"规格 {p['shape'] or '(未选择)'} 不在 Always Free 范围内，"
            f"只能选 {AMD_FREE_SHAPE} 或 {ARM_FREE_SHAPE}")
        return {"allow": False, "plan": p, "checks": checks, "blockers": blockers, "warnings": []}

    if p["family"] == "amd":
        add("amd_micro_instances", "AMD 微型实例",
            current.get("amd_micro_instances", 0), 1, LIMITS["amd_micro_instances"], "台")
    else:
        add("arm_instances", "ARM 实例数",
            current.get("arm_instances", 0), 1, LIMITS["arm_instances"], "台")
        add("arm_ocpu", "ARM OCPU",
            current.get("arm_ocpu", 0), p["ocpus"], LIMITS["arm_ocpu"], "核")
        add("arm_memory_gb", "ARM 内存",
            current.get("arm_memory_gb", 0), p["memory_gb"], LIMITS["arm_memory_gb"], "GB")

    add("storage_gb", "存储合计（引导+块）",
        current.get("storage_gb", 0), p["boot_gb"], LIMITS["storage_gb"], "GB")

    # 规格自身的合法性
    if p["boot_gb"] < MIN_BOOT_GB:
        blockers.append(f"引导卷不能小于 {MIN_BOOT_GB}GB")
    if p["boot_gb"] > MAX_BOOT_GB:
        blockers.append(f"引导卷不能大于 {MAX_BOOT_GB}GB")
    if p["family"] == "arm":
        if not (ARM_MIN_OCPU <= p["ocpus"] <= ARM_MAX_OCPU):
            blockers.append(f"ARM OCPU 只能在 {ARM_MIN_OCPU}-{ARM_MAX_OCPU} 之间")
        if p["memory_gb"] > p["ocpus"] * ARM_GB_PER_OCPU:
            blockers.append(
                f"A1.Flex 每 OCPU 最多配 {ARM_GB_PER_OCPU}GB 内存，"
                f"{p['ocpus']} OCPU 最多 {p['ocpus'] * ARM_GB_PER_OCPU:g}GB")
        if p["memory_gb"] < p["ocpus"]:
            blockers.append("A1.Flex 每 OCPU 至少 1GB 内存")

    warnings = []
    remaining_gb = LIMITS["storage_gb"] - current.get("storage_gb", 0) - p["boot_gb"]
    if not blockers and remaining_gb < MIN_BOOT_GB:
        warnings.append(
            f"建完这台后只剩 {remaining_gb:g}GB 存储额度，不够再开一台"
            f"（新机器引导卷最少 {MIN_BOOT_GB}GB）")

    return {
        "allow": not blockers,
        "plan": p,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
    }
