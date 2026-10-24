#!/bin/bash
# 在新机器（Mac Studio）上装好 tiger-review 的三个定时任务。可重复执行。
# 用法：git clone 后，在仓库目录运行  bash deploy/install_macstudio.sh
# 不碰凭据：~/.config/tiger/ 与 ~/.config/telegram/ 要你自己从旧机器拷过来（目录 700、文件 600）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OLD="/Users/vock/Codes/tiger-review"
AGENTS="$HOME/Library/LaunchAgents"
echo "仓库路径：$ROOT"

# 1. Python 环境
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  if command -v uv >/dev/null 2>&1; then uv venv --python 3.12 "$ROOT/.venv"; else python3 -m venv "$ROOT/.venv"; fi
fi
"$ROOT/.venv/bin/python" -m pip install -q --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -q -r "$ROOT/requirements.txt"
mkdir -p "$ROOT/data/logs" "$ROOT/data/raw" "$ROOT/reports"

# 2. 定时任务：把模板里的旧路径换成本机路径，装进 ~/Library/LaunchAgents
mkdir -p "$AGENTS"
for f in "$ROOT"/deploy/launchd/*.plist; do
  name="$(basename "$f")"
  sed "s#$OLD#$ROOT#g" "$f" > "$AGENTS/$name"
  launchctl bootout "gui/$(id -u)/${name%.plist}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/$name"
  echo "已装：${name%.plist}"
done

# 3. 自检
echo "--- 自检 ---"
echo "时区：$(date +%Z)（必须是 EDT/EST，不是就去系统设置改成 New York）"
for d in "$HOME/.config/tiger" "$HOME/.config/telegram"; do
  if [ -d "$d" ]; then echo "凭据目录 OK：$d"; else echo "缺凭据目录：$d（从旧机器拷过来）"; fi
done
if nc -z -w 2 127.0.0.1 11111 2>/dev/null; then echo "富途 OpenD：在跑"; else echo "富途 OpenD：没在跑（装好并登录，端口 11111）"; fi
echo "任务："; launchctl list | grep -E 'tiger-review|card-telegram' || true
echo "装完后在旧机器上执行 bootout 停掉同名任务，避免两台都跑。试跑：$ROOT/.venv/bin/python -m tiger_review alerts --dry-run --once"
