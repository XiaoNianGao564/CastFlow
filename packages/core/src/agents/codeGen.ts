/**
 * CodeGenAgent - LLM 代码生成智能体
 *
 * 职责：调用 LLM 从数据库结构生成初始预测代码
 * 模型不固定为 ARIMA/SARIMAX，由 LLM 自行选择
 */

import { LLMClient } from "../llm/client"
import { DBReader } from "../db/mysql"
import type { DBConfig } from "../types"

// 模型类型 → LLM 提示指导（30+ 种方法）
// ⚠️ 只引用当前 Python 环境已安装的包
const MODEL_HINTS: Record<string, string> = {
  auto: "根据数据特征自行选择最合适的模型",
  exp_smoothing: "Holt-Winters 指数平滑（加性/乘性季节自动选择，seasonal_periods=12），必须包含 try/except 兜底。可用包：statsmodels.tsa.holtwinters",
  sarima: "SARIMA 自动定阶（遍历 (p,d,q)(P,D,Q,12) 组合，AIC 选优，使用 statsmodels.tsa.statespace.sarimax 实现，不要用 pmdarima）",
  prophet: "Prophet 类方法（Python 环境未安装 fbprophet/prophet 包，请用 statsmodels 曲线分解替代：seasonal_decompose + 线性趋势 + 残差预测 + 季节周期合并）。**注意：** 构建 chart_data 时，必须用 `chart_data.append({\"org\": org, \"month\": f\"{predict_year}-{str(i+1).zfill(2)}\", \"value\": round(float(val), 2)})` 逐条 append，**绝对不能**用字符串拼接方式组合数值，否则会导致 'Could not convert ... to numeric' 错误。每个 value 都要单独 `round(float(val), 2)` 转换。",
  xgboost: "XGBoost 回归（构建时间特征：年/月/滞后 1-12 阶/滚动均值/滚动标准差，early_stopping_rounds=50）。**注意：** chart_data 必须用 `.append()` 逐条追加，每个 value 单独 `round(float(val), 2)` 转换，**绝对不能**字符串拼接数值。",
  lightgbm: "LightGBM 回归（构建时间特征：年/月/滞后 1-12 阶/滚动中位数）",
  ensemble: "集成模型：分别跑 ETS + 季节性ARIMA + XGBoost，取预测值的加权平均（权重自动寻优，minimize MAPE）",
  linear_regression: "线性回归（sklearn.linear_model，构建特征：年/月虚拟变量/滞后 12 阶值/线性趋势项）",
  stl_ets: "STL 分解（statsmodels.tsa.seasonal_decompose）→ 分别预测趋势+季节+余项，再组合",
  naive_seasonal: "Naive 季节性方法：直接使用去年同月值 + 年增长率修正",
  moving_average: "移动平均 + 季节性指数：12 阶中心移动平均 + 月度季节因子调整用 numpy/pandas 实现",
  theta: "Theta 方法（等价于简单指数平滑 + 线性飘移的组合预测，用 statsmodels 实现）",
  garch: "GARCH 模型：先用 SARIMAX 拟合均值方程，残差手工建模 GARCH(1,1) 波动率（用 scipy.optimize 实现，没有 arch 库）",
  hw_mul: "Holt-Winters 乘性季节模型（ExponentialSmoothing(seasonal='mul', trend='add')），seasonal_periods=12",
  hw_damp: "Holt-Winters 阻尼趋势（ExponentialSmoothing(trend='add', damped=True, seasonal='add')）",
  croston: "Croston 方法手工实现（适用于有零值的间歇需求序列，用 numpy 实现）",
  random_forest: "随机森林回归（sklearn.ensemble，n_estimators=500，构建滞后 1-12 阶/年/月/季度特征）",
  catboost: "CatBoost 类方法（Python 环境未安装 catboost，请用 LightGBM 替代：构建时序特征 + 交叉验证）",
  mlp: "MLPRegressor（sklearn.neural_network，隐藏层 (64,32,16)，早停防止过拟合）",
  bats: "TBATS 类方法（Python 环境未安装 tbats 库，请用 statsmodels STL 分解 + ETS 组合替代）",
  arima_xgb: "混合：SARIMA（statsmodels）预测趋势 + XGBoost 预测残差（stacking 思路）",
  ets_xgb: "混合：ETS（statsmodels）预测趋势 + XGBoost 预测残差",
  weighted_ensemble: "加权集成：3-5 个基础模型（ETS/SARIMA/XGBoost/LR），用 scipy.optimize 求解最优组合权重",
  lgb_ets_arima: "三模型投票：ETS/SARIMA/LightGBM 分别预测，取中位数作为最终结果",
  seasonal_decompose: "确定性分解：12 个月度季节均值 + 线性趋势 + 残差平滑（全部用 numpy/pandas）",
  holt_linear: "Holt 线性趋势模型（无季节性，ExponentialSmoothing(trend='add')）",
  ses: "简单指数平滑（无趋势无季节性），适合无明显模式的短序列",
  arima111: "固定 ARIMA(1,1,1) 基线模型（statsmodels.tsa.arima.model.ARIMA）",
  arima_seasonal: "固定 SARIMA(1,1,1)(1,1,1,12) 季节性模型（statsmodels.tsa.statespace.sarimax.SARIMAX）",
  winters_damped: "阻尼 Holt-Winters（ExponentialSmoothing(trend='add', damped=True, seasonal='add')）",
  dynamic_regression: "动态回归：SARIMAX 加上外部回归变量（statsmodels.tsa.statespace.sarimax.SARIMAX，用 exog 参数）",
}

