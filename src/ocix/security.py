import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from .config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ADMIN_PASSWORD,
    ADMIN_USER,
    API_RATE_LIMIT,
    DEV_MODE,
    LOGIN_RATE_LIMIT,
    SESSION_SECRET,
    TRUST_PROXY,
    TRUSTED_PROXY_HOPS,
)
from .db import get_setting, set_setting

# 各 router 通过 security.LOGIN_RATE_LIMIT / security.API_RATE_LIMIT 取限流阈值，
# 这里是有意的再导出——列进 __all__ 才不会被 lint 当成未使用的导入删掉。
__all__ = [
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "API_RATE_LIMIT",
    "LOGIN_RATE_LIMIT",
    "authenticate",
    "bootstrap_admin",
    "check_rate",
    "client_ip",
    "create_token",
    "current_admin_hash",
    "get_current_user",
    "set_admin_password",
    "verify_password",
]

bearer_scheme = HTTPBearer(auto_error=False)

# bcrypt 对密码长度硬性限制为 72 字节，超过部分会被服务端静默截断，
# 这里显式截断以保证不同调用之间行为一致（与 bcrypt 自身逻辑等价）。
_MAX_BCRYPT_BYTES = 72

_KEY_HASH = "admin_password_hash"
_KEY_ENV_FP = "admin_password_env_fp"
_KEY_EPOCH = "token_epoch"

# 环境变量密码的指纹：用于判断 .env 里的密码是否被改过。
# 面板内改密后 DB 里的哈希优先；一旦 .env 中的密码变了（指纹不同），
# 下次启动会以环境变量为准重置——这也是忘记密码时的唯一找回路径。
_ENV_FP = hashlib.sha256(("ocix.v1:" + ADMIN_PASSWORD).encode("utf-8")).hexdigest()


def _to_bytes(value: str) -> bytes:
    return value.encode("utf-8")


def _hash_password(plain: str) -> str:
    pwd = _to_bytes(plain)[:_MAX_BCRYPT_BYTES]
    return bcrypt.hashpw(pwd, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bytes(plain)[:_MAX_BCRYPT_BYTES], hashed.encode("utf-8"))
    except (ValueError, TypeError, AttributeError):
        return False


def bootstrap_admin() -> None:
    """启动时校准管理员口令来源（需在 init_db 之后调用）。"""
    stored_fp = get_setting(_KEY_ENV_FP)
    stored_hash = get_setting(_KEY_HASH)
    if not stored_hash or stored_fp != _ENV_FP:
        set_setting(_KEY_HASH, _hash_password(ADMIN_PASSWORD))
        set_setting(_KEY_ENV_FP, _ENV_FP)
    if get_setting(_KEY_EPOCH) is None:
        set_setting(_KEY_EPOCH, "1")


def current_admin_hash() -> str:
    h = get_setting(_KEY_HASH)
    if not h:
        bootstrap_admin()
        h = get_setting(_KEY_HASH) or ""
    return h


def _token_epoch() -> int:
    try:
        return int(get_setting(_KEY_EPOCH, "1"))
    except (TypeError, ValueError):
        return 1


def set_admin_password(plain: str) -> None:
    """改密并持久化；同时递增 epoch 使所有已签发令牌立即失效。"""
    set_setting(_KEY_HASH, _hash_password(plain))
    set_setting(_KEY_EPOCH, str(_token_epoch() + 1))


def authenticate(username: str, password: str) -> bool:
    """校验用户名 + 密码。

    用户名不对时**也要**走一遍 bcrypt：直接 return False 会让「用户名不存在」
    比「密码错误」快上一个数量级（bcrypt 一次要几十到几百毫秒），
    攻击者据此就能确认管理员账号名，再把爆破火力全压到密码上。
    比较本身用 compare_digest，避免逐字符提前退出。
    """
    ok_user = secrets.compare_digest((username or "").encode("utf-8"),
                                     ADMIN_USER.encode("utf-8"))
    ok_pass = verify_password(password, current_admin_hash())
    return ok_user and ok_pass


def create_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": username, "exp": expire, "epoch": _token_epoch()}
    return jwt.encode(payload, SESSION_SECRET, algorithm="HS256")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if DEV_MODE:
        raise HTTPException(status_code=503, detail="未配置 OCIX_SESSION_SECRET，服务处于只读开发模式")
    if creds is None:
        raise HTTPException(status_code=401, detail="未认证")
    try:
        payload = jwt.decode(creds.credentials, SESSION_SECRET, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="无效或已过期的令牌")
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="无效令牌")
    if int(payload.get("epoch", 0)) != _token_epoch():
        raise HTTPException(status_code=401, detail="密码已变更，请重新登录")
    return username


# ---- 简单内存速率限制器（按客户端 IP） ----
def client_ip(request: Request | None) -> str:
    """识别真实客户端 IP，用于限流与审计。

    X-Forwarded-For 是「客户端, 代理1, 代理2…」的追加式列表，**左边的部分完全由
    客户端伪造**。反代（如 Caddy）会把它实际观测到的对端追加到最右侧，
    所以只有从右往左数第 TRUSTED_PROXY_HOPS 个才可信。

    早先取的是最左边那个，攻击者只要每次请求换一个伪造 IP，
    登录限流就形同虚设，可以无限次爆破密码。
    """
    if request is None:
        return "unknown"
    if TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                idx = max(0, len(parts) - TRUSTED_PROXY_HOPS)
                return parts[idx]
        xri = request.headers.get("x-real-ip")
        if xri:
            return xri.strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self):
        self.hits: dict[str, list] = {}

    def allow(self, key: str, limit: int, window: int = 60) -> bool:
        now = time.time()
        lst = self.hits.setdefault(key, [])
        lst[:] = [t for t in lst if now - t < window]
        if len(lst) >= limit:
            return False
        lst.append(now)
        return True

    def sweep(self, window: int = 60):
        now = time.time()
        for key in list(self.hits):
            lst = self.hits[key]
            lst[:] = [t for t in lst if now - t < window]
            if not lst:
                self.hits.pop(key, None)


rate_limiter = RateLimiter()


def check_rate(request: Request | None, limit: int, window: int = 60, scope: str = "api"):
    key = f"{scope}:{client_ip(request)}"
    if not rate_limiter.allow(key, limit, window):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
