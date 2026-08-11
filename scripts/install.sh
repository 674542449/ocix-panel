#!/usr/bin/env bash
# OCIX 一键部署。域名(HTTPS) 与 IP+端口(HTTP) 二选一。
#
#   交互式:  bash scripts/install.sh
#   域名:    bash scripts/install.sh --domain panel.example.com --email me@example.com
#   直连:    bash scripts/install.sh --port 8000
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="$REPO_ROOT/deploy"
ENV_FILE="$REPO_ROOT/.env"

DOMAIN=""; EMAIL=""; PORT=""; BIND=""; ADMIN_USER="admin"; ADMIN_PASSWORD=""
MODE=""; ASSUME_YES=0

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
info() { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s!%s %s\n' "$c_ylw" "$c_off" "$*"; }
die()  { printf '%s✗ %s%s\n' "$c_red" "$*" "$c_off" >&2; exit 1; }

usage() {
  cat <<'EOF'
用法: bash scripts/install.sh [选项]

  --domain <域名>     用域名访问，自动申请 Let's Encrypt 证书（需 80/443 可达）
  --email  <邮箱>     证书通知邮箱，配合 --domain 使用
  --port   <端口>     用 IP+端口 直连访问（默认 8000）
  --bind   <地址>     端口绑定地址，默认 0.0.0.0；填 127.0.0.1 则只允许本机
  --admin-user <名>   管理员用户名，默认 admin
  --admin-password <密码>  管理员密码，留空则自动生成
  -y, --yes           不交互，缺省项用默认值
  -h, --help          显示本帮助

两种模式二选一：给了 --domain 走 HTTPS 域名模式，否则走 IP+端口 直连模式。
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
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数: $1（用 --help 查看用法）" ;;
  esac
done

# ---------- 依赖检查 ----------
command -v docker >/dev/null 2>&1 || die "没装 docker。Ubuntu/Debian 可执行: curl -fsSL https://get.docker.com | sh"
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  die "没装 docker compose 插件。请执行: sudo apt-get install -y docker-compose-plugin"
fi
docker info >/dev/null 2>&1 || die "docker 守护进程没跑起来，或当前用户没权限（试试 sudo，或把自己加进 docker 组）"

rand_hex() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"
  else head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

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
  if [[ -z "$DOMAIN" ]]; then
    read -r -p "域名（例如 panel.example.com）: " DOMAIN
  fi
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
    if command -v ss >/dev/null 2>&1 && ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${p}$"; then
      warn "${p} 端口已被别的程序占用，Caddy 起不来。请先停掉占用的服务（nginx/apache 等）。"
    fi
  done
else
  [[ -n "$PORT" ]] || { [[ $ASSUME_YES -eq 1 ]] && PORT=8000 || read -r -p "端口 [8000]: " PORT; }
  PORT="${PORT:-8000}"
  [[ "$PORT" =~ ^[0-9]+$ ]] && [[ "$PORT" -ge 1 && "$PORT" -le 65535 ]] || die "端口不合法: $PORT"
  BIND="${BIND:-0.0.0.0}"
fi

# ---------- 生成 .env ----------
if [[ -f "$ENV_FILE" ]]; then
  # 已有配置就复用密钥和密码，避免重新部署把管理员密码重置了
  ok "复用已有的 .env（密钥与管理员密码保持不变）"
  set -a; . "$ENV_FILE"; set +a
  SESSION_SECRET="${OCIX_SESSION_SECRET:-$(rand_hex 32)}"
  ADMIN_USER="${OCIX_ADMIN_USER:-$ADMIN_USER}"
  ADMIN_PASSWORD="${ADMIN_PASSWORD:-${OCIX_ADMIN_PASSWORD:-}}"
  cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%s)"
else
  SESSION_SECRET="$(rand_hex 32)"
fi
[[ -n "$ADMIN_PASSWORD" ]] || { ADMIN_PASSWORD="$(rand_hex 9)"; GENERATED_PW=1; }

VERSION="$(cat "$REPO_ROOT/VERSION" 2>/dev/null || echo dev)"

umask 077
cat > "$ENV_FILE" <<EOF
# 由 scripts/install.sh 生成于 $(date -u '+%Y-%m-%d %H:%M:%S UTC')
# 这个文件含密钥，已被 .gitignore 排除，请勿提交或外传。
OCIX_VERSION=${VERSION}
OCIX_SESSION_SECRET=${SESSION_SECRET}
OCIX_ADMIN_USER=${ADMIN_USER}
OCIX_ADMIN_PASSWORD=${ADMIN_PASSWORD}
OCIX_TOKEN_TTL=${OCIX_TOKEN_TTL:-1440}
EOF

if [[ "$MODE" == "domain" ]]; then
  cat >> "$ENV_FILE" <<EOF
OCIX_DOMAIN=${DOMAIN}
OCIX_ACME_EMAIL=${EMAIL}
OCIX_TRUST_PROXY=true
EOF
  cp "$DEPLOY_DIR/Caddyfile.tmpl" "$DEPLOY_DIR/Caddyfile"
  COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.caddy.yml)
  URL="https://${DOMAIN}"
else
  cat >> "$ENV_FILE" <<EOF
OCIX_PORT=${PORT}
OCIX_BIND=${BIND}
OCIX_TRUST_PROXY=false
EOF
  COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.direct.yml)
  host_ip="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '本机IP')"
  URL="http://${host_ip}:${PORT}"
fi
chmod 600 "$ENV_FILE"
ok "已写入 .env（权限 600）"

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
    warn "看日志定位： cd deploy && $DC ${COMPOSE_FILES[*]} logs caddy"
    warn "面板本身已经在跑，DNS 修好后 Caddy 会自动重试。"
  fi
fi

# ---------- 收尾 ----------
info ""
info "──────────────────────────────────────────"
ok "OCIX v${VERSION} 部署完成"
info ""
info "  访问地址   ${URL}"
info "  用户名     ${ADMIN_USER}"
if [[ "${GENERATED_PW:-0}" == "1" ]]; then
  info "  密码       ${ADMIN_PASSWORD}   ${c_ylw}← 随机生成，请立刻记下并登录后修改${c_off}"
else
  info "  密码       （沿用你设置的密码）"
fi
info ""
info "  ${c_dim}密码也存在 .env 里；登录后到「密码」页改掉更稳妥。${c_off}"
if [[ "$MODE" == "direct" ]]; then
  info "  ${c_ylw}直连模式没有 HTTPS，密码是明文传输的，不建议长期公网使用。${c_off}"
  info "  ${c_dim}记得在云厂商安全组放行 ${PORT} 端口。${c_off}"
fi
info "──────────────────────────────────────────"