const SYSTEM_PROMPT = `你是一位专业的时间序列预测专家，精通各种预测模型。
你的任务是根据数据库结构和数据特征，生成一个完整的 Python 预测代码文件。

## 技术能力
- 你可以自由选择任何模型：ARIMA, SARIMAX, ETS, XGBoost, LightGBM, 线性回归, 指数平滑, 集成方法等
- 选择依据：数据量、季节性特征、趋势复杂度
- 必须使用 Python 标准库 + 常用数据科学库

## Python 环境可用包
以下包已安装，可以直接 import：
- pandas, numpy, pymysql
- statsmodels（tsa.arima.model, tsa.statespace.sarimax, tsa.holtwinters, tsa.seasonal）
- sklearn（linear_model, ensemble, neural_network, preprocessing）
- xgboost, lightgbm
- scipy（optimize, stats）

以下包未安装，禁止使用（会导致 ModuleNotFoundError）：
- fbprophet, prophet, pmdarima, tbats, catboost, arch, tensorflow, torch, keras

请严格遵守可用包列表。对于未安装的包，用已安装的等价方法替代。

## 代码要求
1. 必须包含函数: run_forecast(db_config, predict_year, expect_type)
2. 必须通过 pymysql 从 tb_dlyc_area_qx_month 表读取数据
3. 按区县(org_name)分别预测
4. 返回 {"chart_data": [{"org", "month", "value"}], "summary": {"district_count", "forecast_count"}}
5. 代码必须完整可运行，包含所有 import 语句
6. 如果选择较复杂的模型(如 LSTM/XGBoost)，请确保 try/except 兜底到简单模型
7. 添加充分的注释说明模型选择理由
8. **chart_data 构建必须用 chart_data.append({"org": org, "month": f"{predict_year}-{str(i+1).zfill(2)}", "value": round(float(val), 2)}) 逐条追加，绝对不能将多个数值字符串拼接在一起。每个 value 都要单独 round(float(val), 2) 转换。`

export class CodeGenAgent {
  name = "code_gen_agent"
  private llm: LLMClient
  private reader: DBReader

  constructor(llm: LLMClient, dbConfig: DBConfig) {
    this.llm = llm
    this.reader = new DBReader(dbConfig)
  }

