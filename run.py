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

# legacy_windows=False 关闭 Win32 控制台 API 直写，避免 GBK 编码崩溃
console = Console(legacy_windows=False, force_terminal=True)


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
            console.print(Panel(str(msg.content)[:800], title="LLM", border_style="cyan"))
    elif mtype == "tool":
        name = getattr(msg, "name", "?")
        console.print(
            Panel(
                str(msg.content)[:600],
                title=f"Tool Result · {name}",
                border_style="green",
            )
        )


def main() -> None:
    org = sys.argv[1] if len(sys.argv) > 1 else "耀州"
    target_month = sys.argv[2] if len(sys.argv) > 2 else "2026-12"

    if not settings.dashscope_api_key:
        console.print("[red]缺少 DASHSCOPE_API_KEY，请编辑 .env 后再运行[/]")
        sys.exit(1)

    graph = build_graph()

    initial = {
        "goal": f"预测 {org} 区 {target_month} 月电力负荷，MAPE 不超过 {settings.target_mape}%",
        "org": org,
        "target_month": target_month,
        "target_mape": settings.target_mape,
        "max_iterations": settings.max_iterations,
        "iteration_count": 0,
        "messages": [
            HumanMessage(
                content=(
                    f"请预测【{org}】区 {target_month} 月的电力负荷（expect_type=1 区民用电），"
                    f"目标 MAPE ≤ {settings.target_mape}%。"
                    f"\n\n如果你不确定区县名是否正确，先调用 list_orgs 工具看可用区县列表。"
                )
            )
        ],
        "iterations": [],
        "best_mape": 999.0,
        "best_code": "",
    }

    config = {
        "configurable": {"thread_id": f"{org}-{target_month}"},
        "recursion_limit": 50,
    }

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
