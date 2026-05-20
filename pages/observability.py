"""CastFlow 可观测性 Dashboard

展示 Agent 运行指标：MAPE 分布、工具调用频次、耗时、Token 成本估算。
数据来源：eval/last_report.json + Langfuse API（可选）。
"""
import json
from pathlib import Path

import streamlit as st
import pandas as pd

st.set_page_config(page_title="CastFlow Observability", layout="wide")
st.title("CastFlow 可观测性 Dashboard")

EVAL_PATH = Path(__file__).resolve().parents[1] / "eval" / "last_report.json"


@st.cache_data(ttl=30)
def load_eval_data() -> list[dict]:
    if not EVAL_PATH.exists():
        return []
    with open(EVAL_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_langfuse_traces():
    try:
        from castflow.tracing.langfuse_setup import get_langfuse_client
        client = get_langfuse_client()
        if client is None:
            return None
        traces = client.fetch_traces(limit=50).data
        return traces
    except Exception:
        return None


# --- 本地 Eval 数据 ---
eval_data = load_eval_data()

if not eval_data:
    st.warning("未找到 eval/last_report.json，请先运行 eval 流程。")
else:
    df = pd.DataFrame(eval_data)

    # KPI 卡片
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总运行次数", len(df))
    col2.metric("平均 MAPE", f"{df['actual_mape'].mean():.2f}%")
    col3.metric("平均耗时", f"{df['elapsed_sec'].mean():.0f}s")
    pass_rate = (df["objective"].apply(lambda x: x.get("score", 0)) == 1).mean()
    col4.metric("达标率", f"{pass_rate * 100:.0f}%")

    st.divider()

    # MAPE 分布
    left, right = st.columns(2)
    with left:
        st.subheader("MAPE 分布")
        st.bar_chart(df.set_index("case")["actual_mape"])

    # 耗时分布
    with right:
        st.subheader("运行耗时 (秒)")
        st.bar_chart(df.set_index("case")["elapsed_sec"])

    # 工具调用频次
    st.subheader("工具调用频次")
    from collections import Counter
    tool_counter: Counter = Counter()
    for seq in df["tool_seq"]:
        tool_counter.update(seq)
    if tool_counter:
        tool_df = pd.DataFrame(
            tool_counter.most_common(),
            columns=["工具", "调用次数"],
        ).set_index("工具")
        st.bar_chart(tool_df)

    # Token 成本估算
    st.subheader("Token 成本估算")
    st.caption("基于 DashScope qwen-plus 定价：输入 0.004元/千token，输出 0.012元/千token")
    avg_input_tokens = 8000
    avg_output_tokens = 3000
    runs = len(df)
    avg_iters = df["tool_seq"].apply(len).mean()
    total_input = runs * avg_iters * avg_input_tokens
    total_output = runs * avg_iters * avg_output_tokens
    cost_input = total_input / 1000 * 0.004
    cost_output = total_output / 1000 * 0.012
    cost_df = pd.DataFrame({
        "类别": ["输入 Token", "输出 Token", "合计"],
        "Token 数": [f"{total_input:,.0f}", f"{total_output:,.0f}", f"{total_input + total_output:,.0f}"],
        "估算费用 (元)": [f"¥{cost_input:.2f}", f"¥{cost_output:.2f}", f"¥{cost_input + cost_output:.2f}"],
    })
    st.table(cost_df)

# --- Langfuse 实时数据 ---
st.divider()
st.subheader("Langfuse 实时 Traces")

traces = load_langfuse_traces()
if traces is None:
    st.info("Langfuse 未配置或不可用。设置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 后可查看实时 trace。")
else:
    if not traces:
        st.info("暂无 trace 数据。")
    else:
        trace_rows = []
        for t in traces:
            trace_rows.append({
                "ID": t.id[:8],
                "名称": t.name or "-",
                "状态": t.status or "-",
                "耗时(ms)": t.latency or 0,
                "输入Token": t.input_cost or 0,
                "输出Token": t.output_cost or 0,
                "时间": str(t.timestamp)[:19] if t.timestamp else "-",
            })
        st.dataframe(pd.DataFrame(trace_rows), use_container_width=True)
