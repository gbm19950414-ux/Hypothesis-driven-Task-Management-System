#!/bin/bash
set -euo pipefail

# =========================
# 配置区（按仓库各自改一次）
# =========================

# 源盘仓库路径（你现在所在的也可以用 pwd）
SRC_REPO="/Volumes/Samsung_SSD_990_PRO_2TB_Media/EphB1"

# 旅行盘对应仓库路径（注意你现在有双层 Samsung_SSD...）
TRAVEL_REPO="/Volumes/旅游/backup/Samsung_SSD_990_PRO_2TB_Media/Samsung_SSD_990_PRO_2TB_Media/EphB1"

# 旅行盘 remote 名称
REMOTE_NAME="travel"

# 你要合并的分支名（通常是 main 或 master）
BRANCH_NAME="main"

# =========================
# 安全检查
# =========================

echo "== Checking source repo =="
cd "$SRC_REPO"
if [ ! -d ".git" ]; then
  echo "ERROR: $SRC_REPO is not a git repository"
  exit 1
fi

echo "== Checking travel repo path =="
if [ ! -d "$TRAVEL_REPO/.git" ]; then
  echo "ERROR: $TRAVEL_REPO is not a git repository"
  exit 1
fi

# =========================
# 确保 remote 存在且指向正确路径
# =========================

if git remote | grep -q "^${REMOTE_NAME}$"; then
  echo "Remote '$REMOTE_NAME' already exists. Updating URL..."
  git remote set-url "$REMOTE_NAME" "$TRAVEL_REPO"
else
  echo "Adding remote '$REMOTE_NAME' -> $TRAVEL_REPO"
  git remote add "$REMOTE_NAME" "$TRAVEL_REPO"
fi

echo "== Fetching from travel =="
git fetch "$REMOTE_NAME"

# =========================
# 展示差异（双方各自独有的提交）
# =========================

echo
echo "== Commits only in SOURCE (<) and only in TRAVEL (>) =="
git log --left-right --oneline --graph "HEAD...${REMOTE_NAME}/${BRANCH_NAME}"

echo
read -p "Do you want to merge ${REMOTE_NAME}/${BRANCH_NAME} into current branch? (y/N) " ans
if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
  echo "Aborted. No changes made."
  exit 0
fi

# =========================
# 执行合并
# =========================

echo "== Merging =="
git merge "${REMOTE_NAME}/${BRANCH_NAME}"

echo
echo "== Done! If there were conflicts, please resolve them and commit. =="