"""CastFlow Streamlit UI - 人机交互与可视化控制台。"""
from __future__ import annotations

import json
import time
from typing import Iterable

import pandas as pd
import requests
import streamlit as st

API_BASE = "http://127.0.0.1:8000"

st.set_page_config(page_title="CastFlow Agent", page_icon="⚡", layout="wide")


def _init_state() -> None:
   st.session_state.setdefault("thread_id", "")
   st.session_state.setdefault("events", [])
   st.session_state.setdefault("messages", [])
   st.session_state.setdefault("chat", [])
   st.session_state.setdefault("final_result", None)
   st.session_state.setdefault("pending_decision", None)
   st.session_state.setdefault("run_status", "idle")
   st.session_state.setdefault("processed_decisions", set())


_init_state()

# 页面加载时自动恢复：如果有活跃 session 且数据可能过期，自动从后端拉取最新状态
def _auto_restore() -> None:
    tid = st.session_state.get("thread_id", "")
    if not tid:
        return
    status = st.session_state.get("run_status", "idle")
    if status in {"idle"}:
        return
    try:
        snapshot = requests.get(f"{API_BASE}/runs/{tid}", timeout=5).json()
        st.session_state.events = snapshot.get("events", [])
        st.session_state.messages = snapshot.get("messages", [])
        st.session_state.pending_decision = snapshot.get("pending_decision")
        st.session_state.final_result = snapshot.get("final_result")
        st.session_state.run_status = snapshot.get("status", "unknown")
    except Exception:
        pass


_auto_restore()

st.markdown("# ⚡ CastFlow · 人机协作预测智能体")
st.caption("实时推理日志 · 人工审批 · 自然语言反馈 · 预测结果可视化")


with st.sidebar:
   st.subheader("Backend")
   api_base = st.text_input("API base", API_BASE, key="api_base")
   try:
       health = requests.get(f"{api_base}/health", timeout=10).json()
       st.success(f"✅ Connected: {health['status']} v{health.get('version', '?')}")
   except Exception as e:  # noqa: BLE001
       st.warning(f"⚠️ Backend check failed: {e}")
       st.caption("如果后端正在运行但显示此警告，可能是健康检查超时，可以忽略。")
       st.caption("启动命令：`.venv/Scripts/uvicorn api.server:app --port 8000`")

   st.divider()
   st.subheader("任务配置")
   org = st.selectbox("区县", ["耀州", "印王", "宜君", "客服", "新区"], index=0)
   target_month = st.text_input("目标年月或年份", "2026", help="输入年份如 2026 表示预测全年，或输入 2026-12 表示预测单月")
   target_mape = st.number_input("目标 MAPE (%)", 1.0, 30.0, 5.0, 0.5)
   max_iter = st.number_input("最大迭代次数", 1, 20, 6)
   require_approval = st.toggle("严格审批模式", value=False, help="开启后，更多工具调用需要人工确认。")
   prompt = st.text_area(
       "自然语言任务",
       "预测目标月负荷，优先使用稳定、可解释的模型，并展示模型选择理由。",
       height=90,
   )
   run_btn = st.button("启动交互式 Agent", use_container_width=True, type="primary")
   refresh_btn = st.button("刷新当前会话", use_container_width=True)


def _post(path: str, payload: dict | None = None) -> dict:
   response = requests.post(f"{api_base}{path}", json=payload or {}, timeout=10)
   response.raise_for_status()
   return response.json()


def _get(path: str) -> dict:
   response = requests.get(f"{api_base}{path}", timeout=10)
   response.raise_for_status()
   return response.json()


def _stream_sse(url: str, after: int = 0) -> Iterable[tuple[str, dict]]:
   if after > 0:
       sep = "&" if "?" in url else "?"
       url = f"{url}{sep}after={after}"
   with requests.get(url, stream=True, timeout=600) as response:
       response.raise_for_status()
       event = "message"
       data_lines: list[str] = []
       for raw in response.iter_lines(decode_unicode=True):
           if raw is None:
               continue
           if raw == "":
               if data_lines:
                   data = "\n".join(data_lines)
                   try:
                       yield event, json.loads(data)
                   except json.JSONDecodeError:
                       yield event, {"raw": data}
               event = "message"
               data_lines = []
               continue
           if raw.startswith("event:"):
               event = raw[len("event:"):].strip()
           elif raw.startswith("data:"):
               data_lines.append(raw[len("data:"):].strip())


def _append_event(event: dict) -> None:
   st.session_state.events.append(event)
   if event.get("type") == "message":
       msg = event.get("message", {})
       st.session_state.messages.append(msg)
   if event.get("type") == "approval_required":
       pending = event.get("pending_decision", {})
       decision_id = pending.get("id", "")
       # 已处理过的审批不再重复展示（防止 SSE 重连后历史事件回退）
       if decision_id and decision_id in st.session_state.processed_decisions:
           return
       if st.session_state.run_status != "waiting_for_user" or not st.session_state.pending_decision:
           st.session_state.pending_decision = pending
           st.session_state.run_status = "waiting_for_user"
   if event.get("type") == "approval_submitted":
       st.session_state.pending_decision = None
       # 审批提交后，明确恢复为运行态，避免UI停留在“等待审批”
       if st.session_state.run_status == "waiting_for_user":
           st.session_state.run_status = "running"
   if event.get("type") == "run_completed":
       st.session_state.final_result = event
       st.session_state.run_status = "completed"
       st.session_state.pending_decision = None
   if event.get("type") == "run_failed":
       st.session_state.run_status = "failed"
       st.session_state.pending_decision = None
   if event.get("type") == "run_cancelled":
       st.session_state.run_status = "cancelled"
       st.session_state.pending_decision = None


# ── 工具名 → 中文映射 ──
_TOOL_LABEL: dict[str, str] = {
   "load_history": "加载历史数据",
   "run_python": "执行 Python 代码",
   "evaluate_mape": "评估预测精度",
   "delegate_to_coder": "调度 Coder Agent",
   "delegate_to_reflector": "调度 Reflector Agent",
   "delegate_to_verifier": "调度 Verifier Agent",
   "save_predictions": "保存预测结果",
   "finalize": "确认最终方案",
   "load_actual": "加载真实值",
   "save_lesson": "保存经验",
   "list_orgs": "查询可用区县",
   "recall_similar_runs": "回忆相似案例",
   "recall_lessons": "回忆历史经验",
   "search_knowledge_docs": "搜索知识库",
   "submit_candidate": "提交候选模型",
   "get_strategy_recommendation": "策略推荐",
}

# ── 风险标签 ──
_RISK_LABEL = {"high": "🔴 高风险", "medium": "🟡 中风险", "low": "🟢 低风险"}

# ── 阶段检测：根据最近一轮工具调用序列推断当前阶段 ──
_PHASE_SEQ = [
   (["load_history", "load_actual", "list_orgs"], "📊 数据准备"),
   (["delegate_to_coder", "run_python"], "🧠 模型构建"),
   (["delegate_to_verifier"], "🔍 代码审查"),
   (["submit_candidate", "evaluate_mape"], "📈 效果评估"),
   (["delegate_to_reflector"], "🔄 反思优化"),
   (["save_predictions", "finalize"], "✅ 收尾输出"),
]


