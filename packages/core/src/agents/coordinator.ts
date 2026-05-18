/**
 * CoordinatorAgent - 总控编排智能体
 *
 * 作为顶层 Planner 角色，管理完整的自优化闭环：
 * 数据 → 代码生成 → 执行预测 → 等待真实数据 → 评估 → 分析 → 补丁 → 验证 → 迭代
 */

import { LLMClient } from "../llm/client"
import { IterationStore } from "../store/iteration"
import { ExperienceStore } from "../store/experience"
import { SkillStore, type SkillDataProfile } from "../store/skillStore"
import { RAGStore } from "../store/ragStore"
import { WebSearchEngine } from "../store/webSearch"
import { DataAgent } from "./data"
import { CodeGenAgent } from "./codeGen"
import { ExecutionAgent } from "./execution"
import { EvaluationAgent } from "./evaluation"
import { AnalysisAgent } from "./analysis"
import { PatchAgent } from "./patch"
import { ReActPlanner, type ReActTool, type PipelineState } from "./plannerAgent"
import { PlannerAgent } from "./planner"
import { ForecasterAgent } from "./forecaster"
import { CriticAgent } from "./critic"
import {
  MODEL_POOL,
  selectInitialModel,
  extractCharacteristics,
  getModelLabel,
} from "./modelSelector"
import type { DBConfig, DataPoint, EvaluationMetrics, SystemConfig, StatusUpdate, IterationRecord, ForecastReport } from "../types"

export type StatusCallback = (update: StatusUpdate) => void

export class CoordinatorAgent {
  name = "coordinator"

  private dataAgent: DataAgent
  private codeGenAgent: CodeGenAgent
  private executionAgent: ExecutionAgent
  private evaluationAgent: EvaluationAgent
  private analysisAgent: AnalysisAgent
  private patchAgent: PatchAgent
  private store: IterationStore
  private experience: ExperienceStore
  private skillStore: SkillStore

  private _status = "idle"
  private _currentVersion = 0
  private _bestAccuracy = 0
  private _consecutiveNoImprovement = 0
  private _consecutiveEmptyPredictions = 0
  private _consecutiveEmptyPatches = 0
  private _config: SystemConfig
  private _onStatus?: StatusCallback
  private _abort = false
  private _startTime = 0
  private _modelIndex = 0
  private _totalModels = 0
  private _triedModels = new Set<string>()
  private _lastDataProfile: SkillDataProfile | null = null
  private _taskYear = 2026
  private _taskExpectType = 1
  private _configOverrides: { maxIterations?: number; accuracyTarget?: number } = {}
  private _lastBestCode = ""
  private _researchCache: string | null = null
  private _llm: LLMClient
  private _ragStore: RAGStore
  private _webSearch: WebSearchEngine
  private _plannerAgent: PlannerAgent
  private _forecasterAgent: ForecasterAgent
  private _criticAgent: CriticAgent

  // HITL 人在回路
  private _hitlPaused = false
  private _hitlReason = ""
  private _hitlTable: string[] = []
  private _hitlResolver: ((decision: "continue" | "skip_model" | "stop") => void) | null = null

  constructor(config: SystemConfig, onStatus?: StatusCallback) {
    this._config = config
    this._onStatus = onStatus

    const llm = new LLMClient(config.llm)
    this._llm = llm
    this.dataAgent = new DataAgent(config.db)
    this.codeGenAgent = new CodeGenAgent(llm, config.db)
    this.executionAgent = new ExecutionAgent(config.db)
    this.evaluationAgent = new EvaluationAgent()
    this.analysisAgent = new AnalysisAgent(llm, config.db)
    this.patchAgent = new PatchAgent(llm)
    this.store = new IterationStore()
    this.experience = new ExperienceStore()
    this.skillStore = new SkillStore()
    this._ragStore = new RAGStore()
    this._webSearch = new WebSearchEngine()
    this._plannerAgent = new PlannerAgent(llm, config.db)
    this._forecasterAgent = new ForecasterAgent(llm, config.db)
    this._criticAgent = new CriticAgent(llm)

    // 模型切换状态
    this._modelIndex = 0  // 实际值会在 startFullLoop/predictOnce 时根据数据特征设置
    this._totalModels = MODEL_POOL.length
    this._triedModels = new Set<string>()

    // 从已有记录恢复历史最佳准确率
    const bestRecord = this.getBestRecord()
    if (bestRecord?.accuracy) {
      this._bestAccuracy = bestRecord.accuracy
    }
  }

  get status() {
    return {
      status: this._status,
      currentVersion: this._currentVersion,
      bestAccuracy: Number(this._bestAccuracy.toFixed(2)),
      totalIterations: this.store.length,
      currentModel: `${MODEL_POOL[this._modelIndex]?.label || "?"} (${this._modelIndex + 1}/${MODEL_POOL.length})`,
    }
  }

  /** 用户可以中途停止闭环 */
  stop() {
    this._abort = true
    this._status = "stopped"
    this.emit({ message: "用户已终止闭环，等待当前阶段完成后停止" })
  }

  private checkAbort() {
    if (this._abort) {
      this._status = "stopped"
      this.emit({ message: `闭环已终止（${this._status} 阶段的剩余工作将被跳过）` })
      throw new Error("用户已终止闭环")
    }
  }

  private emit(update: Partial<StatusUpdate>) {
    this._onStatus?.({
      status: this._status,
      message: "",
      timestamp: new Date().toISOString(),
      currentModel: this.getCurrentModelLabel(),
      ...update,
    })
  }

  private getCurrentModelLabel(): string {
    return `${MODEL_POOL[this._modelIndex]?.label || "?"} (${this._modelIndex + 1}/${MODEL_POOL.length})`
  }

  // ================================================================
  // HITL 人在回路
  // ================================================================

  /** 是否正在等待人类决策 */
  get hitlPaused(): boolean {
    return this._hitlPaused
  }

  /** 获取 HITL 暂停信息 */
  get hitlInfo() {
    return {
      paused: this._hitlPaused,
      reason: this._hitlReason,
      status: this._status,
      model: this.getCurrentModelLabel(),
      bestAccuracy: Number(this._bestAccuracy.toFixed(2)),
      consecutiveEmptyPatches: this._consecutiveEmptyPatches,
      consecutiveNoImprovement: this._consecutiveNoImprovement,
      options: this._hitlTable,
    }
  }

  /** 唤醒 HITL，等待人类决策 */
  private async waitForHuman(reason: string, statusLabel: string, options: string[]): Promise<"continue" | "skip_model" | "stop"> {
    this._hitlPaused = true
    this._hitlReason = reason
    this._hitlTable = options
    this._status = statusLabel
    this.emit({ message: `🛑 需要人工决策: ${reason}` })

    return new Promise((resolve) => {
      this._hitlResolver = resolve
    })
  }

  /** 人类做出决策（由 API 端点调用） */
  resolveHITL(decision: "continue" | "skip_model" | "stop"): boolean {
    if (!this._hitlPaused || !this._hitlResolver) return false
    this._hitlPaused = false
    this._hitlReason = ""
    this._hitlTable = []
    const resolver = this._hitlResolver
    this._hitlResolver = null
    resolver(decision)
    this.emit({ message: `👤 人类决策: ${decision === "continue" ? "继续当前模型" : decision === "skip_model" ? "跳过当前模型" : "停止闭环"}` })
    return true
  }

  /** 根据数据特征智能选择初始模型 */
  private async selectBestModel(expectType: number) {
    try {
      this.emit({ message: `分析数据特征，智能选择初始预测模型...` })
      const rawChars = await this.dataAgent.run({ action: "get_characteristics", expectType })
      const chars = extractCharacteristics(rawChars, expectType)
      const result = selectInitialModel(chars)

      // 缓存数据特征画像用于 Skill 沉淀
      this._lastDataProfile = {
        seasonalityStrength: chars.seasonalityStrength,
        trendDirection: chars.trendDirection,
        trendStrength: chars.trendStrength,
        volatility: chars.volatility,
        orgCount: chars.orgCount,
        yearsCount: chars.yearsCount,
      }

      // 查看是否有匹配的历史 Skill
      const matched = this.skillStore.match(chars, 3)
      if (matched.length > 0 && matched[0].score > 50) {
        this.emit({ message: `🏆 匹配到历史技能: ${matched[0].skill.modelLabel} (相似度${matched[0].score}%) — 上次最佳 ${matched[0].skill.bestAccuracy}%` })
      }

      // 更新模型索引
      this._modelIndex = result.index
      this._triedModels.add(MODEL_POOL[result.index].id)

      this.emit({
        message: `📊 数据特征: ${chars.orgCount}区县, ${chars.yearsCount}年, ` +
          `季节强度=${(chars.seasonalityStrength * 100).toFixed(0)}%, ` +
          `趋势=${chars.trendDirection}(强度${(chars.trendStrength * 100).toFixed(0)}%), ` +
          `波动=${(chars.volatility * 100).toFixed(0)}%\n` +
          `🎯 智能选择: ${result.label} (Top3: ${result.reason})`,
      })
    } catch (e: any) {
      // 选择失败时保持默认，不影响主流程
      this.emit({ message: `模型智能选择失败 (${e.message || e})，使用默认模型` })
      this._modelIndex = 0
    }
  }

