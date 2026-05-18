"""Langfuse 全链路 trace 集成。

Langfuse 是国内可自部署的开源 LLM 可观测性平台，
LangChain/LangGraph 通过 callback 机制原生支持。

启用方式：
    1. 自部署 Langfuse Docker（或用云版）
    2. .env 填 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST
    3. run.py 调用 maybe_get_langfuse_callback()，把返回的 handler
       塞进 graph.invoke(config={"callbacks": [handler]})

如果三个环境变量都没设置，本模块返回 None，graceful 降级（不影响主流程）。
"""
from __future__ import annotations

import os
from typing import Optional


def maybe_get_langfuse_callback():
    """返回 Langfuse CallbackHandler 或 None。

    None 表示未配置（key 缺失），主流程应直接跳过 trace 不报错。
    """
    pub = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    sec = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    if not pub or not sec:
        return None

    try:
        # langfuse 4.x：CallbackHandler 在 langfuse.langchain
        from langfuse.langchain import CallbackHandler
        from langfuse import Langfuse

        # 显式构造 client（确保 host/key 走我们配的环境变量）
        Langfuse(
            public_key=pub,
            secret_key=sec,
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
        return CallbackHandler()
    except Exception as e:
        # 装了包但运行失败（如网络问题）→ 降级 None，主流程继续
        print(f"[langfuse] disabled due to error: {e}")
        return None


def trace_metadata(org: str, target_month: str) -> dict:
    """统一的 trace 元数据格式，便于在 Langfuse UI 按业务字段过滤。"""
    return {
        "session_id": f"{org}-{target_month}",
        "tags": ["castflow", "forecast", org],
        "metadata": {
            "org": org,
            "target_month": target_month,
            "stage": "B5",
        },
    }
