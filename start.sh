#!/bin/bash
# 大明王朝·朝堂风云 — 一键启动

set -e

echo "=== 大明王朝·朝堂风云 ==="
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
    echo "错误：需要 Python 3.11+"
    exit 1
fi
PYTHON=$(command -v python3 || command -v python)

# 检查 Node
if ! command -v node &> /dev/null; then
    echo "错误：需要 Node.js 18+"
    exit 1
fi

# 检查 LLM API Key
if [ -z "$LLM_API_KEY" ]; then
    echo "警告：未设置 LLM_API_KEY 环境变量"
    echo "  export LLM_API_KEY=your-api-key"
    echo ""
fi

# 安装 Python 依赖
echo "安装 Python 依赖..."
$PYTHON -m pip install -r requirements.txt -q

# 安装前端依赖
echo "安装前端依赖..."
cd frontend && npm install --silent && cd ..

# 启动后端
echo "启动后端 (localhost:8000)..."
$PYTHON -m uvicorn main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# 启动前端
echo "启动前端 (localhost:3000)..."
cd frontend && npm run dev -- --host &
FRONTEND_PID=$!

echo ""
echo "后端：http://localhost:8000"
echo "前端：http://localhost:3000"
echo ""
echo "按 Ctrl+C 停止"

# 等待退出
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" SIGINT SIGTERM
wait