  /** 启动完整闭环入口（外部 API 调用，支持配置覆盖） */
  startFullLoop(year?: number, expectType?: number, overrides?: { modelIndex?: number; maxIterations?: number; accuracyTarget?: number }) {
    this._abort = false
    this._startTime = Date.now()
    this._taskYear = year || this._config.defaults.year
    this._taskExpectType = expectType || this._config.defaults.expectType
    this._configOverrides = overrides || {}

    // 应用配置覆盖
    if (overrides?.maxIterations != null) this._config.optimizer.maxIterations = overrides.maxIterations
    if (overrides?.accuracyTarget != null) this._config.optimizer.accuracyTarget = overrides.accuracyTarget

    const selectModelThenStart = () => {
      this.fullLoop(year, expectType).catch(e => {
        if (!this._abort && e.message !== "用户已终止闭环") {
          this._status = "failed"
          this.emit({ message: `闭环异常: ${e.message || e}` })
        }
      })
    }

    // 如果指定了初始模型索引，直接使用
    if (overrides?.modelIndex != null && overrides.modelIndex >= 0 && overrides.modelIndex < MODEL_POOL.length) {
      this._modelIndex = overrides.modelIndex
      this._triedModels.add(MODEL_POOL[overrides.modelIndex].id)
      const model = MODEL_POOL[overrides.modelIndex]
      this.emit({ message: `👤 手动指定模型: ${model.label} (${overrides.modelIndex + 1}/${MODEL_POOL.length})` })
      selectModelThenStart()
    } else {
      // 先选模型再启动闭环
      this.selectBestModel(expectType || 1).then(() => {
        selectModelThenStart()
      })
    }
  }

  // ================================================================
  // 单次预测（不迭代）
  // ================================================================
  async predictOnce(year = 2026, expectType = 1) {
    this._abort = false
    this._startTime = Date.now()
    this._status = "running"
    this.emit({ message: `单次预测: ${year}年, ${expectType === 1 ? "区民" : "煤改电"}` })

    try {
      // 智能选择初始模型
      await this.selectBestModel(expectType)
      this.checkAbort()
      this._status = "generating"
      this.emit({ message: "生成预测代码..." })
      const genResult = await this.codeGenAgent.generateInitial(year, expectType, MODEL_POOL[this._modelIndex].id)
      let code = genResult.code
      this.emit({ message: `代码生成完成 (来源: ${genResult.source}, 模型: ${MODEL_POOL[this._modelIndex].label})` })

      this._status = "executing"
      this.emit({ message: "执行预测..." })
      const execResult = await this.executionAgent.execute(code, year, expectType)
      if (!execResult.success) throw new Error(`预测执行失败: ${execResult.error}`)

      this._status = "completed"
      this.emit({ message: `单次预测完成: ${execResult.predictions.length} 条` })
      return execResult.predictions
    } catch (e: any) {
      this._status = "failed"
      this.emit({ message: `单次预测失败: ${e.message || e}` })
      return []
    }
  }

  // ================================================================
  // 多模型并行初始预测（P2 功能）
  // 同时运行 top-3 候选模型，选择最优结果进入迭代
  // ================================================================
  async parallelInitialPrediction(year = 2026, expectType = 1): Promise<{
    code: string;
    predictions: DataPoint[];
    accuracy: number;
    metrics: EvaluationMetrics;
    modelIndex: number;
    runResults: Array<{ modelIndex: number; label: string; code: string; accuracy: number; elapsed: number }>;
  }> {
    this._abort = false
    this._startTime = Date.now()
    this._taskYear = year
    this._taskExpectType = expectType

    this._status = "running"
    this.emit({ message: `🚀 启动多模型并行预测 (${year}年, ${expectType === 1 ? "区民" : "煤改电"})` })

    // 获取 top-3 候选模型
    await this.selectBestModel(expectType)
    const topModels = this.getTopModels(3)

    // 获取真实数据用于评估
    const dataResult = await this.dataAgent.run({ action: "wait_for_data", year, expectType })
    let actuals: DataPoint[] = []
    if (dataResult.status === "data_arrived") {
      actuals = dataResult.actuals!
    } else {
      this.emit({ message: `⚠️ 真实数据未到达，无法评估并行模型效果` })
    }

    this.emit({ message: `并行运行 ${topModels.length} 个候选模型: ${topModels.map(m => m.label).join(", ")}` })

    // 并行执行所有候选模型
    const runner = async (mi: number, label: string, modelId: string) => {
      const t0 = Date.now()
      this.emit({ message: `[${label}] 生成预测代码...` })
      const genResult = await this.codeGenAgent.generateInitial(year, expectType, modelId)
      const code = genResult.code

      this.emit({ message: `[${label}] 执行预测...` })
      const execResult = await this.executionAgent.execute(code, year, expectType)
      const elapsed = Math.floor((Date.now() - t0) / 1000)

      let accuracy = 0
      let metrics: EvaluationMetrics | null = null
      if (execResult.success && execResult.predictions.length > 0 && actuals.length > 0) {
        metrics = await this.evaluationAgent.evaluate(execResult.predictions, actuals)
        accuracy = metrics.accuracy
      }

      return { modelIndex: mi, label, code, predictions: execResult.predictions, accuracy, metrics, elapsed }
    }

    const runResults = await Promise.all(topModels.map(m => runner(m.index, m.label, m.id)))

    // 按准确率排序输出结果
    const sorted = [...runResults].sort((a, b) => b.accuracy - a.accuracy)

    this.emit({ message: `\n📊 多模型并行结果对比:` })
    for (const r of sorted) {
      const mark = r.accuracy === sorted[0].accuracy ? "🏆" : "  "
      this.emit({ message: `${mark} ${r.label}: 准确率 ${r.accuracy.toFixed(2)}%, 耗时 ${r.elapsed}s` })
    }

    // 选择最优结果
    const best = sorted[0]
    this._modelIndex = best.modelIndex
    this._bestAccuracy = best.accuracy
    this._lastBestCode = best.code

    this.emit({ message: `🏆 选择 ${best.label} (${best.accuracy.toFixed(2)}%) 进入迭代优化` })

    return {
      code: best.code,
      predictions: best.predictions,
      accuracy: best.accuracy,
      metrics: best.metrics!,
      modelIndex: best.modelIndex,
      runResults: sorted.map(r => ({
        modelIndex: r.modelIndex,
        label: r.label,
        code: r.code,
        accuracy: r.accuracy,
        elapsed: r.elapsed,
      })),
    }
  }

  // ================================================================
  // 并行迭代循环（P2 功能增强版）
  // 同时运行 top-K 个模型的迭代优化，选择最优结果
  // ================================================================
  async runParallelIterationLoop(
    year: number, expectType: number,
    predictions: DataPoint[], actuals: DataPoint[],
    code: string,
    baseVersion = 0,
    parallelCount = 3,
  ) {
    this.emit({ message: `🚀 启动并行迭代: ${parallelCount} 个模型同时优化` })

    const maxIter = this._config.optimizer.maxIterations
    const targetAccuracy = this._config.optimizer.accuracyTarget

    // 获取 top-K 候选模型
    await this.selectBestModel(expectType)
    const topModels = this.getTopModels(parallelCount)

    // 为每个模型生成初始代码
    const initialResults = await Promise.all(topModels.map(async (m, idx) => {
      this.emit({ message: `[${m.label}] 生成初始预测代码...` })
      const genResult = await this.codeGenAgent.generateInitial(year, expectType, m.id)
      const execResult = await this.executionAgent.execute(genResult.code, year, expectType)
      let metrics = await this.evaluationAgent.evaluate(predictions, actuals)
      if (execResult.success && execResult.predictions.length > 0) {
        metrics = await this.evaluationAgent.evaluate(execResult.predictions, actuals)
      }
      return {
        modelIndex: m.index, modelLabel: m.label, code: execResult.predictions.length > 0 ? genResult.code : code,
        predictions: execResult.predictions.length > 0 ? execResult.predictions : predictions,
        accuracy: metrics.accuracy, metrics,
        iteration: 1, consecutiveNoImprovement: 0, roundsSinceBest: 0,
        bestCode: genResult.code, bestAccuracy: metrics.accuracy, bestMetrics: metrics, bestPredictions: execResult.predictions,
      }
    }))

    // 按准确率排序，取最好的作为全局最优
    initialResults.sort((a, b) => b.accuracy - a.accuracy)
    const bestInitial = initialResults[0]

    let globalBestCode = bestInitial.bestCode
    let globalBestAccuracy = bestInitial.bestAccuracy
    let globalBestPredictions = bestInitial.bestPredictions
    let globalBestMetrics = bestInitial.bestMetrics

    this.emit({ message: `🏆 初始结果: 最佳 ${bestInitial.modelLabel} (${bestInitial.accuracy.toFixed(2)}%)` })
    for (const r of initialResults) {
      this.emit({ message: `   ${r.modelLabel === bestInitial.modelLabel ? "🏆" : "  "} ${r.modelLabel}: ${r.accuracy.toFixed(2)}%` })
    }

    // 并行迭代
    const iterateModel = async (state: typeof initialResults[0]) => {
      for (let iter = 1; iter <= maxIter; iter++) {
        if (this._abort) break

        // 分析
        const analysis = await this.analysisAgent.analyze(
          state.bestMetrics, state.bestCode, year, expectType,
          this.store.getStrategyHistory(), this.experience.getEffectiveStrategies()
        )

        // 生成补丁
        const patchResult = await this.patchAgent.generate(
          state.bestCode, analysis as any, iter,
          this.store.getStrategyHistory()
        )
        let currentCode = patchResult.patched_code

        // 语法检查 + 自动修复（优先 fallbackPatch 本地规则，搞不定才调 LLM）
        let syntaxCheck = await this.checkPatchSyntax(currentCode)
        if (!syntaxCheck.ok) {
          this.emit({ message: `   [${state.modelLabel}] 语法错误: ${syntaxCheck.error.slice(0, 150)}` })
          let fixed = false

          // 第一步：fallbackPatch（本地规则，毫秒级）
          this.emit({ message: `   [${state.modelLabel}] 尝试规则补丁修复...` })
          const fallbackPatch = await this.patchAgent.generate(
            state.bestCode, analysis as any, iter,
            this.store.getStrategyHistory(), true, "",
          )
          if (fallbackPatch.patched_code !== state.bestCode) {
            const fallbackSyntax = await this.checkPatchSyntax(fallbackPatch.patched_code)
            if (fallbackSyntax.ok) {
              currentCode = fallbackPatch.patched_code
              fixed = true
              this.emit({ message: `   [${state.modelLabel}] ✅ 规则补丁修复成功（本地替换）` })
            }
          }

          // 第二步：fallback 不行，才调 LLM
          if (!fixed) {
            for (let attempt = 1; attempt <= 2; attempt++) {
              this.emit({ message: `   [${state.modelLabel}] LLM 自动修复第 ${attempt} 次...` })
              const retryPatch = await this.patchAgent.generate(
                state.bestCode, analysis as any, iter,
                this.store.getStrategyHistory(), false, syntaxCheck.error.slice(0, 500),
              )
              if (retryPatch.patched_code !== state.bestCode) {
                const retrySyntax = await this.checkPatchSyntax(retryPatch.patched_code)
                if (retrySyntax.ok) {
                  currentCode = retryPatch.patched_code
                  fixed = true
                  this.emit({ message: `   [${state.modelLabel}] ✅ LLM 自动修复成功` })
                  break
                }
                syntaxCheck = retrySyntax
              } else {
                this.emit({ message: `   [${state.modelLabel}] 补丁未产生改动` })
                break
              }
            }
          }

          if (!fixed) {
            this.emit({ message: `   [${state.modelLabel}] ❌ 语法错误无法修复，跳过本轮` })
            continue
          }
        }

        // 执行
        const execResult = await this.executionAgent.execute(currentCode, year, expectType)
        if (!execResult.success || execResult.predictions.length === 0) continue

        // 评估
        const newMetrics = await this.evaluationAgent.evaluate(execResult.predictions, actuals)
        const newAccuracy = newMetrics.accuracy

        if (newAccuracy > state.bestAccuracy + 0.5) {
          state.bestCode = currentCode
          state.bestAccuracy = newAccuracy
          state.bestMetrics = newMetrics
          state.bestPredictions = execResult.predictions
          state.roundsSinceBest = 0
          state.consecutiveNoImprovement = 0
          this.emit({ message: `   [${state.modelLabel}] 🎯 ${newAccuracy.toFixed(2)}% (↑${(newAccuracy - state.bestAccuracy).toFixed(2)}%)` })
        } else {
          state.roundsSinceBest++
          state.consecutiveNoImprovement++
          if (state.roundsSinceBest >= 5) break // 停止此模型的迭代
        }
      }
      return state
    }

    // 并行运行所有模型
    const parallelResults = await Promise.all(initialResults.map(iterateModel))

    // 选全局最优
    for (const r of parallelResults) {
      if (r.bestAccuracy > globalBestAccuracy) {
        globalBestAccuracy = r.bestAccuracy
        globalBestCode = r.bestCode
        globalBestPredictions = r.bestPredictions
        globalBestMetrics = r.bestMetrics
      }
    }

    this._bestAccuracy = globalBestAccuracy
    this.emit({ message: `🏆 并行迭代完成! 全局最佳: ${globalBestAccuracy.toFixed(2)}%` })

    return {
      status: this._status,
      bestAccuracy: Number(globalBestAccuracy.toFixed(2)),
      totalIterations: this.store.length,
      accuracyTimeline: this.store.getAccuracyTimeline(),
      strategyHistory: this.store.getStrategyHistory(),
    }
  }

