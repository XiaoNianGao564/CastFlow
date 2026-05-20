from __future__ import annotations

import asyncio
import time
from threading import Thread
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from api.schemas import ApprovalRequest, FeedbackRequest, ForecastRequest, RunResponse
from castflow.config import settings
from castflow.graph.orchestrator import build_graph
from castflow.ui.events import dump_json, make_event, serialize_message, summarize_state, summarize_tool_call
from castflow.ui.session_store import STORE, RunSession

app = FastAPI(title="CastFlow API", version="0.2.0")

import os

allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _make_initial(req: ForecastRequest) -> tuple[dict[str, Any], dict[str, Any], str]:
    target_mape = req.target_mape if req.target_mape is not None else settings.target_mape
    max_iter = req.max_iterations if req.max_iterations is not None else settings.max_iterations
    thread_id = req.session_id or f"{req.org}-{req.target_month}-{int(time.time())}"

    # 支持年份模式：纯4位数字表示全年，YYYY-MM 表示单月
    tm = req.target_month.strip()
    if len(tm) == 4 and tm.isdigit():
        year = tm
        target_months = [f"{year}-{m:02d}" for m in range(1, 13)]
        period_desc = f"{year} 年全年（1-12月）"
        year_hint = (
            f"load_history 的 before_month 传 \"{target_months[0]}\"，"
            f"Coder 生成的代码末尾 print 的 JSON 必须包含 12 个 predictions。"
        )
    else:
        target_months = [tm]
        period_desc = f"{tm} 月"
        year_hint = ""

    default_prompt = (
        f"请预测【{req.org}】区 {period_desc} 的电力负荷，目标 MAPE ≤ {target_mape}%。"
        f"\n目标月份列表：{target_months}"
        + (f"\n{year_hint}" if year_hint else "")
    )
    if req.prompt:
        human_prompt = (
            f"区县={req.org}，目标={period_desc}，MAPE≤{target_mape}%，目标月份列表={target_months}。"
            + (f"\n{year_hint}" if year_hint else "")
            + f"\n用户补充要求：{req.prompt}"
        )
    else:
        human_prompt = default_prompt
    initial = {
        "goal": f"预测 {req.org} 区 {period_desc} 电力负荷，MAPE 不超过 {target_mape}%",
        "org": req.org,
        "target_month": req.target_month,
        "target_months": target_months,
        "target_mape": float(target_mape),
        "max_iterations": int(max_iter),
        "iteration_count": 0,
        "messages": [HumanMessage(content=human_prompt)],
        "iterations": [],
        "best_mape": 999.0,
        "best_code": "",
        "plan_history": [],
        "run_status": "running",
        "approval_history": [],
        "ui_events": [],
        "require_approval": bool(req.require_approval),
    }
    config = {
        "configurable": {"thread_id": thread_id},
        # 全年模式更多递归（12个月）
        "recursion_limit": 200 if len(target_months) > 1 else 60,
    }
    return initial, config, thread_id


def _tool_risk(name: str) -> str:
    if name in {"delegate_to_coder", "delegate_to_reflector", "save_lesson", "finalize"}:
        return "high"
    if name in {"run_python", "delegate_to_verifier", "submit_candidate", "evaluate_mape", "load_actual"}:
        return "medium"
    return "low"


def _requires_approval(name: str, require_approval: bool) -> bool:
    always_approve = {"finalize", "submit_candidate"}
    safe_tools = {
        "list_orgs",
        "load_history",
        "recall_similar_runs",
        "recall_lessons",
        "search_knowledge_docs",
        "run_python",
        "delegate_to_coder",
        "delegate_to_reflector",
        "delegate_to_verifier",
        "evaluate_mape",
    }
    if name in always_approve:
        return True
    if require_approval:
        return name not in safe_tools
    return False


def _wait_for_approval(session: RunSession, decision: dict[str, Any]) -> bool:
    session.mark_waiting(decision)
    session.append_event(make_event("approval_required", session.thread_id, pending_decision=decision))
    max_wait_time = 3600
    elapsed = 0
    while session.waiting_for_user and not session.cancelled and elapsed < max_wait_time:
        with session.cond:
            session.cond.wait(timeout=0.3)
        elapsed += 0.3
    if elapsed >= max_wait_time:
        session.append_event(make_event("approval_timeout", session.thread_id, decision_id=decision.get("id")))
        return False
    if session.cancelled:
        return False
    latest = session.approvals[-1] if session.approvals else {}
    return bool(latest.get("approved", True))


