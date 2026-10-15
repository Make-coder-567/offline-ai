#!/usr/bin/env bash
# 启动离线AI智能体（独立 agent 循环，监听 inbox）
# 用法: bash start_agent.sh
set -e
PY="$HOME/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3 || command -v python)"
fi
"$PY" "$(dirname "$0")/server/agent.py"
