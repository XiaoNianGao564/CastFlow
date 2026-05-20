"""Verifier subagent - 候选代码门禁审查。

职责边界：
- Coder 负责生成候选代码
- Verifier 负责在最终目标月评估前检查代码是否可信、是否有泄漏/作弊/格式风险
- Reflector 负责失败后的根因复盘与经验沉淀
"""
from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from castflow.tools.schema import tool_success

from castflow.llm.client import make_verifier_llm

VERIFIER_SYSTEM = """你是电力负荷预测代码审查专家（Verifier）。

# 你的目标
在主 Agent 使用目标月真实值评估前，检查候选代码是否可信。
你不是最终评估器，不能使用目标月真实值算 MAPE；你只做门禁审查。

# 重点检查
1. 数据泄漏：是否使用目标月真实值、actual、load_actual、硬编码目标月答案
2. 输出协议：最后是否 print JSON，且包含 predictions
3. 训练边界：是否只基于 target_month 之前历史预测目标月
4. 代码风险：是否读取外部文件、联网、执行危险命令、依赖不存在包
5. 业务合理性：预测值是否来自模型/基线逻辑，而不是拍脑袋常数

# 输出要求
只输出一段简短 JSON，不要 markdown：
{
  "approved": true/false,
  "risk": "low|medium|high",
  "issues": ["..."],
  "suggested_action": "proceed_to_eval|retry_coder|call_reflector",
  "summary": "一句话说明"
}
"""


def _extract_json(text: str) -> dict:
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 非贪婪匹配，逐个尝试找到合法 JSON 对象
    for match in re.finditer(r"\{[\s\S]*?\}", text):
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
    return {}


def _static_checks(code: str, target_month: str) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    risk_flags: list[str] = []
    lowered = code.lower()

    banned = ["load_actual", "requests.", "urllib", "subprocess", "os.system", "socket"]
    for token in banned:
        if token in lowered:
            issues.append(f"代码包含高风险片段: {token}")
            risk_flags.append("unsafe_or_leakage_prone_code")

    # open( 只在非注释行检测，避免误杀注释
    for line in code.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "open(" in stripped.lower() and "# safe" not in stripped.lower():
            issues.append("代码包含文件 I/O 操作: open()")
            risk_flags.append("unsafe_or_leakage_prone_code")
            break

    if "print" not in lowered or "predictions" not in lowered:
        issues.append("代码没有明显输出 predictions JSON")
        risk_flags.append("invalid_output_protocol")

    if target_month in code and "load_actual" in lowered:
        issues.append("代码疑似围绕目标月真实值取数")
        risk_flags.append("target_month_leakage")

    return issues, risk_flags


@tool
def delegate_to_verifier(
    task: str,
    org: str,
    target_month: str,
    code: str,
    best_model: str = "",
    best_validation_mape: float | None = None,
    candidate_summaries: str = "",
    constraints: str = "",
) -> dict:
    """审查候选代码是否可以进入最终目标月评估。

    Args:
        task: 当前预测任务
        org: 区县名
        target_month: 目标预测月 YYYY-MM
        code: Coder 返回的候选代码
        best_model: Coder 选出的最佳模型名
        best_validation_mape: Coder 内部 validation MAPE
        candidate_summaries: 候选比较摘要，可传 JSON 字符串
        constraints: 额外约束
    """
    static_issues, risk_flags = _static_checks(code, target_month)
    if static_issues:
        payload = {
            "approved": False,
            "risk": "high",
            "issues": static_issues,
            "risk_flags": risk_flags,
            "suggested_action": "retry_coder",
            "code": code,
            "best_model": best_model,
            "best_validation_mape": best_validation_mape,
        }
        return tool_success(
            "静态审查发现高风险代码，不能进入最终评估。",
            data=payload,
            next_action="回到 coder 修正候选代码。",
            approved=False,
            risk="high",
            issues=static_issues,
            suggested_action="retry_coder",
        )

    llm = make_verifier_llm()
    response = llm.invoke(
        [
            SystemMessage(content=VERIFIER_SYSTEM),
            HumanMessage(
                content=(
                    f"## 任务\n{task}\n\n"
                    f"## 区县/目标月\n{org} / {target_month}\n\n"
                    f"## 最佳模型\n{best_model}\n\n"
                    f"## validation MAPE\n{best_validation_mape}\n\n"
                    f"## 候选摘要\n{candidate_summaries[:1500]}\n\n"
                    f"## 额外约束\n{constraints or '无'}\n\n"
                    f"## 待审查代码\n```python\n{code[:4000]}\n```"
                )
            ),
        ]
    )
    parsed = _extract_json(response.content or "")
    approved = bool(parsed.get("approved", False))  # 解析失败默认拒绝
    risk = parsed.get("risk", "low" if approved else "medium")
    issues = parsed.get("issues") or []
    suggested_action = parsed.get("suggested_action") or ("proceed_to_eval" if approved else "retry_coder")
    summary = parsed.get("summary") or ("审查通过。" if approved else "审查未通过。")
    payload = {
        "approved": approved,
        "risk": risk,
        "issues": issues,
        "risk_flags": risk_flags,
        "suggested_action": suggested_action,
        "code": code,
        "best_model": best_model,
        "best_validation_mape": best_validation_mape,
    }
    # 顶层只放审查结论，不放 code 全文（减少 ToolMessage 体积）
    return tool_success(
        summary[:500],
        data=payload,
        next_action=suggested_action,
        approved=approved,
        risk=risk,
        issues=issues,
        suggested_action=suggested_action,
        best_model=best_model,
        best_validation_mape=best_validation_mape,
    )
