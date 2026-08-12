from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import security
from ..common import OCIError, gather, list_profiles_from_config
from ..oci_helpers import free_tier_usage, get_metrics

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
