import os
from pathlib import Path

# 容器内固定为 /app；本地开发时回退到仓库目录，免去手工设环境变量。
_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parents[1]
_IN_CONTAINER = Path("/app").exists()


def _path_env(name: str, container_default: str, local_default: Path) -> Path:
    raw = os.getenv(name)
    if raw:
        return Path(raw).expanduser()
    return Path(container_default) if _IN_CONTAINER else local_default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---- 目录与文件 ----
DATA_DIR = _path_env("OCIX_DATA_DIR", "/app/data", _REPO_ROOT / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

KEYS_DIR = DATA_DIR / "keys"
KEYS_DIR.mkdir(parents=True, exist_ok=True)
try:
    os.chmod(KEYS_DIR, 0o700)
except Exception:
    pass

DB_PATH = DATA_DIR / "ocix.db"

# OCI CLI 官方配置文件（容器内通常由卷挂载到 /root/.oci/config）
OCI_CONFIG_PATH = Path(
    os.getenv("OCI_CONFIG_PATH") or (Path.home() / ".oci" / "config")
).expanduser()
OCI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

# 前端静态目录：随包一起分发，容器和本地是同一个路径规则
FRONTEND_DIR = Path(os.getenv("OCIX_FRONTEND_DIR") or (_PKG_DIR / "web")).expanduser()

# ---- 鉴权 ----
SESSION_SECRET = os.getenv("OCIX_SESSION_SECRET", "")
ADMIN_USER = os.getenv("OCIX_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("OCIX_ADMIN_PASSWORD", "changeit")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("OCIX_TOKEN_TTL", "1440"))

# ---- 执行层 ----
OCI_CLI_TIMEOUT = int(os.getenv("OCIX_CLI_TIMEOUT", "60"))
# 建实例 / 建网络会等资源就绪，比普通查询慢得多，单独给一个更长的超时
OCI_LAUNCH_TIMEOUT = int(os.getenv("OCIX_LAUNCH_TIMEOUT", "180"))
# 单次请求内并发调用 oci CLI 的上限（每次调用都是一个子进程，别开太大）
OCI_MAX_WORKERS = max(1, int(os.getenv("OCIX_CLI_WORKERS", "8")))
# compartment 列表缓存秒数（compartment 很少变，缓存能显著降低 CLI 调用次数）
COMPARTMENT_CACHE_TTL = int(os.getenv("OCIX_COMPARTMENT_CACHE_TTL", "300"))

# ---- 速率限制 ----
LOGIN_RATE_LIMIT = int(os.getenv("OCIX_LOGIN_LIMIT", "10"))   # 每 60s
API_RATE_LIMIT = int(os.getenv("OCIX_API_LIMIT", "120"))      # 每 60s

# 默认部署形态就是 Caddy 反代，不取 X-Forwarded-For 的话所有人共用一个限流桶
TRUST_PROXY = _bool_env("OCIX_TRUST_PROXY", True)

# 允许跨域的来源；留空表示只允许同源（默认部署由 Caddy 同源反代，无需 CORS）
CORS_ORIGINS = [o.strip() for o in os.getenv("OCIX_CORS_ORIGINS", "").split(",") if o.strip()]

# ---- 运行模式 ----
# 当 SESSION_SECRET 为空时，开发模式允许启动但不允许登录
DEV_MODE = SESSION_SECRET == ""
