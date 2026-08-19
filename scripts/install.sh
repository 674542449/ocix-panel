#!/usr/bin/env bash
# OCIX 一键部署。默认装到 /opt/ocix，域名(HTTPS) 与 IP+端口(HTTP) 二选一。
#
#   交互式:  sudo bash scripts/install.sh
#   域名:    sudo bash scripts/install.sh --domain panel.example.com --email me@example.com
#   直连:    sudo bash scripts/install.sh --port 8000
# 如果在精简环境（如 Alpine Linux ash）下用 sh 运行且未装 bash，自动补全并切入 bash
if [ -z "${BASH_VERSION:-}" ]; then
  if command -v apk >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then
    echo "Alpine Linux 环境：正在自动补全 bash 与基础组件..."
    apk add --no-cache bash curl docker docker-cli-compose openssl git || true
    rc-update add docker default 2>/dev/null || true
    service docker start 2>/dev/null || true
  fi
  if command -v bash >/dev/null 2>&1; then
    exec bash "$0" "$@"
  fi
fi

set -euo pipefail

ORIG_ARGS=("$@")
SRC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${OCIX_HOME:-/opt/ocix}"

DOMAIN=""; EMAIL=""; PORT=""; BIND=""; ADMIN_USER=""; ADMIN_PASSWORD=""
MODE=""; ASSUME_YES=0; STAY_HERE=0

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_cyn=$'\033[36m'
c_dim=$'\033[2m'; c_bold=$'\033[1m'; c_off=$'\033[0m'
info() { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s!%s %s\n' "$c_ylw" "$c_off" "$*"; }
die()  { printf '%s✗ %s%s\n' "$c_red" "$*" "$c_off" >&2; exit 1; }

usage() {
  cat <<'EOF'
用法: sudo bash scripts/install.sh [选项]

  --domain <域名>          用域名访问，部署时就申请 Let's Encrypt 证书（需 80/443 可达）
  --email  <邮箱>          证书通知邮箱，配合 --domain 使用
  --port   <端口>          用 IP+端口 直连访问（默认 8000）
  --bind   <地址>          端口绑定地址，默认 0.0.0.0；填 127.0.0.1 则只允许本机
  --admin-user <名>        管理员用户名，不给会在安装过程中问你
  --admin-password <密码>  管理员密码，不给会在安装过程中问你
  --dir <路径>             安装目录，默认 /opt/ocix
  --here                   就地安装，不搬到 /opt/ocix
  -y, --yes                不交互，缺省项用默认值（密码随机生成）
  -h, --help               显示本帮助

两种访问方式二选一：给了 --domain 走 HTTPS 域名模式，否则走 IP+端口 直连模式。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --bind) BIND="${2:-}"; shift 2 ;;
    --admin-user) ADMIN_USER="${2:-}"; shift 2 ;;
    --admin-password) ADMIN_PASSWORD="${2:-}"; shift 2 ;;
    --dir) INSTALL_DIR="${2:-}"; shift 2 ;;
    --here) STAY_HERE=1; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数: $1（用 --help 查看用法）" ;;
  esac
done

