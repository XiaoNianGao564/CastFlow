/**
 * ReActPlanner - 多智能体 ReAct 调度器
 *
 * 对应 CastClaw 的 Planner Agent（LangChain ReAct），
 * 通过 Thought → Action → Observation 循环驱动 LLM 动态决策。
 *
 * 每个工具封装一条 Agent 能力（load_data, execute_code, analyze 等），
 * LLM 根据当前管道状态自主选择下一步要调用的工具。
 */

import { LLMClient } from "../llm/client"
import type { DataPoint, EvaluationMetrics } from "../types"

// ================================================================
// 类型定义
// ================================================================

export interface PipelineState {
  year: number
  expectType: number
  status: string
  code: string
  predictions: DataPoint[]
  actuals: DataPoint[]
  accuracy: number
  metrics: EvaluationMetrics | null
  bestCode: string
  bestAccuracy: number
  bestPredictions: DataPoint[]
  bestMetrics: EvaluationMetrics | null
  totalIterations: number
  currentModel: string
  currentModelIndex: number
  totalModels: number
  roundsSinceBest: number
  consecutiveEmptyPatches: number
  elapsed: number
  analysisResult: any
  patchResult: any
  message: string
  researchContext: string
}

export interface ReActTool {
  name: string
  description: string
  parameters: {
    [key: string]: {
      type: string
      description: string
      required?: boolean
    }
  }
  handler: (state: PipelineState, input?: Record<string, any>) => Promise<string>
}

export type ReActStep = {
  thought: string
  action: string
  actionInput: Record<string, any>
  observation: string
}

export interface ReActResult {
  success: boolean
  state: PipelineState
  steps: ReActStep[]
  finalAnswer: string
}

// ================================================================
// ReActPlanner
// ================================================================

export class ReActPlanner {
  private llm: LLMClient
  private tools: Map<string, ReActTool>
  private maxSteps = 30
  private maxConsecutiveTool = 3
  private onStatus?: (message: string) => void

  constructor(llm: LLMClient, tools: ReActTool[], onStatus?: (message: string) => void) {
    this.llm = llm
    this.tools = new Map(tools.map(t => [t.name, t]))
    this.onStatus = onStatus
  }

  /** 执行 ReAct 循环 */
  async planAndExecute(initialState: PipelineState): Promise<ReActResult> {
    const state = { ...initialState }
    const steps: ReActStep[] = []
    let consecutiveTool = ""
    let consecutiveCount = 0

    const systemPrompt = this.buildSystemPrompt()
    this.log("开始 ReAct 多智能体调度")

    for (let step = 0; step < this.maxSteps; step++) {
      // 构建当前上下文的 prompt
      const contextPrompt = this.buildContextPrompt(state, step, steps)

      // 调用 LLM
      this.log(`[ReAct Step ${step + 1}/${this.maxSteps}] 调用 LLM 决策...`)
      let llmOutput = ""
      try {
        llmOutput = await this.llm.chat(contextPrompt, systemPrompt, 0.4)
      } catch (e: any) {
        this.log(`⚠️ ReAct LLM 调用失败: ${e.message}，终止`)
        return { success: false, state, steps, finalAnswer: `LLM 错误: ${e.message}` }
      }

      // 解析 LLM 输出
      const parsed = this.parseReActOutput(llmOutput)

      // 检查是否为 Final Answer（终止信号）
      if (parsed.type === "final") {
        this.log(`🏁 ReAct 终止: ${parsed.finalAnswer}`)
        return { success: true, state, steps, finalAnswer: parsed.finalAnswer }
      }

      if (parsed.type === "invalid") {
        this.log(`⚠️ LLM 输出格式异常，重试...\n${llmOutput.slice(0, 200)}`)
        steps.push({ thought: "", action: "parse_error", actionInput: {}, observation: "格式错误，请输出 Thought:/Action:/Action Input: 格式" })
        continue
      }

      // 安全限制：同工具连续调用检查
      if (parsed.action === consecutiveTool) {
        consecutiveCount++
        if (consecutiveCount >= this.maxConsecutiveTool) {
          this.log(`⚠️ 连续 ${consecutiveCount} 次调用 "${parsed.action}"，强制停止`)
          return { success: true, state, steps, finalAnswer: `${parsed.action} 连续调用 ${consecutiveCount} 次无进展，自动终止` }
        }
      } else {
        consecutiveTool = parsed.action
        consecutiveCount = 1
      }

      // 检查工具是否存在
      const tool = this.tools.get(parsed.action)
      if (!tool) {
        this.log(`⚠️ 未知工具 "${parsed.action}"，可用工具: ${[...this.tools.keys()].join(", ")}`)
        steps.push({ thought: parsed.thought, action: parsed.action, actionInput: parsed.actionInput, observation: `未知工具 "${parsed.action}"` })
        continue
      }

      // 执行工具
      try {
        state.message = `[ReAct] ${tool.description}`
        this.log(state.message)
        const observation = await tool.handler(state, parsed.actionInput)
        steps.push({ thought: parsed.thought, action: parsed.action, actionInput: parsed.actionInput, observation })
        this.log(`   → Observation: ${observation.slice(0, 200)}`)
      } catch (e: any) {
        const errMsg = `工具 "${parsed.action}" 执行失败: ${e.message}`
        this.log(`   ❌ ${errMsg}`)
        steps.push({ thought: parsed.thought, action: parsed.action, actionInput: parsed.actionInput, observation: errMsg })
      }
    }

    // 超出最大步数
    this.log(`⚠️ 达到最大 ReAct 步数 (${this.maxSteps})，强制终止`)
    return { success: true, state, steps, finalAnswer: `达到最大 ${this.maxSteps} 步，自动终止` }
  }

