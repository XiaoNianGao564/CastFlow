/**
 * EvaluationAgent - 评估智能体
 * 通过临时 JSON 文件传递数据（解决转义问题）
 */

import { execSync } from "child_process"
import { writeFileSync, unlinkSync, mkdtempSync } from "fs"
import { join } from "path"
import { tmpdir } from "os"
import type { DataPoint, EvaluationMetrics } from "../types"

export class EvaluationAgent {
  name = "evaluation_agent"

  async evaluate(predictions: DataPoint[], actuals: DataPoint[]): Promise<EvaluationMetrics> {
    const tmpDir = mkdtempSync(join(tmpdir(), "castflow-eval-"))

    // 把数据写到临时 JSON 文件（避免转义问题）
    const dataFile = join(tmpDir, "data.json")
    writeFileSync(dataFile, JSON.stringify({ predictions, actuals }), "utf-8")

    // Python 从文件读取数据
    const script = `
import json, numpy as np

with open(r"${dataFile.replace(/\\/g, "\\\\")}", "r", encoding="utf-8") as f:
    data = json.load(f)

preds = data["predictions"]
actuals = data["actuals"]

pred_map = {(p["org"], p["month"]): p["value"] for p in preds}
p_vals, a_vals, pairs = [], [], []

for a in actuals:
    key = (a["org"], a["month"])
    if key in pred_map:
        pv = float(pred_map[key])
        av = float(a["value"])
        p_vals.append(pv)
        a_vals.append(av)
        pairs.append({"org": key[0], "month": key[1], "predicted": round(pv, 2),
                       "actual": round(av, 2), "error": round(pv - av, 2),
                       "error_pct": round((pv - av) / av * 100, 2) if av != 0 else 0})

if not a_vals:
    print(json.dumps({"error": "\u65e0\u5339\u914d\u6570\u636e", "accuracy": 0, "sample_count": 0}))
else:
    pa = np.array(a_vals)
    pp = np.array(p_vals)
    mape = float(np.mean(np.abs((pa - pp) / pa)) * 100)
    mae = float(np.mean(np.abs(pa - pp)))
    rmse = float(np.sqrt(np.mean((pa - pp) ** 2)))
    accuracy = max(0, 100 - mape)

    per_org = {}
    for p in pairs:
        o = p["org"]
        if o not in per_org: per_org[o] = {"actuals": [], "preds": []}
        per_org[o]["actuals"].append(p["actual"])
        per_org[o]["preds"].append(p["predicted"])
    per_org_metrics = {}
    for o, v in per_org.items():
        oa = np.array(v["actuals"])
        op = np.array(v["preds"])
        om = float(np.mean(np.abs((oa - op) / oa)) * 100)
        per_org_metrics[o] = {"mape": round(om, 2), "accuracy": round(max(0, 100 - om), 2), "count": len(v["actuals"])}

    errors = np.array([p["error"] for p in pairs])
    over = int(np.sum(errors > 0))
    under = int(np.sum(errors < 0))
    bias = "overestimate" if over > under * 1.2 else ("underestimate" if under > over * 1.2 else "balanced")
    level = "excellent" if accuracy >= 95 else ("good" if accuracy >= 85 else ("fair" if accuracy >= 70 else "poor"))

    result = {
        "mape": round(mape, 2), "mae": round(mae, 2), "rmse": round(rmse, 2),
        "accuracy": round(accuracy, 2), "sample_count": len(a_vals),
        "bias": bias, "level": level, "per_org": per_org_metrics,
        "pairs": sorted(pairs, key=lambda x: abs(x["error_pct"]), reverse=True)[:50],
    }
    print(json.dumps(result, ensure_ascii=False))
`
    const scriptFile = join(tmpDir, "eval.py")
    writeFileSync(scriptFile, script, "utf-8")

    try {
      const output = execSync(`python "${scriptFile}"`, {
        encoding: "utf-8",
        timeout: 30000,
        windowsHide: true,
        maxBuffer: 50 * 1024 * 1024,
        stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      }).trim()
      return JSON.parse(output)
    } catch (e: any) {
      const errorMsg = e.stderr?.slice(-300) || e.message || "evaluation error"
      return {
        mape: 0, mae: 0, rmse: 0,
        accuracy: 0, sample_count: 0,
        eval_error: true,
        error: errorMsg,
      } as any
    } finally {
      try { unlinkSync(dataFile); unlinkSync(scriptFile); unlinkSync(tmpDir) } catch {}
    }
  }
}