  // ================================================================
  // ReAct 多智能体调度循环
  // 通过 Thought→Action→Observation 动态决策下一步
  // ================================================================
  async reactLoop(year = 2026, expectType = 1) {
    this._abort = false
    this._startTime = Date.now()
    this._taskYear = year
    this._taskExpectType = expectType

    this._status = "running"
    this.emit({ message: `🧠 启动 ReAct 多智能体调度 (${year}年, ${expectType === 1 ? "区民" : "煤改电"})` })

    // 先选模型
    await this.selectBestModel(expectType)

    // 构建初始状态
    const initialState: PipelineState = {
      year, expectType,
      status: "running", code: "", predictions: [], actuals: [],
      accuracy: 0, metrics: null,
      bestCode: "", bestAccuracy: 0, bestPredictions: [], bestMetrics: null,
      totalIterations: 0, currentModel: this.getCurrentModelLabel(),
      currentModelIndex: this._modelIndex, totalModels: MODEL_POOL.length,
      roundsSinceBest: 0, consecutiveEmptyPatches: 0,
      elapsed: 0, analysisResult: null, patchResult: null,
      message: "启动 ReAct 调度", researchContext: "",
    }

    // TODO: 未来可以在这里集成 RAG 查询 + WebSearch 作为初始上下文
    try { initialState.researchContext = await this.webResearch(expectType) } catch {}

    // 构建 ReAct 工具
    const tools = this.buildReactTools()
    const planner = new ReActPlanner(this._llm, tools, (msg) => {
      this.emit({ message: msg })
    })

    // 执行 ReAct 循环
    const result = await planner.planAndExecute(initialState)

    // 结果处理
    this._bestAccuracy = result.state.bestAccuracy
    this._status = result.success ? "completed" : "failed"

    if (result.success) {
      this.emit({ message: `✅ ReAct 完成! 最佳准确率: ${this._bestAccuracy.toFixed(2)}%\n${result.finalAnswer}` })
    } else {
      this.emit({ message: `❌ ReAct 终止: ${result.finalAnswer}` })
    }

    return this.buildSummary()
  }

