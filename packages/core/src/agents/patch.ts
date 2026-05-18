/**
 * PatchAgent - 代码补丁生成智能体
 *
 * 职责：LLM 生成最小代码补丁（不重写全文），
 * 只修改关键参数、数据预处理逻辑或模型结构
 */

import { LLMClient } from "../llm/client"
import type { AnalysisResult } from "../types"

const PATCH_PROMPT = `## 预测代码优化补丁生成

### 原则
对以下代码进行**最小化局部修改**，绝不重写全文。
只改：模型参数、数据预处理逻辑、特征工程、模型结构。
**绝对不改**以下内容（改了会导致预测结果解析失败，优化无效）：
1. run_forecast 的函数名和参数列表
2. run_forecast 的返回值结构（必须包含 key: "chart_data"）
3. 已有 import 语句
4. chart_data 的格式：必须是 [{org: string, month: string, value: number}]

### ⚠️ 常见错误（会导致补丁无效！）
✗ 重命名 run_forecast → 解析器找不到入口函数
✗ 返回格式改为 dict {"org": [...]} → 解析器不识别
✗ 删除或重命名 chart_data 字段 → 解析器读取到空数组
✗ 修改 month 格式（如改成年月日格式）→ 与数据库不匹配

### 输出格式约束
\`\`\`python
def run_forecast(db_config, predict_year, expect_type):
    # ... 你的优化逻辑 ...
    chart_data = [
        {"org": "区县名", "month": "2026-01", "value": 1234.56},
    ]
    return {"chart_data": chart_data, "summary": {"...": "..."}}
\`\`\`

### ⚠️ 输出完整性要求（违反会导致执行失败！）
1. **必须输出完整代码**，绝不能在字符串、括号、方括号中间截断
2. 检查最后一行是否完整：不能以 =、(、[、, 或反斜杠结尾
3. 如果修复语法错误需要删除代码行，**绝不能删除 chart_data 的填充逻辑**（如 chart_data.append()、for org in orgs: 循环）
4. 所有字符串引号必须成对闭合，所有括号必须匹配
5. Python 缩进必须使用 4 个空格，不能用 Tab

### 误差分析结论
{causes}

### 优化建议
{suggestions}

### 修改重点
{targetModules}

### 迭代记录
当前第 {iteration} 次优化。{history}

### ⚠️ 必须遵守：至少修改一行代码
如果认为当前代码已最优，请**调整一个超参数**（如：maxiter / 学习率 / 季节周期 / 正则化强度 / order / seasonal_order）作为微调。
返回与输入完全相同的代码会导致本轮优化被**跳过**，浪费迭代次数。每次迭代至少做一处改动。

{syntaxErrorSection}
### 当前代码
\`\`\`python
{code}
\`\`\`

### 输出格式（只输出 JSON）
{
    "patched_code": "完整的修改后代码（全文，不是 diff）",
    "patch_summary": "一句话中文描述改动",
    "changed_sections": [{"section": "", "old": "", "new": "", "reason": ""}]
}

只输出JSON。`

export class PatchAgent {
  name = "patch_agent"
  private llm: LLMClient

  constructor(llm: LLMClient) {
    this.llm = llm
  }

