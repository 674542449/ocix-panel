#!/usr/bin/env bash
# 提版本号 → 打 tag → 推 GitHub。每次推送都过一遍版本号，避免线上分不清跑的是哪一版。
#
#   bash scripts/release.sh            # 补丁位 +1 (0.3.0 -> 0.3.1)
#   bash scripts/release.sh minor      # 次版本 +1 (0.3.1 -> 0.4.0)
#   bash scripts/release.sh major      # 主版本 +1 (0.4.0 -> 1.0.0)
#   bash scripts/release.sh 1.2.3      # 指定版本号
#   bash scripts/release.sh -m "说明"  # 自定义提交信息
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BUMP="patch"; MESSAGE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    major|minor|patch) BUMP="$1"; shift ;;
    -m|--message) MESSAGE="${2:-}"; shift 2 ;;
    [0-9]*.[0-9]*.[0-9]*) BUMP="$1"; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

git rev-parse --git-dir >/dev/null 2>&1 || { echo "当前目录不是 git 仓库" >&2; exit 1; }

CURRENT="$(cat VERSION 2>/dev/null || echo 0.0.0)"
IFS='.' read -r MA MI PA <<< "$CURRENT"

case "$BUMP" in
  major) NEW="$((MA+1)).0.0" ;;
  minor) NEW="${MA}.$((MI+1)).0" ;;
  patch) NEW="${MA}.${MI}.$((PA+1))" ;;
  *)     NEW="$BUMP" ;;
esac

[[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "版本号不合法: $NEW" >&2; exit 1; }
if git rev-parse "v$NEW" >/dev/null 2>&1; then
  echo "tag v$NEW 已存在，换一个版本号" >&2; exit 1
fi

echo "$NEW" > VERSION
echo "版本号 ${CURRENT} → ${NEW}"

# 版本号变了就跑一遍测试，别把坏版本推上去。
# 挑解释器不能只看 `command -v python3`：Windows 上那是应用商店的占位程序，
# 存在但跑什么都失败，于是这里会一路判成「没装 pytest」，
# 测试守卫等于形同虚设——要挑真的能 import pytest 的那个。
PY=""
for cand in "${OCIX_TEST_PYTHON:-}" python3 python py; do
  [[ -n "$cand" ]] || continue
  command -v "$cand" >/dev/null 2>&1 || continue
  if PYTHONPATH="${OCIX_TEST_PYTHONPATH:-src}" "$cand" -c "import pytest" >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done

if [[ -n "$PY" ]]; then
  echo "跑测试（$PY）…"
  # 依赖装在别处时用 OCIX_TEST_PYTHONPATH 指过去，否则这里会因为缺包而误报失败
  PYTHONPATH="${OCIX_TEST_PYTHONPATH:-src}" "$PY" -m pytest -q \
    || { echo "$CURRENT" > VERSION; echo "测试没过，版本号已还原" >&2; exit 1; }
else
  echo "（找不到装了 pytest 的 python，跳过测试）"
fi

git add -A
if git diff --cached --quiet; then
  echo "没有任何改动，不产生提交"; exit 0
fi

git commit -m "${MESSAGE:-release: v$NEW}"
git tag -a "v$NEW" -m "v$NEW"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git push origin "$BRANCH"
git push origin "v$NEW"

echo "已推送 v${NEW}（分支 ${BRANCH}）"