def _safe_int(val) -> int:
   if val is None:
       return 0
   try:
       return int(val)
   except (ValueError, TypeError):
       return 0


def _detect_phase(events: list[dict], pending_decision: dict | None = None) -> str:
   """从最近的工具调用序列推断当前阶段。"""
   # 最高优先级：检查是否有待审批的决策
   if pending_decision:
       return "⏸ 等待审批"

   # 次优先级：检查最近事件中是否有未处理的审批请求
   for ev in reversed(events[-10:]):
       if ev.get("type") == "approval_required":
           return "⏸ 等待审批"
       if ev.get("type") == "approval_submitted":
           break

   # 正常流程：根据工具调用推断阶段
   recent_tools: list[str] = []
   for ev in reversed(events[-20:]):
       if ev.get("type") == "tool_call_proposed":
           recent_tools.append(ev.get("tool_name", ""))
       if len(recent_tools) >= 3:
           break
   recent_tools.reverse()
   for seq, label in _PHASE_SEQ:
       if any(t in recent_tools for t in seq):
           return label
   return "⏳ 推理中"


_SUBAGENT_HINT = {
   "delegate_to_coder": ("🧠 Coder Agent 正在生成候选模型代码…（通常 30–60 秒）", 60),
   "delegate_to_verifier": ("🔍 Verifier Agent 正在审查代码与逻辑…（通常 10–30 秒）", 30),
   "delegate_to_reflector": ("🔄 Reflector Agent 正在反思失败原因并提出改进…（通常 10–30 秒）", 30),
   "run_python": ("🐍 正在执行 Python 代码…", 30),
}


def _extract_python_intent(args: dict) -> str:
   """从 run_python 的 args 中提取代码意图，生成可读标签。"""
   code = ""
   if isinstance(args, dict):
       code = args.get("code", "") or ""
   if not code:
       return "🐍 正在执行 Python 代码…"
   # 优先取首行注释作为意图描述（跳过含"模拟"等误导性描述）
   for line in code.split("\n")[:5]:
       stripped = line.strip()
       if stripped.startswith("#") and len(stripped) > 3:
           intent = stripped.lstrip("#").strip()
           if "模拟" in intent or "mock" in intent.lower():
               continue
           intent = intent.replace("构造历史数据", "整理历史数据为建模格式").replace("构造数据", "整理数据")
           return f"🐍 正在执行：{intent[:60]}"
   # 无注释时，根据关键词推断
   code_lower = code.lower()
   if "sarimax" in code_lower or "arima" in code_lower:
       return "🐍 正在训练 SARIMAX/ARIMA 模型…"
   if "holt" in code_lower or "exponentialsmoothing" in code_lower:
       return "🐍 正在训练 Holt-Winters 指数平滑模型…"
   if "prophet" in code_lower:
       return "🐍 正在训练 Prophet 模型…"
   if "xgboost" in code_lower or "lightgbm" in code_lower or "gradient" in code_lower:
       return "🐍 正在训练梯度提升模型…"
   if "lstm" in code_lower or "neural" in code_lower or "torch" in code_lower:
       return "🐍 正在训练神经网络模型…"
   if "forecast" in code_lower or "predict" in code_lower:
       return "🐍 正在执行预测计算…"
   if "fit" in code_lower or "train" in code_lower:
       return "🐍 正在训练/拟合模型…"
   if "mape" in code_lower or "evaluate" in code_lower or "error" in code_lower:
       return "🐍 正在评估模型精度…"
   if "plot" in code_lower or "fig" in code_lower:
       return "🐍 正在生成可视化图表…"
   # 兜底：显示代码前几个有意义的字符
   first_meaningful = ""
   for line in code.split("\n")[:10]:
       stripped = line.strip()
       if stripped and not stripped.startswith(("import ", "from ", "#")):
           first_meaningful = stripped[:50]
           break
   if first_meaningful:
       return f"🐍 正在执行：`{first_meaningful}`…"
   return "🐍 正在执行 Python 代码…"


def _pending_subagent(events: list[dict]) -> tuple[str, float, int] | None:
   """检测最近是否有发起、已批准、但尚未返回结果的子 Agent / Python 调用。

   返回 (标签, 已用秒数, 预期秒数) 或 None。
   若工具仍处于待审批状态，则不算执行中，返回 None。
   """
   pending: dict[str, dict] = {}
   awaiting_approval: set[str] = set()
   for ev in events[-200:]:
       et = ev.get("type")
       if et == "tool_call_proposed":
           name = ev.get("tool_name", "")
           if name in _SUBAGENT_HINT:
               pending[name] = ev
       elif et == "approval_required":
           d = ev.get("pending_decision", {})
           tn = d.get("tool_name", "")
           if tn:
               awaiting_approval.add(tn)
       elif et == "approval_submitted":
           approval = ev.get("approval", {})
           tn = approval.get("tool_name") or ""
           awaiting_approval.discard(tn) if tn else awaiting_approval.clear()
           for k in list(pending.keys()):
               pending[k] = {**pending[k], "_resumed_ts": ev.get("ts")}
       elif et == "message":
           msg = ev.get("message", {})
           if msg.get("type") == "tool":
               name = msg.get("name", "")
               pending.pop(name, None)
               awaiting_approval.discard(name)
       elif et in ("run_completed", "run_failed", "run_cancelled"):
           pending.clear()
           awaiting_approval.clear()
   # 排除仍在等待审批的工具
   pending = {k: v for k, v in pending.items() if k not in awaiting_approval}
   if not pending:
       return None
   # 取最近的一个
   name, ev = list(pending.items())[-1]
   label, expected = _SUBAGENT_HINT[name]
   # 对 run_python，从 args 中提取代码意图作为更具体的标签
   if name == "run_python":
       label = _extract_python_intent(ev.get("args", {}))
   ts_str = ev.get("_resumed_ts") or ev.get("ts") or ""
   elapsed = 0.0
   try:
       from datetime import datetime, timezone
       s = ts_str.replace("Z", "+00:00")
       t0 = datetime.fromisoformat(s)
       now = datetime.now(timezone.utc) if t0.tzinfo else datetime.now()
       elapsed = max(0.0, (now - t0).total_seconds())
   except Exception:
       elapsed = 0.0
   return (label, elapsed, expected)


def _render_terminal_output(events: list[dict], widget_key: str = "term") -> None:
   """始终渲染 Python 代码执行的实时终端输出（固定高度 + 可滚动）。"""
   output_lines = []
   for ev in events[-500:]:
       if ev.get("type") == "python_output":
           line = ev.get("line", "")
           if line:
               output_lines.append(line)
   if output_lines:
       st.markdown("##### 🖥️ 代码执行输出")
       terminal_text = "\n".join(output_lines[-80:])
       st.text_area("", terminal_text, height=300, label_visibility="collapsed", key=widget_key)


def _render_subagent_progress(events: list[dict]) -> None:
   """如有子 Agent 正在运行，渲染进度条 + 已用时间。"""
   pend = _pending_subagent(events)
   if not pend:
       return
   label, elapsed, expected = pend
   pct = min(elapsed / max(expected, 1), 0.95)
   st.progress(pct, text=f"{label} · 已用 {int(elapsed)}s / 预计 {expected}s")


