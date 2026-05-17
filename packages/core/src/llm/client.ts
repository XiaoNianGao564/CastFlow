/**
 * 统一 LLM 客户端 - 支持 Vercel AI SDK (generateObject) + 传统 fetch
 *
 * 结构化输出用 generateObject<T>() 自动 Zod 校验，
 * 普通对话用 chat() 保留。
 */

import { readFileSync, existsSync } from "fs"
import { join } from "path"
import { generateObject } from "ai"
import { createOpenAI } from "@ai-sdk/openai"
import { type ZodType } from "zod"
import type { AnalysisResult } from "../types"

export interface LLMConfig {
  apiKey: string
  baseURL: string
  model: string
}

export class LLMClient {
  private config: LLMConfig

  constructor(config?: Partial<LLMConfig>) {
    // 自动读取 通义千问 API Key（与 power_forecast_platform 共用）
    const envKey = process.env.DASHSCOPE_API_KEY || process.env.LLM_API_KEY || ""
    let fileKey = ""
    const apiKeyFile = join(process.cwd(), ".api_key")
    if (!envKey && existsSync(apiKeyFile)) {
      try {
        fileKey = readFileSync(apiKeyFile, "utf-8").trim()
      } catch {}
    }
    // 也尝试从 power_forecast_platform 的 .api_key 读取
    const pfKeyFile = "C:\\Users\\17166\\Desktop\\power_forecast_platform\\.api_key"
    if (!envKey && !fileKey && existsSync(pfKeyFile)) {
      try {
        fileKey = readFileSync(pfKeyFile, "utf-8").trim()
      } catch {}
    }

    this.config = {
      apiKey: config?.apiKey || envKey || fileKey || "",
      baseURL: config?.baseURL || process.env.LLM_API_BASE || "https://dashscope.aliyuncs.com/compatible-mode/v1",
      model: config?.model || process.env.LLM_MODEL || "qwen-plus",
    }
  }

  get isAvailable(): boolean {
    return !!this.config.apiKey
  }

  get modelName(): string {
    return this.config.model
  }

  /** 表示是否初始化为通义千问（用于 generateObject fallback） */
  private _openai: ReturnType<typeof createOpenAI> | null = null
  private get openai() {
    if (!this._openai) {
      this._openai = createOpenAI({
        baseURL: this.config.baseURL,
        apiKey: this.config.apiKey,
      })
    }
    return this._openai
  }

  /**
   * 🆕 Vercel AI SDK generateObject — 带 Zod 模式的结构化输出
   * 自动验证 LLM 输出，失败自动重试 1 次
   */
  async generateObject<T>(schema: ZodType<T>, prompt: string, system?: string, temperature?: number): Promise<T> {
    const lastError: Error[] = []
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const { object } = await generateObject({
          model: this.openai(this.config.model),
          schema,
          system,
          prompt,
          temperature: temperature ?? 0.3,
        })
        return object
      } catch (e: any) {
        lastError.push(e)
        if (attempt === 0) {
          // 第一次失败重试，加提示
          continue
        }
      }
    }
    throw new Error(`generateObject 失败: ${lastError[lastError.length - 1]?.message || "unknown"}`)
  }

  /**
   * 普通对话
   */
  async chat(prompt: string, system?: string, temperature = 0.3, timeoutMs = 120000): Promise<string> {
    return this.chatDirect(prompt, system, temperature, timeoutMs)
  }

  /**
   * 直接通过 fetch 调用（绕过 Vercel AI SDK，兼容性更好）
   */
  async chatDirect(prompt: string, system?: string, temperature = 0.3, timeoutMs = 120000): Promise<string> {
    const messages: any[] = []
    if (system) messages.push({ role: "system", content: system })
    messages.push({ role: "user", content: prompt })

    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)

    try {
      const response = await fetch(`${this.config.baseURL}/chat/completions`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${this.config.apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model: this.config.model,
          messages,
          temperature,
          max_tokens: 8192,
        }),
        signal: controller.signal,
      })

      if (!response.ok) {
        const err = await response.text().catch(() => "unknown error")
        throw new Error(`LLM API ${response.status}: ${err.slice(0, 200)}`)
      }

      const data: any = await response.json()
      return data.choices?.[0]?.message?.content || ""
    } finally {
      clearTimeout(timer)
    }
  }

  /**
   * JSON 模式对话
   */
  async chatJSON(prompt: string, system?: string, temperature = 0.3): Promise<any> {
    const text = await this.chat(prompt, system, temperature)
    return this.parseJSON(text)
  }

  private parseJSON(text: string): any {
    // 尝试直接解析
    try {
      return JSON.parse(text)
    } catch {}

    // 尝试从 ```json ``` 代码块提取
    const match = text.match(/```(?:json)?\s*\n?([\s\S]*?)\n?```/)
    if (match) {
      try {
        return JSON.parse(match[1].trim())
      } catch {}
    }

    // 尝试从 { 到 } 提取
    const start = text.indexOf("{")
    const end = text.lastIndexOf("}")
    if (start >= 0 && end > start) {
      try {
        return JSON.parse(text.slice(start, end + 1))
      } catch {}
    }

    throw new Error(`无法解析 LLM 回复为 JSON: ${text.slice(0, 200)}`)
  }
}
