from fastapi import APIRouter, Depends, HTTPException, Request

from .. import freetier, security
from ..db import audit
from ..oci_cli import OCICLIError
from ..oci_helpers import (
    VPU_OPTIONS,
    add_ipv6_to_instance,
    change_public_ip,
    create_network,
    delete_volume,
    ensure_subnet_ipv6,
    firewall_status,
    launch_instance,
    list_availability_domains,
    list_images,
    list_subnets,
    open_all_ports,
    open_all_ports_on_subnet,
    preflight_create,
    resolve_subnet,
    revoke_all_ports,
    storage_overview,
    terminate_instance,
    update_volume_performance,
)
from ..schemas import (
    CreateInstanceRequest,
    CreateNetworkRequest,
    DeleteVolumeRequest,
    EnableIpv6Request,
    FirewallRequest,
    InstanceRefRequest,
    PreflightRequest,
    TerminateInstanceRequest,
    VolumePerformanceRequest,
)

router = APIRouter(prefix="/api/provision", tags=["provision"])

_CAPACITY_HINT = (
    "当前区域该规格没有可用容量（Out of host capacity）。"
    "这是 Oracle 侧的库存问题，不是配置错误——可以换一个可用域，或过一段时间自己再试一次。"
    "本面板不会自动循环重试。"
)