# ---------- 搬到 /opt/ocix ----------
# 统一安装位置，之后升级、看日志都在同一个地方，不用记当初 clone 到哪了。
if [[ $STAY_HERE -eq 0 && "$SRC_ROOT" != "$INSTALL_DIR" ]]; then
  parent="$(dirname "$INSTALL_DIR")"
  if [[ ! -d "$INSTALL_DIR" && ! -w "$parent" ]] || [[ -d "$INSTALL_DIR" && ! -w "$INSTALL_DIR" ]]; then
    die "没有权限写 ${INSTALL_DIR}。请用 sudo 重跑，或加 --here 就地安装。"
  fi
  info "把项目安装到 ${c_bold}${INSTALL_DIR}${c_off} …"
  mkdir -p "$INSTALL_DIR"
  # 连 .git 一起复制（网页端「检查更新」和 update.sh 都依赖它），
  # 但本地状态和密钥一律不带过去：.env / data / 私钥 / pyc 缓存
  EXCLUDES=(.env '.env.bak.*' data __pycache__ '*.pyc' '*.pem' '*.key' .venv .pytest_cache)
  if command -v rsync >/dev/null 2>&1; then
    rsync_args=()
    for e in "${EXCLUDES[@]}"; do rsync_args+=(--exclude="$e"); done
    rsync -a "${rsync_args[@]}" "$SRC_ROOT"/ "$INSTALL_DIR"/
  else
    tar_args=()
    for e in "${EXCLUDES[@]}"; do tar_args+=(--exclude="$e"); done
    (cd "$SRC_ROOT" && tar "${tar_args[@]}" -cf - .) | (cd "$INSTALL_DIR" && tar -xf -)
  fi
  ok "已复制到 ${INSTALL_DIR}"
  exec bash "$INSTALL_DIR/scripts/install.sh" "${ORIG_ARGS[@]}" --here
fi

REPO_ROOT="$SRC_ROOT"
DEPLOY_DIR="$REPO_ROOT/deploy"
ENV_FILE="$REPO_ROOT/.env"

# ---------- 依赖与发行版检测 ----------
OS_ID=""
if [[ -f /etc/os-release ]]; then
  # shellcheck source=/dev/null
  . /etc/os-release
  OS_ID="${ID:-}"
fi

if ! command -v docker >/dev/null 2>&1; then
  if [[ "$OS_ID" == "alpine" ]]; then
    die "未检测到 docker。Alpine 请执行: apk add --no-cache docker docker-cli-compose bash curl openssl && rc-update add docker default && service docker start"
  else
    die "未检测到 docker。Ubuntu / Debian 可执行: curl -fsSL https://get.docker.com | sh"
  fi
fi

if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  if [[ "$OS_ID" == "alpine" ]]; then
    die "未检测到 docker compose 插件。Alpine 请执行: apk add --no-cache docker-cli-compose"
  else
    die "未检测到 docker compose 插件。Ubuntu / Debian 请执行: sudo apt-get install -y docker-compose-plugin"
  fi
fi

if ! docker info >/dev/null 2>&1; then
  if [[ "$OS_ID" == "alpine" ]]; then
    warn "Docker 服务未运行。正在尝试启动 Docker 服务..."
    service docker start 2>/dev/null || rc-service docker start 2>/dev/null || true
  fi
fi
docker info >/dev/null 2>&1 || die "docker 守护进程未运行或当前用户无权限（可尝试 sudo，或将当前用户加入 docker 用户组）"

rand_hex() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"
  else head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

# ---------- 读取已有配置 ----------
OLD_SECRET=""; OLD_USER=""; OLD_PASSWORD=""; OLD_TTL=""
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  set +a
  OLD_SECRET="${OCIX_SESSION_SECRET:-}"
  OLD_USER="${OCIX_ADMIN_USER:-}"
  OLD_PASSWORD="${OCIX_ADMIN_PASSWORD:-}"
  OLD_TTL="${OCIX_TOKEN_TTL:-}"
  cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%s)"
  ok "发现已有 .env，密钥会沿用（备份已保存）"
fi

# 会话密钥永远自动生成，不需要你操心
SESSION_SECRET="${OLD_SECRET:-$(rand_hex 32)}"

# ---------- 选择访问方式 ----------
if [[ -n "$DOMAIN" ]]; then
  MODE="domain"
elif [[ -n "$PORT" ]]; then
  MODE="direct"
elif [[ $ASSUME_YES -eq 1 ]]; then
  MODE="direct"