def _finalize_args(state: dict[str, Any] | None) -> dict[str, Any]:
    for msg in reversed((state or {}).get("messages", [])):
        if getattr(msg, "type", None) != "ai":
            continue
        for tool_call in getattr(msg, "tool_calls", None) or []:
            if tool_call.get("name") == "finalize":
                args = tool_call.get("args") or {}
                return args if isinstance(args, dict) else {}
    return {}


def _runner(session: RunSession) -> None:
    graph = get_graph()
    require_approval = bool(session.initial_state.get("require_approval", False))

    # 注入 run_python 实时输出回调：每行 stdout/stderr 推送为 SSE 事件
    from castflow.tools.python_exec import set_output_callback

    def _on_python_output(line: str):
        if line:
            session.append_event(make_event(
                "python_output", session.thread_id, line=line[:500]
            ))

    set_output_callback(_on_python_output)

    try:
        session.status = "running"
        session.append_event(make_event("run_started", session.thread_id, org=session.initial_state.get("org"), target_month=session.initial_state.get("target_month")))
        session.append_event(summarize_state(session.state, thread_id=session.thread_id, status="running"))

        from castflow.tracing.langfuse_setup import maybe_get_langfuse_callback
        langfuse_cb = maybe_get_langfuse_callback()
        if langfuse_cb:
            session.config.setdefault("callbacks", []).append(langfuse_cb)

        final_state = None
        seen = 0
        for event in graph.stream(session.initial_state, config=session.config, stream_mode="values"):
            if session.cancelled:
                break
            final_state = event
            session.state = dict(event or {})
            msgs = (event or {}).get("messages", [])
            for msg in msgs[seen:]:
                serialized = serialize_message(msg)
                session.append_message(serialized)
                session.append_event(make_event("message", session.thread_id, message=serialized))
                if serialized.get("type") == "ai":
                    raw_tool_calls = getattr(msg, "tool_calls", None) or []
                    for raw_tc in raw_tool_calls:
                        tool_name = raw_tc.get("name", "")
                        risk = _tool_risk(tool_name)
                        session.append_event(summarize_tool_call(raw_tc, thread_id=session.thread_id, risk_level=risk))
                        if _requires_approval(tool_name, require_approval):
                            decision = {
                                "id": raw_tc.get("id") or f"{tool_name}-{int(time.time() * 1000)}",
                                "kind": "tool_call",
                                "tool_name": tool_name,
                                "tool_args": raw_tc.get("args", {}),
                                "summary": f"待审批：{tool_name}",
                                "risk_level": risk,
                                "status": "pending",
                            }
                            approved = _wait_for_approval(session, decision)
                            session.clear_waiting()
                            if not approved:
                                session.status = "cancelled"
                                session.cancelled = True
                                session.append_event(make_event("run_cancelled", session.thread_id, reason="用户拒绝高风险动作"))
                                break
                session.append_event(summarize_state(session.state, thread_id=session.thread_id, status=session.status))
                if session.cancelled:
                    break
            seen = len(msgs)
            if session.cancelled:
                break
        final_args = _finalize_args(final_state)
        if session.cancelled:
            session.status = "cancelled"
            session.final_result = {
                "iterations": (final_state or {}).get("iteration_count", 0),
                "best_mape": final_args.get("best_mape", (final_state or {}).get("best_mape", 999.0)),
                "best_model": (final_state or {}).get("best_model"),
                "best_code": final_args.get("best_code", (final_state or {}).get("best_code", "")),
                "reason": final_args.get("reason") or (final_state or {}).get("final_reason", "用户取消"),
                "status": "cancelled",
            }
            return
        session.final_result = {
            "iterations": (final_state or {}).get("iteration_count", 0),
            "best_mape": final_args.get("best_mape", (final_state or {}).get("best_mape", 999.0)),
            "best_code": final_args.get("best_code", (final_state or {}).get("best_code", "")),
            "best_model": (final_state or {}).get("best_model"),
            "reason": final_args.get("reason") or (final_state or {}).get("final_reason", "任务结束"),
            "status": "completed",
        }
        session.status = "completed"
        session.append_event(make_event("run_completed", session.thread_id, **session.final_result))
    except Exception as e:  # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        print(f"[CastFlow Runner ERROR] {tb}", flush=True)
        session.error = str(e)
        session.status = "failed"
        session.append_event(make_event("run_failed", session.thread_id, error=f"{e}\n{tb}"))
    finally:
        with session.lock:
            session.cond.notify_all()


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.2.0"}


