from fastapi import APIRouter, Depends, HTTPException, Request

from .. import security
from ..config import ADMIN_USER, DEV_MODE
from ..db import audit
from ..schemas import ChangePasswordRequest, LoginRequest

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
    return {"token": token, "user": req.username, "expires_in": security.ACCESS_TOKEN_EXPIRE_MINUTES * 60}


@router.get("/me")
def me(user: str = Depends(security.get_current_user)):
    return {"user": user, "is_admin": user == ADMIN_USER}


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
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