  /** 构建 ReAct 工具列表（11 个 tool-calling 能力） */
  private buildReactTools(): ReActTool[] {
    const emit = this.emit.bind(this)
    const self = this

    return [
      // 1. 加载数据
      {
        name: "load_data", description: "加载历史数据并检查数据完整性",
        parameters: {}, handler: async (s) => {
          self._status = "running"
          emit({ message: "[ReAct] 加载数据..." })
          const sanity = await self.dataAgent.run({ action: "sanity_check", expectType: s.expectType })
          if (!sanity.passed) return `数据异常: ${sanity.errors.join("; ")}`
          const history = await self.dataAgent.run({ action: "load_history", expectType: s.expectType })
          s.totalIterations = history.totalRecords || 0
          return `数据正常: ${sanity.stats.orgCount} 区县, ${history.totalRecords} 条记录`
        },
      },

      // 2. 搜索网络知识
      {
        name: "web_search", description: "搜索网络获取电力负荷预测行业知识（仅需调用一次）",
        parameters: {}, handler: async (s) => {
          if (s.researchContext) return `已在之前搜索过: ${s.researchContext.length} 字`
          const ctx = await self.webResearch(s.expectType)
          s.researchContext = ctx
          return ctx ? `搜索到 ${ctx.length} 字行业知识` : "搜索无结果"
        },
      },

      // 3. 生成初始代码
      {
        name: "generate_initial_code", description: "为当前模型生成初始预测代码",
        parameters: {}, handler: async (s) => {
          self._status = "generating"
          emit({ message: `[ReAct] 生成 ${s.currentModel} 代码...` })
          const gen = await self.codeGenAgent.generateInitial(s.year, s.expectType, MODEL_POOL[s.currentModelIndex]?.id || "auto", s.researchContext)
          s.code = gen.code
          s.bestCode = gen.code
          return `代码生成完成 (${gen.source}, ${gen.code.length} 字符)`
        },
      },

      // 4. 执行预测（含自动语法修复）
      {
        name: "execute_code", description: "执行当前的预测代码（内置自动语法修复）",
        parameters: {}, handler: async (s) => {
          if (!s.code || s.code.length < 100) return "无可用代码，请先 generate_initial_code"
          self._status = "executing"
          emit({ message: "[ReAct] 执行预测..." })

          // 🆕 自动语法修复：优先 fallbackPatch 本地规则，搞不定才调 LLM
          let codeToRun = s.code
          let syntaxCheck = await self.checkPatchSyntax(codeToRun)
          if (!syntaxCheck.ok) {
            emit({ message: `⚠️ [ReAct] 代码语法错误: ${syntaxCheck.error.slice(0, 150)}` })
            let fixed = false
            const baseCode = s.bestCode || s.code

            // 第一步：fallbackPatch（本地规则，毫秒级）
            emit({ message: `   尝试规则补丁修复...` })
            const fallbackPatch = await self.patchAgent.generate(
              baseCode, s.analysisResult || { root_causes: ["代码语法错误"], suggestions: ["修正语法"] } as any,
              s.totalIterations + 1, "", true, "",
            )
            if (fallbackPatch.patched_code !== baseCode) {
              const fallbackSyntax = await self.checkPatchSyntax(fallbackPatch.patched_code)
              if (fallbackSyntax.ok) {
                codeToRun = fallbackPatch.patched_code
                s.code = fallbackPatch.patched_code
                fixed = true
                emit({ message: `   ✅ 规则补丁修复成功（本地替换，无 LLM 调用）` })
              }
            }

            // 第二步：fallback 不行，才调 LLM
            if (!fixed) {
              for (let attempt = 1; attempt <= 2; attempt++) {
                emit({ message: `   LLM 自动修复第 ${attempt} 次...` })
                const retryPatch = await self.patchAgent.generate(
                  baseCode, s.analysisResult || { root_causes: ["代码语法错误"], suggestions: ["修正语法"] } as any,
                  s.totalIterations + attempt, "", false, syntaxCheck.error.slice(0, 500),
                )
                if (retryPatch.patched_code !== baseCode) {
                  const retrySyntax = await self.checkPatchSyntax(retryPatch.patched_code)
                  if (retrySyntax.ok) {
                    codeToRun = retryPatch.patched_code
                    s.code = retryPatch.patched_code
                    fixed = true
                    emit({ message: `   ✅ LLM 自动修复成功` })
                    break
                  }
                  syntaxCheck = retrySyntax
                } else {
                  emit({ message: `   补丁未产生改动` })
                  break
                }
              }
            }

            if (!fixed) {
              emit({ message: `   ❌ 自动修复失败，跳过本次执行` })
              return `语法错误无法自动修复: ${syntaxCheck.error.slice(0, 200)}`
            }
          }

          const exec = await self.executionAgent.execute(codeToRun, s.year, s.expectType, `react_${s.currentModel.replace(/[^a-zA-Z0-9]/g, "_")}`)
          s.predictions = exec.predictions
          if (exec.success && exec.predictions.length > 0) {
            return `预测完成: ${exec.predictions.length} 条, 耗时 ${exec.elapsed}s`
          }
          return `执行失败: ${exec.error || "无预测结果"}`
        },
      },

      // 5. 等待真实数据
      {
        name: "wait_for_actuals", description: "等待数据库中的真实数据到达",
        parameters: {}, handler: async (s) => {
          self._status = "waiting_data"
          emit({ message: "[ReAct] 等待真实数据..." })
          const data = await self.dataAgent.run({ action: "wait_for_data", year: s.year, expectType: s.expectType })
          if (data.status === "data_arrived" && data.actuals) {
            s.actuals = data.actuals
            return `真实数据到达: ${data.actuals.length} 条 (轮询 ${data.pollAttempts} 次)`
          }
          return "等待超时，无真实数据"
        },
      },

      // 6. 评估预测
      {
        name: "evaluate", description: "评估预测结果与真实数据的对比",
        parameters: {}, handler: async (s) => {
          if (s.actuals.length === 0) return "无真实数据，请先 wait_for_actuals"
          if (s.predictions.length === 0) return "无预测数据，请先 execute_code"
          self._status = "evaluating"
          emit({ message: "[ReAct] 评估预测..." })
          const metrics = await self.evaluationAgent.evaluate(s.predictions, s.actuals)
          s.metrics = metrics
          s.accuracy = metrics.accuracy
          if (metrics.accuracy > s.bestAccuracy) {
            s.bestAccuracy = metrics.accuracy
            s.bestCode = s.code
            s.bestMetrics = metrics
            s.bestPredictions = s.predictions
            s.roundsSinceBest = 0
            // 同步更新 coordinator 实例状态，确保 /api/status 和 SSE 能实时反映
            self._bestAccuracy = metrics.accuracy
          }
          return `准确率: ${metrics.accuracy.toFixed(2)}%, MAPE: ${metrics.mape.toFixed(2)}%, 样本: ${metrics.sample_count}`
        },
      },

      // 7. 误差分析
      {
        name: "analyze_errors", description: "LLM 深度分析预测误差的原因",
        parameters: {}, handler: async (s) => {
          if (!s.bestMetrics) return "请先 evaluate"
          self._status = "analyzing"
          emit({ message: "[ReAct] 误差分析..." })
          const experience = self.experience.getEffectiveStrategies()
          const analysis = await self.analysisAgent.analyze(s.bestMetrics, s.bestCode, s.year, s.expectType, "", experience)
          s.analysisResult = analysis
          return `分析完成: ${(analysis.root_causes || []).length} 个根因, ${(analysis.suggestions || []).length} 条建议`
        },
      },

      // 8. 生成补丁
      {
        name: "patch_code", description: "根据误差分析生成代码补丁",
        parameters: { error_context: { type: "string", description: "可选的语法错误信息", required: false } },
        handler: async (s, input) => {
          if (!s.analysisResult) return "请先 analyze_errors"
          if (!s.bestCode) return "无最优代码"
          self._status = "patching"
          const iteration = s.totalIterations + 1
          emit({ message: `[ReAct] 生成补丁 (v${iteration})...` })
          const patch = await self.patchAgent.generate(
            s.bestCode, s.analysisResult, iteration,
            "", false, (input?.error_context as string) || ""
          )
          s.patchResult = patch
          s.code = patch.patched_code
          s.totalIterations = iteration
          return `补丁完成: ${patch.patch_summary}`
        },
      },

      // 9. 切换模型
      {
        name: "switch_model", description: "切换到下一个预测模型",
        parameters: {}, handler: async (s) => {
          const nextIdx = s.currentModelIndex + 1
          if (nextIdx >= MODEL_POOL.length) return "已尝试全部模型，无法切换"
          s.currentModelIndex = nextIdx
          s.currentModel = `${MODEL_POOL[nextIdx].label} (${nextIdx + 1}/${MODEL_POOL.length})`
          s.roundsSinceBest = 0
          s.consecutiveEmptyPatches = 0
          s.code = ""
          self._modelIndex = nextIdx
          emit({ message: `[ReAct] 切换到 ${MODEL_POOL[nextIdx].label}` })
          return `已切换到 ${MODEL_POOL[nextIdx].label}`
        },
      },

      // 10. 生成报告
      {
        name: "generate_report", description: "生成最终的预测总结报告",
        parameters: {}, handler: async (s) => {
          self._bestAccuracy = s.bestAccuracy
          self._status = "completed"
          const report = self.generateForecastReport()
          return `报告生成成功: ${report.title}, 最佳 ${report.bestResult.accuracy.toFixed(2)}%`
        },
      },

      // 11. 停止
      {
        name: "stop", description: "终止整个预测闭环",
        parameters: {}, handler: async (s) => {
          self._abort = true
          self._status = "stopped"
          emit({ message: "[ReAct] 用户/LLM 选择终止" })
          return "已终止"
        },
      },
    ]
  }

  // ================================================================
  // 3 独立 Agent 管道（Planner → Forecaster → Critic）
  // ================================================================
  async run3AgentPipeline(year = 2026, expectType = 1) {
    this._abort = false
    this._startTime = Date.now()
    this._taskYear = year
    this._taskExpectType = expectType
    this._status = "running"

    this.emit({ message: `🧩 启动 3-Agent 管道: Planner → Forecaster → Critic (${year}年)` })

    try {
      // === Phase 1: Planner (温度 0.3，精确规划) ===
      this._status = "running"
      this.emit({ message: "📋 [Planner] 开始规划..." })
      const planResult = await this._plannerAgent.plan(year, expectType, (msg) => this.emit({ message: `📋 ${msg}` }))

      this._modelIndex = planResult.modelIndex
      this._bestAccuracy = 0
      this.emit({ message: `📋 [Planner] 完成: ${planResult.dataSummary}, 模型=${planResult.modelLabel}` })

      // 执行初始预测
      this._status = "executing"
      this.emit({ message: "执行初始预测..." })
      const execResult = await this.executionAgent.execute(planResult.code, year, expectType, "initial")
      if (!execResult.success) throw new Error(`初始预测失败: ${execResult.error}`)
      planResult.predictions = execResult.predictions
      this.emit({ message: `初始预测完成: ${execResult.predictions.length} 条` })

      // 等待真实数据
      this._status = "waiting_data"
      this.emit({ message: "等待真实数据..." })
      const dataResult = await this.dataAgent.run({ action: "wait_for_data", year, expectType })
      if (dataResult.status !== "data_arrived") {
        this.emit({ message: "真实数据未到达，跳过迭代" })
        return this.buildSummary()
      }
      const actuals = dataResult.actuals!
      this.emit({ message: `真实数据到达: ${actuals.length} 条` })

      // === Phase 2: Forecaster (温度 0.6，探索迭代) ===
      this._status = "analyzing"
      this.emit({ message: "🔬 [Forecaster] 开始迭代优化..." })
      const forecasterResult = await this._forecasterAgent.iterate(
        planResult.code, planResult.predictions, actuals,
        year, expectType,
        this._config.optimizer.maxIterations,
        this._config.optimizer.accuracyTarget,
        planResult.modelIndex,
        (msg) => this.emit({ message: `🔬 ${msg}` }),
      )
      this._bestAccuracy = forecasterResult.bestAccuracy
      this.emit({ message: `🔬 [Forecaster] 完成: 最佳 ${forecasterResult.bestAccuracy.toFixed(2)}%` })

      // 保存最优迭代版本
      const bestRecord: IterationRecord = {
        version: this.store.length,
        code: forecasterResult.bestCode,
        predictions: forecasterResult.bestPredictions,
        evaluation: forecasterResult.bestMetrics,
        accuracy: forecasterResult.bestAccuracy,
        improved: true,
        source: "forecaster_optimized",
      }
      this.store.saveIteration(this.store.length, bestRecord)

      // === Phase 3: Critic (温度 0.2，稳定评审) ===
      this._status = "completed"
      this.emit({ message: "📝 [Critic] 生成总结报告..." })
      const report = await this._criticAgent.review(
        this.store.loadAll(),
        forecasterResult.bestAccuracy,
        forecasterResult.bestMetrics,
        { year, expectType, modelLabel: forecasterResult.modelLabel },
        this._lastDataProfile || {},
        (msg) => this.emit({ message: `📝 ${msg}` }),
      )

      this._status = "completed"
      this.emit({ message: `✅ 3-Agent 管道完成! 最佳准确率: ${forecasterResult.bestAccuracy.toFixed(2)}%` })
      return this.buildSummary()
    } catch (e: any) {
      if (!this._abort && e.message !== "用户已终止闭环") {
        this._status = "failed"
        this.emit({ message: `3-Agent 管道异常: ${e.message || e}` })
      }
      return this.buildSummary()
    }
  }

