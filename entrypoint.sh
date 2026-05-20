#!/bin/bash
set -e

# CastFlow 容器入口
# 1. 启动 FastAPI 后端（后台）
# 2. 等后端健康检查通过
# 3. 启动 Streamlit 前端（前台）

echo "[CastFlow] Starting API server on :8000..."
cd /app
uvicorn api.server:app --host 0.0.0.0 --port 8000 &
API_PID=$!

echo "[CastFlow] Waiting for API health check..."
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8000/health > /dev/null 2>&1; then
        echo "[CastFlow] API is ready."
        break
    fi
    echo "[CastFlow] Waiting... ($i/30)"
    sleep 2
done

echo "[CastFlow] Starting Streamlit on :8501..."
streamlit run streamlit_app.py \
    --server.port 8501 \
    --server.headless true \
    --server.address 0.0.0.0 \
    --browser.gatherUsageStats false &

STREAMLIT_PID=$!

echo "[CastFlow] All services started."
echo "  API:       http://localhost:8000"
echo "  Dashboard: http://localhost:8501"
echo "  API Docs:  http://localhost:8000/docs"

# 如果 API 进程退出，整个容器退出
wait $API_PID
