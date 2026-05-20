"""CastFlow 调度器管理页面

控制轮询式调度器：启动/停止、查看状态、管理已触发记录。
"""
from __future__ import annotations

from typing import Optional

import requests
import streamlit as st

API_BASE = "http://127.0.0.1:8000"

st.set_page_config(page_title="CastFlow 调度器", page_icon="⏰", layout="wide")
st.title("调度器管理")


def _api(method: str, path: str, **kwargs) -> Optional[dict]:
    try:
        resp = getattr(requests, method)(f"{API_BASE}{path}", timeout=5, **kwargs)
        if resp.status_code < 400:
            return resp.json()
        st.error(f"API 错误: {resp.status_code} — {resp.text}")
    except requests.ConnectionError:
        st.error("无法连接后端 API，请确认 FastAPI 服务已启动 (uvicorn api.server:app)")
    except Exception as e:
        st.error(f"请求失败: {e}")
    return None


status = _api("get", "/scheduler/status")
if status is None:
    st.stop()

# --- 状态概览 ---
col1, col2, col3 = st.columns(3)
with col1:
    if status["running"]:
        st.success("运行中", icon="🟢")
    else:
        st.warning("已停止", icon="🔴")
with col2:
    st.metric("轮询间隔", f"{status['interval_sec']}s")
with col3:
    st.metric("已触发次数", status["triggered_count"])

st.divider()

# --- 控制按钮 ---
st.subheader("控制")
btn_col1, btn_col2, btn_col3 = st.columns(3)

with btn_col1:
    if status["running"]:
        if st.button("停止调度器", type="primary", use_container_width=True):
            result = _api("post", "/scheduler/stop")
            if result:
                st.success("调度器已停止")
                st.rerun()
    else:
        if st.button("启动调度器", type="primary", use_container_width=True):
            result = _api("post", "/scheduler/start")
            if result:
                st.success("调度器已启动")
                st.rerun()

with btn_col2:
    if st.button("重置触发记录", use_container_width=True):
        result = _api("post", "/scheduler/reset")
        if result:
            st.success("触发记录已清空")
            st.rerun()

with btn_col3:
    if st.button("刷新状态", use_container_width=True):
        st.rerun()

# --- 监控月份 ---
st.subheader("监控月份")
months = status.get("target_months", [])
if months:
    st.write("  ".join(f"`{m}`" for m in months))
else:
    st.info("未配置目标月份（默认监控当前年份 1-12 月）")

# --- 已触发记录 ---
st.subheader("已触发记录")
records = status.get("triggered_records", {})
if not records:
    st.info("暂无触发记录。调度器检测到新真实值后会自动触发预测 run。")
else:
    for key, triggered_at in records.items():
        col_a, col_b, col_c = st.columns([3, 3, 1])
        with col_a:
            st.text(key)
        with col_b:
            st.text(triggered_at)
        with col_c:
            if st.button("删除", key=f"del_{key}"):
                result = _api("delete", f"/scheduler/triggered/{key}")
                if result:
                    st.rerun()

# --- 说明 ---
st.divider()
with st.expander("使用说明"):
    st.markdown("""
**工作原理**：调度器后台线程定时轮询数据库，检查是否有新的真实值入库。
一旦检测到某个 (区县, 月份) 有新真实值且尚未触发过预测，就自动创建一个 forecast run。

**配置项**（`.env` 文件）：
- `SCHEDULER_ENABLED=true` — 启动 API 时自动开启调度器
- `SCHEDULER_INTERVAL_SEC=300` — 轮询间隔（秒）
- `SCHEDULER_TARGET_MONTHS=` — 监控月份列表（逗号分隔，留空=当前年份全部月份）

**注意**：
- MockDB 模式下所有月份都有数据，调度器会立即触发所有 run
- 建议在 `USE_REAL_DB=true` 时使用调度器
- 重置触发记录后，调度器会重新检测并触发
""")