else
  info ""
  info "怎么访问这个面板？"
  info "  1) 域名 + HTTPS   证书在部署时就申请好，需要域名已解析到本机且 80/443 没被占用"
  info "  2) IP + 端口      直接 http://本机IP:端口 访问，无 HTTPS${c_dim}（仅建议内网或临时用）${c_off}"
  read -r -p "选择 [1/2]: " choice
  case "$choice" in
    1) MODE="domain" ;;
    2) MODE="direct" ;;
    *) die "只能选 1 或 2" ;;
  esac
fi

if [[ "$MODE" == "domain" ]]; then
  [[ -n "$DOMAIN" ]] || read -r -p "域名（例如 panel.example.com）: " DOMAIN
  [[ -n "$DOMAIN" ]] || die "域名不能为空"
  [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "域名格式看着不对: $DOMAIN"
  if [[ -z "$EMAIL" && $ASSUME_YES -eq 0 ]]; then
    read -r -p "证书通知邮箱: " EMAIL
  fi
  [[ -n "$EMAIL" ]] || EMAIL="admin@${DOMAIN}"

  # 部署前先验一遍 DNS，别等 Let's Encrypt 拒绝了才发现解析没生效
  info ""
  info "检查 ${DOMAIN} 的 DNS 解析…"
  resolved=""
  if command -v getent >/dev/null 2>&1; then
    resolved="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}')"
  fi
  [[ -z "$resolved" ]] && command -v dig >/dev/null 2>&1 && resolved="$(dig +short A "$DOMAIN" | head -1)"
  [[ -z "$resolved" ]] && command -v nslookup >/dev/null 2>&1 && resolved="$(nslookup "$DOMAIN" 2>/dev/null | awk '/^Address: / { print $2 }' | tail -1)"
  public_ip="$(curl -fsS --max-time 8 https://api.ipify.org 2>/dev/null || true)"

  if [[ -z "$resolved" ]]; then
    warn "解析不到 ${DOMAIN}。证书会申请失败——请先加一条 A 记录指向本机公网 IP${public_ip:+（$public_ip）}。"
    [[ $ASSUME_YES -eq 1 ]] || { read -r -p "仍然继续？[y/N]: " a; [[ "$a" == "y" || "$a" == "Y" ]] || exit 1; }
  elif [[ -n "$public_ip" && "$resolved" != "$public_ip" ]]; then
    warn "${DOMAIN} 解析到 ${resolved}，但本机公网 IP 是 ${public_ip}。如果没套 CDN，证书会申请失败。"
    [[ $ASSUME_YES -eq 1 ]] || { read -r -p "仍然继续？[y/N]: " a; [[ "$a" == "y" || "$a" == "Y" ]] || exit 1; }
  else
    ok "DNS 解析正确：${DOMAIN} → ${resolved}"
  fi

  for p in 80 443; do
    is_busy=0
    if command -v ss >/dev/null 2>&1; then
      ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${p}$" && is_busy=1
    elif command -v netstat >/dev/null 2>&1; then
      netstat -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${p}$" && is_busy=1
    fi
    if [[ $is_busy -eq 1 ]]; then
      warn "${p} 端口已被别的程序占用，Caddy 起不来。请先停掉占用的服务（nginx/apache 等）。"
    fi
  done
else
  [[ -n "$PORT" ]] || { [[ $ASSUME_YES -eq 1 ]] && PORT=8000 || read -r -p "端口 [8000]: " PORT; }
  PORT="${PORT:-8000}"
  [[ "$PORT" =~ ^[0-9]+$ ]] && [[ "$PORT" -ge 1 && "$PORT" -le 65535 ]] || die "端口不合法: $PORT"
  BIND="${BIND:-0.0.0.0}"
fi

# ---------- 管理员账号 ----------
# 密码全程明文回显，方便你当场核对——装完记得别把终端记录留给别人看。
GENERATED_PW=0
if [[ -z "$ADMIN_USER" ]]; then
  if [[ $ASSUME_YES -eq 1 ]]; then
    ADMIN_USER="${OLD_USER:-admin}"
  else
    info ""
    read -r -p "管理员用户名 [${OLD_USER:-admin}]: " _u
    ADMIN_USER="${_u:-${OLD_USER:-admin}}"
  fi
fi

if [[ -z "$ADMIN_PASSWORD" ]]; then
  if [[ $ASSUME_YES -eq 1 ]]; then
    ADMIN_PASSWORD="${OLD_PASSWORD:-$(rand_hex 9)}"
    [[ -n "$OLD_PASSWORD" ]] || GENERATED_PW=1
  else
    while true; do
      read -r -p "管理员密码（至少 8 位，明文显示便于核对）: " _p1
      if [[ -z "$_p1" && -n "$OLD_PASSWORD" ]]; then
        ADMIN_PASSWORD="$OLD_PASSWORD"
        info "  ${c_dim}留空，沿用原密码${c_off}"
        break
      fi
      if [[ ${#_p1} -lt 8 ]]; then
        warn "至少 8 位，再来一次"; continue
      fi
      read -r -p "再输一次确认: " _p2
      if [[ "$_p1" != "$_p2" ]]; then
        warn "两次输入不一致，再来一次"; continue
      fi
      ADMIN_PASSWORD="$_p1"
      break
    done
  fi
fi

VERSION="$(cat "$REPO_ROOT/VERSION" 2>/dev/null || echo dev)"

# ---------- 写 .env ----------
umask 077
{
  echo "# 由 scripts/install.sh 生成于 $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo "# 这个文件含密钥，已被 .gitignore 排除，请勿提交或外传。"
  echo "OCIX_VERSION=${VERSION}"
  echo "OCIX_SESSION_SECRET=${SESSION_SECRET}"
  echo "OCIX_ADMIN_USER=${ADMIN_USER}"
  echo "OCIX_ADMIN_PASSWORD=${ADMIN_PASSWORD}"
  echo "OCIX_TOKEN_TTL=${OLD_TTL:-1440}"
  echo "OCIX_HOME=${REPO_ROOT}"
  if [[ "$MODE" == "domain" ]]; then
    echo "OCIX_DOMAIN=${DOMAIN}"
    echo "OCIX_ACME_EMAIL=${EMAIL}"
    echo "OCIX_TRUST_PROXY=true"
  else
    echo "OCIX_PORT=${PORT}"
    echo "OCIX_BIND=${BIND}"
    echo "OCIX_TRUST_PROXY=false"
  fi
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"
ok "已写入 ${ENV_FILE}（权限 600）"

if [[ "$MODE" == "domain" ]]; then
  cp "$DEPLOY_DIR/Caddyfile.tmpl" "$DEPLOY_DIR/Caddyfile"
  # compose 的项目目录是 deploy/，而 .env 在仓库根，必须显式指定 --env-file，
  # 否则所有变量都插值不到，报 "required variable OCIX_SESSION_SECRET is missing a value"
  COMPOSE_FILES=(--env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.caddy.yml)
  URL="https://${DOMAIN}"
else
  COMPOSE_FILES=(--env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.direct.yml)
  host_ip="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '本机IP')"
  URL="http://${host_ip}:${PORT}"
fi

# ---------- 更新代理（网页一键更新靠它执行）----------
# 面板容器刻意不挂 docker socket，自己更新不了自己；
# 这个常驻在宿主机上的小进程负责看到请求后跑 update.sh。
mkdir -p "${REPO_ROOT}/var/control"
if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
  if [[ $EUID -eq 0 ]]; then
    info ""
    info "安装更新代理（网页端一键更新）…"
    sed "s|__OCIX_HOME__|${REPO_ROOT}|g" "${REPO_ROOT}/deploy/ocix-updater.service"       > /etc/systemd/system/ocix-updater.service
    systemctl daemon-reload
    systemctl enable --now ocix-updater.service >/dev/null 2>&1 || true
    if systemctl is-active --quiet ocix-updater.service; then
      ok "更新代理已启动，之后可直接在网页上点「立即更新」"
    else
      warn "更新代理没起来： systemctl status ocix-updater"
    fi
  else
    warn "不是 root，跳过更新代理安装；网页端一键更新将不可用。"
    warn "需要的话用 sudo 重跑本脚本，或手动执行："
    warn "  sudo sed 's|__OCIX_HOME__|${REPO_ROOT}|g' ${REPO_ROOT}/deploy/ocix-updater.service > /etc/systemd/system/ocix-updater.service"
    warn "  sudo systemctl enable --now ocix-updater"
  fi
else
  warn "没有 systemd，跳过更新代理；网页端一键更新不可用，更新请手动执行 scripts/update.sh"
fi

# ---------- 构建并启动 ----------
info ""
info "构建镜像并启动…"
cd "$DEPLOY_DIR"
$DC "${COMPOSE_FILES[@]}" up -d --build

# ---------- 等待后端就绪 ----------
info ""
printf '等待后端就绪'
backend_ok=0
for _ in $(seq 1 60); do
  if $DC "${COMPOSE_FILES[@]}" exec -T backend curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then
    backend_ok=1; break
  fi
  printf '.'; sleep 2
done
printf '\n'
[[ $backend_ok -eq 1 ]] || {
  $DC "${COMPOSE_FILES[@]}" logs --tail 40 backend
  die "后端没能起来，日志见上方"
}
ok "后端已就绪"

# ---------- 域名模式：部署阶段就把证书拿到手 ----------
if [[ "$MODE" == "domain" ]]; then
  info ""
  printf '申请 TLS 证书（Let'"'"'s Encrypt）'
  cert_ok=0
  for _ in $(seq 1 60); do
    # 直接问 Caddy 拿证书目录里有没有该域名的 .crt，比等访客触发可靠
    if $DC "${COMPOSE_FILES[@]}" exec -T caddy \
         sh -c "ls /data/caddy/certificates/*/${DOMAIN}/${DOMAIN}.crt" >/dev/null 2>&1; then
      cert_ok=1; break
    fi
    printf '.'; sleep 3
  done
  printf '\n'
  if [[ $cert_ok -eq 1 ]]; then
    ok "证书已签发并落盘，首次访问不需要再等"
  else
    warn "证书还没签发成功。常见原因：DNS 没生效、80/443 被防火墙挡、或云厂商安全组没放行。"
    warn "看日志定位： bash ${REPO_ROOT}/scripts/ocix.sh logs caddy"
    warn "面板本身已经在跑，DNS 修好后 Caddy 会自动重试。"
  fi
fi

# ---------- 收尾 ----------
info ""
info "──────────────────────────────────────────"
ok "OCIX v${VERSION} 部署完成"
info ""
info "  安装目录   ${REPO_ROOT}"
info "  访问地址   ${c_cyn}${URL}${c_off}"
info "  用户名     ${c_bold}${ADMIN_USER}${c_off}"
info "  密码       ${c_bold}${ADMIN_PASSWORD}${c_off}"
if [[ $GENERATED_PW -eq 1 ]]; then
  warn "  上面这个密码是随机生成的，请立刻记下来"
fi
info ""
info "  ${c_dim}日常操作： bash ${REPO_ROOT}/scripts/ocix.sh {logs|restart|ps|stop|start}${c_off}"
info "  ${c_dim}在线更新： bash ${REPO_ROOT}/scripts/update.sh${c_off}"
if [[ "$MODE" == "direct" ]]; then
  info "  ${c_ylw}直连模式没有 HTTPS，密码是明文传输的，不建议长期公网使用。${c_off}"
  info "  ${c_dim}记得在云厂商安全组放行 ${PORT} 端口。${c_off}"
fi
info "──────────────────────────────────────────"