def _latest_metrics(events: list[dict]) -> dict:
   """从 run_progress 事件提取最新迭代指标。"""
   m = {"iter": 0, "max_iter": 0, "best_mape": None, "best_model": None, "target_mape": None, "plan": "", "turns": 0}
   for ev in reversed(events):
       if ev.get("type") == "run_progress":
           m["iter"] = _safe_int(ev.get("completed_candidates") or ev.get("iteration_count"))
           m["turns"] = _safe_int(ev.get("iteration_count"))
           m["max_iter"] = _safe_int(ev.get("max_iterations"))
           best_mape = ev.get("best_mape")
           if best_mape is not None:
               try:
                   best_mape = float(best_mape)
                   if best_mape < 900:
                       m["best_mape"] = best_mape
               except (ValueError, TypeError):
                   pass
           if ev.get("best_model") and str(ev["best_model"]) != "None":
               m["best_model"] = str(ev["best_model"])
           if ev.get("target_mape") is not None:
               try:
                   m["target_mape"] = float(ev["target_mape"])
               except (ValueError, TypeError):
                   pass
           if ev.get("current_plan"):
               m["plan"] = str(ev["current_plan"])[:200]
           break
   return m


def _render_progress_bar(events: list[dict], pending_decision: dict | None = None) -> None:
   """顶部：迭代进度 + 当前阶段 + 关键指标。"""
   m = _latest_metrics(events)
   if not m["max_iter"]:
       return
   col1, col2, col3, col4 = st.columns(4)
   with col1:
       st.progress(min(m["iter"] / max(m["max_iter"], 1), 1.0), text=f"候选 {m['iter']}/{m['max_iter']} (第{m['turns']}轮)")
   with col2:
       st.metric("当前阶段", _detect_phase(events, pending_decision))
   with col3:
       if m["best_mape"] is not None:
           color = "normal" if m["best_mape"] <= (m["target_mape"] or 999) else "inverse"
           st.metric("最优 MAPE", f"{m['best_mape']:.2f}%", delta=f"目标 ≤{m['target_mape'] or '?'}%", delta_color=color)
       else:
           st.metric("最优 MAPE", "—")
   with col4:
       if m["best_model"]:
           st.metric("最优模型", m["best_model"][:30])
       elif m["plan"]:
           st.caption(f"计划：{m['plan']}")
       else:
           st.metric("最优模型", "—")
   st.divider()


def _local_time(ts_str: str) -> str:
   """将 UTC ISO 时间戳转为本地时间 HH:MM:SS。"""
   if not ts_str:
       return ""
   try:
       from datetime import datetime, timezone
       s = ts_str.replace("Z", "+00:00")
       dt = datetime.fromisoformat(s)
       local_dt = dt.astimezone()
       return local_dt.strftime("%H:%M:%S")
   except Exception:
       return ts_str[11:19] if len(ts_str) > 19 else ""


def _humanize_event(event: dict) -> str | None:
   """将任意事件翻译为中文描述，返回 None 表示不需要展示（如心跳、内部进度）。"""
   etype = event.get("type", "")
   ts = _local_time(str(event.get("ts", "")))

   # 心跳 — 不展示（仅在空闲时显示轻量提示）
   if etype == "run_heartbeat":
       return None

   # 运行生命周期
   if etype == "run_started":
       return f"`{ts}` 🚀 **任务启动** · {event.get('org', '')}区 · {event.get('target_month', '')}"
   if etype == "run_created":
       return f"`{ts}` 📋 任务已创建 · {event.get('org', '')}区"
   if etype == "run_completed":
       return (
           f"`{ts}` 🏁 **任务完成** · 共 {event.get('iterations', 0)} 轮迭代 · "
           f"最优 MAPE {event.get('best_mape', '?')}% · 模型：{event.get('best_model') or '未知'}"
       )
   if etype == "run_failed":
       return f"`{ts}` ❌ **运行失败** · {event.get('error', '未知错误')}"
   if etype == "run_cancelled":
       reason = event.get("reason", "用户取消")
       return f"`{ts}` ⛔ **已取消** · {reason}"
   if etype == "run_resumed":
       return f"`{ts}` ▶️ **继续运行**"

   # 工具调用提议
   if etype == "tool_call_proposed":
       name = event.get("tool_name") or ""
       label = _TOOL_LABEL.get(name, name) or name or "未知工具"
       raw_risk = event.get("risk_level") or "low"
       risk = _RISK_LABEL.get(raw_risk, "")
       args = event.get("args", {})
       detail = _tool_one_liner(name, args)
       return f"`{ts}` 🔧 **{label}** {risk}\n> {detail}"

   # 消息（AI / Tool / Human）
   if etype == "message":
       msg = event.get("message", {})
       mtype = msg.get("type", "")
       if mtype == "tool":
           name = msg.get("name") or ""
           label = _TOOL_LABEL.get(name, name) or name or "未知工具"
           content = msg.get("content", "")
           # 尝试提取关键数据
           summary = _tool_result_summary(name, content)
           return f"`{ts}` 📥 **{label}** 返回结果\n> {summary}"
       if mtype == "ai" and msg.get("content"):
           content = msg["content"][:120]
           return f"`{ts}` 💭 **LLM 决策**\n> {content}…"
       return None

   # 审批
   if etype == "approval_required":
       decision = event.get("pending_decision", {})
       name = decision.get("tool_name") or "未知"
       label = _TOOL_LABEL.get(name, name) or name or "未知工具"
       risk = _RISK_LABEL.get(decision.get("risk_level") or "low", "")
       return f"`{ts}` ⚠️ **等待人工审批** · {label} {risk}"
   if etype == "approval_submitted":
       approval = event.get("approval", {})
       ok = approval.get("approved", False)
       icon = "✅" if ok else "❌"
       status = "已批准" if ok else "已拒绝"
       fb = approval.get("feedback", "")
       return f"`{ts}` {icon} **审批{status}**" + (f" · {fb[:80]}" if fb else "")

   # 内部进度 — 不单独展示（进度条已覆盖）
   if etype == "run_progress":
       return None

   # Python 实时输出 — 不在事件列表展示（由终端面板单独渲染）
   if etype == "python_output":
       return None

   # 反馈
   if etype == "feedback_submitted":
       fb = event.get("feedback", {})
       action = fb.get("action", "note")
       action_label = {"note": "📝 反馈", "pause": "⏸ 暂停", "resume": "▶ 继续", "stop": "⏹ 停止", "replan": "🔄 重新规划"}.get(action, action)
       return f"`{ts}` {action_label} · {fb.get('message', '')[:100]}"

   # 未知事件类型 — 跳过（避免显示无意义文字）
   return None


