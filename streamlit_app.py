"""CastFlow Streamlit UI (B9) - 演示门面。

用法:
    .venv/Scripts/streamlit run streamlit_app.py
"""
from __future__ import annotations

import json
import time
from typing import Iterable

import requests
import streamlit as st

API_BASE = "http://127.0.0.1:8000"

st.set_page_config(page_title="CastFlow Agent", page_icon="⚡", layout="wide")

st.markdown(
    """
# ⚡ CastFlow · 自迭代多 Agent 电力负荷预测

LangGraph + Qwen + MCP · 实时观察 Agent 每一步决策
"""
)

# Sidebar: 健康检查 + 输入
with st.sidebar:
    st.subheader("Backend")
    api_base = st.text_input("API base", API_BASE, key="api_base")
    try:
        h = requests.get(f"{api_base}/health", timeout=2).json()
        st.success(f"Connected: {h['status']} v{h.get('version', '?')}")
    except Exception as e:  # noqa: BLE001
        st.error(f"Backend down: {e}")
        st.caption("启动: `.venv/Scripts/uvicorn api.server:app --port 8000`")

    st.divider()
    st.subheader("Forecast task")
    org = st.selectbox(
        "区县",
        ["耀州", "印王", "宜君", "客服", "新区"],
        index=0,
    )
    target_month = st.text_input("目标月 (YYYY-MM)", "2026-12")
    target_mape = st.number_input("目标 MAPE (%)", 1.0, 30.0, 5.0, 0.5)
    max_iter = st.number_input("最大迭代次数", 1, 20, 6)
    mode = st.radio("模式", ["流式 (SSE)", "阻塞式"], index=0)
    run_btn = st.button("🚀 运行 Agent", use_container_width=True, type="primary")


def _format_tool_call(tc: dict) -> str:
    name = tc.get("name", "?")
    args = json.dumps(tc.get("args", {}), ensure_ascii=False)
    if len(args) > 200:
        args = args[:200] + "…"
    return f"**🛠️ {name}**\n```json\n{args}\n```"


def _format_tool_result(name: str, content: str) -> str:
    if len(content) > 1000:
        content = content[:1000] + "…"
    return f"**✅ {name} 返回**\n```\n{content}\n```"


def _render_msg(msg: dict, container) -> None:
    mtype = msg.get("type")
    if mtype == "ai":
        for tc in msg.get("tool_calls", []) or []:
            with container.chat_message("assistant", avatar="🤖"):
                st.markdown(_format_tool_call(tc))
        if msg.get("content"):
            with container.chat_message("assistant", avatar="🧠"):
                st.markdown(msg["content"])
    elif mtype == "tool":
        with container.chat_message("tool", avatar="📦"):
            st.markdown(_format_tool_result(msg.get("name", "?"), msg.get("content", "")))
    elif mtype == "human":
        with container.chat_message("user"):
            st.markdown(msg.get("content", ""))


def _stream_sse(url: str, payload: dict) -> Iterable[tuple[str, dict]]:
    """简易 SSE 解析（按 \\n\\n 分块、同时支持 event:/data: 行）。"""
    with requests.post(url, json=payload, stream=True, timeout=600) as r:
        r.raise_for_status()
        event = "message"
        data_lines: list[str] = []
        for raw in r.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            if raw == "":
                if data_lines:
                    try:
                        yield event, json.loads("\n".join(data_lines))
                    except json.JSONDecodeError:
                        yield event, {"raw": "\n".join(data_lines)}
                data_lines = []
                event = "message"
                continue
            if raw.startswith("event:"):
                event = raw[len("event:"):].strip()
            elif raw.startswith("data:"):
                data_lines.append(raw[len("data:"):].strip())


main = st.container()

if run_btn:
    payload = {
        "org": org,
        "target_month": target_month,
        "target_mape": float(target_mape),
        "max_iterations": int(max_iter),
    }
    main.markdown(f"### Running · {org} / {target_month}")
    log = main.container()
    progress = main.empty()
    started = time.time()
    msg_count = 0
    final_payload: dict | None = None

    if mode.startswith("流"):
        try:
            for ev, data in _stream_sse(f"{api_base}/forecast/stream", payload):
                if ev == "hello":
                    progress.info(f"session_id = {data.get('session_id')}")
                elif ev == "msg":
                    msg_count += 1
                    _render_msg(data, log)
                    progress.caption(f"已收到 {msg_count} 条消息 · 耗时 {time.time() - started:.1f}s")
                elif ev == "done":
                    final_payload = data
                elif ev == "error":
                    main.error(f"Backend error: {data.get('error')}")
        except Exception as e:  # noqa: BLE001
            main.error(f"Stream failed: {e}")
    else:
        with st.spinner("Agent 跑批中…"):
            try:
                resp = requests.post(f"{api_base}/forecast/run", json=payload, timeout=600).json()
            except Exception as e:  # noqa: BLE001
                main.error(f"Request failed: {e}")
                resp = None
            if resp:
                for m in resp.get("messages", []):
                    msg_count += 1
                    _render_msg(m, log)
                final_payload = {
                    "iterations": resp.get("iterations"),
                    "best_mape": resp.get("best_mape"),
                }

    elapsed = time.time() - started
    if final_payload is not None:
        cols = main.columns(3)
        cols[0].metric("Best MAPE", f"{final_payload.get('best_mape', '—')}%")
        cols[1].metric("Iterations", final_payload.get("iterations", "—"))
        cols[2].metric("Elapsed", f"{elapsed:.1f}s")
    else:
        main.warning("没有最终结果（可能被中断）")
else:
    main.info("👈 在左侧选区县和月份，点击「运行 Agent」开始。")
    main.markdown(
        """
**架构**

- LangGraph `StateGraph` + `ToolNode` 主图
- Qwen-max（主）/ Qwen-plus（子 Agent）
- 9 个 native 工具 + 3 个 MCP 工具（castflow-data）
- Chroma 三层记忆（working/episodic/semantic, graceful 降级）
- Reflexion + Coder 两个子 Agent (subagent-as-tool)
- Langfuse 全链路 trace（可选）

**评估命令**

```bash
.venv/Scripts/python -m eval.run_eval --case yaozhou_baseline --no-llm-judge
```
"""
    )
