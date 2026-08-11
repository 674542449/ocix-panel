from fastapi import APIRouter, Depends, Query, Request

from .. import security
from ..db import audit, audit_actions, clear_audit, list_audit

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def get_audit(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str = "",
    result: str = "",
    profile: str = "",
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    """查询审计日志。所有写操作（登录、改密、开关机、导入/删除 profile）都在这里。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    rows, total = list_audit(limit=limit, offset=offset, action=action,
                             result=result, profile=profile)
    return {"logs": rows, "total": total, "actions": audit_actions()}


@router.delete("")
def purge_audit(
    keep_days: int = Query(0, ge=0, le=3650, description="0=全部清空，否则保留最近 N 天"),
    request: Request = None,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    deleted = clear_audit(keep_days)
    audit(user, "purge-audit", detail=f"keep_days={keep_days}, deleted={deleted}",
          result="ok", ip=security.client_ip(request))
    return {"ok": True, "deleted": deleted}
