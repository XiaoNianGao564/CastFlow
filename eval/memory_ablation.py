"""Memory ablation helper.

对同一组 eval case 各跑一遍 memory-on / memory-off，输出 MAPE、耗时、工具调用差异。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from eval.run_eval import load_cases, run_one
from eval.judges.mape_judge import mape_judge


def _run_cases(cases: list[dict], memory_off: bool) -> list[dict]:
    if memory_off:
        os.environ["CASTFLOW_EVAL_MEMORY_ABLATION"] = "1"
    else:
        os.environ.pop("CASTFLOW_EVAL_MEMORY_ABLATION", None)

    rows = []
    for case in cases:
        result = run_one(case)
        mape_max = float(case.get("expected", {}).get("mape_max", case["target_mape"]))
        result["objective"] = mape_judge(result["actual_mape"], mape_max)
        rows.append(result)
    return rows


def _index(rows: list[dict]) -> dict[str, dict]:
    return {r["case"]: r for r in rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="只跑名字含此关键字的 case")
    args = parser.parse_args()

    cases = load_cases(args.case)
    if not cases:
        raise SystemExit("No cases found")

    print(f"===> memory ON: {len(cases)} cases")
    on_rows = _run_cases(cases, memory_off=False)
    print(f"===> memory OFF: {len(cases)} cases")
    off_rows = _run_cases(cases, memory_off=True)

    on_by_case = _index(on_rows)
    off_by_case = _index(off_rows)
    comparison = []
    for name in sorted(on_by_case):
        on = on_by_case[name]
        off = off_by_case[name]
        on_mape = on.get("actual_mape")
        off_mape = off.get("actual_mape")
        delta = None
        if on_mape is not None and off_mape is not None:
            delta = round(on_mape - off_mape, 3)
        comparison.append(
            {
                "case": name,
                "memory_on_mape": on_mape,
                "memory_off_mape": off_mape,
                "delta_on_minus_off": delta,
                "memory_on_tools": on.get("tool_seq", []),
                "memory_off_tools": off.get("tool_seq", []),
                "memory_on_elapsed_sec": on.get("elapsed_sec"),
                "memory_off_elapsed_sec": off.get("elapsed_sec"),
            }
        )

    report = {"memory_on": on_rows, "memory_off": off_rows, "comparison": comparison}
    out = Path("eval") / "memory_ablation_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print("\nCase | memory_on | memory_off | delta(on-off)")
    for row in comparison:
        print(
            f"{row['case']} | {row['memory_on_mape']} | "
            f"{row['memory_off_mape']} | {row['delta_on_minus_off']}"
        )
    print(f"\nReport saved to {out}")


if __name__ == "__main__":
    main()
