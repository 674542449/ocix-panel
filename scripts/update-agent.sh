#!/usr/bin/env bash
# 宿主机更新代理。
#
# 面板容器没有 docker socket，自己更新不了自己——它只能往交换目录里写一个
# 「请求更新」的标记文件。这个脚本跑在宿主机上，看到标记就执行 update.sh。

if [ -z "${BASH_VERSION:-}" ]; then
  if command -v bash >/dev/null 2>&1; then
    exec bash "$0" "$@"
  elif command -v apk >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then
    apk add --no-cache bash >/dev/null 2>&1 || true
    command -v bash >/dev/null 2>&1 && exec bash "$0" "$@"
  fi
fi

set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTROL_DIR="${OCIX_CONTROL_DIR:-${REPO_ROOT}/var/control}"
POLL_SECONDS="${OCIX_AGENT_POLL:-5}"

REQUEST="${CONTROL_DIR}/update.request"
STATUS="${CONTROL_DIR}/update.status"
ALIVE="${CONTROL_DIR}/agent.alive"
LOGFILE="${CONTROL_DIR}/update.log"

mkdir -p "$CONTROL_DIR"

# 启动时立即写下第一次心跳
touch "$ALIVE" 2>/dev/null || true

write_status() {
  local state="$1" message="$2"
  local version; version="$(cat "${REPO_ROOT}/VERSION" 2>/dev/null || echo unknown)"
  local tmp="${STATUS}.$$.tmp"
  printf '{"state":"%s","message":"%s","version":"%s","started_at":%s,"finished_at":%s}\n' \
    "$state" "$message" "$version" "${STARTED_AT:-null}" "${FINISHED_AT:-null}" \
    > "$tmp" && mv -f "$tmp" "$STATUS"
}

run_update() {
  STARTED_AT="$(date +%s)"; FINISHED_AT=""
  : > "$LOGFILE"
  write_status "running" "正在更新…"

  if bash "${REPO_ROOT}/scripts/update.sh" --yes >>"$LOGFILE" 2>&1; then
    FINISHED_AT="$(date +%s)"
    write_status "done" "更新完成"
  else
    FINISHED_AT="$(date +%s)"
    write_status "failed" "更新失败，展开日志看具体原因"
  fi
}

echo "ocix 更新代理已启动，监视 ${CONTROL_DIR}（每 ${POLL_SECONDS}s 轮询一次）"

while true; do
  touch "$ALIVE" 2>/dev/null || true

  if [ -f "$REQUEST" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 收到更新请求：$(head -c 200 "$REQUEST" 2>/dev/null || true)"
    rm -f "$REQUEST"
    run_update
  fi

  sleep "$POLL_SECONDS"
done