@app.on_event("startup")
def _startup_scheduler():
    if settings.scheduler_enabled:
        from castflow.scheduler import ActualValueScheduler
        _scheduler = ActualValueScheduler()
        _scheduler.start()
        app.state.scheduler = _scheduler


@app.post("/runs", response_model=RunResponse)
def create_run(req: ForecastRequest):
    initial, config, thread_id = _make_initial(req)
    session = STORE.create(thread_id, config, initial)
    session.status = "starting"
    session.append_event(make_event("run_created", thread_id, org=req.org, target_month=req.target_month))
    thread = Thread(target=_runner, args=(session,), daemon=True)
    thread.start()
    return RunResponse(thread_id=thread_id, status=session.status, stream_url=f"/runs/{thread_id}/events")


@app.get("/runs/{thread_id}")
def get_run(thread_id: str):
    session = STORE.get(thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="run not found")
    return session.snapshot()


@app.get("/runs/{thread_id}/events")
async def stream_run_events(thread_id: str, after: int = 0):
    session = STORE.get(thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="run not found")

    async def gen():
        idx = max(0, after)
        last_heartbeat = time.time()
        yield f"event: hello\ndata: {dump_json({'thread_id': thread_id, 'after': idx})}\n\n"
        while True:
            heartbeat = None
            new_events = []
            status = None
            error = None
            final_result = None
            with session.lock:
                if idx < len(session.events):
                    new_events = list(session.events)[idx:]
                    idx = len(session.events)
                elif session.status not in {"running", "starting", "waiting_for_user"}:
                    new_events = []
                else:
                    now = time.time()
                    if now - last_heartbeat >= 5:
                        last_heartbeat = now
                        heartbeat = make_event(
                            "run_heartbeat",
                            thread_id,
                            status=session.status,
                            message="Agent 仍在运行，正在等待模型或工具返回。",
                        )
                status = session.status
                error = session.error
                final_result = session.final_result
            if heartbeat is not None:
                yield f"event: run_heartbeat\ndata: {dump_json(heartbeat)}\n\n"
            for ev in new_events:
                yield f"event: {ev['type']}\ndata: {dump_json(ev)}\n\n"
            if status in {"completed", "failed", "cancelled"}:
                payload = {"thread_id": thread_id, "status": status, "error": error, "final_result": final_result}
                yield f"event: done\ndata: {dump_json(payload)}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/runs/{thread_id}/approve")
def approve_run(thread_id: str, req: ApprovalRequest):
    session = STORE.get(thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="run not found")
    approval = {
        "decision_id": req.decision_id,
        "approved": req.approved,
        "feedback": req.feedback,
        "edited_args": req.edited_args or {},
    }
    # 记录被审批的工具名（便于前端关联展示）
    pending = session.pending_decision or {}
    approval["tool_name"] = pending.get("tool_name", "")
    session.approvals.append(approval)
    session.append_event(make_event("approval_submitted", thread_id, approval=approval))
    if req.approved:
        session.clear_waiting()
        session.status = "running"
    else:
        session.cancelled = True
        session.status = "cancelled"
    with session.lock:
        session.cond.notify_all()
    return {"thread_id": thread_id, "status": session.status, "approved": req.approved}


@app.post("/runs/{thread_id}/feedback")
def submit_feedback(thread_id: str, req: FeedbackRequest):
    session = STORE.get(thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="run not found")
    payload = {"message": req.message, "action": req.action}
    session.feedback.append(payload)
    session.append_event(make_event("feedback_submitted", thread_id, feedback=payload))
    if req.action == "pause":
        session.status = "waiting_for_user"
    elif req.action == "resume":
        session.clear_waiting()
        session.status = "running"
    elif req.action == "stop":
        session.cancelled = True
        session.status = "cancelled"
    with session.lock:
        session.cond.notify_all()
    return {"thread_id": thread_id, "status": session.status}


