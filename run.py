"""CastFlow Agent CLI 入口。

用法：
    python run.py                       # 默认跑 TC01 / 2026-01
    python run.py TC02 2026-02
"""
import sys

from dotenv import load_dotenv

load_dotenv()

# Windows 默认 GBK 终端打印不出非 BMP 字符，强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from rich.console import Console
from rich.panel import Panel
from langchain_core.messages import HumanMessage

from castflow.config import settings
from castflow.graph.orchestrator import build_graph
from castflow.tracing import maybe_get_langfuse_callback, trace_metadata

# legacy_windows=False 关闭 Win32 控制台 API 直写，避免 GBK 编码崩溃
console = Console(legacy_windows=False, force_terminal=True)


def _tool_result_style(name: str) -> tuple[str, str]:
    if name in {"recall_similar_runs", "recall_lessons"}:
        return "Memory Trace", "blue"
    if name == "delegate_to_coder":
        return "Coder Candidates", "magenta"
    if name == "delegate_to_verifier":
        return "Verifier Gate", "red"
    if name == "delegate_to_reflector":
        return "Reflector Replan", "cyan"
    return f"Tool Result · {name}", "green"


def _print_message(msg) -> None:
    mtype = getattr(msg, "type", "?")
    if mtype == "ai":
        tool_calls = getattr(msg, "tool_calls", None) or []
        if tool_calls:
            for tc in tool_calls:
                args = str(tc.get("args", ""))[:400]
                console.print(
                    Panel(
                        f"[bold]{tc['name']}[/]\n[dim]{args}[/]",
                        title="Tool Call",
                        border_style="yellow",
                    )
                )
        elif msg.content:
            console.print(Panel(str(msg.content)[:800], title="LLM / Plan", border_style="cyan"))
    elif mtype == "tool":
        name = getattr(msg, "name", "?")
        title, style = _tool_result_style(name)
        console.print(
            Panel(
                str(msg.content)[:900],
                title=title,
                border_style=style,
            )
        )


def main() -> None:
    org = sys.argv[1] if len(sys.argv) > 1 else "耀州"
    target_month = sys.argv[2] if len(sys.argv) > 2 else "2026"

    if not settings.dashscope_api_key:
        console.print("[red]缺少 DASHSCOPE_API_KEY，请编辑 .env 后再运行[/]")
        sys.exit(1)

    # 检测年份模式：纯4位数字 = 全年预测
    tm = target_month.strip()
    if len(tm) == 4 and tm.isdigit():
        target_months = [f"{tm}-{m:02d}" for m in range(1, 13)]
        period_desc = f"{tm} 年全年（1~12 月）"
        before_month = target_months[0]  # 用第一个月做防泄漏截断
        human_msg = (
            f"请预测【{org}】区 {period_desc} 的电力负荷（expect_type=1 区民用电），"
            f"目标 MAPE ≤ {settings.target_mape}%。\n\n"
            f"目标月份列表：{target_months}\n"
            f"你需要逐月预测 12 个月，load_history 的 before_month 传 \"{before_month}\"，"
            f"Coder 生成的代码末尾 print 的 JSON 必须包含 12 个 predictions。"
            f"\n\n如果你不确定区县名是否正确，先调用 list_orgs 工具看可用区县列表。"
        )
    else:
        target_months = [tm]
        period_desc = f"{tm} 月"
        before_month = tm
        human_msg = (
            f"请预测【{org}】区 {period_desc} 的电力负荷（expect_type=1 区民用电），"
            f"目标 MAPE ≤ {settings.target_mape}%。"
            f"\n\n如果你不确定区县名是否正确，先调用 list_orgs 工具看可用区县列表。"
        )

    graph = build_graph()

    initial = {
        "goal": f"预测 {org} 区 {period_desc} 电力负荷，MAPE 不超过 {settings.target_mape}%",
        "org": org,
        "target_month": target_month,
        "target_months": target_months,
        "target_mape": settings.target_mape,
        "max_iterations": settings.max_iterations,
        "iteration_count": 0,
        "messages": [HumanMessage(content=human_msg)],
        "iterations": [],
        "best_mape": 999.0,
        "best_code": "",
        "plan_history": [],
    }

    config = {
        "configurable": {"thread_id": f"{org}-{target_month}"},
        # 全年模式需要更多递归次数（12个月 × 多次迭代）
        "recursion_limit": 200 if len(target_months) > 1 else 50,
    }

    # B5: Langfuse trace（缺 key 时 graceful 降级，不影响主流程）
    lf_handler = maybe_get_langfuse_callback()
    if lf_handler is not None:
        config["callbacks"] = [lf_handler]
        config.update(trace_metadata(org, target_month))
        console.print("[dim]→ Langfuse trace enabled[/]")
    else:
        console.print("[dim]→ Langfuse trace disabled (set LANGFUSE_* in .env to enable)[/]")

    console.rule(f"[bold]CastFlow Agent · {org} / {target_month}[/]")
    seen = 0
    for event in graph.stream(initial, config=config, stream_mode="values"):
        msgs = event.get("messages", [])
        for m in msgs[seen:]:
            _print_message(m)
        seen = len(msgs)

    console.rule("[bold green]Done[/]")


if __name__ == "__main__":
    main()
