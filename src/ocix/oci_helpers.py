import base64
import configparser
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import freetier
from .config import COMPARTMENT_CACHE_TTL, OCI_CONFIG_PATH, OCI_LAUNCH_TIMEOUT
from .oci_cli import OCICLIError, TTLCache, gather, run_oci

# ---- 通用小工具 ----

_ACTIVE_INSTANCE_STATES = (
    "PROVISIONING", "RUNNING", "STARTING", "STOPPING", "STOPPED", "CREATING_IMAGE", "MOVING",
)


def _num(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _get(d: dict, *keys, default=None):
    """oci CLI 输出是 kebab-case，SDK/部分字段是 snake_case，两种都兜住。"""
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def read_profile_config(profile: str) -> dict:
    cp = configparser.ConfigParser()
    # 保留大小写
    cp.optionxform = str
    cp.read(str(OCI_CONFIG_PATH), encoding="utf-8")
    if profile != "DEFAULT" and not cp.has_section(profile):
        raise OCICLIError(f"配置中不存在 profile: {profile}")
    if profile == "DEFAULT" and not cp.defaults():
        raise OCICLIError("配置中不存在 profile: DEFAULT")
    return {k: v for k, v in cp.items(profile)}


def tenancy_of(profile: str) -> str:
    cfg = read_profile_config(profile)
    tenancy = cfg.get("tenancy")
    if not tenancy:
        raise OCICLIError(f"profile [{profile}] 缺少 tenancy 字段")
    return tenancy


def get_user(profile: str) -> dict:
    cfg = read_profile_config(profile)
    user = cfg.get("user")
    if not user:
        raise OCICLIError(f"profile [{profile}] 缺少 user 字段")
    data = run_oci(profile, "iam", "user", "get", "--user-id", user)
    return data.get("data", {})


# ---- Compartment ----

_comp_cache = {}
_comp_lock = threading.Lock()


def invalidate_compartment_cache(profile: str = None):
    with _comp_lock:
        if profile:
            _comp_cache.pop(profile, None)
        else:
            _comp_cache.clear()


def list_compartments(profile: str, use_cache: bool = True) -> list:
    """租户根 + 全部可访问子 compartment（含层级路径）。"""
    if use_cache:
        with _comp_lock:
            hit = _comp_cache.get(profile)
        if hit and time.time() - hit[0] < COMPARTMENT_CACHE_TTL:
            return hit[1]

    tenancy = tenancy_of(profile)
    items = []
    try:
        data = run_oci(
            profile, "iam", "compartment", "list",
            "--compartment-id", tenancy,
            "--compartment-id-in-subtree", "true",
            "--access-level", "ACCESSIBLE",
            "--all",
        )
        items = data.get("data", []) or []
    except OCICLIError:
        # 权限不足时至少还能用租户根
        items = []

    by_id = {}
    for c in items:
        if _get(c, "lifecycle-state", "lifecycle_state") != "ACTIVE":
            continue
        by_id[c.get("id")] = {
            "id": c.get("id"),
            "name": c.get("name"),
            "parent": _get(c, "compartment-id", "compartment_id"),
        }

    def _path(cid, depth=0):
        node = by_id.get(cid)
        if not node or depth > 8:
            return []
        return _path(node["parent"], depth + 1) + [node["name"]]

    result = [{"id": tenancy, "name": "(root)", "path": "(root)", "depth": 0}]
    for cid, node in by_id.items():
        parts = _path(cid)
        result.append({
            "id": cid,
            "name": node["name"],
            "path": "/".join(parts) if parts else node["name"],
            "depth": len(parts),
        })
    result[1:] = sorted(result[1:], key=lambda x: x["path"])

    with _comp_lock:
        _comp_cache[profile] = (time.time(), result)
    return result


def _target_compartments(profile: str, compartment_id: str = None, subtree: bool = False) -> list:
    if compartment_id:
        if not subtree:
            return [compartment_id]
        comps = list_compartments(profile)
        # 指定 compartment 及其后代
        keep, index = [], {c["id"]: c for c in comps}
        root = index.get(compartment_id)
        prefix = (root or {}).get("path", "")
        for c in comps:
            if c["id"] == compartment_id or (prefix and c["path"].startswith(prefix + "/")):
                keep.append(c["id"])
        return keep or [compartment_id]
    if subtree:
        return [c["id"] for c in list_compartments(profile)]
    return [tenancy_of(profile)]


# ---- 实例 ----

# 列表类数据缓存 30 秒：每次 CLI 调用固定 ~1.1s 进程开销，
# 同一个页面里 /instances 和 /monitor/usage 都要用实例列表，不缓存等于翻倍。
_read_cache = TTLCache(ttl=30)


def invalidate_read_cache(profile: str = None):
    """任何写操作之后调用，保证界面立刻看到新状态。"""
    _read_cache.invalidate(profile)


def list_instances(
    profile: str,
    compartment_id: str = None,
    subtree: bool = False,
    include_terminated: bool = False,
) -> list:
    """列出实例。subtree=True 时并发遍历所有可访问的 compartment。"""
    ck = (profile, "instances", compartment_id, subtree, include_terminated)
    cached = _read_cache.get(ck)
    if cached is not None:
        return cached
    targets = _target_compartments(profile, compartment_id, subtree)

    def _one(cid):
        data = run_oci(profile, "compute", "instance", "list", "--compartment-id", cid, "--all")
        return data.get("data", []) or []

    results, errors = [], []
    for _cid, items, err in gather(_one, targets):
        if err is not None:
            # 子 compartment 无权限是常态，单点失败不该让整表空掉
            if len(targets) == 1:
                raise err
            errors.append(str(err))
            continue
        results.extend(items)

    if not include_terminated:
        results = [
            i for i in results
            if _get(i, "lifecycle-state", "lifecycle_state") in _ACTIVE_INSTANCE_STATES
        ]
    # 跨 compartment 可能重复（极少见），按 id 去重
    seen, deduped = set(), []
    for i in results:
        if i.get("id") in seen:
            continue
        seen.add(i.get("id"))
        deduped.append(i)
    return _read_cache.set(ck, deduped)


def attach_ips(profile: str, instances: list) -> list:
    """为实例补齐公网 / 内网 IP。

    按 compartment 批量取 VNIC attachment（1 次），再并发 get vnic（N 次），
    比逐实例 list-vnics 少一半调用。失败时静默跳过，不影响主表。
    """
    if not instances:
        return instances

    comp_ids = {
        _get(i, "compartment-id", "compartment_id")
        for i in instances
        if _get(i, "compartment-id", "compartment_id")
    }

    def _attachments(cid):
        data = run_oci(profile, "compute", "vnic-attachment", "list", "--compartment-id", cid, "--all")
        return data.get("data", []) or []

    inst_to_vnic = {}
    for _cid, atts, err in gather(_attachments, comp_ids):
        if err is not None or not atts:
            continue
        for a in atts:
            if _get(a, "lifecycle-state", "lifecycle_state") != "ATTACHED":
                continue
            iid = _get(a, "instance-id", "instance_id")
            vid = _get(a, "vnic-id", "vnic_id")
            if iid and vid:
                inst_to_vnic.setdefault(iid, vid)

    wanted = {i.get("id"): inst_to_vnic.get(i.get("id")) for i in instances}
    vnic_ids = {v for v in wanted.values() if v}

    def _vnic(vid):
        data = run_oci(profile, "network", "vnic", "get", "--vnic-id", vid)
        return data.get("data", {}) or {}

    vnic_info = {}
    for vid, vnic, err in gather(_vnic, vnic_ids):
        if err is not None or not vnic:
            continue
        v6 = _get(vnic, "ipv6-addresses", "ipv6_addresses", default=[]) or []
        vnic_info[vid] = {
            "public_ip": _get(vnic, "public-ip", "public_ip"),
            "private_ip": _get(vnic, "private-ip", "private_ip"),
            "ipv6": v6[0] if v6 else None,
        }

    for i in instances:
        info = vnic_info.get(wanted.get(i.get("id")) or "", {})
        i["_public_ip"] = info.get("public_ip")
        i["_private_ip"] = info.get("private_ip")
        i["_ipv6"] = info.get("ipv6")
    return instances


def instance_action(profile: str, instance_id: str, action: str) -> dict:
    # 合法 action: START / STOP / SOFTSTOP / RESET / SOFTRESET
    data = run_oci(
        profile,
        "compute",
        "instance",
        "action",
        "--instance-id",
        instance_id,
        "--action",
        action.upper(),
    )
    invalidate_read_cache(profile)
    return data.get("data", {})


# ---- 存储 ----

def list_block_volumes(profile: str, compartment_id: str = None, subtree: bool = False) -> list:
    ck = (profile, "block_volumes", compartment_id, subtree)
    cached = _read_cache.get(ck)
    if cached is not None:
        return cached
    targets = _target_compartments(profile, compartment_id, subtree)

    def _one(cid):
        data = run_oci(profile, "bv", "volume", "list", "--compartment-id", cid, "--all")
        return data.get("data", []) or []

    out = []
    for _cid, items, err in gather(_one, targets):
        if err is not None:
            if len(targets) == 1:
                raise err
            continue
        out.extend(items)
    return _read_cache.set(
        ck, [v for v in out if _get(v, "lifecycle-state", "lifecycle_state") != "TERMINATED"])


def list_boot_volumes(profile: str, compartment_id: str = None, subtree: bool = False) -> list:
    """引导卷同样占用 Always Free 的 200GB 额度，必须计入。

    ListBootVolumes 早期版本要求带可用域，这里先不带，失败再按 AD 逐个查。
    """
    ck = (profile, "boot_volumes", compartment_id, subtree)
    cached = _read_cache.get(ck)
    if cached is not None:
        return cached
    targets = _target_compartments(profile, compartment_id, subtree)

    def _one(cid):
        try:
            data = run_oci(profile, "bv", "boot-volume", "list", "--compartment-id", cid, "--all")
            return data.get("data", []) or []
        except OCICLIError:
            ads = _availability_domains(profile)
            out = []
            for ad in ads:
                try:
                    data = run_oci(
                        profile, "bv", "boot-volume", "list",
                        "--compartment-id", cid, "--availability-domain", ad, "--all",
                    )
                    out.extend(data.get("data", []) or [])
                except OCICLIError:
                    continue
            return out

    out = []
    for _cid, items, err in gather(_one, targets):
        if err is not None:
            continue
        out.extend(items)
    return _read_cache.set(
        ck, [v for v in out if _get(v, "lifecycle-state", "lifecycle_state") != "TERMINATED"])


def _availability_domains(profile: str) -> list:
    try:
        data = run_oci(
            profile, "iam", "availability-domain", "list",
            "--compartment-id", tenancy_of(profile),
        )
        return [a.get("name") for a in (data.get("data", []) or []) if a.get("name")]
    except OCICLIError:
        return []


def list_availability_domains(profile: str) -> list:
    return _availability_domains(profile)


# ---- 存储清理（200GB 额度的主要泄漏点）----

def _boot_volume_attachments(profile: str, compartment_id: str) -> dict:
    """boot-volume-id -> 附着状态。终止实例时勾了「保留引导卷」的卷会留在这里没人管。"""
    out = {}
    for ad in _availability_domains(profile):
        try:
            data = run_oci(
                profile, "compute", "boot-volume-attachment", "list",
                "--compartment-id", compartment_id,
                "--availability-domain", ad, "--all",
            )
        except OCICLIError:
            continue
        for a in data.get("data", []) or []:
            bid = _get(a, "boot-volume-id", "boot_volume_id")
            if not bid:
                continue
            state = _get(a, "lifecycle-state", "lifecycle_state")
            if state == "ATTACHED" or bid not in out:
                out[bid] = {
                    "state": state,
                    "instance_id": _get(a, "instance-id", "instance_id"),
                }
    return out


def _block_volume_attachments(profile: str, compartment_id: str) -> dict:
    out = {}
    try:
        data = run_oci(
            profile, "compute", "volume-attachment", "list",
            "--compartment-id", compartment_id, "--all",
        )
    except OCICLIError:
        return out
    for a in data.get("data", []) or []:
        vid = _get(a, "volume-id", "volume_id")
        if not vid:
            continue
        state = _get(a, "lifecycle-state", "lifecycle_state")
        if state == "ATTACHED" or vid not in out:
            out[vid] = {"state": state, "instance_id": _get(a, "instance-id", "instance_id")}
    return out


def storage_overview(profile: str, compartment_id: str = None, subtree: bool = True) -> dict:
    """列出所有卷并标出孤儿卷（没挂在任何实例上，却仍在吃 200GB 额度）。"""
    boot = list_boot_volumes(profile, compartment_id, subtree=subtree)
    block = list_block_volumes(profile, compartment_id, subtree=subtree)
    instances = list_instances(profile, compartment_id, subtree=subtree)
    inst_names = {i.get("id"): (i.get("display-name") or i.get("display_name")) for i in instances}

    targets = _target_compartments(profile, compartment_id, subtree)
    boot_att, block_att = {}, {}
    for _cid, res, err in gather(lambda c: (_boot_volume_attachments(profile, c),
                                            _block_volume_attachments(profile, c)), targets):
        if err is None and res:
            boot_att.update(res[0])
            block_att.update(res[1])

    def _row(v, kind, att):
        vid = v.get("id")
        a = att.get(vid) or {}
        attached = a.get("state") == "ATTACHED"
        return {
            "id": vid,
            "kind": kind,
            "display_name": _get(v, "display-name", "display_name"),
            "size_gb": _num(_get(v, "size-in-gbs", "size_in_gbs")),
            "state": _get(v, "lifecycle-state", "lifecycle_state"),
            "availability_domain": _get(v, "availability-domain", "availability_domain"),
            "time_created": _get(v, "time-created", "time_created"),
            "compartment_id": _get(v, "compartment-id", "compartment_id"),
            "vpus_per_gb": int(_num(_get(v, "vpus-per-gb", "vpus_per_gb"), 10)),
            "attached": attached,
            "attached_to": inst_names.get(a.get("instance_id")) if attached else None,
            "orphan": not attached,
        }

    volumes = ([_row(v, "boot", boot_att) for v in boot]
               + [_row(v, "block", block_att) for v in block])
    orphans = [v for v in volumes if v["orphan"]]
    return {
        "volumes": sorted(volumes, key=lambda v: (not v["orphan"], v["kind"], v["display_name"] or "")),
        "summary": {
            "total_gb": round(sum(v["size_gb"] for v in volumes), 2),
            "limit_gb": freetier.LIMITS["storage_gb"],
            "orphan_count": len(orphans),
            "orphan_gb": round(sum(v["size_gb"] for v in orphans), 2),
            "boot_count": len(boot),
            "block_count": len(block),
        },
    }


def delete_volume(profile: str, volume_id: str, kind: str) -> None:
    if kind == "boot":
        run_oci(profile, "bv", "boot-volume", "delete", "--boot-volume-id", volume_id, "--force")
    else:
        run_oci(profile, "bv", "volume", "delete", "--volume-id", volume_id, "--force")
    invalidate_read_cache(profile)


# ---- 镜像与网络 ----

def _version_number(v: str) -> str:
    """从 '24.04' / '24.04-Minimal' / '24.04 Minimal' 里取出纯版本号 '24.04'。

    Minimal 变体必须和标准镜像归到同一个大版本，否则它会挤掉一个真正的版本。
    """
    m = re.match(r"\s*(\d+(?:\.\d+)*)", v or "")
    return m.group(1) if m else (v or "")


def _version_key(v: str) -> tuple:
    parts = [int(c) for c in _version_number(v).split(".") if c.isdigit()]
    return tuple(parts[:3] + [0] * (3 - len(parts[:3])))


def list_images(profile: str, compartment_id: str = None, shape: str = None,
                os_name: str = "Canonical Ubuntu", keep_majors: int = 2) -> list:
    """只列 Ubuntu 镜像，按规格过滤（ARM 需要 aarch64），且只保留最新的两个大版本。"""
    cid = compartment_id or tenancy_of(profile)
    args = [
        "compute", "image", "list", "--compartment-id", cid,
        "--operating-system", os_name,
        "--sort-by", "TIMECREATED", "--sort-order", "DESC", "--all",
    ]
    if shape:
        args += ["--shape", shape]
    data = run_oci(profile, *args)

    by_version = {}
    for im in data.get("data", []) or []:
        if _get(im, "lifecycle-state", "lifecycle_state") != "AVAILABLE":
            continue
        name = _get(im, "display-name", "display_name") or ""
        ver = _get(im, "operating-system-version", "operating_system_version") or ""
        row = {
            "id": im.get("id"),
            "display_name": name,
            "os": _get(im, "operating-system", "operating_system"),
            "os_version": ver,
            "time_created": _get(im, "time-created", "time_created"),
            "minimal": "Minimal" in name,
        }
        # 按纯版本号归组：每个大版本只留最新一版，标准镜像优先于 Minimal
        key = _version_number(ver)
        cur = by_version.get(key)
        if cur is None or (cur["minimal"] and not row["minimal"]):
            by_version[key] = row

    versions = sorted(by_version, key=_version_key, reverse=True)[:max(1, keep_majors)]
    return [by_version[v] for v in versions]


def list_subnets(profile: str, compartment_id: str = None) -> list:
    """列出可用于开机的子网（附带所属 VCN 名）。"""
    cid = compartment_id or tenancy_of(profile)
    try:
        vcn_data = run_oci(profile, "network", "vcn", "list", "--compartment-id", cid, "--all")
    except OCICLIError:
        return []
    vcns = {
        v["id"]: _get(v, "display-name", "display_name")
        for v in (vcn_data.get("data", []) or [])
        if _get(v, "lifecycle-state", "lifecycle_state") == "AVAILABLE"
    }
    if not vcns:
        return []

    def _one(vid):
        data = run_oci(profile, "network", "subnet", "list",
                       "--compartment-id", cid, "--vcn-id", vid, "--all")
        return data.get("data", []) or []

    out = []
    for vid, subnets, err in gather(_one, list(vcns)):
        if err is not None:
            continue
        for s in subnets:
            if _get(s, "lifecycle-state", "lifecycle_state") != "AVAILABLE":
                continue
            out.append({
                "id": s.get("id"),
                "display_name": _get(s, "display-name", "display_name"),
                "vcn_id": vid,
                "vcn_name": vcns.get(vid),
                "cidr_block": _get(s, "cidr-block", "cidr_block"),
                "ipv6_cidr_block": _get(s, "ipv6-cidr-block", "ipv6_cidr_block"),
                "ipv6_enabled": bool(_get(s, "ipv6-cidr-block", "ipv6_cidr_block")),
                "public": not _get(s, "prohibit-public-ip-on-vnic",
                                   "prohibit_public_ip_on_vnic", default=False),
            })
    return [s for s in out if s["public"]] + [s for s in out if not s["public"]]


DEFAULT_NETWORK_NAME = "ocix-vcn"


def resolve_subnet(profile: str, compartment_id: str = None, create_if_missing: bool = True) -> dict:
    """定位这个账户开机用的子网，没有就建一个默认的。

    面板不让用户选子网——所有机器共用一个，省掉一堆网络概念。
    优先挑能分配公网 IP 的子网；一个都没有才新建 VCN + 网关 + 公共子网。
    """
    cid = compartment_id or tenancy_of(profile)
    subnets = list_subnets(profile, cid)
    public = [s for s in subnets if s.get("public")]
    if public:
        return {**public[0], "created": False}
    if subnets:
        return {**subnets[0], "created": False}
    if not create_if_missing:
        raise OCICLIError("这个账户还没有可用子网")
    net = create_network(profile, cid, DEFAULT_NETWORK_NAME)
    return {
        "id": net["subnet_id"],
        "display_name": net["subnet_name"],
        "vcn_id": net["vcn_id"],
        "vcn_name": net["vcn_name"],
        "ipv6_enabled": False,
        "public": True,
        "created": True,
    }


def create_network(profile: str, compartment_id: str = None, name: str = "ocix-vcn") -> dict:
    """给全新租户一键建 VCN + 互联网网关 + 公共子网，否则没法开第一台机器。"""
    cid = compartment_id or tenancy_of(profile)
    label = "".join(c for c in name.lower() if c.isalnum())[:13] or "ocixvcn"

    vcn = run_oci(profile, "network", "vcn", "create",
                  "--compartment-id", cid, "--cidr-block", "10.0.0.0/16",
                  "--display-name", name, "--dns-label", label,
                  "--wait-for-state", "AVAILABLE",
                  timeout=OCI_LAUNCH_TIMEOUT).get("data", {})
    vcn_id = vcn.get("id")
    if not vcn_id:
        raise OCICLIError("VCN 创建失败：未返回 VCN id")

    ig = run_oci(profile, "network", "internet-gateway", "create",
                 "--compartment-id", cid, "--vcn-id", vcn_id, "--is-enabled", "true",
                 "--display-name", f"{name}-ig", "--wait-for-state", "AVAILABLE",
                 timeout=OCI_LAUNCH_TIMEOUT).get("data", {})

    rt_id = _get(vcn, "default-route-table-id", "default_route_table_id")
    if rt_id and ig.get("id"):
        run_oci(profile, "network", "route-table", "update", "--rt-id", rt_id, "--force",
                "--route-rules", json.dumps([{
                    "destination": "0.0.0.0/0",
                    "destinationType": "CIDR_BLOCK",
                    "networkEntityId": ig["id"],
                }]))

    subnet = run_oci(profile, "network", "subnet", "create",
                     "--compartment-id", cid, "--vcn-id", vcn_id,
                     "--cidr-block", "10.0.0.0/24", "--display-name", f"{name}-public",
                     "--dns-label", "public", "--wait-for-state", "AVAILABLE",
                     timeout=OCI_LAUNCH_TIMEOUT).get("data", {})
    return {
        "vcn_id": vcn_id,
        "vcn_name": name,
        "subnet_id": subnet.get("id"),
        "subnet_name": _get(subnet, "display-name", "display_name"),
    }


# ---- 创建 / 终止实例 ----

def launch_instance(profile: str, params: dict) -> dict:
    """开一台实例。额度预检必须在调用方先跑过。"""
    cid = params["compartment_id"]
    plan = freetier.normalize_plan(params)
    args = [
        "compute", "instance", "launch",
        "--compartment-id", cid,
        "--availability-domain", params["availability_domain"],
        "--display-name", params["display_name"],
        "--image-id", params["image_id"],
        "--shape", plan["shape"],
        "--boot-volume-size-in-gbs", str(plan["boot_gb"]),
    ]
    # 只用各版本 CLI 都有的扁平参数。
    # 早先给 IPv6 走 --create-vnic-details，但不少 oci CLI 版本压根没有这个选项
    # （报 "No such option: --create-vnic-details"），所以 IPv6 改为建完之后单独挂。
    args += ["--subnet-id", params["subnet_id"], "--assign-public-ip", "true"]
    if plan["family"] == "arm":
        args += ["--shape-config", json.dumps({
            "ocpus": plan["ocpus"], "memoryInGBs": plan["memory_gb"],
        })]

    metadata = {"ssh_authorized_keys": params["ssh_public_key"].strip()}
    user_data = params.get("user_data")
    if user_data:
        metadata["user_data"] = base64.b64encode(user_data.encode("utf-8")).decode("ascii")
    args += ["--metadata", json.dumps(metadata)]

    data = run_oci(profile, *args, timeout=OCI_LAUNCH_TIMEOUT)
    invalidate_read_cache(profile)
    return data.get("data", {})


def primary_vnic(profile: str, instance_id: str, compartment_id: str) -> dict:
    """拿到实例的主 VNIC——换 IP、查 IPv6、定位子网都要靠它。"""
    data = run_oci(profile, "compute", "vnic-attachment", "list",
                   "--compartment-id", compartment_id, "--instance-id", instance_id, "--all")
    vnic_id = None
    for a in data.get("data", []) or []:
        if _get(a, "lifecycle-state", "lifecycle_state") == "ATTACHED":
            vnic_id = _get(a, "vnic-id", "vnic_id")
            break
    if not vnic_id:
        raise OCICLIError("找不到实例的网卡（VNIC），实例可能正在创建或已终止")
    vnic = run_oci(profile, "network", "vnic", "get", "--vnic-id", vnic_id).get("data", {}) or {}
    return {
        "vnic_id": vnic_id,
        "subnet_id": _get(vnic, "subnet-id", "subnet_id"),
        "public_ip": _get(vnic, "public-ip", "public_ip"),
        "private_ip": _get(vnic, "private-ip", "private_ip"),
        "ipv6_addresses": _get(vnic, "ipv6-addresses", "ipv6_addresses", default=[]) or [],
    }


def change_public_ip(profile: str, instance_id: str, compartment_id: str) -> dict:
    """换一个公网 IPv4：删掉临时公网 IP 再申请一个新的。

    OCI 的临时（EPHEMERAL）公网 IP 没有「换一个」的接口，只能删了重建，
    因此中间会有几秒钟没有公网地址。保留（RESERVED）IP 不走这条路，会直接拒绝。
    """
    vnic = primary_vnic(profile, instance_id, compartment_id)
    ips = run_oci(profile, "network", "private-ip", "list",
                  "--vnic-id", vnic["vnic_id"], "--all").get("data", []) or []
    primary = next((p for p in ips if _get(p, "is-primary", "is_primary")), None) or (ips[0] if ips else None)
    if not primary:
        raise OCICLIError("找不到主私网 IP，无法更换公网 IP")
    private_ip_id = primary.get("id")

    old_ip = vnic.get("public_ip")
    if old_ip:
        try:
            # 注意是 `public-ip get --private-ip-id`，
            # 没有 get-public-ip-by-private-ip-id 这个子命令（写错会直接报 No such command）
            cur = run_oci(profile, "network", "public-ip", "get",
                          "--private-ip-id", private_ip_id).get("data", {}) or {}
        except OCICLIError:
            cur = {}
        lifetime = _get(cur, "lifetime")
        if lifetime == "RESERVED":
            raise OCICLIError(
                "这台机器用的是保留（Reserved）公网 IP，面板不会动它——"
                "保留 IP 换掉就找不回来了。请到 OCI 控制台手动处理。")
        if cur.get("id"):
            run_oci(profile, "network", "public-ip", "delete",
                    "--public-ip-id", cur["id"], "--force")

    new = run_oci(profile, "network", "public-ip", "create",
                  "--compartment-id", compartment_id,
                  "--lifetime", "EPHEMERAL",
                  "--private-ip-id", private_ip_id,
                  "--wait-for-state", "ASSIGNED",
                  timeout=OCI_LAUNCH_TIMEOUT).get("data", {}) or {}
    invalidate_read_cache(profile)
    return {"old_ip": old_ip, "new_ip": _get(new, "ip-address", "ip_address")}


# ---- IPv6 ----

def subnet_ipv6_status(profile: str, subnet_id: str) -> dict:
    sub = run_oci(profile, "network", "subnet", "get",
                  "--subnet-id", subnet_id).get("data", {}) or {}
    return {
        "subnet_id": subnet_id,
        "vcn_id": _get(sub, "vcn-id", "vcn_id"),
        "ipv6_cidr_block": _get(sub, "ipv6-cidr-block", "ipv6_cidr_block"),
        "enabled": bool(_get(sub, "ipv6-cidr-block", "ipv6_cidr_block")),
        "route_table_id": _get(sub, "route-table-id", "route_table_id"),
    }


def _vcn_ipv6_blocks(profile: str, vcn_id: str) -> tuple:
    vcn = run_oci(profile, "network", "vcn", "get", "--vcn-id", vcn_id).get("data", {}) or {}
    blocks = _get(vcn, "ipv6-cidr-blocks", "ipv6_cidr_blocks", default=[]) or []
    return vcn, blocks


def _add_vcn_ipv6(profile: str, vcn_id: str) -> None:
    """给 VCN 申请一段 Oracle 分配的 /56。

    AddVcnIpv6CidrDetails 要么给显式 ipv6CidrBlock，要么把 isOracleGuaAllocationEnabled
    置为 true——两个都不给会被服务端拒掉，这正是之前 IPv6 一直开不起来的原因。
    老版本 CLI 没有这个参数，所以失败后再退回不带参数的写法。
    """
    try:
        run_oci(profile, "network", "vcn", "add-ipv6-vcn-cidr", "--vcn-id", vcn_id,
                "--is-oracle-gua-allocation-enabled", "true", timeout=OCI_LAUNCH_TIMEOUT)
        return
    except OCICLIError as e:
        msg = (e.message or "").lower()
        if "already" in msg or "limitexceeded" in msg or "conflict" in msg:
            return
        if "no such option" not in msg and "unrecognized" not in msg and "usage:" not in msg:
            raise
    run_oci(profile, "network", "vcn", "add-ipv6-vcn-cidr", "--vcn-id", vcn_id,
            timeout=OCI_LAUNCH_TIMEOUT)


def _ensure_ipv6_route(profile: str, vcn_id: str, compartment_id: str, route_table_id: str) -> None:
    """补一条 ::/0 → 互联网网关的默认路由，否则 IPv6 出不去。"""
    igs = run_oci(profile, "network", "internet-gateway", "list",
                  "--compartment-id", compartment_id, "--vcn-id", vcn_id,
                  "--all").get("data", []) or []
    ig_id = next((g.get("id") for g in igs
                  if _get(g, "lifecycle-state", "lifecycle_state") == "AVAILABLE"), None)
    if not ig_id or not route_table_id:
        return
    rt = run_oci(profile, "network", "route-table", "get",
                 "--rt-id", route_table_id).get("data", {}) or {}
    rules = _get(rt, "route-rules", "route_rules", default=[]) or []
    norm = []
    for r in rules:
        rule = {
            "destination": _get(r, "destination"),
            "destinationType": _get(r, "destination-type", "destinationType") or "CIDR_BLOCK",
            "networkEntityId": _get(r, "network-entity-id", "networkEntityId"),
        }
        if _get(r, "description"):
            rule["description"] = _get(r, "description")
        norm.append(rule)
    if any(r["destination"] == "::/0" for r in norm):
        return
    norm.append({"destination": "::/0", "destinationType": "CIDR_BLOCK",
                 "networkEntityId": ig_id, "description": "ocix: ipv6 default route"})
    run_oci(profile, "network", "route-table", "update", "--rt-id", route_table_id,
            "--force", "--route-rules", json.dumps(norm), timeout=OCI_LAUNCH_TIMEOUT)


def _ensure_ipv6_ingress(profile: str, subnet_id: str) -> None:
    """放行 ::/0 的 SSH 入站。

    OCI 默认安全列表只写了 IPv4 的规则，不补这一条的话 IPv6 地址分下来也连不上，
    看起来就像「IPv6 不好使」。
    """
    sub = run_oci(profile, "network", "subnet", "get",
                  "--subnet-id", subnet_id).get("data", {}) or {}
    sl_ids = _get(sub, "security-list-ids", "security_list_ids", default=[]) or []
    if not sl_ids:
        return
    sid = sl_ids[0]
    rules = _raw_ingress(profile, sid)
    if any(r.get("source") == _ALL_V6 for r in rules):
        return
    rules.append({
        "protocol": "6", "source": _ALL_V6, "sourceType": "CIDR_BLOCK",
        "isStateless": False, "description": "ocix: ssh over ipv6",
        "tcpOptions": {"destinationPortRange": {"min": 22, "max": 22}},
    })
    _set_ingress(profile, sid, rules)


def ensure_subnet_ipv6(profile: str, subnet_id: str, compartment_id: str) -> dict:
    """给子网开通 IPv6：VCN 申请 /56 → 子网切一个 /64 → 补 ::/0 路由与入站规则。

    幂等：已经开通过就直接返回，不会重复申请。
    """
    st = subnet_ipv6_status(profile, subnet_id)
    vcn_id = st["vcn_id"]

    if st["enabled"]:
        # 子网已有 IPv6，但路由/安全组可能是缺的，补齐后再返回
        try:
            _ensure_ipv6_route(profile, vcn_id, compartment_id, st["route_table_id"])
            _ensure_ipv6_ingress(profile, subnet_id)
        except OCICLIError:
            pass
        return {**st, "changed": False}

    vcn, v6 = _vcn_ipv6_blocks(profile, vcn_id)
    if not v6:
        _add_vcn_ipv6(profile, vcn_id)
        vcn, v6 = _vcn_ipv6_blocks(profile, vcn_id)
    if not v6:
        raise OCICLIError(
            "VCN 没能拿到 IPv6 地址段。请确认该区域支持 IPv6，"
            "以及你的用户对 VCN 有 manage 权限。")

    # /56 里切第一个 /64 给子网
    subnet_cidr = v6[0].rsplit("/", 1)[0] + "/64"
    try:
        run_oci(profile, "network", "subnet", "update", "--subnet-id", subnet_id,
                "--ipv6-cidr-block", subnet_cidr, "--force", timeout=OCI_LAUNCH_TIMEOUT)
    except OCICLIError as e:
        if "already" not in (e.message or "").lower():
            raise

    # 路由和安全组补不上不影响地址已分配，但要让调用方知道
    warnings = []
    for step, fn in (("默认路由", lambda: _ensure_ipv6_route(
                          profile, vcn_id, compartment_id, st["route_table_id"])),
                     ("安全列表入站规则", lambda: _ensure_ipv6_ingress(profile, subnet_id))):
        try:
            fn()
        except OCICLIError as e:
            warnings.append(f"{step}没能自动补上：{e.message}")

    return {**subnet_ipv6_status(profile, subnet_id), "changed": True, "warnings": warnings}


def wait_for_primary_vnic(profile: str, instance_id: str, compartment_id: str,
                          timeout: int = 150) -> dict:
    """等实例的网卡挂上来。

    instance launch 立刻返回 PROVISIONING，这时 VNIC 往往还没挂好，
    直接去分配 IPv6 会找不到网卡。
    """
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            return primary_vnic(profile, instance_id, compartment_id)
        except OCICLIError as e:
            last_err = e
            time.sleep(5)
    raise last_err or OCICLIError(f"等待网卡就绪超时（{timeout}s）")


def add_ipv6_to_instance(profile: str, instance_id: str, compartment_id: str,
                         wait_seconds: int = 0) -> dict:
    """给已有实例加一个 IPv6 地址（子网没开通 IPv6 就顺手开通）。

    wait_seconds > 0 时会先等网卡就绪——刚创建出来的实例要用这个。
    """
    if wait_seconds > 0:
        vnic = wait_for_primary_vnic(profile, instance_id, compartment_id, wait_seconds)
    else:
        vnic = primary_vnic(profile, instance_id, compartment_id)
    if vnic["ipv6_addresses"]:
        return {"ipv6": vnic["ipv6_addresses"][0], "changed": False, "warnings": []}

    net = ensure_subnet_ipv6(profile, vnic["subnet_id"], compartment_id)

    data = run_oci(profile, "network", "ipv6", "create",
                   "--vnic-id", vnic["vnic_id"], timeout=OCI_LAUNCH_TIMEOUT)
    addr = _get(data.get("data", {}) or {}, "ip-address", "ip_address")
    invalidate_read_cache(profile)
    return {"ipv6": addr, "changed": True, "warnings": net.get("warnings", [])}


# ---- 卷性能 ----

# 卷性能以 VPU/GB 表示，取值 0-120，必须是 10 的整数倍。
# Always Free 覆盖到均衡档（10），更高档位会计费。
VPU_MIN, VPU_MAX, VPU_STEP = 0, 120, 10
VPU_FREE_MAX = 10


def vpu_tier(vpus: int) -> str:
    if vpus <= 0:
        return "较低成本"
    if vpus <= 10:
        return "均衡"
    if vpus <= 20:
        return "较高性能"
    return "超高性能"


VPU_RANGE = {
    "min": VPU_MIN,
    "max": VPU_MAX,
    "step": VPU_STEP,
    "free_max": VPU_FREE_MAX,
    "tiers": [
        {"vpus": v, "label": vpu_tier(v), "free": v <= VPU_FREE_MAX}
        for v in range(VPU_MIN, VPU_MAX + 1, VPU_STEP)
    ],
}


def update_volume_performance(profile: str, volume_id: str, kind: str, vpus: int) -> dict:
    if kind == "boot":
        data = run_oci(profile, "bv", "boot-volume", "update",
                       "--boot-volume-id", volume_id, "--vpus-per-gb", str(vpus),
                       timeout=OCI_LAUNCH_TIMEOUT)
    else:
        data = run_oci(profile, "bv", "volume", "update",
                       "--volume-id", volume_id, "--vpus-per-gb", str(vpus),
                       timeout=OCI_LAUNCH_TIMEOUT)
    d = data.get("data", {}) or {}
    invalidate_read_cache(profile)
    return {"id": volume_id, "vpus_per_gb": _get(d, "vpus-per-gb", "vpus_per_gb", default=vpus)}


# ---- 防火墙（VCN 安全列表）----

_ALL_V4 = "0.0.0.0/0"
_ALL_V6 = "::/0"


def _rule_ports(rule: dict) -> str:
    for key in ("tcp-options", "tcpOptions", "udp-options", "udpOptions"):
        opt = rule.get(key)
        if not opt:
            continue
        dst = _get(opt, "destination-port-range", "destinationPortRange")
        if dst:
            lo, hi = _get(dst, "min"), _get(dst, "max")
            return f"{lo}-{hi}" if lo != hi else str(lo)
        return "全部端口"
    return "全部端口"


_PROTO = {"1": "ICMP", "6": "TCP", "17": "UDP", "58": "ICMPv6", "all": "全部协议"}


def firewall_status(profile: str, instance_id: str, compartment_id: str) -> dict:
    """读取实例所在子网的安全列表——这是 OCI 云端防火墙的真实状态。"""
    vnic = primary_vnic(profile, instance_id, compartment_id)
    sub = run_oci(profile, "network", "subnet", "get",
                  "--subnet-id", vnic["subnet_id"]).get("data", {}) or {}
    sl_ids = _get(sub, "security-list-ids", "security_list_ids", default=[]) or []

    def _one(sid):
        return run_oci(profile, "network", "security-list", "get",
                       "--security-list-id", sid).get("data", {}) or {}

    lists, rules = [], []
    for sid, sl, err in gather(_one, sl_ids):
        if err is not None or not sl:
            continue
        lists.append({"id": sid, "name": _get(sl, "display-name", "display_name")})
        for r in _get(sl, "ingress-security-rules", "ingress_security_rules", default=[]) or []:
            proto = str(_get(r, "protocol", default="all"))
            rules.append({
                "security_list_id": sid,
                "protocol": _PROTO.get(proto, proto),
                "protocol_raw": proto,
                "source": _get(r, "source"),
                "ports": _rule_ports(r),
                "stateless": bool(_get(r, "is-stateless", "isStateless", default=False)),
                "description": _get(r, "description"),
            })

    open_v4 = any(r["protocol_raw"] == "all" and r["source"] == _ALL_V4 for r in rules)
    open_v6 = any(r["protocol_raw"] == "all" and r["source"] == _ALL_V6 for r in rules)
    return {
        "instance_id": instance_id,
        "subnet_id": vnic["subnet_id"],
        "subnet_name": _get(sub, "display-name", "display_name"),
        "ipv6_enabled": bool(_get(sub, "ipv6-cidr-block", "ipv6_cidr_block")),
        "security_lists": lists,
        "ingress_rules": rules,
        "all_open_v4": open_v4,
        "all_open_v6": open_v6,
        "verdict": "已放行全部端口" if open_v4 else ("仅放行部分端口" if rules else "未放行任何入站"),
    }


def _set_ingress(profile: str, security_list_id: str, rules: list) -> None:
    run_oci(profile, "network", "security-list", "update",
            "--security-list-id", security_list_id, "--force",
            "--ingress-security-rules", json.dumps(rules),
            timeout=OCI_LAUNCH_TIMEOUT)


def _raw_ingress(profile: str, security_list_id: str) -> list:
    sl = run_oci(profile, "network", "security-list", "get",
                 "--security-list-id", security_list_id).get("data", {}) or {}
    out = []
    for r in _get(sl, "ingress-security-rules", "ingress_security_rules", default=[]) or []:
        rule = {
            "protocol": str(_get(r, "protocol", default="all")),
            "source": _get(r, "source"),
            "sourceType": _get(r, "source-type", "sourceType") or "CIDR_BLOCK",
            "isStateless": bool(_get(r, "is-stateless", "isStateless", default=False)),
        }
        if _get(r, "description"):
            rule["description"] = _get(r, "description")
        for src, dst in (("tcp-options", "tcpOptions"), ("udp-options", "udpOptions"),
                         ("icmp-options", "icmpOptions")):
            opt = _get(r, src, dst)
            if opt:
                rule[dst] = opt
        out.append(rule)
    return out


def _subnet_security(profile: str, subnet_id: str) -> tuple:
    sub = run_oci(profile, "network", "subnet", "get",
                  "--subnet-id", subnet_id).get("data", {}) or {}
    sl_ids = _get(sub, "security-list-ids", "security_list_ids", default=[]) or []
    if not sl_ids:
        raise OCICLIError("该子网未关联安全列表，无法修改防火墙规则")
    return sl_ids[0], bool(_get(sub, "ipv6-cidr-block", "ipv6_cidr_block"))


def open_all_ports_on_subnet(profile: str, subnet_id: str, include_ipv6: bool = True) -> dict:
    """清空子网安全列表的入站规则，只保留全放行。

    先删除 Oracle 预置的默认规则（仅 22 端口 + ICMP）再写入，
    避免与全放行规则重复叠加，规则列表也更容易看懂。
    """
    sid, ipv6_ready = _subnet_security(profile, subnet_id)
    before = len(_raw_ingress(profile, sid))

    rules = [{"protocol": "all", "source": _ALL_V4, "sourceType": "CIDR_BLOCK",
              "isStateless": False, "description": "OCIX 全放行"}]
    if include_ipv6 and ipv6_ready:
        rules.append({"protocol": "all", "source": _ALL_V6, "sourceType": "CIDR_BLOCK",
                      "isStateless": False, "description": "OCIX 全放行 (IPv6)"})

    _set_ingress(profile, sid, rules)
    return {"security_list_id": sid, "subnet_id": subnet_id,
            "removed": before, "added": [r["source"] for r in rules]}


_PROTO_NUM = {"TCP": "6", "UDP": "17", "ICMP": "1", "ICMPV6": "58", "ALL": "all"}


def add_port_rule(profile: str, subnet_id: str, protocol: str, port_from: int,
                  port_to: int, source: str, description: str = "") -> dict:
    """追加一条入站规则。protocol 取 TCP / UDP / ICMP / ALL。"""
    proto = _PROTO_NUM.get((protocol or "").upper())
    if not proto:
        raise OCICLIError(f"不支持的协议: {protocol}")
    sid, _ = _subnet_security(profile, subnet_id)
    rules = _raw_ingress(profile, sid)

    rule = {
        "protocol": proto,
        "source": source,
        "sourceType": "CIDR_BLOCK",
        "isStateless": False,
        "description": description or f"OCIX {protocol.upper()} {port_from}-{port_to}",
    }
    if proto in ("6", "17"):
        key = "tcpOptions" if proto == "6" else "udpOptions"
        rule[key] = {"destinationPortRange": {"min": int(port_from), "max": int(port_to)}}

    if any(r.get("protocol") == rule["protocol"] and r.get("source") == rule["source"]
           and r.get("tcpOptions") == rule.get("tcpOptions")
           and r.get("udpOptions") == rule.get("udpOptions") for r in rules):
        return {"security_list_id": sid, "subnet_id": subnet_id, "added": False}

    rules.append(rule)
    _set_ingress(profile, sid, rules)
    return {"security_list_id": sid, "subnet_id": subnet_id, "added": True}


def delete_port_rule(profile: str, subnet_id: str, index: int) -> dict:
    """按序号删除一条入站规则（序号与 firewall_status 返回的顺序一致）。"""
    sid, _ = _subnet_security(profile, subnet_id)
    rules = _raw_ingress(profile, sid)
    if index < 0 or index >= len(rules):
        raise OCICLIError("规则序号超出范围，请刷新后重试")
    removed = rules.pop(index)
    _set_ingress(profile, sid, rules)
    return {"security_list_id": sid, "subnet_id": subnet_id,
            "removed": removed.get("source"), "remaining": len(rules)}


def clear_ingress_rules(profile: str, subnet_id: str, keep_ssh: bool = True) -> dict:
    """清空全部入站规则；keep_ssh 时保留 22 端口，避免把自己关在门外。"""
    sid, _ = _subnet_security(profile, subnet_id)
    before = len(_raw_ingress(profile, sid))
    rules = []
    if keep_ssh:
        rules.append({"protocol": "6", "source": _ALL_V4, "sourceType": "CIDR_BLOCK",
                      "isStateless": False, "description": "OCIX 保留 SSH",
                      "tcpOptions": {"destinationPortRange": {"min": 22, "max": 22}}})
    _set_ingress(profile, sid, rules)
    # removed 报的是「原本删掉了几条」，不是净差值——
    # 保留 SSH 时用净差值会算出 0 条，读起来像什么都没做
    return {"security_list_id": sid, "subnet_id": subnet_id,
            "removed": before, "kept_ssh": keep_ssh}


def open_all_ports(profile: str, instance_id: str, compartment_id: str,
                   include_ipv6: bool = True) -> dict:
    """一键放行：给实例所在子网加全放行入站规则。"""
    st = firewall_status(profile, instance_id, compartment_id)
    res = open_all_ports_on_subnet(profile, st["subnet_id"], include_ipv6)
    return {**res, "status": firewall_status(profile, instance_id, compartment_id)}


def revoke_all_ports(profile: str, instance_id: str, compartment_id: str) -> dict:
    """撤掉全放行规则，回到只剩具体端口的状态。"""
    st = firewall_status(profile, instance_id, compartment_id)
    if not st["security_lists"]:
        raise OCICLIError("这个子网没有关联安全列表，无法修改防火墙")
    sid = st["security_lists"][0]["id"]
    rules = _raw_ingress(profile, sid)
    kept = [r for r in rules
            if not (r["protocol"] == "all" and r["source"] in (_ALL_V4, _ALL_V6))]
    removed = len(rules) - len(kept)
    if removed:
        if not kept:
            # 全删会把自己关在门外，至少留一条 SSH
            kept = [{"protocol": "6", "source": _ALL_V4, "sourceType": "CIDR_BLOCK",
                     "isStateless": False, "description": "ocix: keep ssh",
                     "tcpOptions": {"destinationPortRange": {"min": 22, "max": 22}}}]
        _set_ingress(profile, sid, kept)
    return {"security_list_id": sid, "removed": removed,
            "status": firewall_status(profile, instance_id, compartment_id)}


def terminate_instance(profile: str, instance_id: str, preserve_boot_volume: bool = False) -> None:
    run_oci(
        profile, "compute", "instance", "terminate",
        "--instance-id", instance_id, "--force",
        "--preserve-boot-volume", "true" if preserve_boot_volume else "false",
    )
    invalidate_read_cache(profile)


# ---- 免费额度 ----

def current_usage(profile: str, compartment_id: str = None, subtree: bool = True) -> dict:
    """拉取当前用量（额度视角）。创建预检和额度页共用这一份。"""
    instances = list_instances(profile, compartment_id, subtree=subtree)
    volumes = list_block_volumes(profile, compartment_id, subtree=subtree)
    boot_volumes = list_boot_volumes(profile, compartment_id, subtree=subtree)
    return freetier.summarize(instances, volumes, boot_volumes)


def free_tier_usage(profile: str, compartment_id: str = None, subtree: bool = True) -> dict:
    """对照 Oracle Always Free 上限，统计当前使用情况。"""
    current = current_usage(profile, compartment_id, subtree=subtree)
    items = freetier.usage_items(current)
    return {
        "always_free_limits": freetier.LIMITS,
        "current": current,
        "items": items,
        "warnings": freetier.usage_warnings(current, items),
    }


def preflight_create(profile: str, plan: dict, compartment_id: str = None) -> dict:
    """创建实例前的额度预检：先查真实用量，再核算「建完之后」。"""
    current = current_usage(profile, compartment_id, subtree=True)
    result = freetier.preflight(current, plan)
    result["current"] = current
    return result


# ---- 监控 ----

_METRIC_QUERIES = [
    ("CpuUtilization", "CPU 利用率", "%"),
    ("MemoryUtilization", "内存利用率", "%"),
]


def _interval_for(hours: int) -> str:
    if hours <= 2:
        return "1m"
    if hours <= 12:
        return "5m"
    if hours <= 48:
        return "1h"
    return "6h"


def get_metrics(profile: str, instance_id: str, compartment_id: str = None, hours: int = 1) -> list:
    """查询实例监控指标。需实例已安装并启用 Oracle Cloud Agent 的监控插件。"""
    hours = max(1, min(int(hours or 1), 168))
    cid = compartment_id or tenancy_of(profile)
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    interval = _interval_for(hours)

    def _one(spec):
        metric, label, unit = spec
        query = f'{metric}[{interval}]{{resourceId = "{instance_id}"}}.mean()'
        data = run_oci(
            profile,
            "monitoring", "metric-data", "summarize-metrics-data",
            "--compartment-id", cid,
            "--namespace", "oci_computeagent",
            "--query-text", query,
            "--start-time", start.strftime(fmt),
            "--end-time", end.strftime(fmt),
        )
        series = data.get("data", []) or []
        points = []
        for s in series:
            for dp in _get(s, "aggregated-datapoints", "aggregatedDatapoints", default=[]) or []:
                points.append({"t": dp.get("timestamp"), "v": _num(dp.get("value"), None)})
        points = [p for p in points if p["v"] is not None]
        points.sort(key=lambda p: p["t"] or "")
        values = [p["v"] for p in points]
        return {
            "metric": metric,
            "name": label,
            "unit": unit,
            "points": points,
            "count": len(points),
            "latest": round(values[-1], 2) if values else None,
            "latest_time": points[-1]["t"] if points else None,
            "min": round(min(values), 2) if values else None,
            "max": round(max(values), 2) if values else None,
            "avg": round(sum(values) / len(values), 2) if values else None,
        }

    out = []
    errors = []
    for spec, result, err in gather(_one, _METRIC_QUERIES):
        if err is not None:
            errors.append(str(err))
            out.append({
                "metric": spec[0], "name": spec[1], "unit": spec[2],
                "points": [], "count": 0, "latest": None, "latest_time": None,
                "min": None, "max": None, "avg": None, "error": str(err),
            })
            continue
        out.append(result)
    # 全部失败说明是权限 / compartment 错了，交给上层报错
    if errors and len(errors) == len(_METRIC_QUERIES):
        raise OCICLIError(errors[0])
    order = {m[0]: idx for idx, m in enumerate(_METRIC_QUERIES)}
    out.sort(key=lambda x: order.get(x["metric"], 99))
    return out