# ── 模型类型识别：从代码 / 模型名中匹配出可读的算法标签 ──
_MODEL_PATTERNS: list[tuple[str, str]] = [
   (r"sarimax|SARIMAX", "SARIMAX（季节性自回归滑动平均 + 外生变量）"),
   (r"\bARIMA\b|statsmodels\.tsa\.arima", "ARIMA（自回归综合滑动平均）"),
   (r"ExponentialSmoothing|HoltWinters|Holt-?Winters|holt_winters", "Holt-Winters 指数平滑"),
   (r"seasonal[_ ]?weighted|seasonal_naive|SeasonalNaive", "季节性加权（Seasonal Weighted / Naive）"),
   (r"Prophet|prophet", "Prophet（Facebook 时序模型）"),
   (r"XGBRegressor|xgboost|XGBoost", "XGBoost 梯度提升"),
   (r"LGBMRegressor|lightgbm|LightGBM", "LightGBM 梯度提升"),
   (r"RandomForestRegressor", "随机森林回归"),
   (r"LinearRegression|Ridge|Lasso", "线性 / Ridge / Lasso 回归"),
   (r"LSTM|GRU|RNN|tensorflow|torch\.nn", "神经网络（LSTM/GRU/RNN）"),
   (r"\.rolling\(|moving[_ ]?average|MA\b", "滑动平均 / Moving Average"),
   (r"ensemble|stacking|blend", "集成 / Stacking / Blending"),
]


def _detect_model_type(code: str, fallback: str = "") -> str:
   """从代码片段中识别使用的预测模型；识别不到则回退到 fallback。"""
   import re
   if code:
       for pat, label in _MODEL_PATTERNS:
           if re.search(pat, code):
               return label
   if fallback:
       for pat, label in _MODEL_PATTERNS:
           if re.search(pat, fallback, re.IGNORECASE):
               return label
       return fallback
   return "未识别（请查看代码）"


def _current_iteration_info(events: list[dict]) -> dict:
   """汇总当前迭代上下文：第几轮、目标月、目标 MAPE、当前最优 MAPE/模型。
   从多种事件源综合提取，确保审批面板始终展示最新进展。"""
   info = {
       "iter": 0,
       "max_iter": 0,
       "target_month": "",
       "target_mape": None,
       "best_mape": None,
       "best_model": None,
       "org": "",
       "last_tool": "",
       "last_tool_summary": "",
   }
   for ev in events:
       if ev.get("type") == "run_started":
           info["target_month"] = ev.get("target_month", info["target_month"]) or info["target_month"]
           info["org"] = ev.get("org", info["org"]) or info["org"]
   # 从所有 run_progress 事件中取最新的非初始值
   for ev in reversed(events):
       if ev.get("type") == "run_progress":
           if not info["iter"]:
               info["iter"] = ev.get("completed_candidates") or ev.get("iteration_count", 0) or 0
           if not info["max_iter"]:
               info["max_iter"] = ev.get("max_iterations", 0) or 0
           if info["target_mape"] is None and ev.get("target_mape") is not None:
               info["target_mape"] = ev["target_mape"]
           if info["best_mape"] is None and ev.get("best_mape") is not None and ev["best_mape"] < 900:
               info["best_mape"] = ev["best_mape"]
           if not info["best_model"] and ev.get("best_model") and str(ev["best_model"]) != "None":
               info["best_model"] = ev["best_model"]
           if info["iter"] and info["max_iter"] and info["best_mape"] is not None:
               break
   # 从最近的工具调用/结果中补充动态上下文
   for ev in reversed(events[-30:]):
       if ev.get("type") == "tool_call_proposed" and not info["last_tool"]:
           info["last_tool"] = ev.get("tool_name", "")
           info["last_tool_summary"] = ev.get("summary", "")
       if ev.get("type") == "message":
           msg = ev.get("message", {})
           if msg.get("type") == "tool" and msg.get("name") == "evaluate_mape":
               content = msg.get("content", "")
               parsed = _parse_tool_content(content)
               if parsed and isinstance(parsed, dict):
                   data = parsed.get("data", parsed) if isinstance(parsed.get("data"), dict) else parsed
                   mape_val = data.get("mape")
                   if mape_val is not None and (info["best_mape"] is None or float(mape_val) < info["best_mape"]):
                       info["best_mape"] = float(mape_val)
                   model = data.get("model") or data.get("best_model")
                   if model and str(model) != "None" and not info["best_model"]:
                       info["best_model"] = model
           if msg.get("type") == "tool" and msg.get("name") == "delegate_to_coder":
               content = msg.get("content", "")
               parsed = _parse_tool_content(content)
               if parsed and isinstance(parsed, dict):
                   data = parsed.get("data", parsed) if isinstance(parsed.get("data"), dict) else parsed
                   model = data.get("best_model")
                   if model and str(model) != "None" and not info["best_model"]:
                       info["best_model"] = model
       if info["last_tool"] and info["best_mape"] is not None:
           break
   return info


