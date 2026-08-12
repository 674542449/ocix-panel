import os
import re
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

# OCI 官方配置文件，格式与 oci CLI 通用（容器内由卷挂载到 /root/.oci/config）
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
# 建实例 / 建网络要等资源就绪，需要更长的等待上限
OCI_LAUNCH_TIMEOUT = int(os.getenv("OCIX_LAUNCH_TIMEOUT", "180"))
# 单次请求内的并发上限。SDK 调用是网络 IO，可以比子进程时代开得大一些
OCI_MAX_WORKERS = max(1, int(os.getenv("OCIX_WORKERS", "16")))
# compartment 列表缓存秒数（compartment 很少变，缓存能显著降低请求次数）
COMPARTMENT_CACHE_TTL = int(os.getenv("OCIX_COMPARTMENT_CACHE_TTL", "300"))

# ---- 速率限制 ----
LOGIN_RATE_LIMIT = int(os.getenv("OCIX_LOGIN_LIMIT", "10"))   # 每 60s
API_RATE_LIMIT = int(os.getenv("OCIX_API_LIMIT", "120"))      # 每 60s

# 是否信任 X-Forwarded-For。默认 False：这个头是客户端可伪造的，
# 只有确实部署在自己的反代后面才能打开，否则攻击者可以靠伪造 IP 绕过登录限流。
# install.sh 在域名（Caddy 反代）模式下会自动置为 true。
TRUST_PROXY = _bool_env("OCIX_TRUST_PROXY", False)
# 面板前面有几层可信代理。取 X-Forwarded-For 时从右往左数第 N 个才是代理观测到的真实客户端；
# 取最左边那个等于直接采信客户端自己写的值。
TRUSTED_PROXY_HOPS = max(1, int(os.getenv("OCIX_PROXY_HOPS", "1")))

# 允许跨域的来源；留空表示只允许同源（默认部署由 Caddy 同源反代，无需 CORS）
CORS_ORIGINS = [o.strip() for o in os.getenv("OCIX_CORS_ORIGINS", "").split(",") if o.strip()]

# 交互式 API 文档（/docs、/openapi.json）。默认关闭：面板是自用后台，
# 对外暴露完整接口结构没有必要。
ENABLE_DOCS = _bool_env("OCIX_ENABLE_DOCS", False)

# ---- 在线更新 ----
# 宿主机上的安装目录，用于在网页上给出正确的更新命令
INSTALL_DIR = os.getenv("OCIX_HOME", "/opt/ocix")
_DEFAULT_REPO = "674542449/ocix-panel"
_repo = os.getenv("OCIX_GITHUB_REPO", _DEFAULT_REPO).strip()
# 只允许 owner/name，防止被塞进 "evil.com/x#" 之类把检查更新的请求引到别处
GITHUB_REPO = (
    _repo if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}/[A-Za-z0-9_.-]{1,100}", _repo)
    else _DEFAULT_REPO
)

# ---- 运行模式 ----
# 当 SESSION_SECRET 为空时，开发模式允许启动但不允许登录
DEV_MODE = SESSION_SECRET == ""
