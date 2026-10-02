#!/usr/bin/env bash
# 一条本地命令启动阳光果园采摘券演示系统（含雨天停园样例）。
# 用法：
#   ./run.sh            启动（首次自动建库+播种；已有数据保留）
#   ./run.sh --reset    清库重建演示数据后启动
#   PORT=8123 ./run.sh  指定端口
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

echo "🌱 初始化演示数据（作物/园区/票种/当日券 + 后天雨天全园停园样例）..."
python3 -m app.seed "$@" >/dev/null

echo "▶️  启动服务 http://${HOST}:${PORT}"
echo "    游客端 /  |  现场端 /onsite  |  园区图 /map  |  管理端 /admin"
exec python3 -m app.server --host "$HOST" --port "$PORT"
