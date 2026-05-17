/**
 * 设置页面 - 配置数据库连接和 LLM 参数
 * 支持编辑常用参数并保存
 */

import { Component, createSignal, createResource, onMount, Show, For } from "solid-js"

const Settings: Component = () => {
  const [config, setConfig] = createSignal<any>(null)
  const [model, setModel] = createSignal("qwen-plus")
  const [maxIter, setMaxIter] = createSignal(10)
  const [accuracyTarget, setAccuracyTarget] = createSignal(98)
  const [saving, setSaving] = createSignal(false)
  const [saveMsg, setSaveMsg] = createSignal<{ type: "success" | "error"; text: string } | null>(null)
  const [testingDb, setTestingDb] = createSignal(false)
  const [dbTestResult, setDbTestResult] = createSignal<string | null>(null)

  const [skills] = createResource(async () => {
    const res = await fetch("/api/skills")
    return await res.json()
  })

  const LLM_MODELS = [
    { id: "qwen-plus", name: "通义千问 Plus" },
    { id: "qwen-max", name: "通义千问 Max" },
    { id: "qwen-turbo", name: "通义千问 Turbo" },
    { id: "deepseek-chat", name: "DeepSeek Chat" },
  ]

  onMount(async () => {
    try {
      const res = await fetch("/api/config")
      const data = await res.json()
      setConfig(data)
      setModel(data.llm?.model || "qwen-plus")
      setMaxIter(data.optimizer?.maxIterations || 10)
      setAccuracyTarget(data.optimizer?.accuracyTarget || 98)
    } catch {}
  })

  const saveConfig = async () => {
    setSaving(true)
    setSaveMsg(null)
    try {
      const res = await fetch("/api/config/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: model(), maxIterations: maxIter(), accuracyTarget: accuracyTarget() }),
      })
      const data = await res.json()
      if (data.status === "ok") {
        setSaveMsg({ type: "success", text: "✅ 配置已保存" })
      } else {
        setSaveMsg({ type: "error", text: "❌ 保存失败" })
      }
    } catch (e) {
      setSaveMsg({ type: "error", text: "❌ 保存失败: 后端未响应" })
    } finally {
      setSaving(false)
      setTimeout(() => setSaveMsg(null), 3000)
    }
  }

  const testDbConnection = async () => {
    setTestingDb(true)
    setDbTestResult(null)
    try {
      const res = await fetch("/api/health")
      const data = await res.json()
      if (data.ok) {
        setDbTestResult("✅ 服务器连接正常")
      }
    } catch {
      setDbTestResult("❌ 连接失败")
    } finally {
      setTestingDb(false)
      setTimeout(() => setDbTestResult(null), 5000)
    }
  }

  return (
    <div class="max-w-3xl mx-auto space-y-6">
      <div class="flex items-center justify-between">
        <h2 class="text-2xl font-bold">系统设置</h2>
        <button onClick={saveConfig} disabled={saving || !config()}
                class={`px-4 py-2 rounded-lg text-sm font-medium text-white transition-all ${
                  saving ? "bg-blue-400 cursor-not-allowed" : "bg-blue-600 hover:bg-blue-700"
                }`}>
          {saving ? "保存中..." : "保存配置"}
        </button>
      </div>

      {/* 保存提示 */}
      <Show when={saveMsg()}>
        <div class={`px-4 py-2 rounded-lg text-sm font-medium ${
          saveMsg()?.type === "success" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
        }`}>
          {saveMsg()?.text}
        </div>
      </Show>

      {/* 数据库配置 */}
      <div class="bg-white rounded-xl border border-gray-200 p-6">
        <h3 class="font-semibold mb-4">数据库连接</h3>
        <div class="grid grid-cols-2 gap-4">
          <div class="bg-gray-50 rounded-lg p-3">
            <div class="text-xs text-gray-400 mb-1">主机</div>
            <div class="font-mono text-sm">{config()?.db?.host || "127.0.0.1"}</div>
          </div>
          <div class="bg-gray-50 rounded-lg p-3">
            <div class="text-xs text-gray-400 mb-1">数据库</div>
            <div class="font-mono text-sm">{config()?.db?.database || "sxfhyc11"}</div>
          </div>
        </div>
        <div class="mt-3">
          <button onClick={testDbConnection} disabled={testingDb}
                  class="text-sm text-blue-600 hover:text-blue-800 underline">
            {testingDb ? "测试中..." : "测试数据库连接"}
          </button>
          <Show when={dbTestResult()}>
            <span class="ml-3 text-sm">{dbTestResult()}</span>
          </Show>
        </div>
      </div>

      {/* LLM 配置 - 可编辑 */}
      <div class="bg-white rounded-xl border border-gray-200 p-6">
        <h3 class="font-semibold mb-4">大模型配置</h3>
        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-gray-600 mb-2">选择模型</label>
            <select value={model()} onChange={(e) => setModel(e.currentTarget.value)}
                    class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm">
              <For each={LLM_MODELS}>
                {(m) => <option value={m.id}>{m.name} ({m.id})</option>}
              </For>
            </select>
          </div>
          <div class="text-xs text-gray-400">
            API Key 通过环境变量 <code class="bg-gray-100 px-1 rounded">DASHSCOPE_API_KEY</code>
            {' '}或 <code class="bg-gray-100 px-1 rounded">.api_key</code> 文件设置
          </div>
        </div>
      </div>

      {/* 优化参数 - 可编辑 */}
      <div class="bg-white rounded-xl border border-gray-200 p-6">
        <h3 class="font-semibold mb-4">优化参数</h3>
        <div class="grid grid-cols-2 gap-6">
          <div>
            <label class="block text-sm font-medium text-gray-600 mb-2">最大迭代次数</label>
            <input type="number" value={maxIter()} min="1" max="50"
                   onChange={(e) => setMaxIter(Math.max(1, Math.min(50, Number(e.currentTarget.value))))}
                   class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" />
            <div class="text-xs text-gray-400 mt-1">范围: 1-50</div>
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-600 mb-2">目标准确率 (%)</label>
            <input type="number" value={accuracyTarget()} min="50" max="100"
                   onChange={(e) => setAccuracyTarget(Math.max(50, Math.min(100, Number(e.currentTarget.value))))}
                   class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" />
            <div class="text-xs text-gray-400 mt-1">范围: 50-100</div>
          </div>
        </div>
      </div>

      {/* 🆕 技能沉淀 - 结构化经验库 */}
      <div class="bg-white rounded-xl border border-gray-200 p-6">
        <h3 class="font-semibold mb-2">技能沉淀 <span class="text-xs text-gray-400 font-normal">（结构化经验库，跨项目复用）</span></h3>
        <div class="text-xs text-gray-400 mb-4">
          每次有效迭代自动沉淀为 Skill，包含数据特征画像。下次遇到相似任务自动匹配推荐，避免重复踩坑。
        </div>
        <Show when={skills() && skills().length > 0} fallback={
          <div class="text-center py-8 text-gray-400 text-sm">暂无技能数据，启动闭环后自动积累</div>
        }>
          <div class="space-y-3">
            <For each={skills()}>
              {(skill: any) => (
                <div class="bg-gray-50 rounded-lg p-3">
                  <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2">
                      <span class="font-mono text-sm font-medium">{skill.modelLabel || skill.modelId}</span>
                      <span class="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full">使用 {skill.usageCount} 次</span>
                    </div>
                    <div class="flex gap-3 text-xs">
                      <span class="text-green-600">✅ {skill.effectiveStrategies?.length || 0} 有效</span>
                      <span class="text-red-400">❌ {skill.failedStrategies?.length || 0} 无效</span>
                      <span class="text-blue-600 font-bold">最佳 {skill.bestAccuracy}%</span>
                    </div>
                  </div>
                  {/* 数据特征画像 */}
                  <Show when={skill.dataProfile}>
                    <div class="flex gap-3 mt-1.5 text-xs text-gray-400">
                      <span>📊 季节 {(skill.dataProfile.seasonalityStrength * 100).toFixed(0)}%</span>
                      <span>📈 趋势 {skill.dataProfile.trendDirection}</span>
                      <span>🌊 波动 {(skill.dataProfile.volatility * 100).toFixed(0)}%</span>
                      <span>🏘 {skill.dataProfile.orgCount} 区县</span>
                    </div>
                  </Show>
                  {/* 有效策略 */}
                  <Show when={skill.effectiveStrategies && skill.effectiveStrategies.length > 0}>
                    <div class="mt-2 space-y-1">
                      <For each={skill.effectiveStrategies}>
                        {(s: any) => (
                          <div class="text-xs text-green-700 font-mono pl-2 border-l-2 border-green-400">
                            {s.description}: {s.accuracyBefore}% → {s.accuracyAfter}%
                          </div>
                        )}
                      </For>
                    </div>
                  </Show>
                  {/* 失败策略 */}
                  <Show when={skill.failedStrategies && skill.failedStrategies.length > 0}>
                    <div class="mt-1 space-y-0.5">
                      <For each={skill.failedStrategies}>
                        {(s: any) => (
                          <div class="text-xs text-red-400 font-mono pl-2 border-l-2 border-red-200">
                            {s.description}: {s.accuracyBefore}% → {s.accuracyAfter}%
                          </div>
                        )}
                      </For>
                    </div>
                  </Show>
                </div>
              )}
            </For>
          </div>
        </Show>
      </div>

      {/* 关于 */}
      <div class="bg-white rounded-xl border border-gray-200 p-6 text-center text-sm text-gray-400">
        <p>CastFlow v0.1 — 基于 CastClaw 多智能体架构</p>
        <p class="mt-1">自迭代预测优化闭环 | 数据源: MySQL sxfhyc11</p>
      </div>
    </div>
  )
}

export default Settings
