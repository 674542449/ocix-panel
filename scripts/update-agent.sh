#!/usr/bin/env bash
# 宿主机更新代理。
#
# 面板容器没有 docker socket，自己更新不了自己——它只能往交换目录里写一个
# 「请求更新」的标记文件。这个脚本跑在宿主机上，看到标记就执行 update.sh。
#
# 安全要点：本脚本**只执行固定的 update.sh**，请求文件的内容除了记进日志之外
# 不会以任何形式进入命令行。哪怕面板被攻破、请求文件被写成任意内容，
# 攻击者能做的也仅仅是触发一次正常的更新。
#
# 由 systemd 常驻运行，见 deploy/ocix-updater.service。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTROL_DIR="${OCIX_CONTROL_DIR:-${REPO_ROOT}/var/control}"
POLL_SECONDS="${OCIX_AGENT_POLL:-10}"

REQUEST="${CONTROL_DIR}/update.request"
STATUS="${CONTROL_DIR}/update.status"
ALIVE="${CONTROL_DIR}/agent.alive"
LOGFILE="${CONTROL_DIR}/update.log"

mkdir -p "$CONTROL_DIR"

# 写状态文件给面板读。
#
# 这里**只写受控的简单值**（状态、固定文案、版本号、时间戳），不往 JSON 里塞日志：
# update.sh 的输出带 ANSI 颜色码、制表符、引号、反斜杠，用 shell 去做 JSON 转义
# 又脆又难验证（试过，栽在制表符和 sed 的转义差异上）。
# 日志原样留在 update.log 里，由面板那边读取——Python 的 json 编码器
# 处理任意字节都不会出错，问题从根上就不存在了。
write_status() {
  local state="$1" message="$2"
  local version; version="$(cat "${REPO_ROOT}/VERSION" 2>/dev/null || echo unknown)"
  # 先写临时文件再原子改名，避免面板正好读到写了一半的内容。
  # 临时文件名带上 PID：万一有人手工又跑了一个代理，两个进程用同一个
  # .tmp 会互相抢，其中一个的 mv 直接失败（本地测试就撞上过）。
  local tmp="${STATUS}.$$.tmp"
  printf '{"state":"%s","message":"%s","version":"%s","started_at":%s,"finished_at":%s}\n' \
    "$state" "$message" "$version" "${STARTED_AT:-null}" "${FINISHED_AT:-null}" \
    > "$tmp" && mv -f "$tmp" "$STATUS"
}

run_update() {
  STARTED_AT="$(date +%s)"; FINISHED_AT=""
  : > "$LOGFILE"
  write_status "running" "正在更新…"

  # 只跑这一条固定命令；--yes 让它在没有终端的情况下也能走完
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
  # 心跳：面板据此判断代理在不在，不在就直接提示而不是让用户干等
  touch "$ALIVE" 2>/dev/null || true

  if [[ -f "$REQUEST" ]]; then
    echo "[$(date '+%F %T')] 收到更新请求：$(cat "$REQUEST" 2>/dev/null | head -c 200)"
    # 先删请求再执行：update.sh 会重启面板容器，
    # 留着的话下一轮会被当成新请求，无限重复更新
    rm -f "$REQUEST"
    run_update
  fi

  sleep "$POLL_SECONDS"
done