  async generate(code: string, analysis: AnalysisResult, iteration: number, strategyHistory = "", forceFallback = false, syntaxError = ""): Promise<{
    patched_code: string
    diff: string
    patch_summary: string
    changed_sections: Array<{ section: string; old: string; new: string; reason: string }>
    version: number
  }> {
    const causesText = (analysis.root_causes || [])
      .map(c => `[${c.impact}] ${c.factor}: ${c.detail.slice(0, 100)}`)
      .join("\n") || "无明显异常"

    const suggText = (analysis.suggestions || [])
      .map(s => `[${s.type}] P${s.priority} ${s.description} → ${s.expected_improvement}`)
      .join("\n") || "保持当前策略"

    const targetText = (analysis.target_modules || ["模型参数", "数据预处理"]).join(", ")
    const historyText = iteration > 1 ? `已尝试 ${iteration - 1} 次优化。\n${strategyHistory.slice(0, 1000)}` : "首次优化"

    // forceFallback = true 时跳过 LLM，直接使用规则兜底
    if (forceFallback) {
      return this.fallbackPatch(code, iteration)
    }

    if (this.llm.isAvailable) {
      try {
        const prompt = PATCH_PROMPT
          .replace("{causes}", causesText)
          .replace("{suggestions}", suggText)
          .replace("{targetModules}", targetText)
          .replace("{iteration}", String(iteration))
          .replace("{history}", historyText)
          .replace("{code}", code.slice(0, 6000))
          .replace("{syntaxErrorSection}", syntaxError
            ? `### ⚠️ 上一轮语法错误（必须修正，但不得破坏数据逻辑）\n\`\`\`\n${syntaxError.slice(0, 500)}\n\`\`\`\n**修正原则：**\n1. 只修改导致语法错误的最小范围，不要重写整段逻辑\n2. 引号/括号/方括号必须成对匹配，字符串不能截断\n3. 缩进用 4 空格，不能用 Tab\n4. **严禁删除 chart_data.append / chart_data.extend / for 循环等数据填充代码**\n5. 输出必须完整，最后一行不能以 =、(、[、, 或反斜杠结尾`
            : "")

        const result = await this.llm.chatJSON(prompt, undefined, 0.6)
        if (result?.patched_code) {
          // 结构验证：确保关键元素未被破坏
          const patched = result.patched_code
          const hasRunForecast = patched.includes("def run_forecast")
          const hasChartData = patched.includes("chart_data")
          if (!hasRunForecast || !hasChartData) {
            console.warn(`[PatchAgent] 补丁破坏了核心结构，回退 (run_forecast=${hasRunForecast}, chart_data=${hasChartData})`)
            return this.fallbackPatch(code, iteration)
          }
          const diff = this.generateDiff(code, result.patched_code, iteration)
          return {
            patched_code: result.patched_code,
            diff,
            patch_summary: result.patch_summary || "自动优化",
            changed_sections: result.changed_sections || [],
            version: iteration,
          }
        }
      } catch (e) {
        console.warn(`[PatchAgent] LLM 补丁生成失败: ${e}`)
      }
    }

    return this.fallbackPatch(code, iteration)
  }

