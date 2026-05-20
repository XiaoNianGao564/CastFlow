"""Multi-tier model routing with token budget tracking.

三层模型路由：
- NANO: 轻量任务（verifier 审查、JSON 提取）
- LIGHT: 编排/反思/中等推理（orchestrator、reflector）
- HEAVY: 代码生成、复杂规划（coder、planner）

Token budget 追踪通过 LangChain callback 自动完成，
超预算时 BudgetExhaustedError 触发早停。
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Optional

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import LLMResult
from langchain_openai import ChatOpenAI
from openai import APIStatusError, APITimeoutError, RateLimitError

from castflow.config import settings

logger = logging.getLogger(__name__)

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_FALLBACK_EXCEPTIONS = (RateLimitError, APIStatusError, APITimeoutError)


class Tier(Enum):
    NANO = "nano"
    LIGHT = "light"
    HEAVY = "heavy"


TIER_TIMEOUT: dict[Tier, int] = {
    Tier.NANO: 60,
    Tier.LIGHT: 120,
    Tier.HEAVY: 180,
}


MODEL_OF_TIER: dict[Tier, tuple[str, str]] = {
    Tier.NANO: ("qwen3.6-35b-a3b", "qwen3-14b"),
    Tier.LIGHT: ("qwen3.6-plus-2026-04-02", "qwen-plus-2025-12-01"),
    Tier.HEAVY: ("qwen-plus-2025-12-01", "qwen3-max-2025-09-23"),
}


class BudgetExhaustedError(Exception):
    """Token budget 耗尽时抛出，触发 orchestrator 早停。"""

    def __init__(self, tier: Tier, used: int, limit: int):
        self.tier = tier
        self.used = used
        self.limit = limit
        super().__init__(
            f"Budget exhausted for tier {tier.value}: "
            f"{used:,} tokens used, limit {limit:,}"
        )


# --------------- Token Budget Tracker ---------------

_lock = threading.Lock()
_token_usage: dict[Tier, dict[str, int]] = {
    Tier.NANO: {"prompt": 0, "completion": 0},
    Tier.LIGHT: {"prompt": 0, "completion": 0},
    Tier.HEAVY: {"prompt": 0, "completion": 0},
}


def get_token_usage(tier: Optional[Tier] = None) -> dict:
    with _lock:
        if tier is None:
            return {t: u.copy() for t, u in _token_usage.items()}
        return _token_usage[tier].copy()


def get_total_tokens() -> int:
    with _lock:
        return sum(
            u["prompt"] + u["completion"] for u in _token_usage.values()
        )


def reset_token_usage(tier: Optional[Tier] = None):
    with _lock:
        if tier is None:
            for t in Tier:
                _token_usage[t] = {"prompt": 0, "completion": 0}
        else:
            _token_usage[tier] = {"prompt": 0, "completion": 0}


class _BudgetCallbackHandler(BaseCallbackHandler):
    """LangChain callback：自动追踪每次 LLM 调用的 token 消耗。"""

    def __init__(self, tier: Tier):
        self.tier = tier

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        if not response.llm_output:
            return
        usage = response.llm_output.get("token_usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        if prompt_tokens == 0 and completion_tokens == 0:
            return

        with _lock:
            _token_usage[self.tier]["prompt"] += prompt_tokens
            _token_usage[self.tier]["completion"] += completion_tokens

        total = get_total_tokens()
        budget_limit = settings.token_budget_total
        logger.debug(
            f"[{self.tier.value}] +{prompt_tokens}p +{completion_tokens}c "
            f"| total={total:,}/{budget_limit:,}"
        )

        if budget_limit > 0 and total > budget_limit:
            raise BudgetExhaustedError(self.tier, total, budget_limit)


# --------------- LLM Factory ---------------

def make_llm(
    tier: Tier,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> BaseChatModel:
    """创建指定 tier 的 LLM，带 fallback 和 token 追踪。"""
    primary_model, fallback_model = MODEL_OF_TIER[tier]
    api_key = settings.dashscope_api_key
    callback = _BudgetCallbackHandler(tier)
    timeout = TIER_TIMEOUT[tier]

    def _mk(model: str) -> ChatOpenAI:
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=DASHSCOPE_BASE_URL,
            timeout=timeout,
            max_retries=2,
            callbacks=[callback],
        )

    primary = _mk(primary_model)
    fallback = _mk(fallback_model)
    llm = primary.with_fallbacks([fallback], exceptions_to_handle=_FALLBACK_EXCEPTIONS)

    logger.info(f"[Router] {tier.value}: {primary_model} → {fallback_model} (timeout={timeout}s)")
    return llm


def log_budget_summary() -> dict[str, int]:
    """输出 token 使用汇总，返回统计 dict。"""
    summary = {}
    total_prompt = 0
    total_completion = 0

    logger.info("=" * 50)
    logger.info("Token Budget Summary")
    logger.info("=" * 50)

    for tier in Tier:
        usage = get_token_usage(tier)
        p, c = usage["prompt"], usage["completion"]
        total_prompt += p
        total_completion += c
        tier_total = p + c
        summary[f"{tier.value}_prompt"] = p
        summary[f"{tier.value}_completion"] = c
        summary[f"{tier.value}_total"] = tier_total
        logger.info(f"  {tier.value:6s}: {p:>8,}p + {c:>8,}c = {tier_total:>8,}")

    total = total_prompt + total_completion
    summary["total_prompt"] = total_prompt
    summary["total_completion"] = total_completion
    summary["total"] = total
    budget = settings.token_budget_total
    pct = (total / budget * 100) if budget > 0 else 0
    logger.info(f"  {'TOTAL':6s}: {total_prompt:>8,}p + {total_completion:>8,}c = {total:>8,}")
    logger.info(f"  Budget: {total:,} / {budget:,} ({pct:.1f}%)")
    logger.info("=" * 50)

    return summary