def _humanize_decision(decision: dict, events: list[dict] | None = None) -> str:
   """将审批决策中的 tool_args 翻译为人类可读的中文描述。"""
   tool_name = decision.get("tool_name") or "未知工具"
   args = decision.get("tool_args", {})
   risk = decision.get("risk_level") or "low"
   risk_label = {"high": "🔴 高风险", "medium": "🟡 中风险", "low": "🟢 低风险"}.get(risk, "")

   # ── 顶部：本次审批的核心信息（让用户一眼看懂在批什么） ──
   info = _current_iteration_info(events or [])
   iter_str = f"候选 {info['iter']}/{info['max_iter']}" if info["max_iter"] else "初始化阶段"
   best_str = (
       f"当前最优 MAPE={info['best_mape']:.2f}%（模型：{info['best_model'] or '—'}）"
       if info["best_mape"] is not None else "尚无候选结果"
   )
   target_str = (
       f"目标 ≤ {info['target_mape']}%" if info["target_mape"] is not None else ""
   )
   # 从最近事件中提取上一步做了什么
   last_action = ""
   if info.get("last_tool"):
       last_label = _TOOL_LABEL.get(info["last_tool"], info["last_tool"])
       last_action = f"上一步：{last_label}"
   header = [
       "#### 🧾 本次审批的核心信息",
       f"- **操作**：{_TOOL_LABEL.get(tool_name, tool_name) or tool_name}  {risk_label}",
   ]
   header.append(f"- **进度**：{iter_str}")
   if info["org"] or info["target_month"]:
       header.append(f"- **任务对象**：{info['org']} 区 · {info['target_month']}")
   header.append(f"- **当前进展**：{best_str}{('，' + target_str) if target_str else ''}")
   if last_action:
       header.append(f"- **上下文**：{last_action}")
   lines: list[str] = list(header) + ["", "#### 🔍 详细参数"]
   if tool_name == "run_python":
       code = args.get("code", "")
       model_label = _detect_model_type(code)
       lines.append(f"**使用模型**：{model_label}")
       imports = [l.split()[1] for l in code.split("\n") if l.strip().startswith(("import ", "from "))]
       if imports:
           lines.append(f"**依赖库**：{', '.join(list(dict.fromkeys(imports))[:6])}")
       # 从注释中提取意图描述
       desc_lines = [l.strip().lstrip("#").strip() for l in code.split("\n") if l.strip().startswith("#") and len(l.strip()) > 3]
       if desc_lines:
           lines.append(f"**意图**：{'；'.join(desc_lines[:3])}")
       else:
           # 根据代码内容推断意图
           if "mape" in code.lower() or "evaluate" in code.lower() or "mean_absolute_percentage" in code.lower():
               lines.append("**意图**：评估模型预测精度（MAPE）")
           elif ("fit" in code or "train" in code.lower()) and ("model" in code.lower()):
               lines.append("**意图**：训练/拟合预测模型")
           elif "forecast" in code or "predict" in code:
               lines.append("**意图**：生成预测结果")
           elif "load" in code.lower() or "read" in code.lower():
               lines.append("**意图**：加载/读取数据")
           else:
               lines.append("**意图**：执行 Python 代码进行数据处理")
       # 显示代码摘要（前几行非 import 的有效代码）
       code_lines = [l for l in code.split("\n") if l.strip() and not l.strip().startswith(("#", "import ", "from "))]
       if code_lines:
           preview = code_lines[0].strip()[:120]
           lines.append(f"**代码首行**：`{preview}`")
       lines.append(f"**代码长度**：{len(code)} 字符 / {len(code.split(chr(10)))} 行")
   elif tool_name == "save_predictions":
       org = args.get("org", "未知区县")
       months = args.get("months", [])
       preds = args.get("predictions", [])
       lines.append(f"**操作**：将 {len(preds)} 个月份（{', '.join(months[:3])}{'...' if len(months) > 3 else ''}）的预测结果写入数据库")
       lines.append(f"**对象**：{org} 区")
   elif tool_name == "finalize":
       best_model = args.get("best_model", "未知模型")
       best_mape = args.get("best_mape", "未知")
       lines.append(f"**操作**：结束迭代，确定最终方案")
       lines.append(f"**选用模型**：{best_model}，MAPE = {best_mape}%")
   elif tool_name == "delegate_to_coder":
       org = args.get("org", "")
       target = args.get("target_month", "")
       lines.append(f"**操作**：让 Coder Agent 生成候选模型代码")
       if org:
           lines.append(f"**对象**：{org} 区 {target} 月")
   elif tool_name == "delegate_to_reflector":
       reason = args.get("reason", "")
       lines.append(f"**操作**：让 Reflector Agent 分析失败原因并重新规划")
       if reason:
           lines.append(f"**原因**：{reason[:200]}")
   elif tool_name == "delegate_to_verifier":
       code = args.get("code", "")
       lines.append(f"**操作**：让 Verifier Agent 审查生成的代码")
       lines.append(f"**代码长度**：{len(code)} 字符")
   elif tool_name == "evaluate_mape":
       org = args.get("org", "")
       months = args.get("months", [])
       lines.append(f"**操作**：评估预测精度（MAPE）")
       if org:
           lines.append(f"**对象**：{org} 区 {', '.join(months)} 月")
   elif tool_name == "load_actual":
       org = args.get("org", "")
       months = args.get("months", [])
       lines.append(f"**操作**：从真实值表加载历史/验证数据")
       if org:
           lines.append(f"**对象**：{org} 区 {', '.join(months)} 月")
   else:
       for k, v in list(args.items())[:4]:
           v_str = str(v)[:100] + "…" if len(str(v)) > 100 else str(v)
           lines.append(f"**{k}**：{v_str}")
   return "\n\n".join(lines)


def _tool_one_liner(name: str, args: dict) -> str:
   """工具调用的一句话摘要。"""
   if name == "load_history":
       org = args.get("org", "?")
       return f"请求加载 {org} 区历史负荷数据"
   if name == "load_actual":
       org = args.get("org", "?")
       return f"请求加载 {org} 区真实值数据"
   if name == "run_python":
       code = args.get("code", "")
       desc_lines = [l.strip().lstrip("#").strip() for l in code.split("\n") if l.strip().startswith("#") and len(l.strip()) > 3]
       if desc_lines:
           desc = desc_lines[0][:120]
           desc = desc.replace("构造历史数据", "整理历史数据为建模格式").replace("构造数据", "整理数据")
           return desc
       # 猜测意图
       if "forecast" in code or "predict" in code:
           return "执行预测代码"
       if "model" in code.lower() or "fit" in code:
           return "训练/拟合模型"
       if "mape" in code.lower() or "evaluate" in code:
           return "评估模型精度"
       return "执行数据处理或分析代码"
   if name == "evaluate_mape":
       org = args.get("org", "?")
       return f"评估 {org} 区预测精度（MAPE/MAE/RMSE）"
   if name == "delegate_to_coder":
       return "让 Coder Agent 生成候选模型代码"
   if name == "delegate_to_reflector":
       reason = args.get("reason", "")
       return f"让 Reflector 分析并重新规划" + (f"：{reason[:80]}" if reason else "")
   if name == "delegate_to_verifier":
       return "让 Verifier Agent 审查代码正确性"
   if name == "save_predictions":
       org = args.get("org", "?")
       months = args.get("months", [])
       return f"将 {org} 区 {len(months)} 个月预测结果写入数据库"
   if name == "finalize":
       best = args.get("best_model", "?")
       return f"确认最终方案，最优模型：{best}"
   if name == "save_lesson":
       return "保存本次运行经验到记忆库"
   if name == "list_orgs":
       return "查询系统中可用的区县列表"
   if name == "recall_similar_runs":
       return "从历史经验库中回忆相似案例"
   if name == "recall_lessons":
       return "检索历史经验教训"
   if name == "search_knowledge_docs":
       return "搜索领域知识库"
   if name == "submit_candidate":
       model = args.get("model_name", "")
       return f"提交候选模型：{model}" if model else "提交候选模型"
   # 通用
   parts = [f"{k}={v}" for k, v in list(args.items())[:3]]
   return "；".join(parts) if parts else name


def _parse_tool_content(content: str) -> dict | None:
   """多策略解析工具返回的 JSON/Python dict 字符串。"""
   if not content:
       return None
   # 策略 1：标准 JSON
   try:
       return json.loads(content)
   except (json.JSONDecodeError, TypeError):
       pass
   # 策略 2：Python dict repr（安全解析）
   import ast
   try:
       result = ast.literal_eval(content)
       if isinstance(result, dict):
           return result
   except (ValueError, SyntaxError):
       pass
   return None


def _tool_result_summary(name: str, content: str) -> str:
   """工具返回值的一句话摘要。"""
   if not content:
       return "（空结果）"
   parsed = _parse_tool_content(content)
   if parsed and isinstance(parsed, dict):
       # 有 success/summary 结构的通用处理
       summary = parsed.get("summary", "")
       inner = parsed.get("data", parsed) if isinstance(parsed.get("data"), dict) else parsed
       if name == "evaluate_mape":
           mape = inner.get("mape", "?")
           mae = inner.get("mae", "?")
           rmse = inner.get("rmse", "?")
           return f"MAPE={mape}% · MAE={mae} · RMSE={rmse}"
       if name == "load_history":
           n = inner.get("count", inner.get("records", inner.get("total", 0)))
           range_info = inner.get("range", [])
           range_str = f"，{range_info[0]} ~ {range_info[-1]}" if range_info else ""
           return f"加载 {n} 条记录{range_str}"
       if name in ("list_orgs",):
           orgs = inner.get("orgs", [])
           return f"找到 {len(orgs)} 个区县：" + "、".join(orgs) if orgs else "获取区县列表"
       if name in ("recall_similar_runs",):
           found = inner.get("found", 0)
           return f"召回 {found} 条相似历史案例"
       if name in ("search_knowledge_docs",):
           docs = inner.get("documents", [])
           found = inner.get("found", len(docs))
           return f"检索到 {found} 篇知识文档"
       if name in ("recall_lessons",):
           found = inner.get("found", 0)
           return f"召回 {found} 条历史教训"
       if name in ("save_lesson",):
           return "经验已存入记忆库"
       if name == "delegate_to_coder":
           summaries = inner.get("candidate_summaries", [])
           if summaries:
               return f"生成 {len(summaries)} 个候选模型：" + "、".join(
                   s.get("name", "?") for s in summaries[:4]
               )
           return "代码生成完成"
       if name == "load_actual":
           n = inner.get("count", inner.get("records", inner.get("total", 0)))
           if isinstance(n, (list, int)):
               n = len(n) if isinstance(n, list) else n
           return f"加载 {n} 条真实值记录"
   # 纯文本截断
   return content[:150].replace("\n", " ") if len(content) > 150 else content.replace("\n", " ")