  async generateInitial(year: number, expectType: number, modelId = "auto", researchContext = ""): Promise<{
    code: string
    source: "llm" | "default"
    syntaxOk: boolean
  }> {
    // 先获取数据特征，提供给 LLM
    const chars = await this.reader.getCharacteristics(expectType)
    const orgs = Object.keys(chars)

    let prompt = `## 电力负荷预测代码生成任务

### 数据库
- MySQL: 127.0.0.1:3306/sxfhyc11
- 表: tb_dlyc_area_qx_month (org_name, power_year, power_month, power_num, expect_type)
- 预测目标: power_num（月度电量值）
- 预测年份: ${year}
- 用电类型: ${expectType === 1 ? "区民用电" : "煤改电"}

### 数据特征
共有 ${orgs.length} 个区县。
区县列表: ${orgs.join(", ")}

以下是各区县的历史数据特征：
${JSON.stringify(chars, null, 2).slice(0, 3000)}

### 模型选择
${
  modelId === "auto"
    ? "根据数据特征自行选择最合适的模型。"
    : `请使用以下模型方法：${MODEL_HINTS[modelId] || modelId}`
}

### 网络调研上下文（可选，仅供参考）
${researchContext ? `以下是我从行业研究中获取的知识，可作为建模参考：\n${researchContext.slice(0, 2000)}` : "（暂无网络调研结果）"}

### 返回格式
只输出Python代码，不要额外说明。
代码中必须包含 run_forecast(db_config, predict_year, expect_type) 函数。`

    let code = this.buildDefaultCode(year, expectType)
    let source: "llm" | "default" = "default"

    if (this.llm.isAvailable) {
      // 最多重试 3 次，每次把编译错误传给 LLM 修正
      const maxRetries = 3
      for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
          const result = await this.llm.chat(prompt, SYSTEM_PROMPT, 0.3)
          const extracted = this.extractCode(result)
          if (extracted.length <= 200) {
            if (attempt < maxRetries) {
              prompt += `\n\n### 上一轮错误\n生成的代码太短（${extracted.length} 字符），请生成完整的可运行代码。`
              continue
            }
            break
          }
          code = extracted
          source = "llm"

          // 语法检查
          const syntaxResult = this.checkSyntax(code)
          if (syntaxResult.ok) break // 语法正确，跳出重试

          // 语法错误，重新生成时传入具体错误信息
          if (attempt < maxRetries) {
            const errorMsg = syntaxResult.error.slice(0, 300)
            prompt += `\n\n### 上一轮错误\nPython 语法错误，请修正后重新输出完整代码：\n\`\`\`\n${errorMsg}\n\`\`\``
          }
        } catch (e: any) {
          console.warn(`[CodeGenAgent] LLM 生成失败(第${attempt}次): ${e.message}`)
          if (attempt < maxRetries) {
            prompt += `\n\n### 上一轮错误\nLLM 调用失败，请重新生成: ${e.message.slice(0, 200)}`
          }
        }
      }
    }

    const syntaxResult = this.checkSyntax(code)
    return { code, source, syntaxOk: syntaxResult.ok }
  }

  private buildDefaultCode(year: number, expectType: number): string {
    const typeName = expectType === 1 ? "区民" : "煤改电"
    return `"""
自动生成的电力负荷预测代码
生成时间: ${new Date().toISOString()}
模型: SARIMAX (默认兜底)
"""

import pandas as pd
import numpy as np
import pymysql
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.holtwinters import ExponentialSmoothing
import warnings
warnings.filterwarnings("ignore")


def run_forecast(db_config: dict, predict_year: int = ${year},
                 expect_type: int = ${expectType}) -> dict:
    conn = pymysql.connect(
        host=db_config.get("host", "127.0.0.1"),
        port=int(db_config.get("port", 3306)),
        user=db_config.get("user", "root"),
        password=db_config.get("password", ""),
        database=db_config.get("database", "sxfhyc11"),
        charset="utf8",
    )
    query = f"""
        SELECT org_name, power_year, power_month, power_num
        FROM tb_dlyc_area_qx_month WHERE expect_type = {expectType}
        ORDER BY org_name, power_year, power_month
    """
    df = pd.read_sql(query, conn)
    conn.close()
    if df.empty:
        return {"chart_data": [], "summary": {"error": "无数据"}}

    chart_data = []
    for org in df["org_name"].unique():
        odf = df[df["org_name"] == org].sort_values(["power_year", "power_month"])
        ts = odf["power_num"].values.astype(float)
        if len(ts) < 12:
            continue

        pred = None
        # 策略1: SARIMAX（有季节性）
        if len(ts) >= 24:
            try:
                model = SARIMAX(ts, order=(1,1,1), seasonal_order=(1,1,1,12),
                                enforce_stationarity=False, enforce_invertibility=False)
                fitted = model.fit(disp=False, maxiter=200)
                pred = fitted.forecast(steps=12)
            except Exception:
                pass

        # 策略2: Holt-Winters
        if pred is None:
            try:
                model = ExponentialSmoothing(ts, seasonal_periods=12, trend="add", seasonal="add")
                fitted = model.fit()
                pred = fitted.forecast(12)
            except Exception:
                pass

        # 策略3: 普通 ARIMA
        if pred is None:
            try:
                model = ARIMA(ts, order=(1,1,1))
                fitted = model.fit()
                pred = fitted.forecast(steps=12)
            except Exception:
                continue

        for i, val in enumerate(pred):
            chart_data.append({
                "org": org,
                "month": f"{predict_year}-{str(i+1).zfill(2)}",
                "value": round(float(val), 2),
                "type": "${typeName}",
            })

    return {"chart_data": chart_data, "summary": {"district_count": len(df["org_name"].unique()), "forecast_count": len(chart_data)}}
`
  }

  private extractCode(text: string): string {
    const pyMatch = text.match(/```(?:python|py)\s*\n?([\s\S]*?)\n?```/)
    if (pyMatch) return pyMatch[1].trim()
    const codeMatch = text.match(/```\s*\n?([\s\S]*?)\n?```/)
    if (codeMatch) return codeMatch[1].trim()
    return text.trim()
  }

  private checkSyntax(code: string): { ok: boolean; error: string } {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const { writeFileSync, unlinkSync } = require("fs")
        const { join } = require("path")
        const { tmpdir } = require("os")
        const { execSync } = require("child_process")
        const tmpFile = join(tmpdir(), `castflow_syntax_${Date.now()}.py`)
        writeFileSync(tmpFile, code, "utf-8")
        execSync(`python -m py_compile "${tmpFile}"`, {
          timeout: 10000,
          windowsHide: true,
          stdio: ["ignore", "pipe", "pipe"],
          env: { ...process.env, PYTHONIOENCODING: "utf-8" },
        })
        unlinkSync(tmpFile)
        return { ok: true, error: "" }
      } catch (e: any) {
        // ETIMEDOUT 时重试
        if (attempt < 2) {
          try { require("child_process").execSync(`ping -n 1 127.0.0.1 >nul`, { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] }) } catch {}
          continue
        }
        const stderr = (e.stderr || e.message || "").toString().slice(0, 300)
        return { ok: false, error: stderr }
      }
    }
    return { ok: false, error: "重试多次后仍无法验证" }
  }
}
