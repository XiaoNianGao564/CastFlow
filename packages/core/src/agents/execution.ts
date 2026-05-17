/**
 * ExecutionAgent - 代码执行智能体
 * 对应 CastClaw 的 tool/bash + Python runner
 *
 * 通过 subprocess 在隔离的 Python 进程中执行预测代码，
 * 捕获输出、错误、执行时间。
 */

import { execSync } from "child_process"
import { writeFileSync, unlinkSync, mkdtempSync, existsSync, mkdirSync } from "fs"
import { join } from "path"
import { tmpdir } from "os"
import type { DataPoint, DBConfig } from "../types"

export interface ExecutionResult {
  success: boolean
  predictions: DataPoint[]
  elapsed: number
  error?: string
}

export class ExecutionAgent {
  name = "execution_agent"

  constructor(private dbConfig: DBConfig) {}

  /** 从 Python 返回结果中提取预测数据，支持多种格式 */
  private extractPredictions(result: any): DataPoint[] {
    // 策略1: 标准格式 chart_data / predictions
    const raw = result.chart_data || result.predictions || result.data || []
    if (Array.isArray(raw) && raw.length > 0) {
      return raw.map((p: any) => ({
        org: p.org || p.area || p.district || p.region || "",
        month: p.month || p.date || p.time || "",
        value: Number(p.value) || Number(p.val) || Number(p.predicted) || Number(p.forecast) || 0,
        type: p.type,
      }))
    }

    // 策略2: dict 格式 {"区县名": [月度值列表]}
    if (typeof result === "object" && !Array.isArray(result)) {
      const entries: DataPoint[] = []
      for (const [org, vals] of Object.entries(result)) {
        if (Array.isArray(vals)) {
          vals.forEach((v: any, idx: number) => {
            if (typeof v === "number" || typeof v === "string") {
              entries.push({ org, month: String(idx + 1).padStart(2, "0"), value: Number(v) })
            } else if (typeof v === "object" && v !== null) {
              entries.push({
                org,
                month: v.month || v.date || String(idx + 1),
                value: Number(v.value) || Number(v.val) || Number(v.predicted) || 0,
              })
            }
          })
        }
      }
      if (entries.length > 0) return entries
    }

    // 策略3: 扫描 result 所有字段，找第一个非空数组
    for (const key of Object.keys(result)) {
      const val = result[key]
      if (Array.isArray(val) && val.length > 0 && typeof val[0] === "object") {
        const first = val[0]
        if (first.org || first.area || first.month || first.value || first.val) {
          return val.map((p: any) => ({
            org: p.org || p.area || p.district || p.region || "",
            month: p.month || p.date || p.time || "",
            value: Number(p.value) || Number(p.val) || Number(p.predicted) || 0,
            type: p.type,
          }))
        }
      }
    }

    return []
  }