  // ================================================================
  // Prompt 构建
  // ================================================================

  /** 系统提示词（工具定义 + 格式约束） */
  private buildSystemPrompt(): string {
    const toolDescs = [...this.tools.values()].map(t => {
      const params = Object.entries(t.parameters).map(([k, v]) =>
        `    ${k} (${v.type})${v.required ? " [必填]" : ""}: ${v.description}`
      ).join("\n")
      return `## ${t.name}\n${t.description}\n参数:\n${params || "    无参数"}`
    }).join("\n\n")

    return `你是一位专业的时间序列预测管线的控制智能体 (Planner)。

## 可用工具
${toolDescs}

## 工作规则
1. 每次只调用一个工具
2. 根据当前状态和上一步的 Observation 决定下一步
3. 只有在以下情况才能输出 Final Answer:
   - 目标准确率已达成 (>= 98%)
   - 所有模型都已尝试过
   - 连续多轮无提升，已无法继续优化
   - 出现无法恢复的错误

## 输出格式（严格遵循）
每轮输出必须严格遵循以下格式之一：

### 调用工具时：
Thought: (分析当前状态，解释为什么调用这个工具)
Action: (工具名称)
Action Input: (JSON 格式的参数，如 {"key": "value"})

### 完成任务时：
Thought: (总结完成情况)
Final Answer: (最终的预测结果总结，包含最佳准确率)`
  }

  /** 上下文 prompt（当前状态 + 历史步骤） */
  private buildContextPrompt(state: PipelineState, step: number, steps: ReActStep[]): string {
    // 最近 3 步历史
    const recentHistory = steps.slice(-3).map(s =>
      `Thought: ${s.thought}\nAction: ${s.action}\nAction Input: ${JSON.stringify(s.actionInput)}\nObservation: ${s.observation}`
    ).join("\n\n")

    return `## 当前状态 (第 ${step + 1} 轮决策)

- 状态: ${state.status}
- 年份: ${state.year}, 用电类型: ${state.expectType === 1 ? "区民" : "煤改电"}
- 当前模型: ${state.currentModel} (${state.currentModelIndex + 1}/${state.totalModels})
- 最佳准确率: ${state.bestAccuracy.toFixed(2)}%
- 当前代码: ${state.code ? (state.code.length > 100 ? "存在" : "太短") : "空"}
- 预测数据: ${state.predictions.length} 条
- 真实数据: ${state.actuals.length} 条
- 无提升轮数: ${state.roundsSinceBest}
- 空补丁次数: ${state.consecutiveEmptyPatches}
- 已运行: ${state.elapsed} 秒
- 最新消息: ${state.message}

## 最近操作历史
${recentHistory || "无"}

## 决策要求
请基于当前状态和可用工具，输出下一步操作。输出严格遵循格式：Thought: ...\nAction: ...\nAction Input: ...`
  }

  // ================================================================
  // 解析 LLM 输出
  // ================================================================

  private parseReActOutput(text: string): {
    type: "action" | "final" | "invalid"
    thought?: string
    action?: string
    actionInput?: Record<string, any>
    finalAnswer?: string
  } {
    if (!text || text.trim().length < 10) {
      return { type: "invalid" }
    }

    // 检查 Final Answer
    const finalMatch = text.match(/Final Answer:\s*(.+?)(?:\n|$)/s)
    if (finalMatch) {
      const thought = this.extractThought(text)
      return { type: "final", thought, finalAnswer: finalMatch[1].trim() }
    }

    // 检查 Action
    const actionMatch = text.match(/Action:\s*(.+?)(?:\n|$)/)
    const inputMatch = text.match(/Action Input:\s*(\{.+?\}|\[.+?\]|`[^`]+`|"[^"]*")(?:\n|$)/s)

    if (actionMatch) {
      const thought = this.extractThought(text)
      const action = actionMatch[1].trim()
      let actionInput: Record<string, any> = {}

      if (inputMatch) {
        try {
          let raw = inputMatch[1].trim()
          // 去除可能的引号包裹
          if (raw.startsWith("`") && raw.endsWith("`")) raw = raw.slice(1, -1)
          if (raw.startsWith('"') && raw.endsWith('"')) raw = raw.slice(1, -1)
          actionInput = JSON.parse(raw)
        } catch {
          actionInput = {}
        }
      }

      return { type: "action", thought, action, actionInput }
    }

    // 只有 Thought 没有 Action → 可能是还在思考，重试
    return { type: "invalid" }
  }

  private extractThought(text: string): string {
    const match = text.match(/Thought:\s*(.+?)(?:\n|$)/)
    return match ? match[1].trim().slice(0, 300) : ""
  }

  private log(msg: string) {
    console.log(`[ReAct] ${msg}`)
    this.onStatus?.(msg)
  }
}