  /** 获取得分最高的 N 个候选模型 */
  private getTopModels(n: number): Array<{ index: number; id: string; label: string }> {
    const chars = this._lastDataProfile
    if (!chars) {
      // 无特征数据时取前 N 个
      return MODEL_POOL.slice(0, Math.min(n, MODEL_POOL.length)).map((m, i) => ({ index: i, id: m.id, label: m.label }))
    }
    try {
      const charsObj = {
        seasonalityStrength: chars.seasonalityStrength || 0,
        trendDirection: chars.trendDirection || "flat",
        trendStrength: chars.trendStrength || 0,
        volatility: chars.volatility || 0,
        orgCount: chars.orgCount || 0,
        yearsCount: chars.yearsCount || 0,
        hasSeasonality: chars.seasonalityStrength > 0.3,
        expectType: this._taskExpectType,
        peakRatio: 1.2,
        yoyGrowthAvg: 0,
      }
      const result = selectInitialModel(charsObj as any)
      // result.index 是得分最高的，我们还需要得分次高的
      // 简单取 result.index 和附近的
      const indices = new Set<number>()
      indices.add(result.index)
      indices.add((result.index + 1) % MODEL_POOL.length)
      indices.add((result.index + 2) % MODEL_POOL.length)
      return [...indices].slice(0, n).map(i => ({ index: i, id: MODEL_POOL[i].id, label: MODEL_POOL[i].label }))
    } catch {
      return MODEL_POOL.slice(0, Math.min(n, MODEL_POOL.length)).map((m, i) => ({ index: i, id: m.id, label: m.label }))
    }
  }

  // ================================================================
  // 完整闭环
  // ================================================================
  async fullLoop(year = 2026, expectType = 1) {
    this._abort = false
    this._startTime = Date.now()

    // 使用 store 现有长度作为本轮起始偏移，避免覆盖历史记录
    const baseVersion = this.store.length
    this.emit({ message: `启动自优化闭环 (第 ${baseVersion + 1} 轮, 从 v${baseVersion} 开始, 初始模型: ${MODEL_POOL[this._modelIndex]?.label}): ${year}年, ${expectType === 1 ? "区民" : "煤改电"}` })

    try {
      this._status = "running"
      this.emit({ message: `启动自优化闭环: ${year}年, ${expectType === 1 ? "区民" : "煤改电"}` })

      // Phase 0: 数据完整性检查
      this.checkAbort()
      const sanity = await this.dataAgent.run({ action: "sanity_check", expectType })
      if (!sanity.passed) {
        const errMsg = `数据异常: ${sanity.errors.join("; ")}`
        this.emit({ message: `⚠️ ${errMsg}` })
        throw new Error(errMsg)
      }
      if (sanity.warnings.length > 0) {
        this.emit({ message: `数据检查: ${sanity.warnings.join("; ")}` })
      }
      this.emit({ message: `数据完整性检查通过: ${sanity.stats.orgCount} 区县, 共 ${sanity.stats.totalMonths} 月数据` })

      // Phase 1: 生成初始代码
      this.checkAbort()
      this._status = "generating"
      this.emit({ message: "LLM 生成初始预测代码..." })

      // 🆕 P2.2: 数据探索 — 网络调研（注入行业知识）
      let researchContext = ""
      try {
        researchContext = await this.webResearch(expectType)
        if (researchContext) {
          this.emit({ message: `🌐 网络调研完成: 获取到 ${researchContext.length} 字行业知识上下文` })
        }
      } catch (e: any) {
        this.emit({ message: `🌐 网络调研可选步骤未执行: ${e.message || e}` })
      }

      const genResult = await this.codeGenAgent.generateInitial(year, expectType, MODEL_POOL[this._modelIndex].id, researchContext)
      this.checkAbort()
      let code = genResult.code
      this.emit({ message: `初始代码生成完成 (来源: ${genResult.source}, 模型: ${MODEL_POOL[this._modelIndex].label})` })

      // Phase 2: 执行初始预测
      this.checkAbort()
      this._status = "executing"
      this.emit({ message: "执行初始预测..." })
      let execResult = await this.executionAgent.execute(code, year, expectType)
      this.checkAbort()
      if (!execResult.success) throw new Error(`初始预测执行失败: ${execResult.error}`)
      let predictions = execResult.predictions
      this.emit({ message: `初始预测完成: ${predictions.length} 条` })

      // 保存 v{baseVersion} (不再覆盖之前的版本)
      this.store.saveIteration(baseVersion, { version: baseVersion, code, predictions, source: genResult.source })

      // Phase 3: 等待真实数据
      this.checkAbort()
      this._status = "waiting_data"
      this.emit({ message: "等待真实数据到达..." })
      const dataResult = await this.dataAgent.run({ action: "wait_for_data", year, expectType })
      this.checkAbort()
      if (dataResult.status !== "data_arrived") {
        return this.buildSummary()
      }
      const actuals = dataResult.actuals!
      this.emit({ message: `真实数据到达: ${actuals.length} 条` })

      // Phase 4: 迭代优化
      this.checkAbort()
      return this.runIterationLoop(year, expectType, predictions, actuals, code, baseVersion)
    } catch (e: any) {
      // 区分"用户中止"和"真正错误"
      if (this._abort || e.message === "用户已终止闭环") {
        this._status = "stopped"
        this.emit({ message: `闭环已终止` })
      } else {
        this._status = "failed"
        this.emit({ message: `闭环异常: ${e.message || e}` })
      }
      return this.buildSummary()
    }
  }