  private fallbackPatch(code: string, iteration: number) {
    let patched = code
    const changes: Array<{ section: string; old: string; new: string; reason: string }> = []

    // 策略1: 调高 maxiter
    if (code.includes("maxiter=200")) {
      patched = patched.replace(/maxiter=200/g, "maxiter=500")
      changes.push({ section: "模型训练参数", old: "maxiter=200", new: "maxiter=500", reason: "增加迭代次数提高收敛质量" })
    } else if (/maxiter=\d+/.test(code)) {
      // 通用: 非200的 maxiter 乘以 1.5
      patched = patched.replace(/maxiter=(\d+)/g, (_, n) => `maxiter=${Math.round(Number(n) * 1.5)}`)
      changes.push({ section: "模型训练参数", old: "原始 maxiter", new: "1.5倍 maxiter", reason: "增加迭代次数提高收敛质量" })
    }

    // 策略2: 扩大 SARIMAX 参数搜索
    if (code.includes("order=(1, 1, 1)") && !changes.length) {
      patched = patched.replace("order=(1, 1, 1)", "order=(2, 1, 1)")
      changes.push({ section: "SARIMAX 模型参数", old: "order=(1,1,1)", new: "order=(2,1,1)", reason: "提高 AR 阶数以捕获更多历史依赖" })
    } else if (code.includes("order=(1,1,1)") && !changes.length) {
      patched = patched.replace("order=(1,1,1)", "order=(2,1,1)")
      changes.push({ section: "SARIMAX 模型参数", old: "order=(1,1,1)", new: "order=(2,1,1)", reason: "提高 AR 阶数以捕获更多历史依赖" })
    }

    // 策略3: Prophet 参数
    if (!changes.length && /\bProphet\b/.test(code)) {
      if (code.includes("changepoint_prior_scale=0.05")) {
        patched = patched.replace("changepoint_prior_scale=0.05", "changepoint_prior_scale=0.1")
        changes.push({ section: "Prophet 参数", old: "changepoint_prior_scale=0.05", new: "changepoint_prior_scale=0.1", reason: "增大变点先验尺度使模型更灵活" })
      } else if (code.includes("seasonality_prior_scale=10.0") || code.includes("seasonality_prior_scale=10")) {
        patched = patched.replace(/seasonality_prior_scale=10\.?0?/, "seasonality_prior_scale=15.0")
        changes.push({ section: "Prophet 参数", old: "seasonality_prior_scale=10", new: "seasonality_prior_scale=15", reason: "增强季节性成分权重" })
      } else if (code.includes("seasonality_mode='additive'")) {
        patched = patched.replace("seasonality_mode='additive'", "seasonality_mode='multiplicative'")
        changes.push({ section: "Prophet 参数", old: "seasonality_mode='additive'", new: "seasonality_mode='multiplicative'", reason: "用电负荷季节效应与量级正相关，乘法更合理" })
      }
    }

    // 策略4: XGBoost/LightGBM 参数
    if (!changes.length && (/\bXGBoost\b|\bXGBRegressor\b|\bLGBM\b|\bLGBMRegressor\b/.test(code))) {
      if (code.includes("learning_rate=0.1") || code.includes("learning_rate = 0.1")) {
        patched = patched.replace(/learning_rate\s*=\s*0\.1/g, "learning_rate=0.05")
        changes.push({ section: "梯度提升参数", old: "learning_rate=0.1", new: "learning_rate=0.05", reason: "降低学习率配合更多弱学习器减少过拟合" })
      } else if (code.includes("n_estimators=100")) {
        patched = patched.replace("n_estimators=100", "n_estimators=200")
        changes.push({ section: "梯度提升参数", old: "n_estimators=100", new: "n_estimators=200", reason: "增加弱学习器数量提高拟合能力" })
      }
    }

    // 策略5: sklearn MLP/随机森林
    if (!changes.length && /\bRandomForestRegressor\b|\bMLPRegressor\b/.test(code)) {
      if (code.includes("n_estimators=100")) {
        patched = patched.replace("n_estimators=100", "n_estimators=200")
        changes.push({ section: "随机森林参数", old: "n_estimators=100", new: "n_estimators=200", reason: "增加树的数量降低方差提高准确率" })
      } else if (code.includes("hidden_layer_sizes=(50,)")) {
        patched = patched.replace("hidden_layer_sizes=(50,)", "hidden_layer_sizes=(100,50)")
        changes.push({ section: "MLP 网络结构", old: "hidden_layer_sizes=(50,)", new: "hidden_layer_sizes=(100,50)", reason: "增加隐藏层神经元数量提高模型容量" })
      } else if (code.includes("max_iter=200")) {
        patched = patched.replace("max_iter=200", "max_iter=500")
        changes.push({ section: "MLP 训练参数", old: "max_iter=200", new: "max_iter=500", reason: "增加训练迭代次数确保收敛" })
      }
    }

    // 策略6: 通用 scipy/statsmodels 参数（⚠️ 只改函数体内的内容，不改 import 行）
    if (!changes.length) {
      if (/\bseasonal\b|\bperiod\b/.test(code) && /\b12\b/.test(code)) {
        // 只在非 import 行中修改 seasonal 为显式注释
        const lines = patched.split("\n")
        patched = lines.map(line => {
          if (/^\s*import\b|^\s*from\b/.test(line)) return line // 保留 import 行
          return line.replace(/\bseasonal\b(?![^()]*\))/g, "seasonal  # 12步季节周期")
        }).join("\n")
        // 修改 period=12（只在赋值上下文中）
        patched = patched.replace(/period\s*=\s*12(?![^;]*import)/g, "period=12  # 12个月\n        # 尝试24步双年周期")
        changes.push({ section: "季节周期", old: "12个月", new: "12+24个月双周期", reason: "双季节周期捕获年度和双年度模式" })
      }
    }

    if (!changes.length) {
      changes.push({ section: "无自动修改", old: "—", new: "—", reason: "规则兜底未能识别可修改模式" })
    }

    return {
      patched_code: patched,
      diff: "（规则补丁）",
      patch_summary: `自动参数调整: ${changes.map(c => c.section).join(", ")}`,
      changed_sections: changes,
      version: iteration,
    }
  }

  private generateDiff(oldCode: string, newCode: string, iteration: number): string {
    try {
      const { execSync } = require("child_process")
      const { writeFileSync, unlinkSync } = require("fs")
      const { join } = require("path")
      const { tmpdir } = require("os")
      const oldFile = join(tmpdir(), `castflow_v${iteration - 1}.py`)
      const newFile = join(tmpdir(), `castflow_v${iteration}.py`)
      writeFileSync(oldFile, oldCode, "utf-8")
      writeFileSync(newFile, newCode, "utf-8")
      const diff = execSync(`diff -u "${oldFile}" "${newFile}" || true`, { encoding: "utf-8", windowsHide: true, stdio: ["ignore", "pipe", "pipe"] })
      unlinkSync(oldFile); unlinkSync(newFile)
      return diff
    } catch {
      return "（diff 生成失败）"
    }
  }
}
