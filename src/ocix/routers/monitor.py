from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import security
from ..common import OCIError, account_gate, gather, list_profiles_from_config
from ..oci_helpers import (
    egress_usage,
    free_tier_usage,
    get_metrics,
    list_invoices,
    month_cost,
)

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("/usage")
def usage(
    profile: str,
    compartment_id: str = None,
    subtree: bool = Query(False),
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        data = free_tier_usage(profile, compartment_id, subtree=subtree)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return data


@router.get("/usage/all")
def usage_all(
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """所有账户的免费额度对照，用于总览页快速发现超额账户。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    profiles = list_profiles_from_config()

    def _one(name):
        return free_tier_usage(name)

    accounts = []
    for name, data, err in gather(_one, profiles):
        accounts.append({
            "profile": name,
            "error": getattr(err, "message", str(err)) if err is not None else None,
            "usage": data,
        })
    accounts.sort(key=lambda a: a["profile"])
    return {"accounts": accounts}


@router.get("/metrics")
def metrics(
    profile: str,
    instance_id: str,
    compartment_id: str = None,
    hours: int = Query(1, ge=1, le=168),
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        data = get_metrics(profile, instance_id, compartment_id, hours)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return {"metrics": data, "hours": hours, "instance_id": instance_id}


# ---- 流量与账单 ----

@router.get("/egress")
def egress(
    profile: str,
    compartment_id: str = None,
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """当月出网流量（上限估算，不是账单）。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        with account_gate(profile):
            return egress_usage(profile, compartment_id)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.get("/invoices")
def invoices(
    profile: str,
    limit: int = Query(24, ge=1, le=100),
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """账单列表：待支付 / 已支付 / 已逾期。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        with account_gate(profile):
            return list_invoices(profile, limit)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.get("/cost")
def cost(
    profile: str,
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """当月消费：按天与按服务拆开。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    try:
        with account_gate(profile):
            return month_cost(profile)
    except OCIError as e:
        raise HTTPException(status_code=400, detail=e.message)
