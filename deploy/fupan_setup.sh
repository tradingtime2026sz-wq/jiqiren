#!/bin/bash
# 复盘这一侧（复盘会话维护）：公开仓库提交检查 + 私有复盘仓库 + 取回备注与复盘状态。可重复执行。
# 由 deploy/install_macstudio.sh 末尾调用，也可单独运行：bash deploy/fupan_setup.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PRIV="${JIQIREN_FUPAN_DIR:-$HOME/Codes/jiqiren-fupan}"

# 1. 提交前检查：jiqiren 是公开仓库，拦个人数据、报告、账户号、密钥
#    hooks 目录用 git 问（作为子模块检出时 .git 是文件，hooks 在上级仓库的 .git/modules/ 下）
HOOKS="$(git -C "$ROOT" rev-parse --git-path hooks)"
mkdir -p "$HOOKS"
install -m 755 "$ROOT/deploy/git-hooks/pre-commit" "$HOOKS/pre-commit"
echo "提交前检查：已装（$HOOKS）"

# 2. 私有复盘仓库（复盘含个人盈亏，只放私有仓库 jiqiren-fupan）
if ! gh auth status >/dev/null 2>&1; then
  echo "这台机器还没登录 GitHub：先运行 gh auth login（账户 tradingtime2026sz-wq），再重跑本脚本。"
  exit 1
fi
gh auth setup-git >/dev/null 2>&1 || true
if [ -e "$PRIV/.git" ]; then   # -e 而非 -d：子模块的 .git 是文件
  git -C "$PRIV" pull -q --rebase origin main
else
  gh repo clone tradingtime2026sz-wq/jiqiren-fupan "$PRIV" -- -q
fi
echo "私有复盘仓库：$PRIV"

# 3. 取回选票备注、复盘状态（防重报）和历史报告
"$ROOT/.venv/bin/python" -m tiger_review fupan --pull

# 4. 自检：老虎凭据、富途
"$ROOT/.venv/bin/python" -m tiger_review doctor || echo "自检没全过：看上面哪一项缺，补上后重跑。"
echo "复盘侧装好了。Studio 跑通一天后，告诉复盘会话停掉笔记本上的 com.vock.tiger-review-daily，避免两台都复盘。"
