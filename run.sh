#!/usr/bin/env bash
# 一条本地命令：初始化/播种演示数据（含次日雨天停园样例）并启动服务。
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

# 首次运行自动播种；已有数据库时不覆盖
python3 -m app.seed

echo
echo "启动中… 浏览器打开 http://${HOST}:${PORT}"
echo "  游客端 http://${HOST}:${PORT}/   现场端 http://${HOST}:${PORT}/gate   管理端 http://${HOST}:${PORT}/admin"
exec python3 -m app.server --host "$HOST" --port "$PORT"
