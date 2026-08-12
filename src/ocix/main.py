from contextlib import asynccontextmanager
from pathlib import PurePosixPath

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, security
from .backends import get_backend
from .common import list_profiles_from_config
from .config import CORS_ORIGINS, DEV_MODE, FRONTEND_DIR, OCI_CONFIG_PATH
from .db import init_db
from .routers import audit, auth, instances, monitor, profiles, provision, system

VERSION = __version__


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    security.bootstrap_admin()
    yield


app = FastAPI(title="OCIX - Oracle Always Free 开机面板", version=VERSION, lifespan=lifespan)

# 默认部署由 Caddy 同源反代，无需 CORS；仅在显式配置来源时才放开。
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
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
app.include_router(system.router)


@app.get("/api/health")
def health():
    """探活 + 环境自检，部署完先看这个接口。"""
    return {
        "ok": True,
        "service": "ocix",
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

        return FileResponse(str(_ROOT / "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
