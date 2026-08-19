import time

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import freetier, jobs, notifier, security
from ..cloudinit import ROOT_PW_TAG, root_password_cloud_config
from ..common import OCIError, account_gate
from ..db import audit
from ..jobs import JobError
from ..oci_helpers import (
    VPU_RANGE,
    add_ipv6_to_instance,
    add_port_rule,
    attach_ips,
    boot_volume_backups,
    change_public_ip,
    clear_ingress_rules,
    console_connections,
    create_backup,
    create_console,
    create_network,
    delete_backup,
    delete_console,
    delete_port_rule,
    delete_volume,
    ensure_subnet_ipv6,
    firewall_status,
    instance_compartment,
    instance_detail,
    is_capacity_available,
    launch_instance,
    list_availability_domains,
    list_images,
    list_subnets,
    list_subscribed_regions,
    open_all_ports,
    open_all_ports_on_subnet,
    preflight_create,
    resize_boot_volume,
    resize_instance_shape,
    resolve_subnet,
    restore_backup,
    revoke_all_ports,
    scan_capacity_radar,
    storage_overview,
    terminate_instance,
    update_volume_performance,
)
from ..schemas import (
    BackupRequest,
    CapacityRadarRequest,
    ClearRulesRequest,
    ConsoleRequest,
    CreateInstanceRequest,
    CreateNetworkRequest,
    DeleteBackupRequest,
    DeleteConsoleRequest,
    DeleteRuleRequest,
    DeleteVolumeRequest,
    EnableIpv6Request,
    FirewallRequest,
    InstanceRefRequest,
    PortRuleRequest,
    PreflightRequest,
    ResizeBootVolumeRequest,
    ResizeShapeRequest,
    RestoreBackupRequest,
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
    shape: str = freetier.AMD_FREE_SHAPE,
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """新建实例表单需要的全部下拉数据，一次取回。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        ads = list_availability_domains(profile)
        images = list_images(profile, compartment_id, shape=shape)
        subnets = list_subnets(profile, compartment_id)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return {
        "availability_domains": ads,
        "images": images,
        "subnets": subnets,
        "shapes": freetier.FREE_SHAPES,
        "limits": freetier.LIMITS,
        "min_boot_gb": freetier.MIN_BOOT_GB,
        "arm_gb_per_ocpu": freetier.ARM_GB_PER_OCPU,
        "vpu_range": VPU_RANGE,
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
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/capacity-radar")
def capacity_radar(
    req: CapacityRadarRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """全区域 / 当前区域容量雷达扫描。仅纯检测，绝不开机。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        return scan_capacity_radar(
            req.profile,
            compartment_id=req.compartment_id,
            shape=req.shape,
            ocpus=req.ocpus,
            memory_in_gbs=req.memory_gb,
            all_regions=req.all_regions,
            regions=req.regions,
        )
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.get("/subscribed-regions")
def subscribed_regions(
    profile: str,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """列出当前账户已订阅的所有区域列表。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        return list_subscribed_regions(profile)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)


def _run_create(job, req: CreateInstanceRequest, params: dict, user: str, ip: str) -> dict:
    """真正干活的那段，跑在后台线程里。

    每一步都往 job 里记一条，前端轮询时能看到「在干什么」——
    这条链路正常也要一分多钟，干等一个转圈用户会以为卡死了。
    """
    job.step("核算免费额度")
    try:
        check = preflight_create(req.profile, params, req.compartment_id)
    except OCIError as e:
        raise JobError({"message": f"额度预检失败，未创建任何资源: {e.message}"}) from None

    if not check["allow"]:
        audit(user, "create-instance", profile=req.profile, target=req.display_name,
              detail="额度预检拒绝: " + "; ".join(check["blockers"]), result="fail", ip=ip)
        raise JobError({
            "message": "超出 Always Free 额度，已阻止创建",
            "blockers": check["blockers"],
            "checks": check["checks"],
        })

    # 子网：用户不用选，这里自动定位；账户第一次开机时顺手把网络建出来
    job.step("准备网络（首次开机会自动建 VCN、网关和子网）")
    network_created = False
    try:
        subnet = resolve_subnet(req.profile, req.compartment_id)
        params["subnet_id"] = subnet["id"]
        network_created = subnet.get("created", False)
    except OCIError as e:
        raise JobError({"message": f"没能准备好网络，未创建任何实例: {e.message}"}) from None

    # 勾了 IPv6 就在这里把子网的 IPv6 一并开通，用户不用再点一次
    ipv6_warnings = []
    if req.assign_ipv6:
        job.step("开通子网 IPv6")
        try:
            net = ensure_subnet_ipv6(req.profile, params["subnet_id"], req.compartment_id)
            ipv6_warnings = net.get("warnings", [])
        except OCIError as e:
            audit(user, "create-instance", profile=req.profile, target=req.display_name,
                  detail=f"IPv6 开通失败: {e.message}", result="fail", ip=ip)
            raise JobError({
                "message": f"子网 IPv6 开通失败，未创建实例: {e.message}"}) from None

    # 选了 root + 密码：下发 cloud-init 打开密码登录，并把密码写进实例标签。
    # 标签是给人看的（控制台 / 面板 / 换台电脑都能看到），
    # 代价写在 cloudinit 模块开头——这条是拿安全换方便。
    if req.root_password:
        job.step("准备 root 密码登录")
        params["user_data"] = root_password_cloud_config(req.root_password)
        tags = dict(params.get("freeform_tags") or {})
        tags[ROOT_PW_TAG] = req.root_password
        params["freeform_tags"] = tags

    # ── 智能容量探测前置 (先探测 OCI 放货状态，避免频繁 429 报错与封号) ──
    if req.capacity_probe:
        job.step("探测 OCI 实时容量状态（智能节流防封号）")
        ocpus_val = float(check["plan"].get("ocpus", 1))
        mem_val = float(check["plan"].get("memory_gb", 6))
        avail, best_fd = is_capacity_available(
            req.profile, req.compartment_id, req.availability_domain,
            shape=req.shape, ocpus=ocpus_val, memory_in_gbs=mem_val
        )
        if avail and best_fd and not params.get("fault_domain"):
            params["fault_domain"] = best_fd

        if not avail:
            if req.auto_retry_until_available:
                max_retries = max(1, min(int(req.max_retry_minutes or 60) * 3, 360))
                retry_count = 0
                job.step(
                    f"当前可用域暂无库存，已进入智能低频容量探测抢机（最大 {req.max_retry_minutes} 分钟）"
                )
                while not avail and retry_count < max_retries:
                    time.sleep(20)
                    retry_count += 1
                    job.step(f"智能容量雷达探测中（第 {retry_count} 次，已等待 {retry_count * 20}s）...")
                    avail, best_fd = is_capacity_available(
                        req.profile, req.compartment_id, req.availability_domain,
                        shape=req.shape, ocpus=ocpus_val, memory_in_gbs=mem_val
                    )
                    if avail:
                        job.step(f"检测到 OCI 已放货！立即锁定 {best_fd or '推荐故障域'} 下单创建...")
                        if best_fd and not params.get("fault_domain"):
                            params["fault_domain"] = best_fd
                        break
                if not avail:
                    raise JobError({
                        "message": (
                            f"在指定的 {req.max_retry_minutes} 分钟内未探测到放货。"
                            "已安全退出，未产生无效请求或风控风险。"
                        )
                    })
            else:
                audit(user, "create-instance", profile=req.profile, target=req.display_name,
                      detail=f"{req.shape} -> 容量探测拦截（暂无库存）", result="fail", ip=ip)
                raise JobError({
                    "message": (
                        "通过 OCI 容量探测接口检测到当前可用域暂无库存（Out of host capacity）。"
                        "已智能拦截，未向 OCI 提交无效订单（彻底避免 429 报错与风控）。"
                        "你可以开启「放货自动抢机」或更换可用域重试。"
                    )
                })

    job.step(f"向 OCI 下单：{req.shape}")
    try:
        data = launch_instance(req.profile, params)
    except OCIError as e:
        msg = e.message
        if "capacity" in msg.lower() or "OutOfCapacity" in (e.code or ""):
            msg = _CAPACITY_HINT
        audit(user, "create-instance", profile=req.profile, target=req.display_name,
              detail=f"{req.shape} -> {msg}", result="fail", ip=ip)
        raise JobError({"message": msg}) from None

    instance_id = data.get("id")
    audit(user, "create-instance", profile=req.profile,
          target=instance_id or req.display_name,
          detail=f"{req.shape} {check['plan']['ocpus']}C/{check['plan']['memory_gb']}G "
                 f"boot={check['plan']['boot_gb']}G",
          result="ok", ip=ip)
    job.step("实例已下单，正在初始化")

    # ---- 收尾步骤：实例已经建出来了，这里失败只警告，不能把整个任务判成失败 ----
    warnings = list(ipv6_warnings)

    if req.open_all_ports:
        job.step("放行子网入站端口")
        try:
            res = open_all_ports_on_subnet(req.profile, params["subnet_id"],
                                           include_ipv6=req.assign_ipv6)
            audit(user, "firewall-allow-all", profile=req.profile,
                  target=params["subnet_id"],
                  detail="随实例创建自动放行 " + (", ".join(res["added"]) or "（已是全放行）"),
                  result="ok", ip=ip)
        except OCIError as e:
            warnings.append(f"实例已创建，但端口没能自动放行：{e.message}（可到「防火墙」页手动放行）")

    ipv6_addr = None
    if req.assign_ipv6 and instance_id:
        # SDK 后端在 launch 时就带了 assign_ipv6_ip，地址随实例一起分配，直接读返回值；
        # CLI 后端没有对应参数，只能等网卡挂好后再补一个。
        assigned = data.get("ipv6_addresses") or data.get("ipv6-addresses") or []
        ipv6_addr = assigned[0] if assigned else None

        if ipv6_addr is None:
            job.step("等网卡挂好，再分配 IPv6")
            try:
                res = add_ipv6_to_instance(req.profile, instance_id, req.compartment_id,
                                           wait_seconds=90)
                ipv6_addr = res.get("ipv6")
                warnings.extend(res.get("warnings", []))
            except OCIError as e:
                warnings.append(
                    f"实例已创建，但 IPv6 还没挂上：{e.message}。"
                    f"等实例跑起来后到实例列表点击「+ 添加」即可。")

        if ipv6_addr:
            audit(user, "add-ipv6", profile=req.profile, target=instance_id,
                  detail=ipv6_addr, result="ok", ip=ip)

    # 尝试获取新实例的公网 IPv4（TG 通知会在后台持续轮询直到获取完毕）
    public_ip = data.get("_public_ip")
    if instance_id and not public_ip:
        try:
            target = {"id": instance_id, "compartment_id": req.compartment_id}
            with_ip = attach_ips(req.profile, [target])
            if with_ip and with_ip[0].get("_public_ip"):
                public_ip = with_ip[0].get("_public_ip")
                data["_public_ip"] = public_ip
        except Exception:
            pass

    job.step("完成")
    disp_name = data.get("display-name") or data.get("display_name") or req.display_name
    elapsed_time = round(time.time() - job.created, 1)
    notifier.notify_instance_created(
        profile=req.profile,
        display_name=disp_name,
        shape=req.shape,
        ocpus=check["plan"]["ocpus"],
        memory_gb=check["plan"]["memory_gb"],
        boot_gb=check["plan"]["boot_gb"],
        public_ip=public_ip,
        ipv6=ipv6_addr,
        region=data.get("region"),
        root_password=req.root_password or None,
        vpus_per_gb=getattr(req, "vpu", None),
        instance_id=instance_id,
        compartment_id=req.compartment_id,
        success=True,
        elapsed=elapsed_time,
    )
    return {
        "ok": True,
        "instance_id": instance_id,
        "display_name": disp_name,
        "state": data.get("lifecycle-state") or data.get("lifecycle_state"),
        "preflight": check,
        "network_created": network_created,
        "public_ip": public_ip,
        "ipv6": ipv6_addr,
        "ports_opened": req.open_all_ports,
        "root_password": req.root_password or None,
        "warnings": warnings,
    }


@router.post("/instances", status_code=202)
def create_instance(
    req: CreateInstanceRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """下单建实例。**立刻返回任务号，活儿在后台干。**

    这条链路正常就要一分多钟（建网络三次等待、网卡挂载再等 90 秒），
    而面板挂在 Cloudflare 后面时对方 100 秒不回就给访客一个 524
    「源站超时」页——请求其实还在跑、实例照样建出来，用户却看到报错，
    然后重试，于是又建一台。所以这里不能同步等。

    额度预检也挪进任务里跑：它本身要打好几次 OCI，同步做同样会拖时间。
    闸门一点没松，只是判定结果通过轮询回给前端。
    """
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    params = req.model_dump()

    # 同一个账户 + 同一个实例名，正在跑就不再起第二个。
    # 用户手快点两下、或者被 524 吓到去重试，都不该变成两台机器。
    key = ("create-instance", req.profile, req.display_name)
    def _do_work(j):
        try:
            return _run_create(j, req, params, user, ip)
        except Exception as e:
            err_msg = getattr(e, "detail", None) or getattr(e, "message", None) or str(e)
            if isinstance(err_msg, dict):
                err_msg = err_msg.get("message") or str(err_msg)
            notifier.notify_instance_created(
                profile=req.profile,
                display_name=req.display_name,
                shape=req.shape,
                success=False,
                error_msg=str(err_msg),
            )
            raise

    job, fresh = jobs.submit(
        "create-instance", req.profile, key, _do_work)
    return {"job_id": job.id, "state": job.state, "started": fresh}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: str = Depends(security.get_current_user)):
    """查任务进度。前端轮询这个。"""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job.snapshot()


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
    except OCIError as e:
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
    except OCIError as e:
        audit(user, "terminate-instance", profile=req.profile, target=req.instance_id,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "terminate-instance", profile=req.profile, target=req.instance_id,
          detail="保留引导卷" if req.preserve_boot_volume else "同时删除引导卷",
          result="ok", ip=ip)
    notifier.notify_instance_terminated(
        profile=req.profile,
        instance_id=req.instance_id,
        display_name=req.display_name,
        preserve_boot_volume=req.preserve_boot_volume,
        user=user,
        ip=ip,
    )
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
    except OCIError as e:
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
    except OCIError as e:
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
    except OCIError as e:
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
    except OCIError as e:
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
    except OCIError as e:
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
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "firewall-revoke-all", profile=req.profile, target=req.instance_id,
          detail=f"移除 {res['removed']} 条全放行规则", result="ok", ip=ip)
    return {"ok": True, **res}


@router.post("/firewall/rules")
def add_firewall_rule(
    req: PortRuleRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """新增一条入站规则（指定协议与端口范围）。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    cid = _compartment_of(req)
    try:
        st = firewall_status(req.profile, req.instance_id, cid)
        res = add_port_rule(req.profile, st["subnet_id"], req.protocol,
                            req.port_from, req.port_to, req.source, req.description)
        status = firewall_status(req.profile, req.instance_id, cid)
    except OCIError as e:
        audit(user, "firewall-add-rule", profile=req.profile, target=req.instance_id,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "firewall-add-rule", profile=req.profile, target=req.instance_id,
          detail=f"{req.protocol} {req.port_from}-{req.port_to} from {req.source}",
          result="ok", ip=ip)
    return {"ok": True, **res, "status": status}


@router.post("/firewall/rules/delete")
def delete_firewall_rule(
    req: DeleteRuleRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """按序号删除一条入站规则。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    cid = _compartment_of(req)
    try:
        st = firewall_status(req.profile, req.instance_id, cid)
        res = delete_port_rule(req.profile, st["subnet_id"], req.index)
        status = firewall_status(req.profile, req.instance_id, cid)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "firewall-delete-rule", profile=req.profile, target=req.instance_id,
          detail=str(res.get("removed")), result="ok", ip=ip)
    return {"ok": True, **res, "status": status}


@router.post("/firewall/clear")
def clear_firewall_rules(
    req: ClearRulesRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """清空全部入站规则（默认保留 22 端口，避免失去 SSH 连接）。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    cid = _compartment_of(req)
    try:
        st = firewall_status(req.profile, req.instance_id, cid)
        res = clear_ingress_rules(req.profile, st["subnet_id"], req.keep_ssh)
        status = firewall_status(req.profile, req.instance_id, cid)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "firewall-clear", profile=req.profile, target=req.instance_id,
          detail=f"移除 {res['removed']} 条，保留SSH={req.keep_ssh}", result="ok", ip=ip)
    return {"ok": True, **res, "status": status}


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
    except OCIError as e:
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
    except OCIError as e:
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
    except OCIError as e:
        audit(user, "create-network", profile=req.profile, target=req.name,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "create-network", profile=req.profile, target=net.get("vcn_id") or req.name,
          detail=f"subnet={net.get('subnet_id')}", result="ok", ip=ip)
    return {"ok": True, **net}


# ---- 实例详情 / 改规格 ----

@router.get("/instances/detail")
def get_instance_detail(
    profile: str,
    instance_id: str,
    compartment_id: str = None,
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """一台实例的完整信息。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        with account_gate(profile):
            return instance_detail(profile, instance_id, compartment_id)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/instances/resize")
def resize_shape(
    req: ResizeShapeRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """改 Flex 机型的 OCPU / 内存。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        with account_gate(req.profile):
            res = resize_instance_shape(req.profile, req.instance_id, req.ocpus,
                                        req.memory_gb, req.compartment_id)
    except OCIError as e:
        audit(user, "resize-instance", profile=req.profile, target=req.instance_id,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "resize-instance", profile=req.profile, target=req.instance_id,
          detail=f"改为 {req.ocpus:g} OCPU / {req.memory_gb:g}GB", result="ok", ip=ip)
    return res


# ---- 串口控制台 ----

@router.get("/console")
def get_console(
    profile: str,
    instance_id: str,
    compartment_id: str = None,
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """只查串口控制台连接。

    详情页刷新整块要 5 次 OCI 调用；只想看连接状态时用这个，1 次就够。
    """
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        with account_gate(profile):
            # compartment 兜底成实例自己的，跟创建那条路径保持一致
            cid = compartment_id or instance_compartment(profile, instance_id)
            return {"connections": console_connections(profile, cid, instance_id)}
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/console")
def open_console(
    req: ConsoleRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """建串口控制台连接（SSH 进不去时的救命通道）。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        with account_gate(req.profile):
            res = create_console(req.profile, req.instance_id, req.public_key,
                                 req.compartment_id)
    except OCIError as e:
        audit(user, "console-connection", profile=req.profile, target=req.instance_id,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "console-connection", profile=req.profile, target=req.instance_id,
          detail="创建串口控制台连接" if res.get("created") else "复用已有连接",
          result="ok", ip=ip)
    return res


@router.post("/console/delete")
def close_console(
    req: DeleteConsoleRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        with account_gate(req.profile):
            res = delete_console(req.profile, req.connection_id)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "console-connection", profile=req.profile, target=req.connection_id,
          detail="删除串口控制台连接", result="ok", ip=ip)
    return res


# ---- 引导卷备份 / 扩容 ----

@router.get("/backups")
def list_backups(
    profile: str,
    compartment_id: str = None,
    boot_volume_id: str = None,
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        with account_gate(profile):
            return boot_volume_backups(profile, compartment_id, boot_volume_id)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/backups")
def make_backup(
    req: BackupRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        with account_gate(req.profile):
            res = create_backup(req.profile, req.boot_volume_id,
                                req.display_name, req.backup_type)
    except OCIError as e:
        audit(user, "boot-volume-backup", profile=req.profile, target=req.boot_volume_id,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "boot-volume-backup", profile=req.profile, target=req.boot_volume_id,
          detail=f"创建{req.backup_type}备份 {res['display_name']}", result="ok", ip=ip)
    return res


@router.post("/backups/delete")
def remove_backup(
    req: DeleteBackupRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        with account_gate(req.profile):
            res = delete_backup(req.profile, req.backup_id)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "boot-volume-backup", profile=req.profile, target=req.backup_id,
          detail="删除备份", result="ok", ip=ip)
    return res


@router.post("/backups/restore")
def restore(
    req: RestoreBackupRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """从备份还原出一个新引导卷（不是原地恢复）。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        with account_gate(req.profile):
            res = restore_backup(req.profile, req.backup_id, req.availability_domain,
                                 req.display_name, req.compartment_id)
    except OCIError as e:
        audit(user, "restore-backup", profile=req.profile, target=req.backup_id,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "restore-backup", profile=req.profile, target=req.backup_id,
          detail=f"还原为引导卷 {res['display_name']}（{res['size_gb']}GB）",
          result="ok", ip=ip)
    return res


@router.post("/storage/resize")
def resize_boot(
    req: ResizeBootVolumeRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """引导卷扩容。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        with account_gate(req.profile):
            res = resize_boot_volume(req.profile, req.boot_volume_id,
                                     req.size_gb, req.compartment_id)
    except OCIError as e:
        audit(user, "resize-boot-volume", profile=req.profile, target=req.boot_volume_id,
              detail=e.message, result="fail", ip=ip)
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "resize-boot-volume", profile=req.profile, target=req.boot_volume_id,
          detail=f"扩容到 {req.size_gb}GB", result="ok", ip=ip)
    return res