  private async runIterationLoop(
    year: number, expectType: number,
    predictions: DataPoint[], actuals: DataPoint[],
    code: string,
    baseVersion = 0,
  ) {
    const maxIter = this._config.optimizer.maxIterations
    const targetAccuracy = this._config.optimizer.accuracyTarget
    let currentCode = code
    let currentPredictions = predictions

    // 评估初始版本
    this.checkAbort()
    this._status = "evaluating"
    this.emit({ message: "评估初始预测..." })
    let metrics = await this.evaluationAgent.evaluate(currentPredictions, actuals)
    let currentAccuracy = metrics.accuracy
    this._bestAccuracy = currentAccuracy
    this.store.saveIteration(baseVersion, { version: baseVersion, code: currentCode, predictions: currentPredictions, evaluation: metrics, accuracy: currentAccuracy })

    this.emit({ message: `初始准确率: ${currentAccuracy.toFixed(2)}%`, accuracy: currentAccuracy })

    if (currentAccuracy >= targetAccuracy) {
      this._status = "completed"
      this.emit({ message: `初始模型已达目标 ${targetAccuracy}%` })
      return this.buildSummary()
    }

    // 初始版本即为当前最优
    let bestCode = currentCode
    let bestPredictions = currentPredictions
    let bestMetrics = metrics
    let bestAccuracy = currentAccuracy
    let roundsSinceBest = 0  // 距离上次最好成绩的轮数

    // 迭代循环
    for (let i = 1; i <= maxIter; i++) {
      this.checkAbort()
      const elapsed = Math.floor((Date.now() - this._startTime) / 1000)
      this.emit({ message: `迭代 ${i}/${maxIter} (已运行 ${elapsed}s, 最优 ${bestAccuracy.toFixed(2)}% 在 ${roundsSinceBest} 轮前)`,
        version: baseVersion + i, iteration: i, maxIterations: maxIter, elapsed })

      // D1: 误差深度分析（基于最优代码，而不是当前可能已退化的代码）
      this.checkAbort()
      this._status = "analyzing"
      this.emit({ message: `[${i}/${maxIter}] LLM 误差深度分析（基准: 最优 ${bestAccuracy.toFixed(2)}%）...`,
        iteration: i, maxIterations: maxIter, elapsed })

      // 🆕 RAG 检索：从历史分析中获取相关文档作为上下文
      let ragContext = ""
      try {
        const ragResults = this._ragStore.query(
          `误差分析 MAPE=${bestMetrics.mape} 准确率=${bestAccuracy}% 电力负荷预测`,
          3
        )
        if (ragResults.length > 0) {
          ragContext = ragResults.map((r, idx) =>
            `[历史参考 ${idx + 1}] ${r.document.content.slice(0, 500)}`
          ).join("\n\n")
          this.emit({ message: `📚 RAG 检索到 ${ragResults.length} 条相关历史分析` })
        }
      } catch {}

      // 注入跨会话经验，让 LLM 知道历史上什么有效/无效
      const crossSessionEff = this.experience.getEffectiveStrategies()
      const crossSessionFail = this.experience.getFailedStrategies()
      const experienceInjection = `[跨会话有效策略]\n${crossSessionEff}\n\n[跨会话失败策略]\n${crossSessionFail}\n\n[RAG 相关分析]\n${ragContext || "无相关历史"}`
      const analysis = await this.analysisAgent.analyze(bestMetrics, bestCode, year, expectType,
        this.store.getStrategyHistory(), experienceInjection)

      // 🆕 将分析结果存入 RAG
      try {
        const analysisText = `MAPE=${bestMetrics.mape}%, 准确率=${bestAccuracy}%, 偏差=${bestMetrics.bias}\n根因: ${
          (analysis.root_causes || []).map(c => c.factor).join("; ")
        }\n建议: ${
          (analysis.suggestions || []).map(s => s.description).join("; ")
        }`
        this._ragStore.add(analysisText, {
          category: "analysis",
          iteration: i,
          accuracy: bestAccuracy,
          model: MODEL_POOL[this._modelIndex]?.label || "?",
        })
      } catch {}

      // D2: 生成代码补丁（在最优代码上修改，而非退化版本）
      this.checkAbort()
      this._status = "patching"
      this.emit({ message: `[${i}/${maxIter}] 基于最优代码 (${bestAccuracy.toFixed(2)}%) 生成补丁...`,
        iteration: i, maxIterations: maxIter, elapsed })
      const patchResult = await this.patchAgent.generate(bestCode, analysis as any, i, this.store.getStrategyHistory())
      currentCode = patchResult.patched_code

      // 补丁语法检查：执行前先编译验证，优先 fallbackPatch 本地规则修复
      const syntaxResult = await this.checkPatchSyntax(currentCode)
      if (!syntaxResult.ok) {
        this.emit({ message: `⚠️ 补丁代码语法错误: ${syntaxResult.error.slice(0, 200)}` })
        let fixedOk = false

        // 🆕 第一步：fallbackPatch（本地规则，毫秒级，不会引入新错误）
        this.emit({ message: `   尝试规则补丁修复...` })
        const fallbackPatch = await this.patchAgent.generate(bestCode, analysis as any, i, this.store.getStrategyHistory(), true, "")
        if (fallbackPatch.patched_code !== bestCode) {
          const fallbackSyntax = await this.checkPatchSyntax(fallbackPatch.patched_code)
          if (fallbackSyntax.ok) {
            currentCode = fallbackPatch.patched_code
            fixedOk = true
            this.emit({ message: `   ✅ 规则补丁修复成功（本地替换，无 LLM 调用）` })
          }
        }

        // 第二步：fallback 不行，才调 LLM（最多 2 次）
        if (!fixedOk) {
          let patchRetries = 0
          let fixedCode = currentCode
          while (patchRetries < 2 && !fixedOk) {
            patchRetries++
            this.emit({ message: `   LLM 重试第 ${patchRetries} 次...` })
            const retryPatch = await this.patchAgent.generate(bestCode, analysis as any, i, this.store.getStrategyHistory(), false, syntaxResult.error.slice(0, 500))
            fixedCode = retryPatch.patched_code
            if (fixedCode === bestCode) {
              this.emit({ message: `   重试补丁未产生改动，跳过` })
              break
            }
            const retrySyntax = await this.checkPatchSyntax(fixedCode)
            if (retrySyntax.ok) {
              fixedOk = true
              currentCode = fixedCode
              this.emit({ message: `   ✅ LLM 第 ${patchRetries} 次重试语法通过` })
            } else {
              this.emit({ message: `   ❌ 第 ${patchRetries} 次重试仍有语法错误: ${retrySyntax.error.slice(0, 150)}` })
            }
          }
        }

        if (!fixedOk) {
          this.emit({ message: `   回滚到最优代码，跳过本轮` })
          currentCode = bestCode  // 回滚
          if (this._consecutiveEmptyPatches < 3) this._consecutiveEmptyPatches++
          continue
        }
      }

      // D3: 执行优化后代码
      this.checkAbort()
      this._status = "executing"
      this.emit({ message: `[${i}/${maxIter}] 执行优化后代码...`, iteration: i, maxIterations: maxIter, elapsed })
      const execResult = await this.executionAgent.execute(currentCode, year, expectType)
      if (!execResult.success) {
        this.emit({ message: `执行失败: ${execResult.error}，跳过本轮` })
        continue
      }

      // 空补丁检测：如果补丁没有实际改变代码，跳过本轮（不会产生新版本）
      if (currentCode === bestCode) {
        this._consecutiveEmptyPatches++
        this.emit({ message: `⚠️ 补丁未产生实际改动 (第${this._consecutiveEmptyPatches}次)，跳过本轮` })
        if (this._consecutiveEmptyPatches >= 3) {
          // 🆕 HITL: 连续 3 次空补丁，暂停让专家决定
          const decision = await this.waitForHuman(
            `「${MODEL_POOL[this._modelIndex].label}」已连续 ${this._consecutiveEmptyPatches} 次补丁无效（最优 ${bestAccuracy.toFixed(2)}%）`,
            "stuck",
            ["强制规则兜底修改参数", "跳过当前模型", "停止闭环"],
          )
          if (decision === "stop") {
            this._status = "stopped"
            this.emit({ message: `👤 用户选择停止闭环` })
            break
          }
          if (decision === "skip_model") {
            this._modelIndex++
            if (this._modelIndex >= MODEL_POOL.length) {
              this.emit({ message: `已尝试全部模型，停止优化` })
              break
            }
            const nextModel = MODEL_POOL[this._modelIndex]
            this.emit({ message: `👤 跳过到 [${nextModel.label}]` })
            this._status = "generating"
            const newGen = await this.codeGenAgent.generateInitial(year, expectType, nextModel.id)
            currentCode = newGen.code
            this._consecutiveEmptyPatches = 0
            this._consecutiveNoImprovement = 0
            continue
          }
          // continue → 强制使用 fallback 规则兜底
          this.emit({ message: `强制使用规则兜底修改参数` })
          const fallbackPatch = await this.patchAgent.generate(code, {} as any, i, this.store.getStrategyHistory(), true)
          currentCode = fallbackPatch.patched_code
          this._consecutiveEmptyPatches = 0
          // 检查兜底是否真的有修改
          if (currentCode === bestCode) {
            this.emit({ message: `规则兜底也未产生改动，终止优化 (所有模型参数已尝试过)` })
            break
          }
          this.emit({ message: `规则兜底: ${fallbackPatch.patch_summary}` })
          // 执行兜底修改后的代码
          this.checkAbort()
          this._status = "executing"
          const fbExec = await this.executionAgent.execute(currentCode, year, expectType)
          if (!fbExec.success || fbExec.predictions.length === 0) {
            this.emit({ message: `兜底代码执行失败，跳过本轮` })
            currentCode = bestCode  // 回滚
            continue
          }
          const fbMetrics = await this.evaluationAgent.evaluate(fbExec.predictions, actuals)
          const fbAccuracy = fbMetrics.accuracy
          this.emit({ message: `兜底修改后准确率: ${fbAccuracy.toFixed(2)}%` })
          if (fbAccuracy > bestAccuracy) {
            bestCode = currentCode
            bestPredictions = fbExec.predictions
            bestMetrics = fbMetrics
            bestAccuracy = fbAccuracy
            this._bestAccuracy = fbAccuracy
          }
          // 无论是否提升，继续下一轮迭代
        }
        continue
      }

      // 空预测检测：LLM 补丁破坏输出格式，跳过本轮
      if (execResult.predictions.length === 0) {
        this._consecutiveEmptyPredictions++
        this.emit({ message: `⚠️ 补丁后预测为空 (第${this._consecutiveEmptyPredictions}次)，跳过` })
        if (this._consecutiveEmptyPredictions >= 3) {
          this.emit({ message: `连续 3 次补丁无效，提前终止` })
          break
        }
        continue
      }
      this._consecutiveEmptyPredictions = 0

      // D4: 评估优化效果
      this.checkAbort()
      this._status = "evaluating"
      const newMetrics = await this.evaluationAgent.evaluate(execResult.predictions, actuals)
      const newAccuracy = newMetrics.accuracy

      // 评估异常检测：sample_count=0 或 eval_error=true，跳过本轮
      if (newMetrics.eval_error || newMetrics.sample_count === 0) {
        this._consecutiveEmptyPredictions++
        const reason = newMetrics.eval_error ? "评估抛异常" : "预测与实际无匹配"
        this.emit({ message: `⚠️ 评估失败 (${reason}): ${newMetrics.error || "sample_count=0"}，回滚到最优` })
        if (this._consecutiveEmptyPredictions >= 3) {
          this.emit({ message: `连续 3 次评估失败，提前终止` })
          break
        }
        currentCode = bestCode  // 回滚
        continue
      }
      this._consecutiveEmptyPredictions = 0

      const realImprovement = newAccuracy - bestAccuracy
      const isImproved = realImprovement > 0.5

      // 只要是提升就更新最优
      if (isImproved) {
        const oldBestAccuracy = bestAccuracy  // 保存旧值用于 experience + skill
        bestCode = currentCode
        this._lastBestCode = currentCode
        bestPredictions = execResult.predictions
        bestMetrics = newMetrics
        bestAccuracy = newAccuracy
        this._bestAccuracy = newAccuracy
        this._consecutiveNoImprovement = 0
        this._consecutiveEmptyPatches = 0
        roundsSinceBest = 0

        // 🏆 只有真正提升时才保存版本
        const record: IterationRecord = {
          version: baseVersion + i,
          code: currentCode,
          predictions: execResult.predictions,
          evaluation: newMetrics,
          accuracy: newAccuracy,
          analysis: analysis as any,
          patch: patchResult as any,
          execution: { elapsed: execResult.elapsed, success: execResult.success, error: execResult.error },
          improved: true,
          source: "optimization",
        }
        this.store.saveIteration(baseVersion + i, record)

        // 跨会话经验：记录有效策略
        const patchInfo = patchResult?.patch_summary || patchResult?.changed_sections?.[0]?.section || "未知修改"
        this.experience.addEntry({
          modelId: MODEL_POOL[this._modelIndex]?.id || "unknown",
          strategyType: "model_params",
          strategy: patchResult?.patch_summary || "参数调整",
          whatChanged: patchInfo,
          accuracyBefore: oldBestAccuracy,
          accuracyAfter: newAccuracy,
          effective: true,
          hadSideEffect: false,
        })

        // 🆕 Skill 沉淀：记录结构化策略到跨项目 Skill 库
        if (this._lastDataProfile && (newAccuracy - oldBestAccuracy) > 1) {
          this.skillStore.createFromExperience(
            MODEL_POOL[this._modelIndex]?.id || "unknown",
            MODEL_POOL[this._modelIndex]?.label || "?",
            patchInfo,
            oldBestAccuracy,
            newAccuracy,
            true,
            false,
            this._lastDataProfile,
            expectType,
          )
        }

        this.emit({ message: `🎯 新最佳! ${newAccuracy.toFixed(2)}% (提升 ${realImprovement.toFixed(2)}%) 已保存为 v${baseVersion + i}`,
          accuracy: newAccuracy, version: baseVersion + i, iteration: i, maxIterations: maxIter, elapsed })

        // 检查是否达成目标
        if (newAccuracy >= targetAccuracy) {
          this.emit({ message: `目标达成! 准确率 ${newAccuracy.toFixed(2)}% >= ${targetAccuracy}%`, iteration: i, maxIterations: maxIter })
          break
        }
      } else {
        // 下降或持平 → 不保存，回滚到最优代码，继续尝试
        this._consecutiveNoImprovement++
        roundsSinceBest++
        currentCode = bestCode  // 核心：回滚到最优

        if (Math.abs(realImprovement) > 1) {
          this.emit({ message: `⬇ 退化 ${Math.abs(realImprovement).toFixed(2)}% (当前 ${newAccuracy.toFixed(2)}% vs 最优 ${bestAccuracy.toFixed(2)}%)，不保存版本，回滚重试`,
            accuracy: newAccuracy, version: baseVersion + i, iteration: i, maxIterations: maxIter, elapsed })
          // 跨会话经验：记录无效策略
          const failInfo = patchResult?.patch_summary || patchResult?.changed_sections?.[0]?.section || "未知修改"
          this.experience.addEntry({
            modelId: MODEL_POOL[this._modelIndex]?.id || "unknown",
            strategyType: "model_params",
            strategy: patchResult?.patch_summary || "参数调整",
            whatChanged: failInfo,
            accuracyBefore: bestAccuracy,
            accuracyAfter: newAccuracy,
            effective: false,
            hadSideEffect: false,
          })
        } else {
          this.emit({ message: `→ 无提升 (当前 ${newAccuracy.toFixed(2)}% vs 最优 ${bestAccuracy.toFixed(2)}%)，不保存版本，回滚重试`,
            accuracy: newAccuracy, version: baseVersion + i, iteration: i, maxIterations: maxIter, elapsed })
        }

        // 连续 10 轮没有提升 → 切换到下一个预测模型
        // 🆕 HITL: 连续 5 轮没提升时暂停，让专家决定
        if (roundsSinceBest >= 5 && roundsSinceBest < 10) {
          const decision = await this.waitForHuman(
            `「${MODEL_POOL[this._modelIndex].label}」已连续 ${roundsSinceBest} 轮无提升（最优 ${bestAccuracy.toFixed(2)}%），请选择操作`,
            "stuck",
            ["继续当前模型", "跳过当前模型，切换下一个", "停止整个闭环"],
          )
          if (decision === "skip_model") {
            // 手动跳过当前模型
            this._modelIndex++
            if (this._modelIndex >= MODEL_POOL.length) {
              this.emit({ message: `已尝试全部模型，停止优化` })
              break
            }
            const nextModel = MODEL_POOL[this._modelIndex]
            this.emit({ message: `👤 用户选择跳过，切换到 [${nextModel.label}]` })
            this._status = "generating"
            const newGen = await this.codeGenAgent.generateInitial(year, expectType, nextModel.id)
            currentCode = newGen.code
            roundsSinceBest = 0
            this._consecutiveNoImprovement = 0
            this._consecutiveEmptyPredictions = 0
            this._consecutiveEmptyPatches = 0
            continue
          } else if (decision === "stop") {
            this._status = "stopped"
            this.emit({ message: `👤 用户选择停止闭环` })
            break
          }
          // continue → 什么都不做，继续迭代
        }

        if (roundsSinceBest >= 10) {
          this._modelIndex++
          if (this._modelIndex >= MODEL_POOL.length) {
            // 所有模型都已尝试，真停
            this.emit({ message: `已尝试全部 ${MODEL_POOL.length} 种模型，均未超越 ${bestAccuracy.toFixed(2)}%，停止优化`, iteration: i, maxIterations: maxIter })
            break
          }

          // 切换到下一个模型
          const nextModel = MODEL_POOL[this._modelIndex]
          this.emit({ message: `🔄 当前模型优化停滞，切换到 [${nextModel.label}] (${this._modelIndex + 1}/${MODEL_POOL.length})...`,
            iteration: i, maxIterations: maxIter, elapsed })

          // 用新模型重新生成初始代码
          this._status = "generating"
          const newGen = await this.codeGenAgent.generateInitial(year, expectType, nextModel.id)
          currentCode = newGen.code
          roundsSinceBest = 0
          this._consecutiveNoImprovement = 0
          this._consecutiveEmptyPredictions = 0
          this._consecutiveEmptyPatches = 0

          this.emit({ message: `✅ 切换到 ${nextModel.label}，继续优化 (已有最优 ${bestAccuracy.toFixed(2)}%)`,
            iteration: i, maxIterations: maxIter, elapsed })
          continue  // 重新执行预测 + 评估
        }
      }
    }

    // 确保最终报告返回的是最优准确率
    this._bestAccuracy = bestAccuracy
    // 检查中途中止
    if (this._abort) {
      this._status = "stopped"
      this.emit({ message: `闭环已终止` })
    } else {
      this._status = "completed"
      this.emit({ message: `闭环完成! 最佳准确率: ${this._bestAccuracy.toFixed(2)}%` })
    }
    return this.buildSummary()
  }

