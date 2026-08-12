#!/usr/bin/env bash
# 在线更新：拉最新代码 → 重新构建 → 重启。配置和数据都不动。
#
#   bash /opt/ocix/scripts/update.sh          更新到最新版
#   bash /opt/ocix/scripts/update.sh --check  只看有没有新版本，不动手
#   bash /opt/ocix/scripts/update.sh --yes    不交互，本地改动直接丢弃（更新代理用这个）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_red=$'\033[31m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
ok()   { printf '%s✓%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s!%s %s\n' "$c_ylw" "$c_off" "$*"; }
die()  { printf '%s✗ %s%s\n' "$c_red" "$*" "$c_off" >&2; exit 1; }

CHECK_ONLY=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
  esac
done

git rev-parse --git-dir >/dev/null 2>&1 || die "${REPO_ROOT} 不是 git 仓库，没法自动更新。请重新 git clone 一份。"

CURRENT="$(cat VERSION 2>/dev/null || echo unknown)"
echo "当前版本 v${CURRENT}"

echo "检查远端…"
git fetch --tags --quiet origin || die "拉取远端失败，检查网络或 git 凭据"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
LATEST="$(git show "origin/${BRANCH}:VERSION" 2>/dev/null | tr -d '[:space:]' || echo "")"
BEHIND="$(git rev-list --count "HEAD..origin/${BRANCH}" 2>/dev/null || echo 0)"

if [[ "$BEHIND" == "0" ]]; then
  ok "已经是最新版了（v${CURRENT}）"
  exit 0
fi

echo "远端版本 v${LATEST:-未知}，落后 ${BEHIND} 个提交"
echo "${c_dim}"
git --no-pager log --oneline --no-decorate "HEAD..origin/${BRANCH}" | head -15
echo "${c_off}"

if [[ $CHECK_ONLY -eq 1 ]]; then
  echo "有新版本可用。执行以下命令更新："
  echo "  bash ${REPO_ROOT}/scripts/update.sh"
  exit 0
fi

# 本地改动会挡住 pull，提前说清楚
if ! git diff --quiet || ! git diff --cached --quiet; then
  warn "检测到本地有未提交的改动："
  git --no-pager status --short
  if [[ $ASSUME_YES -eq 1 ]]; then
    warn "--yes 已指定，丢弃本地改动继续"
  else
    # 没有终端时 read 会直接 EOF，别把脚本卡在这儿
    if [[ ! -t 0 ]]; then
      die "有本地改动且当前不是交互终端。确认要丢弃就加 --yes 重跑。"
    fi
    read -r -p "丢弃这些改动并继续更新？[y/N]: " a
    [[ "$a" == "y" || "$a" == "Y" ]] || die "已取消。请先处理本地改动。"
  fi
  git reset --hard HEAD
fi

echo "拉取最新代码…"
git pull --ff-only origin "$BRANCH" || die "拉取失败（可能有冲突）。可执行 git reset --hard origin/${BRANCH} 后重试。"

NEW="$(cat VERSION 2>/dev/null || echo unknown)"
ok "代码已更新到 v${NEW}"

# .env 里的版本号跟着走，compose 的镜像 tag 才对得上
if [[ -f .env ]]; then
  if grep -q '^OCIX_VERSION=' .env; then
    sed -i.bak "s/^OCIX_VERSION=.*/OCIX_VERSION=${NEW}/" .env && rm -f .env.bak
  else
    echo "OCIX_VERSION=${NEW}" >> .env
  fi
fi

echo "重新构建并重启…"
bash "$REPO_ROOT/scripts/ocix.sh" up -d --build

printf '等待后端就绪'
for _ in $(seq 1 60); do
  if bash "$REPO_ROOT/scripts/ocix.sh" exec -T backend \
       curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then
    printf '\n'; ok "OCIX 已更新到 v${NEW} 并重新启动"
    exit 0
  fi
  printf '.'; sleep 2
done
printf '\n'
warn "更新完成了，但后端健康检查没通过。看日志： bash ${REPO_ROOT}/scripts/ocix.sh logs backend"
exit 1