def _render_trace(events: list[dict], pending_decision: dict | None = None) -> None:
   """结构化推理日志：进度条 + 终端输出 + 分阶段事件流。"""
   if not events:
       st.info("尚未开始运行。")
       return
   _render_progress_bar(events, pending_decision)
   _render_subagent_progress(events)
   _render_terminal_output(events, f"term_trace_{len(events)}")
   shown = 0
   for event in events[-100:]:
       text = _humanize_event(event)
       if text is None:
           continue
       # 跳过 run_progress 但已经通过进度条展示
       st.markdown(text)
       shown += 1
       # 高风险工具和审批事件展开参数
       if event.get("type") == "tool_call_proposed" and event.get("risk_level") == "high":
           with st.expander("查看详细参数", expanded=False):
               st.json(event.get("args", {}))
       if event.get("type") == "approval_required":
           decision = event.get("pending_decision", {})
           st.markdown("---")
           st.markdown(_humanize_decision(decision, events))
           with st.expander("原始参数（JSON）", expanded=False):
               st.json(decision)
           st.markdown("---")
   if shown == 0:
       st.info("Agent 正在运行，等待事件…")


def _render_live_events(events: list[dict], pending_decision: dict | None = None) -> None:
   """实时事件面板：精简版，仅展示最近关键动态。"""
   if not events:
       st.info("等待 Agent 启动…")
       return
   m = _latest_metrics(events)
   phase = _detect_phase(events, pending_decision)
   # 顶部状态
   col1, col2 = st.columns(2)
   with col1:
       if m["max_iter"]:
           st.progress(min(m["iter"] / max(m["max_iter"], 1), 1.0), text=f"候选 {m['iter']}/{m['max_iter']} (第{m['turns']}轮)")
       else:
           st.info("Agent 已启动")
   with col2:
       st.metric("阶段", phase)
   if m["best_mape"] is not None:
       st.metric("当前最优 MAPE", f"{m['best_mape']:.2f}%")
   _render_subagent_progress(events)
   _render_terminal_output(events, f"term_live_{len(events)}")
   st.divider()
   # 最近事件（时间正序，最新在底部，符合阅读习惯）
   recent = []
   for event in events[-30:]:
       text = _humanize_event(event)
       if text is not None:
           recent.append(text)
   for text in recent[-8:]:
       st.markdown(text)