  // ================================================================
  // 快速迭代（基于最优代码）
  // ================================================================
  async quickLoop(year = 2026, expectType = 1) {
    const best = this.getBestRecord()
    if (!best?.code) {
      const latest = this.store.getLatest()
      if (!latest?.code) {
        throw new Error("无可用代码，请先运行 fullLoop")
      }
      this._status = "running"
      this.emit({ message: `从最新代码开始 (v${latest.version})` })
      return this.fullLoop(year, expectType)
    }

    this._status = "running"
    this.emit({ message: `从最优版本恢复 (v${best.version}: ${best.accuracy?.toFixed(2) || "?"}%)` })
    // 先用最优代码覆盖初始版本，然后走完整闭环的开头逻辑
    this._abort = false
    this._startTime = Date.now()
    return this.fullLoop(year, expectType)
  }

  /** 获取最优版本的代码 */
  getBestCode(): { version: number; code: string; accuracy: number } | null {
    const best = this.getBestRecord()
    if (!best?.code) return null
    return { version: best.version, code: best.code, accuracy: best.accuracy || 0 }
  }

  private getBestRecord(): IterationRecord | null {
    const records = this.store.loadAll()
    if (!records.length) return null
    let best = records[0]
    for (const r of records) {
      if ((r.accuracy || 0) > (best.accuracy || 0)) best = r
    }
    return best
  }

  // ================================================================
  // 获取状态和记录
  // ================================================================
  getHistory() {
    return this.store.loadAll()
  }

  getAccuracyTimeline() {
    return this.store.getAccuracyTimeline()
  }

  private buildSummary() {
    const timeline = this.store.getAccuracyTimeline()
    return {
      status: this._status,
      bestAccuracy: Number(this._bestAccuracy.toFixed(2)),
      totalIterations: this.store.length,
      accuracyTimeline: timeline,
      strategyHistory: this.store.getStrategyHistory(),
    }
  }

