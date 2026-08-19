#!/usr/bin/env bash
# docker compose 的包装：自动带上正确的 -f 与 --env-file，省得每次手打一长串。
#
#   bash scripts/ocix.sh logs -f          跟踪日志
#   bash scripts/ocix.sh restart          重启
#   bash scripts/ocix.sh ps               看状态
#   bash scripts/ocix.sh stop | start     停 / 起
#   bash scripts/ocix.sh up -d --build    重新构建并启动
if [ -z "${BASH_VERSION:-}" ]; then
  if command -v bash >/dev/null 2>&1; then
    exec bash "$0" "$@"
  elif command -v apk >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then
    apk add --no-cache bash >/dev/null 2>&1 || true
    command -v bash >/dev/null 2>&1 && exec bash "$0" "$@"
  fi
fi

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
DEPLOY_DIR="$REPO_ROOT/deploy"

[[ -f "$ENV_FILE" ]] || { echo "找不到 $ENV_FILE，请先运行 scripts/install.sh" >&2; exit 1; }

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "没装 docker compose" >&2; exit 1
fi

# 有没有配域名决定用哪套叠加文件
if grep -qE '^OCIX_DOMAIN=.+' "$ENV_FILE"; then
  OVERLAY=docker-compose.caddy.yml
else
  OVERLAY=docker-compose.direct.yml
fi

cd "$DEPLOY_DIR"
exec "${DC[@]}" --env-file "$ENV_FILE" -f docker-compose.yml -f "$OVERLAY" "$@"
