"""Coder subagent - 代码生成专员（subagent-as-tool 模式）。

为什么独立成子 Agent：
- 主 Agent 关心"流程"（取数据→评估→反思→收尾），不该被代码细节淹没
- Coder 在自己的 context 里 ReAct，写完一遍跑通才回主 Agent
- 失败的中间代码 / stderr 留在 Coder context，主 Agent 只看到最终成品摘要
- 用 qwen-plus + 高温度（探索性强），主 Agent 用 qwen-max + 低温度（决策稳）

实现：用 langgraph.prebuilt.create_react_agent 拉一个独立子图，
绑定 load_history + run_python，让 Coder 自主迭代。
"""
from __future__ import annotations

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from castflow.llm.client import make_subagent_llm
from castflow.tools.data import load_history
from castflow.tools.python_exec import run_python

CODER_SYSTEM = """你是预测代码工程师 (Coder)。

# 你的目标
拿到任务描述后，独立完成：load_history → 写 Python → run_python → 跑通 → 返回最终代码。

# 工具
- load_history(org, months, before_month): 取历史月度负荷
- run_python(code): 执行 Python，检查 returncode 和 parsed 字段

# 工作方式
1. 用 load_history 拿训练数据（必须传 before_month 防数据泄漏）
2. 根据任务选模型（ARIMA / SARIMAX / LightGBM）
3. 写完整 Python 代码，把 hist 列表写死，代码末尾必须
   print(json.dumps({"predictions": [一个数字]}))
4. run_python 执行，检查 returncode == 0 且 parsed 不为 None
5. 失败 → 读 stderr 找根因 → 改代码再跑（最多 4 次尝试）
6. 跑通 → 用一句话总结你做了什么，并返回最终代码字符串

# 重要原则
- 历史数据必须严格来自 load_history 的真实返回，**绝不允许凭空编造数字**
- 代码必须 returncode==0 才算跑通
- pandas 新版用 series.ffill() / .bfill()，不要 fillna(method=)
- Prophet 包名是 prophet 不是 fbprophet（且当前环境未装，优先 ARIMA/SARIMAX）
- 不要嵌套函数、不要写 main()，直接平铺，最后一行 print

# 输出格式
最后一条 AI 回复要包含：
1. 一段简短诊断（你最终选了什么模型、几次尝试跑通）
2. 跑通的最终代码（用 ```python ``` 包起来）

主 Agent 拿到这两块就能继续后续的评估流程。
"""


def _build_coder_agent():
    """懒构建子图，避免 import 阶段就调起 LLM。"""
    llm = make_subagent_llm()
    return create_react_agent(
        llm,
        tools=[load_history, run_python],
        prompt=CODER_SYSTEM,
    )


_coder = None


def _get_coder():
    global _coder
    if _coder is None:
        _coder = _build_coder_agent()
    return _coder


def _extract_code(text: str) -> str:
    """从 LLM 回复里抽取 ```python ... ``` 块。"""
    if not text:
        return ""
    if "```python" in text:
        chunk = text.split("```python", 1)[1]
        if "```" in chunk:
            return chunk.split("```", 1)[0].strip()
    if "```" in text:
        chunk = text.split("```", 1)[1]
        if "```" in chunk:
            return chunk.split("```", 1)[0].strip()
    return ""


@tool
def delegate_to_coder(
    task: str,
    org: str,
    target_month: str,
    constraints: str = "",
    avoid_models: str = "",
) -> dict:
    """委派给代码生成子 Agent。它会独立 load_history + run_python 跑通后才返回。

    使用场景：主 Agent 想要一份新的预测代码（首次或换策略），
    把任务描述 + 区县 + 目标月给它，子 Agent 自己写自己跑。
    主 Agent 只看到最终代码 + 简短诊断，节省主 context。

    Args:
        task: 任务描述，例如 "为 耀州 区写一份预测 2026-12 月负荷的代码"
        org: 区县名（中文，从 list_orgs 拿到的真实值）
        target_month: 目标预测月，YYYY-MM。Coder 必须用这个值传给 load_history 的 before_month
        constraints: 额外约束，例如 "用 SARIMAX seasonal_order=(0,1,1,12)"
        avoid_models: 之前失败过的模型名，逗号分隔，例如 "lightgbm,prophet"
    """
    coder = _get_coder()

    user_msg = (
        f"## 任务\n{task}\n\n"
        f"## 区县\n{org}\n\n"
        f"## 目标月\n{target_month}\n\n"
        f"## 额外约束\n{constraints or '无'}\n\n"
        f"## 避免使用的模型\n{avoid_models or '无'}\n\n"
        f"请用 load_history(org='{org}', months=36, before_month='{target_month}') 拿数据，"
        f"然后选合适的模型写代码并 run_python 跑通。"
    )

    try:
        result = coder.invoke(
            {"messages": [("user", user_msg)]},
            config={"recursion_limit": 25},
        )
    except Exception as e:
        return {"error": f"coder agent crashed: {e}"}

    msgs = result.get("messages", [])
    final_text = ""
    for m in reversed(msgs):
        if getattr(m, "type", None) == "ai" and getattr(m, "content", ""):
            final_text = m.content
            break

    # 统计 Coder 内部跑了几次 run_python
    tool_calls_count = 0
    successful_run = False
    for m in msgs:
        if getattr(m, "type", None) == "ai":
            for tc in getattr(m, "tool_calls", None) or []:
                if tc.get("name") == "run_python":
                    tool_calls_count += 1
        elif getattr(m, "type", None) == "tool" and getattr(m, "name", None) == "run_python":
            content = str(getattr(m, "content", ""))
            if '"returncode": 0' in content and '"parsed":' in content:
                successful_run = True

    return {
        "summary": final_text[:600],
        "code": _extract_code(final_text),
        "code_runs": tool_calls_count,
        "success": successful_run,
        "internal_steps": len(msgs),
    }