  /** 生成结构化预测总结报告 */
  generateForecastReport(): ForecastReport {
    const records = this.store.loadAll()
    const timeline = this.store.getAccuracyTimeline()
    const bestRecord = this.getBestRecord()
    const now = new Date().toLocaleString("zh-CN")
    const elapsed = Math.floor((Date.now() - this._startTime) / 1000)
    const elapsedStr = elapsed > 3600
      ? `${Math.floor(elapsed / 3600)}时${Math.floor((elapsed % 3600) / 60)}分${elapsed % 60}秒`
      : elapsed > 60
        ? `${Math.floor(elapsed / 60)}分${elapsed % 60}秒`
        : `${elapsed}秒`

    const dp = this._lastDataProfile

    // 区县级性能分析
    const perOrg = bestRecord?.evaluation?.per_org || {}
    const perOrgList = Object.entries(perOrg).map(([org, m]) => ({
      org,
      accuracy: m.accuracy,
      mape: m.mape,
      status: (m.accuracy >= 90 ? "优秀" : m.accuracy >= 80 ? "良好" : "待优化") as "优秀" | "良好" | "待优化",
    }))

    // 迭代摘要
    const iterationSummary = records.map(r => {
      let status: "initial" | "improved" | "no_change" | "regressed" | "failed"
      if (r.version === 0) status = "initial"
      else if (r.evaluation?.eval_error) status = "failed"
      else if (r.improved) status = "improved"
      else status = "no_change"

      let improvement = "—"
      if (r.improved && r.accuracy != null) {
        const prevRecord = records.find(p => p.version === r.version - 1)
        if (prevRecord?.accuracy != null) {
          const diff = r.accuracy - prevRecord.accuracy
          improvement = diff > 0 ? `+${diff.toFixed(2)}%` : `${diff.toFixed(2)}%`
        }
      }

      return {
        version: r.version,
        accuracy: r.accuracy ?? null,
        improvement,
        patchSummary: r.patch?.patch_summary || r.source || (r.version === 0 ? "初始生成" : "—"),
        status,
      }
    })

    // 洞察分析
    let seasonalityImpact = dp ? `季节强度 ${(dp.seasonalityStrength * 100).toFixed(0)}%` : "数据不足，无法分析季节性"
    if (dp) {
      if (dp.seasonalityStrength > 0.7) seasonalityImpact += " — 强季节性，模型有效捕捉了年度周期模式"
      else if (dp.seasonalityStrength > 0.4) seasonalityImpact += " — 中等季节性，周期性特征明显"
      else seasonalityImpact += " — 弱季节性，序列以随机波动为主"
    }

    let trendAnalysis = dp ? `趋势方向 ${dp.trendDirection} (强度 ${(dp.trendStrength * 100).toFixed(0)}%)` : "数据不足"
    if (dp) {
      if (dp.trendStrength > 0.6) trendAnalysis += " — 显著趋势，差分或去趋势处理有效"
      else trendAnalysis += " — 趋势平稳，无需过度差分"
    }

    let volatilityAnalysis = dp
      ? `波动率 ${(dp.volatility * 100).toFixed(0)}%` + (dp.volatility > 0.5 ? " — 高波动，推荐稳健模型" : " — 波动可控，经典时序模型适用")
      : "数据不足"

    let convergenceAnalysis = "优化过程"
    if (timeline.length >= 2) {
      const first = timeline[0].accuracy
      const last = timeline[timeline.length - 1].accuracy
      const totalImprovement = last - first
      if (totalImprovement > 5) convergenceAnalysis += ` 显著收敛：${first.toFixed(2)}% → ${last.toFixed(2)}%（提升 ${totalImprovement.toFixed(2)}%）`
      else if (totalImprovement > 0) convergenceAnalysis += ` 小幅提升：${first.toFixed(2)}% → ${last.toFixed(2)}%（提升 ${totalImprovement.toFixed(2)}%）`
      else convergenceAnalysis += ` 稳定运行：最佳 ${last.toFixed(2)}%（无明显提升窗口）`
    } else {
      convergenceAnalysis += " 该轮无多版本迭代数据"
    }

    const bestModelLabel = MODEL_POOL[this._modelIndex]?.label || "未知"
    const bestModelReason = `基于数据特征智能匹配 + 迭代优化，${bestModelLabel} 在该场景下表现最优`

    // 推荐建议
    const recommendations: string[] = []
    if (bestRecord?.accuracy && bestRecord.accuracy < 90) {
      recommendations.push("📈 尝试更多模型变体，如 XGBoost + ARIMA 混合模型")
    }
    if (dp?.seasonalityStrength && dp.seasonalityStrength > 0.6) {
      recommendations.push("🔄 强季节性数据，建议尝试 STL 分解 + 专用季节模型")
    }
    if (dp?.volatility && dp.volatility > 0.5) {
      recommendations.push("⚡ 高波动数据，可探索 GARCH 波动率建模或异常值预处理")
    }
    if (perOrgList.some(o => o.accuracy < 80)) {
      const poorOrgs = perOrgList.filter(o => o.accuracy < 80).map(o => o.org).join("、")
      recommendations.push(`🎯 ${poorOrgs} 准确率偏低，建议单独调参或增加专属特征`)
    }
    if (dp?.yearsCount && dp.yearsCount < 3) {
      recommendations.push("📊 历史数据有限（不足3年），建议积累更多数据后重训模型")
    }
    if (recommendations.length === 0) {
      recommendations.push("✅ 当前模型表现良好，建议持续监控数据漂移")
    }

    return {
      title: `CastFlow 自迭代预测总结报告`,
      generatedAt: now,
      taskInfo: {
        year: this._taskYear,
        expectType: this._taskExpectType === 1 ? "区民用电" : "煤改电",
        totalIterations: this.store.length,
        elapsed: elapsedStr,
        initialModel: `${MODEL_POOL[this._modelIndex]?.label || "自动选择"}`,
        bestModel: bestModelLabel,
        iterationsCompleted: iterationSummary.length,
      },
      bestResult: {
        accuracy: Number(this._bestAccuracy.toFixed(2)),
        mape: bestRecord?.evaluation?.mape ?? 0,
        mae: bestRecord?.evaluation?.mae ?? 0,
        rmse: bestRecord?.evaluation?.rmse ?? 0,
        bias: bestRecord?.evaluation?.bias || "balanced",
        level: bestRecord?.evaluation?.level || "—",
      },
      perOrgPerformance: perOrgList,
      dataCharacteristics: {
        seasonalityStrength: dp?.seasonalityStrength ?? 0,
        trendDirection: dp?.trendDirection ?? "flat",
        trendStrength: dp?.trendStrength ?? 0,
        volatility: dp?.volatility ?? 0,
        orgCount: dp?.orgCount ?? 0,
        yearsCount: dp?.yearsCount ?? 0,
        description: dp
          ? `${dp.orgCount} 个区县 × ${dp.yearsCount} 年历史数据，` +
            `季节强度 ${(dp.seasonalityStrength * 100).toFixed(0)}%，` +
            `趋势 ${dp.trendDirection}，` +
            `波动 ${(dp.volatility * 100).toFixed(0)}%`
          : "数据特征不足",
      },
      accuracyTimeline: timeline,
      iterationSummary,
      recommendations,
      strategyHistory: this.store.getStrategyHistory(),
      insights: {
        seasonalityImpact,
        trendAnalysis,
        volatilityAnalysis,
        convergenceAnalysis,
        bestModelReason,
      },
    }
  }

  /** 🌐 真实 WebSearch：通过搜狗搜索引擎获取实时电力负荷预测行业知识 */
  private async webResearch(expectType: number): Promise<string> {
    if (this._researchCache) return this._researchCache
    const typeName = expectType === 1 ? "区民用电" : "煤改电"

    try {
      this.emit({ message: `🌐 真实 WebSearch: 搜索电力负荷预测行业知识...` })

      // 并行搜索多个相关查询
      const queries = [
        `电力负荷预测 最新方法 2025`,
        `${typeName} 月度负荷特性 季节性 周期`,
        `时间序列预测 电力数据 最佳实践 sklearn`,
      ]

      const allResults = await Promise.all(
        queries.map(q => this._webSearch.search(q, 3))
      )

      // 合并结果
      const merged = allResults.flat()
      const text = merged.map((r, i) =>
        `[${i + 1}] ${r.title}\n   ${r.snippet}\n   来源: ${r.url}`
      ).join("\n\n")

      if (text.length < 50) {
        // 搜索无结果时回退到 LLM 模拟
        this.emit({ message: `🌐 WebSearch 无结果，回退到 LLM 知识` })
        return this.fallbackResearch(expectType)
      }

      // 存入 RAG 供后续分析使用
      this._ragStore.add(text, { category: "web_research", expectType, queries: queries.join("; ") })
      this.emit({ message: `🌐 真实 WebSearch 完成: ${merged.length} 条结果, 已存入 RAG` })

      this._researchCache = text
      return text
    } catch (e: any) {
      console.warn(`[WebSearch] 失败: ${e.message}，回退到 LLM 知识`)
      return this.fallbackResearch(expectType)
    }
  }

  /** LLM 知识回退（当 WebSearch 不可达时） */
  private async fallbackResearch(expectType: number): Promise<string> {
    const typeName = expectType === 1 ? "区民用电" : "煤改电"
    const prompt = `你是一位电力行业数据分析专家。请提供以下信息（用中文，300字以内）：

1. 电力负荷预测的常用方法
2. ${typeName} 的负荷特性（季节性特征等）
3. 对月度电量数据（5个区县）的最佳实践

请给出简洁、实用的技术建议。`

    try {
      const result = await this._llm.chat(prompt, "你是一位专业的电力行业数据科学家。", 0.3)
      const trimmed = (result || "").trim()
      this._researchCache = trimmed
      return trimmed
    } catch {
      return ""
    }
  }

  /** 补丁代码语法检查（写临时文件 → python -m py_compile）
   * 返回 { ok: boolean, error: string }，error 包含具体错误信息 */
  private async checkPatchSyntax(code: string): Promise<{ ok: boolean; error: string }> {
    const { writeFileSync, unlinkSync, mkdtempSync } = require("fs")
    const { join } = require("path")
    const { tmpdir } = require("os")
    const { execSync } = require("child_process")
    const tmpDir = mkdtempSync(join(tmpdir(), "castflow-patch-"))
    const tmpFile = join(tmpDir, `patch_check_${Date.now()}.py`)

    // 同时保存一份出错代码到项目目录（供用户事后检查）
    const debugDir = join(process.cwd(), "generated_code")
    const debugFile = join(debugDir, `syntax_error_${Date.now()}.py`)

    writeFileSync(tmpFile, code, "utf-8")
    // 先写调试文件（命名前检查前缀）
    try { writeFileSync(debugFile, code, "utf-8") } catch {}

    try {
      execSync(`python -m py_compile "${tmpFile}"`, {
        timeout: 10000,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
        encoding: "utf-8",
        env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      })
      unlinkSync(tmpFile)
      try { unlinkSync(debugFile) } catch {} // 语法正确就删掉调试文件
      try { unlinkSync(tmpDir) } catch {}
      return { ok: true, error: "" }
    } catch (e: any) {
      // 提取关键错误信息
      const stderr = (e.stderr || e.message || "").toString()
      // 从 py_compile 输出中提取错误摘要（通常格式: "SyntaxError: ... (line X)"）
      const lines = stderr.split("\n").filter((l: string) => l.trim())
      const errorSummary = lines.slice(-3).join("\n").slice(0, 500)

      // 保留调试文件供用户检查
      this.emit({ message: `⚠️ 语法错误详情: ${errorSummary.replace(/\n/g, " | ")}` })
      this.emit({ message: `📄 出错代码已保存至: generated_code/syntax_error_${Date.now()}.py` })

      try { unlinkSync(tmpFile) } catch {}
      try { unlinkSync(tmpDir) } catch {}
      return { ok: false, error: errorSummary }
    }
  }

  /** 获取技能摘要（用于前端展示） */
  getSkills() {
    return this.skillStore.listAll()
  }

  /** 获取 Skill 库统计 */
  getSkillStats() {
    return this.skillStore.getStats()
  }
}