  async execute(code: string, year: number, expectType: number, debugLabel = ""): Promise<ExecutionResult> {
    const start = Date.now()

    // 保存生成的代码到 generated_code/ 目录供调试
    const debugDir = join(process.cwd(), "generated_code")
    if (!existsSync(debugDir)) mkdirSync(debugDir, { recursive: true })
    const debugFile = join(debugDir, `forecast_${debugLabel || year}_${expectType}_${Date.now()}.py`)
    writeFileSync(debugFile, code, "utf-8")

    // 写入代码到临时文件
    const tmpDir = mkdtempSync(join(tmpdir(), "castflow-exec-"))
    const codeFile = join(tmpDir, "forecast.py")

    // 生成包装代码（注入参数 + 捕获结果，双重保护）
    const wrapper = `
import json, sys, traceback, re, os
sys.path.insert(0, r"${tmpDir.replace(/\\/g, "\\\\")}")

# 顶层保护：捕获所有未预料异常
try:
    # 加载预测代码
    import importlib.util
    spec = importlib.util.spec_from_file_location("forecast_mod", r"${codeFile.replace(/\\/g, "\\\\")}")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SyntaxError as se:
        out = {"status": "error", "error": f"SyntaxError: {se.msg} (line {se.lineno}, offset {se.offset})", "traceback": traceback.format_exc()}
        print("__CASTFLOW_RESULT__" + json.dumps(out, ensure_ascii=False, default=str))
        sys.exit(0)
    except Exception as mod_err:
        out = {"status": "error", "error": f"ModuleLoadError: {mod_err}", "traceback": traceback.format_exc()}
        print("__CASTFLOW_RESULT__" + json.dumps(out, ensure_ascii=False, default=str))
        sys.exit(0)

    db_config = ${JSON.stringify(this.dbConfig)}

    try:
        result = mod.run_forecast(db_config=db_config, predict_year=${year}, expect_type=${expectType})
        out = {"status": "ok", "result": result}
        print("__CASTFLOW_RESULT__" + json.dumps(out, ensure_ascii=False, default=str))
    except Exception as e:
        out = {"status": "error", "error": str(e), "traceback": traceback.format_exc()}
        print("__CASTFLOW_RESULT__" + json.dumps(out, ensure_ascii=False, default=str))
except Exception as fatal_err:
    # 顶层兜底：json.dumps 也失败时的最后保护
    try:
        out = {"status": "error", "error": f"Fatal: {fatal_err}"}
        print("__CASTFLOW_RESULT__" + json.dumps(out, ensure_ascii=False, default=str))
    except:
        print("__CASTFLOW_RESULT__" + json.dumps({"status": "error", "error": f"Fatal(nojson): {fatal_err}"}))
`

    writeFileSync(codeFile, code, "utf-8")
    const wrapperFile = join(tmpDir, "wrapper.py")
    writeFileSync(wrapperFile, wrapper, "utf-8")

    try {
      const output = execSync(`python "${wrapperFile}"`, {
        encoding: "utf-8",
        timeout: 300000, // 5 分钟
        windowsHide: true,
        maxBuffer: 50 * 1024 * 1024,
        stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      })

      const elapsed = (Date.now() - start) / 1000

      // 解析结果（同时检查 stdout 和 stderr）
      const allOutput = output || ""
      for (const line of allOutput.split("\n")) {
        if (line.startsWith("__CASTFLOW_RESULT__")) {
          const data = JSON.parse(line.slice("__CASTFLOW_RESULT__".length))
          if (data.status === "ok") {
            const result = data.result || {}
            const predictions = this.extractPredictions(result)
            return { success: predictions.length > 0, predictions, elapsed }
          } else {
            return { success: false, predictions: [], elapsed, error: data.error || "执行错误" }
          }
        }
      }

      // 如果没有 __CASTFLOW_RESULT__ 标记，尝试解析整个 stdout 为 JSON
      try {
        const trimmed = allOutput.trim()
        if (trimmed) {
          const parsed = JSON.parse(trimmed)
          if (parsed.result || parsed.predictions || parsed.chart_data) {
            const predictions = this.extractPredictions(parsed)
            if (predictions.length > 0) return { success: true, predictions, elapsed }
          }
        }
      } catch {}

      return { success: false, predictions: [], elapsed, error: `未找到预测结果（代码已保存至 ${debugFile}）` }

    } catch (e: any) {
      const elapsed = (Date.now() - start) / 1000
      // 即使 execSync 抛出异常，也尝试从 stdout/stderr 中找 __CASTFLOW_RESULT__
      const stderrText = (e.stderr && e.stderr.toString()) || ""
      const stdoutText = (e.stdout && e.stdout.toString()) || ""
      const combined = stdoutText + "\n" + stderrText

      // 尝试从合并输出中提取错误信息
      for (const line of combined.split("\n")) {
        if (line.startsWith("__CASTFLOW_RESULT__")) {
          try {
            const data = JSON.parse(line.slice("__CASTFLOW_RESULT__".length))
            if (data.error) {
              return { success: false, predictions: [], elapsed, error: `${data.error}（代码已保存至 ${debugFile}）` }
            }
          } catch {}
        }
      }

      // 没找到结构化错误，用原始 stderr
      const shortError = stderrText.slice(-500) || stdoutText.slice(-200) || e.message?.slice(-200) || "执行异常"
      return {
        success: false,
        predictions: [],
        elapsed,
        error: `${shortError}（代码已保存至 ${debugFile}）`,
      }
    } finally {
      try { unlinkSync(codeFile); unlinkSync(wrapperFile); unlinkSync(tmpDir) } catch {}
    }
  }
}
