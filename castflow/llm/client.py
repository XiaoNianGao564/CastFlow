"""LLM 客户端入口 — 基于 router 的多层模型路由。

对外接口保持不变：
- make_orchestrator_llm() → LIGHT tier (编排/规划)
- make_subagent_llm()     → HEAVY tier (代码生成)
- make_verifier_llm()     → NANO tier  (审查/提取)

所有 token 消耗自动追踪到 router 的 budget 系统。
"""
from langchain_core.language_models import BaseChatModel

from castflow.llm.router import Tier, make_llm


def make_orchestrator_llm() -> BaseChatModel:
    """主图 Orchestrator / Planner：LIGHT tier，低温度决策稳。"""
    return make_llm(Tier.LIGHT, temperature=0.3)


def make_subagent_llm() -> BaseChatModel:
    """Coder / Reflector 子 Agent：HEAVY tier，高温度探索性强。"""
    return make_llm(Tier.HEAVY, temperature=0.6)


def make_verifier_llm() -> BaseChatModel:
    """Verifier 审查：NANO tier，轻量快速。"""
    return make_llm(Tier.NANO, temperature=0.1)
