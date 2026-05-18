"""FastAPI HTTP 入口（B8）。

提供两条路径：
- POST /forecast/stream  : SSE 流式返回 Agent 每一步事件（最常用）
- POST /forecast/run     : 阻塞式跑完返回最终摘要（CI / 自动化用）
- GET  /health           : 健康检查

启动：
    .venv/Scripts/uvicorn api.server:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from castflow.config import settings
from castflow.graph.orchestrator import build_graph
from castflow.tracing import maybe_get_langfuse_callback, trace_metadata

app = FastAPI(title="CastFlow API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局复用一个图实例（state via thread_id）
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


class ForecastRequest(BaseModel):
    org: str
    target_month: str
    target_mape: Optional[float] = None
    max_iterations: Optional[int] = None
    session_id: Optional[str] = None


def _make_initial(req: ForecastRequest) -> tuple[dict, dict]:
    target_mape = req.target_mape if req.target_mape is not None else settings.target_mape
    max_iter = req.max_iterations if req.max_iterations is not None else settings.max_iterations
    initial = {
        "goal": f"预测 {req.org} 区 {req.target_month} 月电力负荷，MAPE 不超过 {target_mape}%",
        "org": req.org,
        "target_month": req.target_month,
        "target_mape": float(target_mape),
        "max_iterations": int(max_iter),
        "iteration_count": 0,
        "messages": [
            HumanMessage(
                content=(
                    f"请预测【{req.org}】区 {req.target_month} 月的电力负荷，"
                    f"目标 MAPE ≤ {target_mape}%。"
                )
            )
        ],
        "iterations": [],
        "best_mape": 999.0,
        "best_code": "",
    }
    sid = req.session_id or f"{req.org}-{req.target_month}-{int(time.time())}"
    config: dict = {
        "configurable": {"thread_id": sid},
        "recursion_limit": 60,
    }
    handler = maybe_get_langfuse_callback()
    if handler is not None:
        config["callbacks"] = [handler]
        config.update(trace_metadata(req.org, req.target_month))
    return initial, config


def _serialize_msg(msg) -> dict:
    """把 LangGraph message 压缩成前端友好 JSON。"""
    mtype = getattr(msg, "type", "?")
    out: dict = {"type": mtype}
    if mtype == "ai":
        tcs = getattr(msg, "tool_calls", None) or []
        if tcs:
            out["tool_calls"] = [
                {"name": tc["name"], "args": tc.get("args", {})} for tc in tcs
            ]
        if getattr(msg, "content", None):
            out["content"] = msg.content[:1000]
    elif mtype == "tool":
        out["name"] = getattr(msg, "name", None)
        out["content"] = str(getattr(msg, "content", ""))[:1500]
    elif mtype == "human":
        out["content"] = str(getattr(msg, "content", ""))[:500]
    return out


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/forecast/run")
def forecast_run(req: ForecastRequest):
    """阻塞式跑完整流程，一次性返回最终结果。"""
    graph = get_graph()
    initial, config = _make_initial(req)
    final_state = None
    for event in graph.stream(initial, config=config, stream_mode="values"):
        final_state = event
    msgs = (final_state or {}).get("messages", [])
    return {
        "session_id": config["configurable"]["thread_id"],
        "iterations": (final_state or {}).get("iteration_count", 0),
        "best_mape": (final_state or {}).get("best_mape", 999.0),
        "messages": [_serialize_msg(m) for m in msgs],
    }


@app.post("/forecast/stream")
async def forecast_stream(req: ForecastRequest):
    """SSE 流式：每个新消息推一条 event。"""
    graph = get_graph()
    initial, config = _make_initial(req)

    async def gen():
        # 在线程池跑同步迭代器，避免阻塞 event loop
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def run():
            try:
                seen = 0
                for event in graph.stream(initial, config=config, stream_mode="values"):
                    msgs = event.get("messages", [])
                    for m in msgs[seen:]:
                        loop.call_soon_threadsafe(queue.put_nowait, ("msg", _serialize_msg(m)))
                    seen = len(msgs)
                loop.call_soon_threadsafe(queue.put_nowait, ("done", {
                    "iterations": event.get("iteration_count", 0) if event else 0,
                    "best_mape": event.get("best_mape", 999.0) if event else 999.0,
                }))
            except Exception as e:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, ("error", {"error": str(e)}))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("__end__", None))

        loop.run_in_executor(None, run)

        # 起手发一个 hello
        yield f"event: hello\ndata: {json.dumps({'session_id': config['configurable']['thread_id']})}\n\n"

        while True:
            kind, payload = await queue.get()
            if kind == "__end__":
                break
            yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
