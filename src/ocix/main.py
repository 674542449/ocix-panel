from contextlib import asynccontextmanager
from pathlib import PurePosixPath

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, security
from .backends import get_backend
from .common import list_profiles_from_config
from .config import CORS_ORIGINS, DEV_MODE, ENABLE_DOCS, FRONTEND_DIR, OCI_CONFIG_PATH
from .db import init_db
from .routers import (
    audit,
    auth,
    instances,
    monitor,
    profiles,
    provision,
    ssh_keys,
    system,
    terminal,
)

VERSION = __version__


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    security.bootstrap_admin()
    yield


app = FastAPI(
    title="OCIX - Oracle Always Free 开机面板",
    version=VERSION,
    lifespan=lifespan,
    # 自用后台，默认不对外暴露接口结构；需要时用 OCIX_ENABLE_DOCS=true 打开
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)

# 前端依赖在构建期落到 /assets；本地开发缺文件时会回退到这几个公共 CDN。
_CDN = "https://unpkg.com https://cdn.jsdelivr.net https://registry.npmmirror.com"
# Vue 的运行时模板编译要用 new Function，因此必须留 unsafe-eval；
# 页面里的应用代码是内联的，故 script 也需要 unsafe-inline。
# 即便如此，frame-ancestors / base-uri / object-src / connect-src 仍是实打实的收敛。
_CSP = (
    "default-src 'self'; "
    f"script-src 'self' 'unsafe-inline' 'unsafe-eval' {_CDN}; "
    f"style-src 'self' 'unsafe-inline' {_CDN}; "
    f"font-src 'self' data: {_CDN}; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """把参数校验错误压成一句人话。

    FastAPI 默认给的 detail 是 [{loc, msg, type, ...}] 这样的数组，
    而前端 errMsg 直接取 detail 往弹窗里塞，拿到数组就显示成 [object Object]，
    用户根本看不出是哪个字段填错了。这里统一拍平成字符串。
    """
    parts = []
    for err in exc.errors():
        # Pydantic 会在自定义报错前面加 "Value error, "，对用户是噪音
        msg = str(err.get("msg", "")).removeprefix("Value error, ")
        field = ".".join(str(x) for x in err.get("loc", []) if x not in ("body", "query"))
        parts.append(f"{field}: {msg}" if field and len(exc.errors()) > 1 else msg)
    return JSONResponse(status_code=422, content={"detail": "；".join(parts) or "请求参数不合法"})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """安全响应头。

    域名模式下 Caddy 也会加一部分，但直连（IP+端口）模式没有反代，
    只能由应用自己保证，否则那条路径完全裸奔。
    """
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", _CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-XSS-Protection", "1; mode=block")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy",
                                "geolocation=(), microphone=(), camera=(), payment=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/api"):
        # 接口响应含账户与资源信息，不允许任何中间层或浏览器缓存
        response.headers.setdefault("Cache-Control", "no-store")
    return response

# 默认部署由 Caddy 同源反代，无需 CORS；仅在显式配置来源时才放开。
# 通配符会被丢掉：allow_origins=["*"] 配上 allow_credentials=True，
# 等于任何站点都能带着凭据打这套接口。要跨域就把来源一个个写清楚。
_cors_origins = [o for o in CORS_ORIGINS if o != "*"]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(instances.router)
app.include_router(monitor.router)
app.include_router(provision.router)
app.include_router(audit.router)
app.include_router(ssh_keys.router)
app.include_router(system.router)
app.include_router(terminal.router)


@app.get("/api/health")
def health():
    """探活。

    只回最少信息：这个接口不需要鉴权（部署脚本和容器 healthcheck 都要用），
    版本号、配置路径这些留给下面那个需要登录的自检接口，
    免得未登录就能看出跑的是哪一版、方便按已知漏洞下手。
    """
    return {"ok": True, "service": "ocix"}


@app.get("/api/diagnostics")
def diagnostics(user: str = Depends(security.get_current_user)):
    """环境自检，部署完登录后看这个。"""
    return {
        "ok": True,
        "version": VERSION,
        "dev_mode": DEV_MODE,
        "oci_sdk": get_backend().version(),
        "oci_config": {
            "path": str(OCI_CONFIG_PATH),
            "exists": OCI_CONFIG_PATH.exists(),
            "profiles": len(list_profiles_from_config()),
        },
        "frontend_dir": str(FRONTEND_DIR),
    }


# ---- 前端 SPA 托管（必须放在所有 /api 路由之后）----
if FRONTEND_DIR.exists():
    _static_dir = FRONTEND_DIR / "assets"
    if _static_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_static_dir)), name="assets")

    _ROOT = FRONTEND_DIR.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if full_path.startswith("api"):
            raise HTTPException(status_code=404, detail="Not Found")

        # 归一化后必须仍在前端目录内，防止 ../ 目录穿越
        try:
            candidate = (_ROOT / full_path).resolve()
            candidate.relative_to(_ROOT)
        except (ValueError, OSError):
            raise HTTPException(status_code=404, detail="Not Found")

        if candidate.is_file():
            return FileResponse(str(candidate))

        # 带扩展名却不存在的资源直接 404：
        # 否则前端会把 index.html 当成 js/css 执行，报一堆莫名其妙的语法错误
        if "." in PurePosixPath(full_path).name:
            raise HTTPException(status_code=404, detail="Not Found")

        return FileResponse(
            str(_ROOT / "index.html"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
