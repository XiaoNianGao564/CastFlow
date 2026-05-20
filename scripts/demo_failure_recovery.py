"""Failure recovery demo.

演示 Reflector 如何从失败上下文中提取根因、保存 lesson，并给出下一步策略建议。
"""
from __future__ import annotations

import json
import sys

from dotenv import load_dotenv

load_dotenv()

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from castflow.subagents.reflector import delegate_to_reflector


def main() -> None:
    failed_attempt = """
模型: high-order SARIMAX
场景: 耀州 2026-12 月度负荷预测
现象: 连续两轮 validation MAPE > 25%，且 statsmodels 收敛警告反复出现。
代码片段: SARIMAX(series, order=(3,1,3), seasonal_order=(2,1,2,12)).fit()
stderr: ConvergenceWarning: Maximum Likelihood optimization failed to converge
候选摘要: seasonal_naive_12 validation_mape=4.1, high-order SARIMAX validation_mape=28.6
"""
    context = """
目标: 预测耀州 2026-12 电力负荷，MAPE <= 5%。
已有策略: Coder 已比较 baseline 和复杂 SARIMAX；复杂模型明显差于 baseline。
希望 Reflector 输出根因、保存 lesson，并给出下一轮 replan 策略。
"""
    result = delegate_to_reflector.invoke(
        {
            "failed_attempt": failed_attempt,
            "context": context,
            "pattern": "low-mape-plateau",
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
