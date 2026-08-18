from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import notifier, security
from ..cloudinit import ROOT_PW_TAG
from ..common import OCIError, gather
from ..db import audit
from ..oci_helpers import (
    attach_ips,
    instance_action,
    list_compartments,
    list_instances,
)
from ..schemas import BatchActionRequest, InstanceActionRequest

router = APIRouter(prefix="/api/instances", tags=["instances"])


def _simplify(it: dict) -> dict:
    sc = it.get("shape-config") or it.get("shape_config") or {}
    return {
        "id": it.get("id"),
        "display_name": it.get("display-name") or it.get("display_name"),
        "shape": it.get("shape"),
        "state": it.get("lifecycle-state") or it.get("lifecycle_state"),
        "region": it.get("region"),
        "availability_domain": it.get("availability-domain") or it.get("availability_domain"),
        "fault_domain": it.get("fault-domain") or it.get("fault_domain"),
        "time_created": it.get("time-created") or it.get("time_created"),
        "compartment_id": it.get("compartment-id") or it.get("compartment_id"),
        "ocpus": sc.get("ocpus"),
        "memory_gb": sc.get("memory-in-gbs") or sc.get("memory_in_gbs"),
        "public_ip": it.get("_public_ip"),
        "private_ip": it.get("_private_ip"),
        "ipv6": it.get("_ipv6"),
        # 建实例时如果选了「root + 密码」，密码存在这个标签里。
        # 带出来是为了换台电脑、换个浏览器也能在实例旁边直接看到。
        "root_password": (it.get("freeform-tags") or it.get("freeform_tags")
                          or {}).get(ROOT_PW_TAG),
    }


def _simplify_list(items: list) -> list:
    return [_simplify(it) for it in items]


@router.get("/compartments")
def get_compartments(
    profile: str,
    refresh: bool = False,
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """供前端做 compartment 下拉，避免手工粘 OCID。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        items = list_compartments(profile, use_cache=not refresh)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return {"compartments": items}


@router.get("")
def get_instances(
    profile: str,
    compartment_id: str = None,
    subtree: bool = Query(False, description="是否遍历子 compartment（开启会成倍增加查询次数）"),
    with_ip: bool = Query(True, description="是否附带公网/内网 IP（每台实例会多一次请求）"),
    include_terminated: bool = False,
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        items = list_instances(profile, compartment_id, subtree=subtree,
                               include_terminated=include_terminated)
        if with_ip:
            items = attach_ips(profile, items)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return {"instances": _simplify_list(items)}


@router.post("/action")
def do_action(
    req: InstanceActionRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    try:
        data = instance_action(req.profile, req.instance_id, req.action)
    except OCIError as e:
        audit(user, "instance-action", profile=req.profile, target=req.instance_id,
              detail=f"{req.action} -> {e.message}", result="fail", ip=ip)
        notifier.notify_instance_action(
            profile=req.profile,
            instance_id=req.instance_id,
            action=req.action,
            success=False,
            error_msg=e.message,
            user=user,
            ip=ip,
        )
        raise HTTPException(status_code=400, detail=e.message)
    audit(user, "instance-action", profile=req.profile, target=req.instance_id,
          detail=req.action, result="ok", ip=ip)
    notifier.notify_instance_action(
        profile=req.profile,
        instance_id=req.instance_id,
        action=req.action,
        success=True,
        user=user,
        ip=ip,
    )
    return {
        "ok": True,
        "state": data.get("lifecycle-state") or data.get("lifecycle_state"),
        "action": req.action,
    }


@router.post("/batch-action")
def do_batch_action(
    req: BatchActionRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """对多台实例（可跨账户）执行同一操作。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    targets = [t for t in req.targets if t.get("profile") and t.get("instance_id")]
    if not targets:
        raise HTTPException(status_code=400, detail="targets 为空")
    if len(targets) > 50:
        raise HTTPException(status_code=400, detail="单次批量操作不得超过 50 台")

    def _one(t):
        return instance_action(t["profile"], t["instance_id"], req.action)

    results = []
    for t, _data, err in gather(_one, targets):
        ok = err is None
        msg = "" if ok else getattr(err, "message", str(err))
        audit(user, "instance-action", profile=t["profile"], target=t["instance_id"],
              detail=f"{req.action}(batch)" + ("" if ok else f" -> {msg}"),
              result="ok" if ok else "fail", ip=ip)
        results.append({
            "profile": t["profile"],
            "instance_id": t["instance_id"],
            "display_name": t.get("display_name"),
            "ok": ok,
            "error": msg,
        })
    return {
        "ok": all(r["ok"] for r in results),
        "action": req.action,
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }
