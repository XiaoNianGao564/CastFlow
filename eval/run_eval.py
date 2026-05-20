"""Eval runner - 跑一组 case，对每个 case 运行 Agent 并打分。

用法：
    python -m eval.run_eval                       # 跑全部 case
    python -m eval.run_eval --case yaozhou_baseline  # 只跑指定 case
    python -m eval.run_eval --no-llm-judge         # 跳过 LLM Judge 省 token
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

load_dotenv()

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from langchain_core.messages import HumanMessage
from rich.console import Console
from rich.table import Table

from castflow.graph.orchestrator import build_graph
from eval.judges.llm_judge import llm_judge
from eval.judges.mape_judge import mape_judge

console = Console(legacy_windows=False, force_terminal=True)
CASES_DIR = Path(__file__).resolve().parent / "cases"


def _parse_tool_payload(content) -> dict:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return {}
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def load_cases(name_filter: Optional[str] = None) -> list[dict]:
    out = []
    for p in sorted(CASES_DIR.glob("*.yaml")):
        case = yaml.safe_load(p.read_text(encoding="utf-8"))
        case["_name"] = p.stem
        if name_filter and name_filter not in p.stem:
            continue
        out.append(case)
    return out


def run_one(case: dict) -> dict:
    """跑一个 case 拿 Agent 输出。"""
    graph = build_graph()
    initial = {
        "goal": f"预测 {case['org']} 区 {case['target_month']} 月电力负荷，MAPE 不超过 {case['target_mape']}%",
        "org": case["org"],
        "target_month": case["target_month"],
        "target_mape": float(case["target_mape"]),
        "max_iterations": int(case.get("max_iterations", 6)),
        "iteration_count": 0,
        "messages": [
            HumanMessage(
                content=(
                    f"请预测【{case['org']}】区 {case['target_month']} 月的电力负荷，"
                    f"目标 MAPE ≤ {case['target_mape']}%。"
                )
            )
        ],
        "iterations": [],
        "best_mape": 999.0,
        "best_code": "",
    }
    config = {
        "configurable": {"thread_id": f"eval-{case['_name']}-{int(time.time())}"},
        "recursion_limit": 60,
    }

    t0 = time.time()
    final_state = None
    for event in graph.stream(initial, config=config, stream_mode="values"):
        final_state = event
    elapsed = time.time() - t0

    # 从 messages 抽取最后一次 evaluate_mape 的 mape 值
    mape: Optional[float] = None
    finalize_mape: Optional[float] = None
    tool_seq: list[str] = []
    for m in (final_state or {}).get("messages", []):
        mtype = getattr(m, "type", None)
        if mtype == "ai":
            for tc in getattr(m, "tool_calls", None) or []:
                tool_seq.append(tc["name"])
                if tc["name"] == "finalize":
                    args = tc.get("args", {})
                    val = args.get("best_mape")
                    if isinstance(val, (int, float)):
                        finalize_mape = float(val)
        elif mtype == "tool" and getattr(m, "name", None) == "evaluate_mape":
            payload = _parse_tool_payload(getattr(m, "content", ""))
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            val = data.get("mape") if isinstance(data, dict) else None
            if isinstance(val, (int, float)):
                mape = float(val)
            elif isinstance(val, str):
                try:
                    mape = float(val)
                except ValueError:
                    pass
            if mape is None:
                content = str(getattr(m, "content", ""))
                mt = re.search(r'"mape"\s*:\s*([\d.]+)', content)
                if mt:
                    try:
                        mape = float(mt.group(1))
                    except ValueError:
                        pass

    actual_mape = mape if mape is not None else finalize_mape
    memory_enabled = os.environ.get("CASTFLOW_EVAL_MEMORY_ABLATION", "0") != "1"
    return {
        "case": case["_name"],
        "actual_mape": actual_mape,
        "tool_seq": tool_seq,
        "elapsed_sec": round(elapsed, 1),
        "memory_enabled": memory_enabled,
    }


def summarize_seq(tool_seq: list[str]) -> str:
    """把 tool_seq 压缩成一行字符串给 LLM Judge."""
    counter: dict[str, int] = {}
    out: list[str] = []
    for name in tool_seq:
        counter[name] = counter.get(name, 0) + 1
        out.append(name)
    return " → ".join(out) + "\n\n聚合: " + ", ".join(f"{k}×{v}" for k, v in counter.items())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="只跑名字含此关键字的 case")
    parser.add_argument("--no-llm-judge", action="store_true")
    parser.add_argument("--memory-off", action="store_true", help="关闭 memory 召回，用于 ablation 对比")
    args = parser.parse_args()
    if args.memory_off:
        os.environ["CASTFLOW_EVAL_MEMORY_ABLATION"] = "1"

    cases = load_cases(args.case)
    if not cases:
        console.print("[red]No cases found.[/]")
        sys.exit(1)

    console.rule(f"[bold]CastFlow Eval · {len(cases)} cases[/]")

    table = Table(title="Eval Results")
    table.add_column("Case", style="cyan")
    table.add_column("MAPE", justify="right")
    table.add_column("MAPE judge", justify="center")
    table.add_column("LLM judge (workflow/recovery/memory)", justify="center")
    table.add_column("Tools", overflow="fold")
    table.add_column("Time", justify="right")
    table.add_column("Memory", justify="center")

    pass_count = 0
    rows: list[dict] = []
    for case in cases:
        console.print(f"\n[bold cyan]→ Running {case['_name']} ({case['org']} / {case['target_month']})[/]")
        result = run_one(case)
        mape_max = float(case.get("expected", {}).get("mape_max", case["target_mape"]))
        objective = mape_judge(result["actual_mape"], mape_max)
        if objective["score"] == 1:
            pass_count += 1

        if args.no_llm_judge:
            llm_score = {"workflow_compliance": "-", "recovery": "-", "memory_usage": "-"}
        else:
            llm_score = llm_judge(summarize_seq(result["tool_seq"]))

        rows.append({**result, "objective": objective, "llm": llm_score})

        wcr = "/".join(
            str(llm_score.get(k, "?"))
            for k in ("workflow_compliance", "recovery", "memory_usage")
        )
        mape_str = f"{result['actual_mape']:.2f}%" if result["actual_mape"] is not None else "—"
        table.add_row(
            result["case"],
            mape_str,
            "[green]PASS[/]" if objective["score"] else "[red]FAIL[/]",
            wcr,
            ", ".join(dict.fromkeys(result["tool_seq"])),  # 去重保序
            f"{result['elapsed_sec']}s",
            "on" if result["memory_enabled"] else "off",
        )

    console.print(table)
    console.rule(f"[bold]{'green' if pass_count == len(cases) else 'yellow'}]Passed {pass_count}/{len(cases)}")

    # 保存 JSON 报告
    report_path = Path("eval") / "last_report.json"
    report_path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    console.print(f"Report saved to [cyan]{report_path}[/]")


if __name__ == "__main__":
    main()