@router.get("/options")
def options(
    profile: str,
    compartment_id: str = None,
    shape: str = freetier.ARM_FREE_SHAPE,
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """新建实例表单需要的全部下拉数据，一次取回。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        ads = list_availability_domains(profile)
        images = list_images(profile, compartment_id, shape=shape)
        subnets = list_subnets(profile, compartment_id)
    except OCICLIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return {
        "availability_domains": ads,
        "images": images,
        "subnets": subnets,
        "shapes": freetier.FREE_SHAPES,
        "limits": freetier.LIMITS,
        "min_boot_gb": freetier.MIN_BOOT_GB,
        "arm_gb_per_ocpu": freetier.ARM_GB_PER_OCPU,
        "vpu_options": VPU_OPTIONS,
        "needs_network": not subnets,
    }


@router.post("/preflight")
def preflight(
    req: PreflightRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """开机前额度核算：报告「这台建出来之后」是否仍在免费额度内。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        return preflight_create(req.profile, req.model_dump(), req.compartment_id)
    except OCICLIError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/instances")
def create_instance(
    req: CreateInstanceRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """创建实例。额度预检不通过一律拒绝——这道闸门在服务端，绕不过去。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    params = req.model_dump()

    try:
        check = preflight_create(req.profile, params, req.compartment_id)
    except OCICLIError as e:
        raise HTTPException(status_code=400, detail=f"额度预检失败，未创建任何资源: {e.message}")

    if not check["allow"]:
        audit(user, "create-instance", profile=req.profile, target=req.display_name,
              detail="额度预检拒绝: " + "; ".join(check["blockers"]), result="fail", ip=ip)
        raise HTTPException(status_code=400, detail={
            "message": "超出 Always Free 额度，已阻止创建",
            "blockers": check["blockers"],
            "checks": check["checks"],
        })

    # 子网：用户不用选，这里自动定位；账户第一次开机时顺手把网络建出来
    network_created = False
    try:
        subnet = resolve_subnet(req.profile, req.compartment_id)
        params["subnet_id"] = subnet["id"]
        network_created = subnet.get("created", False)
    except OCICLIError as e:
        raise HTTPException(status_code=400, detail=f"没能准备好网络，未创建任何实例: {e.message}")

    # 勾了 IPv6 就在这里把子网的 IPv6 一并开通，用户不用再点一次
    ipv6_warnings = []
    if req.assign_ipv6:
        try:
            net = ensure_subnet_ipv6(req.profile, params["subnet_id"], req.compartment_id)
            ipv6_warnings = net.get("warnings", [])
        except OCICLIError as e:
            audit(user, "create-instance", profile=req.profile, target=req.display_name,
                  detail=f"IPv6 开通失败: {e.message}", result="fail", ip=ip)
            raise HTTPException(
                status_code=400,
                detail=f"子网 IPv6 开通失败，未创建实例: {e.message}")

    try:
        data = launch_instance(req.profile, params)
    except OCICLIError as e:
        msg = e.message
        if "capacity" in msg.lower() or "OutOfCapacity" in (e.code or ""):
            msg = _CAPACITY_HINT
        audit(user, "create-instance", profile=req.profile, target=req.display_name,
              detail=f"{req.shape} -> {msg}", result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=msg)

    instance_id = data.get("id")
    audit(user, "create-instance", profile=req.profile,
          target=instance_id or req.display_name,
          detail=f"{req.shape} {check['plan']['ocpus']}C/{check['plan']['memory_gb']}G "
                 f"boot={check['plan']['boot_gb']}G",
          result="ok", ip=ip)

    # ---- 收尾步骤：实例已经建出来了，这里失败只警告，不能把整个请求判成失败 ----
    warnings = list(ipv6_warnings)

    if req.open_all_ports:
        try:
            res = open_all_ports_on_subnet(req.profile, params["subnet_id"],
                                           include_ipv6=req.assign_ipv6)
            audit(user, "firewall-allow-all", profile=req.profile,
                  target=params["subnet_id"],
                  detail="随实例创建自动放行 " + (", ".join(res["added"]) or "（已是全放行）"),
                  result="ok", ip=ip)
        except OCICLIError as e:
            warnings.append(f"实例已创建，但端口没能自动放行：{e.message}（可到「防火墙」页手动放行）")

    ipv6_addr = None
    if req.assign_ipv6 and instance_id:
        try:
            # launch 立刻返回 PROVISIONING，网卡还没挂好，得等一会儿再挂 IPv6
            res = add_ipv6_to_instance(req.profile, instance_id, req.compartment_id,
                                       wait_seconds=90)
            ipv6_addr = res.get("ipv6")
            warnings.extend(res.get("warnings", []))
            audit(user, "add-ipv6", profile=req.profile, target=instance_id,
                  detail=ipv6_addr or "", result="ok", ip=ip)
        except OCICLIError as e:
            warnings.append(
                f"实例已创建，但 IPv6 还没挂上：{e.message}。"
                f"等实例跑起来后到实例列表点「+ 添加」即可。")

    return {
        "ok": True,
        "instance_id": instance_id,
        "display_name": data.get("display-name") or data.get("display_name"),
        "state": data.get("lifecycle-state") or data.get("lifecycle_state"),
        "preflight": check,
        "network_created": network_created,
        "ipv6": ipv6_addr,
        "ports_opened": req.open_all_ports,
        "warnings": warnings,
    }


@router.post("/instances/add-ipv6")
def add_ipv6(
    req: InstanceRefRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """给已有实例补一个 IPv6 地址；子网没开通 IPv6 会一并开通。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        res = add_ipv6_to_instance(req.profile, req.instance_id, _compartment_of(req))
    except OCICLIError as e:
        audit(user, "add-ipv6", profile=req.profile, target=req.instance_id,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "add-ipv6", profile=req.profile, target=req.instance_id,
          detail=res.get("ipv6") or "", result="ok", ip=ip)
    return {"ok": True, **res}


@router.post("/instances/terminate")
def terminate(
    req: TerminateInstanceRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        terminate_instance(req.profile, req.instance_id, req.preserve_boot_volume)
    except OCICLIError as e:
        audit(user, "terminate-instance", profile=req.profile, target=req.instance_id,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "terminate-instance", profile=req.profile, target=req.instance_id,
          detail="保留引导卷" if req.preserve_boot_volume else "同时删除引导卷",
          result="ok", ip=ip)
    return {"ok": True, "preserved_boot_volume": req.preserve_boot_volume}


@router.get("/storage")
def storage(
    profile: str,
    compartment_id: str = None,
    subtree: bool = True,
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """卷清单 + 孤儿卷标记。孤儿卷是 200GB 额度被吃掉的主要原因。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        return storage_overview(profile, compartment_id, subtree=subtree)
    except OCICLIError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/storage/delete")
def remove_volume(
    req: DeleteVolumeRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        delete_volume(req.profile, req.volume_id, req.kind)
    except OCICLIError as e:
        audit(user, "delete-volume", profile=req.profile, target=req.volume_id,
              detail=f"{req.kind} -> {e.message}", result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "delete-volume", profile=req.profile, target=req.volume_id,
          detail=req.kind, result="ok", ip=ip)
    return {"ok": True}


def _compartment_of(req) -> str:
    if req.compartment_id:
        return req.compartment_id
    from ..oci_helpers import tenancy_of
    return tenancy_of(req.profile)


@router.post("/instances/change-ip")
def change_ip(
    req: InstanceRefRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """更换公网 IPv4（删掉临时 IP 再申请新的，中间会短暂无公网地址）。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        res = change_public_ip(req.profile, req.instance_id, _compartment_of(req))
    except OCICLIError as e:
        audit(user, "change-public-ip", profile=req.profile, target=req.instance_id,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "change-public-ip", profile=req.profile, target=req.instance_id,
          detail=f"{res.get('old_ip')} -> {res.get('new_ip')}", result="ok", ip=ip)
    return {"ok": True, **res}


@router.get("/firewall")
def get_firewall(
    profile: str,
    instance_id: str,
    compartment_id: str = None,
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """读取实例所在子网的云端防火墙（安全列表）状态。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        from ..oci_helpers import tenancy_of
        return firewall_status(profile, instance_id, compartment_id or tenancy_of(profile))
    except OCICLIError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/firewall/allow-all")
def firewall_allow_all(
    req: FirewallRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        res = open_all_ports(req.profile, req.instance_id, _compartment_of(req), req.include_ipv6)
    except OCICLIError as e:
        audit(user, "firewall-allow-all", profile=req.profile, target=req.instance_id,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "firewall-allow-all", profile=req.profile, target=req.instance_id,
          detail="放行 " + (", ".join(res["added"]) or "（已是全放行）"), result="ok", ip=ip)
    return {"ok": True, **res}


@router.post("/firewall/revoke-all")
def firewall_revoke_all(
    req: InstanceRefRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        res = revoke_all_ports(req.profile, req.instance_id, _compartment_of(req))
    except OCICLIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "firewall-revoke-all", profile=req.profile, target=req.instance_id,
          detail=f"移除 {res['removed']} 条全放行规则", result="ok", ip=ip)
    return {"ok": True, **res}


@router.post("/ipv6")
def enable_ipv6(
    req: EnableIpv6Request,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """给子网开通 IPv6（VCN 取 /56、子网切 /64、补 ::/0 路由）。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        res = ensure_subnet_ipv6(req.profile, req.subnet_id, req.compartment_id)
    except OCICLIError as e:
        audit(user, "enable-ipv6", profile=req.profile, target=req.subnet_id,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "enable-ipv6", profile=req.profile, target=req.subnet_id,
          detail=res.get("ipv6_cidr_block") or "", result="ok", ip=ip)
    return {"ok": True, **res}


@router.post("/storage/performance")
def set_performance(
    req: VolumePerformanceRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """调整卷性能档位（VPU/GB）。超过均衡档会脱离免费额度，前端已标注。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        res = update_volume_performance(req.profile, req.volume_id, req.kind, req.vpus)
    except OCICLIError as e:
        audit(user, "volume-performance", profile=req.profile, target=req.volume_id,
              detail=f"vpus={req.vpus} -> {e.message}", result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "volume-performance", profile=req.profile, target=req.volume_id,
          detail=f"vpus={req.vpus}", result="ok", ip=ip)
    return {"ok": True, **res}


@router.post("/network")
def make_network(
    req: CreateNetworkRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """全新租户没有 VCN 就开不了机，这里一键补齐 VCN + 网关 + 公共子网。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        net = create_network(req.profile, req.compartment_id, req.name)
    except OCICLIError as e:
        audit(user, "create-network", profile=req.profile, target=req.name,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "create-network", profile=req.profile, target=net.get("vcn_id") or req.name,
          detail=f"subnet={net.get('subnet_id')}", result="ok", ip=ip)
    return {"ok": True, **net}