@app.post("/runs/{thread_id}/resume")
def resume_run(thread_id: str):
    session = STORE.get(thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="run not found")
    session.clear_waiting()
    session.status = "running"
    session.append_event(make_event("run_resumed", thread_id))
    with session.lock:
        session.cond.notify_all()
    return {"thread_id": thread_id, "status": session.status}


@app.post("/runs/{thread_id}/cancel")
def cancel_run(thread_id: str):
    session = STORE.get(thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="run not found")
    session.cancelled = True
    session.status = "cancelled"
    session.append_event(make_event("run_cancelled", thread_id))
    with session.lock:
        session.cond.notify_all()
    return {"thread_id": thread_id, "status": session.status}


# --------------- Scheduler Control API ---------------

@app.get("/scheduler/status")
def scheduler_status():
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is None:
        return {
            "running": False,
            "enabled_in_env": settings.scheduler_enabled,
            "interval_sec": settings.scheduler_interval_sec,
            "target_months": [],
            "triggered_count": 0,
            "triggered_records": {},
        }
    return {
        "running": scheduler.is_running,
        "enabled_in_env": settings.scheduler_enabled,
        "interval_sec": settings.scheduler_interval_sec,
        "target_months": scheduler._get_target_months(),
        "triggered_count": scheduler.triggered_count,
        "triggered_records": scheduler.triggered_records,
    }


@app.post("/scheduler/start")
def scheduler_start():
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is None:
        from castflow.scheduler import ActualValueScheduler
        scheduler = ActualValueScheduler()
        app.state.scheduler = scheduler
    if scheduler.is_running:
        return {"status": "already_running"}
    scheduler.start()
    return {"status": "started"}


@app.post("/scheduler/stop")
def scheduler_stop():
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is None or not scheduler.is_running:
        return {"status": "not_running"}
    scheduler.stop()
    return {"status": "stopped"}


@app.post("/scheduler/reset")
def scheduler_reset():
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is None:
        from castflow.scheduler import ActualValueScheduler
        scheduler = ActualValueScheduler()
        app.state.scheduler = scheduler
    scheduler.reset_state()
    return {"status": "reset", "triggered_count": 0}


@app.delete("/scheduler/triggered/{key}")
def scheduler_remove_triggered(key: str):
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=404, detail="scheduler not initialized")
    removed = scheduler.remove_triggered(key)
    if not removed:
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    return {"status": "removed", "key": key}


@app.post("/forecast/run")
def forecast_run(req: ForecastRequest):
    initial, config, thread_id = _make_initial(req)
    graph = get_graph()
    final_state = None
    for event in graph.stream(initial, config=config, stream_mode="values"):
        final_state = event
    messages = (final_state or {}).get("messages", [])
    return {
        "session_id": thread_id,
        "iterations": (final_state or {}).get("iteration_count", 0),
        "best_mape": (final_state or {}).get("best_mape", 999.0),
        "messages": [serialize_message(m) for m in messages],
    }


@app.post("/forecast/stream")
async def forecast_stream(req: ForecastRequest):
    initial, config, thread_id = _make_initial(req)
    graph = get_graph()

    async def gen():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def run():
            try:
                seen = 0
                last_event = None
                for event in graph.stream(initial, config=config, stream_mode="values"):
                    last_event = event
                    msgs = event.get("messages", [])
                    for msg in msgs[seen:]:
                        loop.call_soon_threadsafe(queue.put_nowait, ("msg", serialize_message(msg)))
                    seen = len(msgs)
                loop.call_soon_threadsafe(queue.put_nowait, ("done", {
                    "iterations": (last_event or {}).get("iteration_count", 0),
                    "best_mape": (last_event or {}).get("best_mape", 999.0),
                }))
            except Exception as e:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, ("error", {"error": str(e)}))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("__end__", None))

        loop.run_in_executor(None, run)
        yield f"event: hello\ndata: {dump_json({'session_id': thread_id})}\n\n"
        while True:
            kind, payload = await queue.get()
            if kind == "__end__":
                break
            yield f"event: {kind}\ndata: {dump_json(payload)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
