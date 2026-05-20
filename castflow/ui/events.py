from __future__ import annotations

import ast
import json
import uuid
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_event_id() -> str:
    return uuid.uuid4().hex


def make_event(event_type: str, thread_id: str, **fields: Any) -> dict[str, Any]:
    payload = {
        "id": new_event_id(),
        "type": event_type,
        "thread_id": thread_id,
        "ts": utc_now_iso(),
    }
    payload.update(fields)
    return payload


def _strip_code_from_payload(payload: Any) -> Any:
    """从 submit_candidate / delegate_to_coder 的返回值中剥离 code 字段，
    避免序列化后超长导致 JSON 截断。图表只需指标数据，不需要完整代码。"""
    if not isinstance(payload, dict):
        return payload
    result = {k: v for k, v in payload.items() if k != "code"}
    if "data" in result and isinstance(result["data"], dict):
        result["data"] = {k: v for k, v in result["data"].items() if k != "code"}
    return result


def _clip(value: Any, limit: int = 1200) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, list):
        return [_clip(item, limit=limit) for item in value]
    if isinstance(value, dict):
        return {key: _clip(val, limit=limit) for key, val in value.items()}
    return value


def serialize_message(msg: Any) -> dict[str, Any]:
    mtype = getattr(msg, "type", "?")
    out: dict[str, Any] = {"type": mtype}
    if mtype == "ai":
        tool_calls = getattr(msg, "tool_calls", None) or []
        if tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.get("id"),
                    "name": tc.get("name"),
                    "args": _clip(tc.get("args", {}), limit=500),
                }
                for tc in tool_calls
            ]
        content = getattr(msg, "content", None)
        if content:
            out["content"] = _clip(str(content), limit=2000)
    elif mtype == "tool":
        out["name"] = getattr(msg, "name", None)
        tool_name = out["name"] or ""
        # 关键工具（评估/候选）的返回值不截断，确保前端图表数据完整
        if tool_name in {"evaluate_mape", "delegate_to_coder", "submit_candidate"}:
            limit = 20000
        else:
            limit = 3000
        raw_content = getattr(msg, "content", "")
        if isinstance(raw_content, (dict, list)):
            if tool_name in {"submit_candidate", "delegate_to_coder"}:
                raw_content = _strip_code_from_payload(raw_content)
            raw_content = json.dumps(raw_content, ensure_ascii=False, default=str)
        elif isinstance(raw_content, str):
            # 尝试将所有工具的字符串内容规范化为合法 JSON
            # LangGraph 可能用 Python repr (True/False/None) 序列化 dict
            try:
                parsed = json.loads(raw_content)
                if isinstance(parsed, dict) and tool_name in {"submit_candidate", "delegate_to_coder"}:
                    parsed = _strip_code_from_payload(parsed)
                raw_content = json.dumps(parsed, ensure_ascii=False, default=str)
            except (json.JSONDecodeError, TypeError):
                try:
                    parsed = ast.literal_eval(raw_content)
                    if isinstance(parsed, dict):
                        if tool_name in {"submit_candidate", "delegate_to_coder"}:
                            parsed = _strip_code_from_payload(parsed)
                        raw_content = json.dumps(parsed, ensure_ascii=False, default=str)
                except (ValueError, SyntaxError):
                    pass
        out["content"] = _clip(str(raw_content), limit=limit)
    elif mtype == "human":
        out["content"] = _clip(str(getattr(msg, "content", "")), limit=1200)
    else:
        content = getattr(msg, "content", None)
        if content is not None:
            out["content"] = _clip(str(content), limit=1200)
    return out


def summarize_tool_call(tool_call: dict[str, Any], *, thread_id: str, risk_level: str = "medium") -> dict[str, Any]:
    return make_event(
        "tool_call_proposed",
        thread_id,
        tool_call_id=tool_call.get("id"),
        tool_name=tool_call.get("name"),
        args=_clip(tool_call.get("args", {}), limit=600),
        risk_level=risk_level,
        summary=f"提议调用 {tool_call.get('name', 'unknown')}",
    )


def summarize_state(state: dict[str, Any], *, thread_id: str, status: str = "running") -> dict[str, Any]:
    best_model = state.get("best_model") or ""
    if not best_model:
        iterations = state.get("iterations") or []
        for it in reversed(iterations):
            if isinstance(it, dict) and it.get("model"):
                best_model = it["model"]
                break
    best_mape = state.get("best_mape", 999.0)
    if best_mape >= 900:
        iterations = state.get("iterations") or []
        for it in reversed(iterations):
            if isinstance(it, dict) and it.get("mape") is not None and it["mape"] < 900:
                best_mape = it["mape"]
                break
    return make_event(
        "run_progress",
        thread_id,
        run_status=status,
        goal=state.get("goal"),
        org=state.get("org"),
        target_month=state.get("target_month"),
        target_mape=state.get("target_mape"),
        iteration_count=state.get("iteration_count", 0),
        completed_candidates=len(state.get("iterations", [])),
        max_iterations=state.get("max_iterations", 0),
        best_mape=best_mape,
        best_model=best_model,
        current_plan=state.get("current_plan"),
        selection_reason=state.get("selection_reason"),
        last_diagnostics=_clip(state.get("last_diagnostics", {}), limit=1000),
        pending_decision=state.get("pending_decision"),
    )


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)
