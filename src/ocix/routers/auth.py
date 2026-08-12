from fastapi import APIRouter, Depends, HTTPException, Request

from .. import security
from ..config import ADMIN_USER, DEV_MODE
from ..db import audit
from ..schemas import ChangePasswordRequest, LoginRequest, PasswordPolicyRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(req: LoginRequest, request: Request):
    security.check_rate(request, security.LOGIN_RATE_LIMIT, scope="login")
    ip = security.client_ip(request)
    if DEV_MODE:
        raise HTTPException(
            status_code=500,
            detail="SESSION_SECRET 未设置，无法签发令牌。请在环境变量中配置 OCIX_SESSION_SECRET。",
        )
    if not security.authenticate(req.username, req.password):
        audit(req.username, "login", result="fail", ip=ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = security.create_token(req.username)
    audit(req.username, "login", result="ok", ip=ip)
    return {"token": token, "user": req.username,
            "expires_in": security.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "password": security.password_status()}


@router.get("/me")
def me(user: str = Depends(security.get_current_user_allow_expired)):
    return {"user": user, "is_admin": user == ADMIN_USER,
            "password": security.password_status()}


@router.get("/password-policy")
def get_password_policy(user: str = Depends(security.get_current_user_allow_expired)):
    """当前的密码有效期设置与剩余天数。"""
    return security.password_status()


@router.put("/password-policy")
def set_password_policy(
    req: PasswordPolicyRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    """修改密码有效期。0 = 永不过期。"""
    security.check_rate(request, security.API_RATE_LIMIT)
    security.set_password_max_age_days(req.max_age_days)
    audit(user, "password-policy", result="ok",
          detail=("已关闭密码有效期" if req.max_age_days == 0
                  else f"密码有效期设为 {req.max_age_days} 天"),
          ip=security.client_ip(request))
    return {"ok": True, **security.password_status()}


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    request: Request,
    user: str = Depends(security.get_current_user_allow_expired),
):
    security.check_rate(request, security.LOGIN_RATE_LIMIT, scope="login")
    ip = security.client_ip(request)
    if not security.verify_password(req.old_password, security.current_admin_hash()):
        audit(user, "change-password", result="fail", ip=ip)
        raise HTTPException(status_code=400, detail="原密码错误")
    if req.old_password == req.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    security.set_admin_password(req.new_password)
    audit(user, "change-password", result="ok", detail="所有令牌已失效", ip=ip)
    return {"ok": True, "msg": "密码已更新，所有登录状态已失效，请重新登录"}