def _extract_rows_from_messages(messages: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
   """从工具消息中提取 MAPE 指标、候选模型数据、每次迭代的逐点预测/真实值。"""
   mape_rows: list[dict] = []
   candidate_rows: list[dict] = []
   per_point_iterations: list[dict] = []
   iteration = 0
   seen_eval_mapes: set = set()  # 去重：记录已从 evaluate_mape 提取的 MAPE 值
   for msg in messages:
       if msg.get("type") != "tool":
           continue
       content = msg.get("content", "")
       try:
           payload = json.loads(content)
       except Exception:
           payload = _parse_tool_content(content) or {}
       data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
       if msg.get("name") == "evaluate_mape" and isinstance(data, dict):
           eval_mape = data.get("mape")
           if eval_mape is None and isinstance(payload, dict):
               eval_mape = payload.get("mape")
           if eval_mape is not None:
               iteration += 1
               seen_eval_mapes.add(eval_mape)
               mape_rows.append(
                   {
                       "iteration": iteration,
                       "MAPE": eval_mape,
                       "MAE": data.get("mae") or payload.get("mae"),
                       "RMSE": data.get("rmse") or payload.get("rmse"),
                       "bias": data.get("bias") or payload.get("bias"),
                   }
               )
               points = data.get("per_point_errors") or payload.get("per_point_errors") or []
               if isinstance(points, list) and points:
                   per_point_iterations.append(
                       {
                           "iteration": iteration,
                           "mape": eval_mape,
                           "points": points,
                       }
                   )
       elif msg.get("name") == "submit_candidate" and isinstance(data, dict):
           mape_val = data.get("mape")
           if mape_val is None and isinstance(payload, dict):
               mape_val = payload.get("mape")
           if mape_val is not None and mape_val not in seen_eval_mapes:
               iteration += 1
               diag = data.get("diagnostics") or payload.get("diagnostics") or {}
               # LLM 可能把 evaluate_mape 的完整返回值（含 data 嵌套）作为 diagnostics 传入
               if isinstance(diag, dict) and "data" in diag and isinstance(diag["data"], dict):
                   diag = diag["data"]
               mape_rows.append(
                   {
                       "iteration": iteration,
                       "MAPE": mape_val,
                       "MAE": diag.get("mae"),
                       "RMSE": diag.get("rmse"),
                       "bias": diag.get("bias"),
                   }
               )
               points = diag.get("per_point_errors") or diag.get("worst_points") or []
               if isinstance(points, list) and points:
                   per_point_iterations.append(
                       {
                           "iteration": iteration,
                           "mape": mape_val,
                           "points": points,
                       }
                   )
       if msg.get("name") == "delegate_to_coder" and isinstance(data, dict):
           for item in data.get("candidate_summaries", []) or []:
               if isinstance(item, dict):
                   candidate_rows.append(item)
   return mape_rows, candidate_rows, per_point_iterations


if run_btn:
   st.session_state.events = []
   st.session_state.messages = []
   st.session_state.final_result = None
   st.session_state.pending_decision = None
   st.session_state.processed_decisions = set()
   st.session_state.run_status = "starting"
   payload = {
       "org": org,
       "target_month": target_month,
       "target_mape": float(target_mape),
       "max_iterations": int(max_iter),
       "prompt": prompt,
       "require_approval": bool(require_approval),
   }
   try:
       created = _post("/runs", payload)
       st.session_state.thread_id = created["thread_id"]
       st.session_state.run_status = created["status"]
   except Exception as e:  # noqa: BLE001
       st.error(f"启动失败：{e}")

if refresh_btn and st.session_state.thread_id:
   try:
       snapshot = _get(f"/runs/{st.session_state.thread_id}")
       st.session_state.events = snapshot.get("events", [])
       st.session_state.messages = snapshot.get("messages", [])
       st.session_state.pending_decision = snapshot.get("pending_decision")
       st.session_state.final_result = snapshot.get("final_result")
       st.session_state.run_status = snapshot.get("status", "unknown")
   except Exception as e:  # noqa: BLE001
       st.error(f"刷新失败：{e}")

_STATUS_LABEL = {
   "idle": "💤 空闲",
   "starting": "🚀 启动中",
   "running": "▶️ 运行中",
   "waiting_for_user": "⏸ 等待审批",
   "completed": "✅ 已完成",
   "failed": "❌ 失败",
   "cancelled": "⛔ 已取消",
   "unknown": "❓ 未知",
}


def _count_approvals(events: list[dict]) -> tuple[int, int]:
   """返回 (已批准次数, 累计审批请求次数)。"""
   submitted = sum(1 for e in events if e.get("type") == "approval_submitted")
   requested = sum(1 for e in events if e.get("type") == "approval_required")
   return submitted, requested


thread_id = st.session_state.thread_id
status = st.session_state.run_status

status_cols = st.columns(5)
status_cols[0].metric("会话", thread_id or "—")
status_cols[1].metric("状态", _STATUS_LABEL.get(status, status))
status_cols[2].metric("事件数", len(st.session_state.events))
status_cols[3].metric("消息数", len(st.session_state.messages))
final = st.session_state.final_result or {}
status_cols[4].metric("Best MAPE", final.get("best_mape", "—"))

# 关键：等待审批时不要进入 SSE 阻塞循环 —— 否则用户点击「批准」按钮触发的 rerun
# 会先在顶部 SSE for-loop 阻塞，下面右栏的审批回调代码永远跑不到，UI 就卡在「等待审批」。
_should_stream = (
   thread_id
   and status in {"starting", "running"}
   and not st.session_state.pending_decision
)
if _should_stream:
   live_box = st.empty()
   with st.spinner('正在连接事件流...'):
       try:
           after_idx = len(st.session_state.events)
           for ev_type, event in _stream_sse(f"{api_base}/runs/{thread_id}/events", after=after_idx):
               if ev_type == "hello":
                   continue
               if ev_type == "done":
                   st.session_state.run_status = event.get("status", "completed")
                   st.session_state.final_result = event.get("final_result") or st.session_state.final_result
                   break
               _append_event(event)
               with live_box.container():
                   _render_live_events(st.session_state.events, st.session_state.pending_decision)
               if event.get("type") == "approval_required":
                   break
       except requests.ConnectionError:
           st.error('❌ 后端服务连接中断。请检查 API 服务是否正常运行，然后点击"刷新当前会话"。')
           st.session_state.run_status = "idle"
       except requests.Timeout:
           st.warning('⚠️ 连接超时，正在自动重连...')
           time.sleep(1)
           st.rerun()
       except Exception as e:  # noqa: BLE001
           st.warning(f'⚠️ 事件流中断：{e}（可点击"刷新当前会话"重试）')

left, right = st.columns([2.1, 1])

with left:
   tabs = st.tabs(["智能体推理日志", "对话/工具消息", "曲线与趋势", "最终结果"])
   with tabs[0]:
       _render_trace(st.session_state.events, st.session_state.pending_decision)
   with tabs[1]:
       for msg in st.session_state.messages[-40:]:
           if msg.get("type") == "human":
               with st.chat_message("user"):
                   st.markdown(msg.get("content", ""))
           elif msg.get("type") == "ai":
               for tc in msg.get("tool_calls", []) or []:
                   tool_name = tc.get("name", "")
                   purpose = _tool_one_liner(tool_name, tc.get("args", {}))
                   with st.chat_message("assistant"):
                       st.markdown(f"🛠 **调用工具**：`{tool_name}`")
                       st.caption(f"目的：{purpose}")
                       with st.expander("参数详情", expanded=False):
                           st.json(tc.get("args", {}))
               if msg.get("content"):
                   with st.chat_message("assistant"):
                       st.markdown(msg.get("content"))
           elif msg.get("type") == "tool":
               tool_name = msg.get("name", "")
               raw = msg.get("content", "") or ""
               parsed = _parse_tool_content(raw)
               with st.chat_message("assistant"):
                   success = None
                   err_msg = ""
                   next_action = ""
                   if isinstance(parsed, dict):
                       success = parsed.get("success")
                       err_msg = parsed.get("error") or parsed.get("message", "") or ""
                       next_action = parsed.get("next_action", "") or ""
                   summary = _tool_result_summary(tool_name, raw)
                   if success is False:
                       st.markdown(f"❌ **{tool_name} 失败**")
                       st.error(err_msg or summary or "工具调用失败")
                       if next_action:
                           st.info(f"建议：{next_action}")
                   elif success is True:
                       st.markdown(f"✅ **{tool_name} 完成**")
                       st.markdown(f"摘要：{summary}")
                   else:
                       st.markdown(f"📤 **{tool_name} 返回**")
                       st.markdown(f"摘要：{summary}")
                   with st.expander("原始返回（JSON）", expanded=False):
                       if isinstance(parsed, dict):
                           st.json(parsed)
                       else:
                           st.code(raw[:3000])
   with tabs[2]:
       mape_rows, candidate_rows, per_point_iters = _extract_rows_from_messages(st.session_state.messages)
       # 调试信息：帮助排查图表数据提取问题
       tool_msg_count = sum(1 for m in st.session_state.messages if m.get("type") == "tool")
       eval_msg_count = sum(1 for m in st.session_state.messages if m.get("name") == "evaluate_mape")
       submit_msg_count = sum(1 for m in st.session_state.messages if m.get("name") == "submit_candidate")
       with st.expander(f"数据诊断（工具消息={tool_msg_count}, evaluate={eval_msg_count}, submit={submit_msg_count}, 提取行={len(mape_rows)}）", expanded=False):
           st.caption(f"总消息数: {len(st.session_state.messages)}, 工具消息: {tool_msg_count}")
           st.caption(f"evaluate_mape 消息: {eval_msg_count}, submit_candidate 消息: {submit_msg_count}")
           st.caption(f"提取到的迭代数据行: {len(mape_rows)}")
           if mape_rows:
               st.json(mape_rows)
       if mape_rows:
           df = pd.DataFrame(mape_rows)
           st.subheader("准确率趋势")
           st.line_chart(df.set_index("iteration")[[col for col in ["MAPE", "MAE", "RMSE", "bias"] if col in df]])
           st.dataframe(df, use_container_width=True)
       else:
           st.info("运行后会展示 MAPE / MAE / RMSE / bias 趋势。")
       if candidate_rows:
           st.subheader("候选模型对比")
           cdf = pd.DataFrame(candidate_rows)
           st.dataframe(cdf, use_container_width=True)
           if "validation_mape" in cdf:
               st.bar_chart(cdf.set_index("name")["validation_mape"])

       st.subheader("真实值 vs 预测值（精度最优 Top 3）")
       if per_point_iters:
           # 按 MAPE 升序排列，取前3名
           valid_iters = [it for it in per_point_iters if isinstance(it.get("mape"), (int, float))]
           valid_iters.sort(key=lambda x: x["mape"])
           top3 = valid_iters[:3]
           if not top3:
               top3 = per_point_iters[:3]

           actual_map: dict[int, float] = {}
           for it in top3:
               for pt in it["points"]:
                   idx = pt.get("index")
                   actual = pt.get("actual")
                   if idx is None or actual is None:
                       continue
                   actual_map.setdefault(int(idx), float(actual))
           if actual_map:
               indices = sorted(actual_map.keys())
               curve_df = pd.DataFrame({"index": indices, "真实值": [actual_map[i] for i in indices]})
               for rank, it in enumerate(top3, 1):
                   pred_map: dict[int, float] = {}
                   for pt in it["points"]:
                       idx = pt.get("index")
                       pred = pt.get("prediction")
                       if idx is None or pred is None:
                           continue
                       pred_map[int(idx)] = float(pred)
                   mape_val = it.get("mape")
                   label = f"Top{rank} 第{it['iteration']}次"
                   if isinstance(mape_val, (int, float)):
                       label += f"（MAPE={mape_val:.2f}%）"
                   curve_df[label] = [pred_map.get(i) for i in indices]
               st.line_chart(curve_df.set_index("index"))
               with st.expander("逐点数据表", expanded=False):
                   st.dataframe(curve_df, use_container_width=True)

               st.subheader("逐点误差（最优迭代）")
               best = top3[0]
               err_df = pd.DataFrame(best["points"])
               if not err_df.empty:
                   cols_to_show = [c for c in ["index", "prediction", "actual", "error", "abs_error", "pct_error"] if c in err_df.columns]
                   st.dataframe(err_df[cols_to_show], use_container_width=True)
                   if "pct_error" in err_df.columns:
                       chart_df = err_df.set_index("index")[["pct_error"]].rename(columns={"pct_error": "百分比误差(%)"})
                       st.bar_chart(chart_df)
           else:
               st.info("evaluate_mape 工具消息暂未携带逐点数据。")
       else:
           st.info("等待 evaluate_mape 工具返回逐点预测/真实值数据后展示。")
   with tabs[3]:
       if st.session_state.final_result:
           st.json(st.session_state.final_result)
           code = st.session_state.final_result.get("best_code") or ""
           if code:
               st.code(code, language="python")
       else:
           st.info("尚无最终结果。")

with right:
   submitted_n, requested_n = _count_approvals(st.session_state.events)
   st.subheader(f"人工审批（已处理 {submitted_n} / 共 {requested_n} 次）")
   decision = st.session_state.pending_decision
   if decision:
       decision_id = decision.get("id", "")
       st.warning(f"⚠️ 第 {requested_n} 次审批 · {decision.get('summary', '等待审批')}")
       st.caption(f"决策 ID：`{decision_id[:12]}…`")
       # 人类可读的翻译说明
       st.markdown("---")
       st.markdown(_humanize_decision(decision, st.session_state.events))
       st.markdown("---")
       with st.expander("原始参数（JSON）", expanded=False):
           st.json(decision)
       feedback = st.text_area("审批意见", "同意继续执行。", key=f"fb_{decision_id}")
       c1, c2 = st.columns(2)
       if c1.button("批准", type="primary", use_container_width=True, key=f"approve_{decision_id}"):
           try:
               _post(f"/runs/{thread_id}/approve", {"decision_id": decision_id, "approved": True, "feedback": feedback})
               st.session_state.processed_decisions.add(decision_id)
               _append_event({
                   "type": "approval_submitted",
                   "thread_id": thread_id,
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "approval": {
                       "decision_id": decision_id,
                       "approved": True,
                       "feedback": feedback,
                       "tool_name": decision.get("tool_name", ""),
                   },
               })
               st.toast("✅ 已批准，Agent 继续执行", icon="✅")
               time.sleep(0.3)
               st.rerun()
           except Exception as e:  # noqa: BLE001
               st.error(f"审批失败：{e}")
       if c2.button("拒绝并终止", use_container_width=True, key=f"reject_{decision_id}"):
           try:
               _post(f"/runs/{thread_id}/approve", {"decision_id": decision_id, "approved": False, "feedback": feedback})
               st.session_state.processed_decisions.add(decision_id)
               _append_event({
                   "type": "approval_submitted",
                   "thread_id": thread_id,
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "approval": {
                       "decision_id": decision_id,
                       "approved": False,
                       "feedback": feedback,
                       "tool_name": decision.get("tool_name", ""),
                   },
               })
               st.session_state.run_status = "cancelled"
               st.toast("⛔ 已拒绝，运行终止", icon="⛔")
               st.rerun()
           except Exception as e:  # noqa: BLE001
               st.error(f"拒绝失败：{e}")
   else:
       # 区分三种“无审批”状态
       if st.session_state.run_status == "waiting_for_user":
           # 后端仍在等审批，但前端 pending 为空 — 数据不一致，提示刷新
           st.warning("⏳ 后端报告仍在等待审批，但本地数据未同步。请点击侧栏「刷新当前会话」。")
       elif submitted_n > 0 and st.session_state.run_status in {"running", "starting"}:
           st.info(f"✅ 第 {submitted_n} 次审批已提交，Agent 正在进入下一步…")
           pend = _pending_subagent(st.session_state.events)
           if pend:
               label, elapsed, expected = pend
               pct = min(elapsed / max(expected, 1), 0.95)
               st.progress(pct, text=f"{label} · 已用 {int(elapsed)}s")
       elif st.session_state.run_status in {"completed", "failed", "cancelled"}:
           st.success(f"运行已结束（{_STATUS_LABEL.get(st.session_state.run_status, st.session_state.run_status)}）")
       else:
           st.success("当前没有待审批动作。")

   st.divider()
   st.subheader("底部人机对话")
   for item in st.session_state.chat[-8:]:
       with st.chat_message(item["role"]):
           st.markdown(item["content"])
   user_msg = st.chat_input("输入约束、反馈、暂停/继续/重新规划等自然语言指令")
   if user_msg and thread_id:
       st.session_state.chat.append({"role": "user", "content": user_msg})
       action = "note"
       if "暂停" in user_msg:
           action = "pause"
       elif "继续" in user_msg:
           action = "resume"
       elif "停止" in user_msg or "终止" in user_msg:
           action = "stop"
       elif "重新规划" in user_msg or "replan" in user_msg.lower():
           action = "replan"
       try:
           _post(f"/runs/{thread_id}/feedback", {"message": user_msg, "action": action})
           st.session_state.chat.append({"role": "assistant", "content": "已收到反馈，并写入当前会话事件流。"})
           st.rerun()
       except Exception as e:  # noqa: BLE001
           st.error(f"反馈提交失败：{e}")

   st.divider()
   st.subheader("运行控制")
   c1, c2 = st.columns(2)
   if c1.button("继续", use_container_width=True, disabled=not bool(thread_id)):
       try:
           _post(f"/runs/{thread_id}/resume")
           st.session_state.run_status = "running"
           st.rerun()
       except Exception as e:  # noqa: BLE001
           st.error(f"继续失败：{e}")
   if c2.button("终止", use_container_width=True, disabled=not bool(thread_id)):
       try:
           _post(f"/runs/{thread_id}/cancel")
           st.session_state.run_status = "cancelled"
           st.rerun()
       except Exception as e:  # noqa: BLE001
           st.error(f"终止失败：{e}")

if not thread_id:
   st.info("在左侧配置任务后点击“启动交互式 Agent”。")
