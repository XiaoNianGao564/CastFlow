"""通义千问 LLM 客户端（走 OpenAI 兼容协议，更稳定且对 tool-calling 支持好）。"""
from langchain_openai import ChatOpenAI

from castflow.config import settings

QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def make_orchestrator_llm() -> ChatOpenAI:
    """主图 Orchestrator 用 qwen-max + 低温度，决策更稳。"""
    return ChatOpenAI(
        model=settings.llm_model,
        temperature=0.3,
        api_key=settings.dashscope_api_key,
        base_url=QWEN_BASE_URL,
    )


def make_subagent_llm() -> ChatOpenAI:
    """子 Agent 用 qwen-plus + 高一点温度，探索性强。"""
    return ChatOpenAI(
        model=settings.llm_model_subagent,
        temperature=0.6,
        api_key=settings.dashscope_api_key,
        base_url=QWEN_BASE_URL,
    )
