/**
 * 迭代历史页面 - 查看所有迭代记录的详细信息
 *
 * 功能：
 * - 版本列表 + 详情
 * - 版本对比模式（左右分栏）
 * - 各区县准确率点击钻取，显示预测 vs 实际曲线
 * - 完整 Diff 视图
 */

import { Component, createResource, For, Show, createSignal } from "solid-js"

interface IterationRecord {
  version: number
  accuracy?: number
  improved?: boolean
  predictions?: Array<{ org: string; month: string; value: number }>
  patch?: { patch_summary?: string; diff?: string; changed_sections?: Array<{ section: string; old: string; new: string; reason: string }> }
  evaluation?: {
    mape?: number; mae?: number; rmse?: number; sample_count?: number
    eval_error?: boolean
    error?: string
    per_org?: Record<string, { accuracy: number; mape: number }>
    pairs?: Array<{ org: string; month: string; predicted: number; actual: number; error: number; error_pct: number }>
  }
  source?: string
  timestamp?: string
}

// 区县预测对比曲线
const OrgChart = (props: { org: string; pairs: Array<{ month: string; predicted: number; actual: number }>; onClose: () => void }) => {
  const sorted = () => [...props.pairs].sort((a, b) => a.month.localeCompare(b.month))
  const points = sorted()
  if (!points.length) return null

  const width = 500, height = 200
  const values = points.flatMap(p => [p.predicted, p.actual])
  const maxV = Math.max(...values) * 1.1
  const minV = Math.min(...values) * 0.9
  const range = maxV - minV || 1
  const xStep = width / Math.max(points.length - 1, 1)

  const predPath = points.map((p, i) => {
    const x = i * xStep
    const y = height - ((p.predicted - minV) / range) * height
    return `${i === 0 ? "M" : "L"}${x},${y}`
  }).join(" ")

  const actualPath = points.map((p, i) => {
    const x = i * xStep
    const y = height - ((p.actual - minV) / range) * height
    return `${i === 0 ? "M" : "L"}${x},${y}`
  }).join(" ")

  return (
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={props.onClose}>
      <div class="bg-white rounded-xl shadow-xl p-6 max-w-2xl mx-4" onClick={(e) => e.stopPropagation()}>
        <div class="flex justify-between items-center mb-4">
          <h3 class="text-lg font-semibold">{props.org} — 预测 vs 实际</h3>
          <button onClick={props.onClose} class="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>
        <svg viewBox={`0 0 ${width} ${height}`} class="w-full">
          <defs>
            <linearGradient id="predGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#2563eb" stop-opacity="0.15" />
              <stop offset="100%" stop-color="#2563eb" stop-opacity="0.02" />
            </linearGradient>
            <linearGradient id="actGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#16a34a" stop-opacity="0.15" />
              <stop offset="100%" stop-color="#16a34a" stop-opacity="0.02" />
            </linearGradient>
          </defs>
          {/* 预测线 */}
          <path d={predPath} fill="none" stroke="#2563eb" stroke-width="2" stroke-dasharray="5,3" />
          {/* 实际线 */}
          <path d={actualPath} fill="none" stroke="#16a34a" stroke-width="2" />
          {/* 数据点 */}
          <For each={points}>
            {(p, i) => (
              <g>
                <circle cx={i() * xStep} cy={height - ((p.predicted - minV) / range) * height} r="2" fill="#2563eb" />
                <circle cx={i() * xStep} cy={height - ((p.actual - minV) / range) * height} r="2" fill="#16a34a" />
                <title>{`${p.month}\n预测: ${p.predicted.toFixed(2)}\n实际: ${p.actual.toFixed(2)}`}</title>
              </g>
            )}
          </For>
        </svg>
        <div class="flex justify-center gap-6 mt-2 text-xs">
          <span class="flex items-center gap-1"><span class="w-4 h-0.5 bg-blue-600 inline-block" /> 预测值</span>
          <span class="flex items-center gap-1"><span class="w-4 h-0.5 bg-green-600 inline-block" /> 实际值</span>
        </div>
        <div class="flex justify-between mt-2 text-xs text-gray-400">
          <span>{points[0]?.month}</span>
          <span>{points[points.length - 1]?.month}</span>
        </div>
      </div>
    </div>
  )
}

// 版本对比详情
const VersionDiff = (props: { a: IterationRecord; b: IterationRecord | null }) => (
  <div class="grid grid-cols-2 gap-4">
    {/* 版本 A */}
    <div class="space-y-3">
      <div class="text-sm font-semibold text-blue-700">v{props.a.version}</div>
      <div class="grid grid-cols-2 gap-2 text-center">
        <div class="bg-white rounded border p-2">
          <div class="text-xs text-gray-400">准确率</div>
          <div class="font-bold text-green-600">{props.a.accuracy?.toFixed(2) ?? "—"}%</div>
        </div>
        <div class="bg-white rounded border p-2">
          <div class="text-xs text-gray-400">MAPE</div>
          <div class="font-bold">{props.a.evaluation?.mape?.toFixed(2) ?? "—"}%</div>
        </div>
      </div>
      <Show when={props.a.evaluation?.per_org}>
        <table class="w-full text-xs">
          <thead><tr class="border-b text-gray-400"><th class="text-left py-1">区县</th><th class="text-right py-1">准确率</th></tr></thead>
          <tbody>
            <For each={Object.entries(props.a.evaluation!.per_org!)}>
              {([org, m]) => (
                <tr class="border-b border-gray-50">
                  <td class="py-1">{org}</td><td class="text-right font-mono">{m.accuracy.toFixed(2)}%</td>
                </tr>
              )}
            </For>
          </tbody>
        </table>
      </Show>
    </div>

    {/* 版本 B */}
    <div class="space-y-3">
      <Show when={props.b} fallback={<div class="text-sm text-gray-400 mt-6 text-center">选择另一个版本对比</div>}>
        {(b) => (
          <>
            <div class="text-sm font-semibold text-green-700">v{b().version}</div>
            <div class="grid grid-cols-2 gap-2 text-center">
              <div class="bg-white rounded border p-2">
                <div class="text-xs text-gray-400">准确率</div>
                <div class="font-bold text-green-600">{b().accuracy?.toFixed(2) ?? "—"}%</div>
              </div>
              <div class="bg-white rounded border p-2">
                <div class="text-xs text-gray-400">MAPE</div>
                <div class="font-bold">{b().evaluation?.mape?.toFixed(2) ?? "—"}%</div>
              </div>
            </div>
            <Show when={b().evaluation?.per_org}>
              <table class="w-full text-xs">
                <thead><tr class="border-b text-gray-400"><th class="text-left py-1">区县</th><th class="text-right py-1">准确率</th></tr></thead>
                <tbody>
                  <For each={Object.entries(b().evaluation!.per_org!)}>
                    {([org, m]) => (
                      <tr class="border-b border-gray-50">
                        <td class="py-1">{org}</td><td class="text-right font-mono">{m.accuracy.toFixed(2)}%</td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
            </Show>
          </>
        )}
      </Show>
    </div>
  </div>
)

const History: Component = () => {
  const [records] = createResource(async () => {
    const res = await fetch("/api/history")
    return (await res.json()) as IterationRecord[]
  })

  const [selectedVersion, setSelectedVersion] = createSignal<number | null>(null)
  const [compareMode, setCompareMode] = createSignal(false)
  const [compareVersions, setCompareVersions] = createSignal<number[]>([])
  const [drillOrg, setDrillOrg] = createSignal<{ org: string; pairs: Array<{ month: string; predicted: number; actual: number }> } | null>(null)

  const selectedRecord = () => records()?.find(r => r.version === selectedVersion()) || null

  const handleVersionClick = (version: number) => {
    if (!compareMode()) {
      setSelectedVersion(version)
      return
    }
    setCompareVersions((prev) => {
      if (prev.includes(version)) return prev.filter(v => v !== version)
      if (prev.length >= 2) return [prev[1], version]
      return [...prev, version]
    })
  }

  const compA = () => records()?.find(r => r.version === compareVersions()[0]) || null
  const compB = () => records()?.find(r => r.version === compareVersions()[1]) || null

  // 导出 HTML 报告
  const exportReport = () => {
    const all = records()
    if (!all?.length) return

    const rows = all.map(r => `
      <tr>
        <td>v${r.version}</td>
        <td>${r.improved ? "↑" : r.version === 0 ? "初始" : "→"}</td>
        <td>${r.accuracy?.toFixed(2) ?? "—"}%</td>
        <td>${r.evaluation?.mape?.toFixed(2) ?? "—"}%</td>
        <td>${r.patch?.patch_summary || r.source || "—"}</td>
      </tr>
    `).join("")

    const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>CastFlow 报告</title>
<style>
  body { font-family: system-ui; max-width: 900px; margin: 40px auto; padding: 0 20px; color: #333; }
  h1 { color: #2563eb; }
  table { width: 100%; border-collapse: collapse; margin: 20px 0; }
  th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }
  th { background: #f3f4f6; font-weight: 600; }
  .footer { margin-top: 40px; font-size: 12px; color: #9ca3af; text-align: center; }
  .best { font-size: 24px; font-weight: bold; color: #16a34a; text-align: center; margin: 20px 0; }
</style></head>
<body>
  <h1>CastFlow 自迭代预测报告</h1>
  <p>生成时间: ${new Date().toLocaleString()}</p>
  <div class="best">最佳准确率: ${Math.max(...all.map(r => r.accuracy ?? 0)).toFixed(2)}%</div>
  <h2>迭代历史</h2>
  <table>
    <thead><tr><th>版本</th><th>状态</th><th>准确率</th><th>MAPE</th><th>修改摘要</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <div class="footer">
    <p>CastFlow v0.1 — 基于多智能体架构的自迭代预测平台</p>
    <p>数据源: MySQL sxfhyc11 | 共计 ${all.length} 次迭代</p>
  </div>
</body></html>`

    const blob = new Blob([html], { type: "text/html;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    window.open(url, "_blank")
  }

  return (
    <div class="space-y-6">
      <div class="flex items-center justify-between">
        <h2 class="text-2xl font-bold">迭代历史</h2>
        <div class="flex items-center gap-3">
          <button onClick={exportReport} disabled={!records()?.length}
                  class="px-3 py-1.5 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">
            导出报告
          </button>
          <label class="flex items-center gap-2 text-sm cursor-pointer select-none">
          <span class="text-gray-500">对比模式</span>
          <div class={`w-10 h-5 rounded-full transition-colors relative ${compareMode() ? "bg-blue-600" : "bg-gray-300"}`}
               onClick={() => { setCompareMode(!compareMode()); setCompareVersions([]) }}>
            <div class={`absolute top-0.5 w-4 h-4 bg-white rounded-full transition-all shadow ${compareMode() ? "left-5" : "left-0.5"}`} />
          </div>
        </label>
      </div>
      </div>

      {/* 迭代列表 */}
      <div class="grid grid-cols-4 gap-6">
        <div class="col-span-1 space-y-2">
          <For each={records()?.slice().reverse()}>
            {(record) => {
              // 找到上一个版本用于新旧对比
              const prev = records()?.find(r => r.version === record.version - 1)
              const prevAccuracy = prev?.accuracy ?? prev?.evaluation?.accuracy
              const delta = (record.accuracy != null && prevAccuracy != null)
                ? record.accuracy - prevAccuracy
                : null
              return (
              <button onClick={() => handleVersionClick(record.version)}
                      class={`w-full text-left px-4 py-3 rounded-lg border transition-colors ${
                        compareMode()
                          ? compareVersions().includes(record.version)
                            ? "border-blue-500 bg-blue-50 ring-2 ring-blue-300"
                            : "border-gray-200 bg-white hover:bg-gray-50"
                          : selectedVersion() === record.version
                            ? "border-blue-500 bg-blue-50"
                            : "border-gray-200 bg-white hover:bg-gray-50"
                      }`}>
                <div class="flex justify-between items-center">
                  <span class="font-semibold">v{record.version}</span>
                  <span class={`text-xs px-2 py-0.5 rounded-full ${
                    record.evaluation?.eval_error ? "bg-red-100 text-red-700" :
                    record.improved ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"
                  }`}>
                    {record.evaluation?.eval_error ? "⚡ 评估失败" :
                     record.version === 0 ? "初始" : record.improved ? "↑ 提升" : "→"}
                  </span>
                </div>
                <div class="text-sm mt-1">
                  <Show when={!record.evaluation?.eval_error} fallback={
                    <span class="text-red-500 font-mono text-xs">评估异常</span>
                  }>
                    <span class="font-mono font-bold">{record.accuracy?.toFixed(2) ?? "—"}%</span>
                    {/* 🆕 新旧对比 delta */}
                    <Show when={delta != null && record.version > 0}>
                      <span class={`ml-1 text-xs font-mono ${
                        delta! > 0.5 ? "text-green-600" :
                        delta! < -0.5 ? "text-red-500" :
                        "text-gray-400"
                      }`}>
                        {delta! > 0 ? "↑" : delta! < 0 ? "↓" : "→"}
                        {delta!.toFixed(2)}
                      </span>
                    </Show>
                    <span class="text-gray-400 ml-2 text-xs">
                      {record.evaluation?.mape ? `MAPE ${record.evaluation.mape}%` : ""}
                    </span>
                  </Show>
                </div>
                <div class="text-xs text-gray-400 mt-1 truncate">
                  {record.patch?.patch_summary || record.source || ""}
                </div>
              </button>
            )}}
          </For>
        </div>

        {/* 详细内容 */}
        <div class={`${compareMode() ? "col-span-3" : "col-span-3"}`}>
          <Show when={compareMode() && compareVersions().length > 0} fallback={
            <Show when={selectedRecord()} fallback={
              <div class="bg-white rounded-xl border border-gray-200 p-12 text-center text-gray-400">
                {compareMode() ? "在对比模式下点击左侧版本进行比较" : "选择一个版本查看详情"}
              </div>
            }>
              {(record) => (
                <div class="space-y-4">
                  {/* 评估异常提示 */}
                  <Show when={record().evaluation?.eval_error}>
                    <div class="bg-red-50 border border-red-200 rounded-lg p-4 text-sm">
                      <div class="font-semibold text-red-700 mb-1">⚡ 评估失败</div>
                      <div class="text-red-600 text-xs font-mono break-all">{record().evaluation!.error || "未知错误"}</div>
                      <div class="text-gray-500 mt-1">该版本的预测数据格式异常，无法与真实数据匹配。</div>
                    </div>
                  </Show>
                  {/* 指标卡片 */}
                  <div class="grid grid-cols-4 gap-3">
                    {[
                      { label: "准确率", value: `${record().accuracy?.toFixed(2) ?? "—"}%`, color: "text-green-600" },
                      { label: "MAPE", value: `${record().evaluation?.mape ?? "—"}%`, color: "text-blue-600" },
                      { label: "MAE", value: record().evaluation?.mae?.toFixed(2) ?? "—", color: "text-orange-600" },
                      { label: "RMSE", value: record().evaluation?.rmse?.toFixed(2) ?? "—", color: "text-purple-600" },
                    ].map(m => (
                      <div class="bg-white rounded-lg border border-gray-200 p-4 text-center">
                        <div class="text-sm text-gray-500">{m.label}</div>
                        <div class={`text-xl font-bold font-mono ${m.color}`}>{m.value}</div>
                      </div>
                    ))}
                  </div>

                  {/* 🆕 与前版本的准确率对比 */}
                  <Show when={record().version > 0}>
                    {(() => {
                      const prev = records()?.find(r => r.version === record().version - 1)
                      const prevAcc = prev?.accuracy ?? prev?.evaluation?.accuracy
                      if (prevAcc == null) return null
                      const diff = (record().accuracy ?? 0) - prevAcc
                      const absDiff = Math.abs(diff)
                      return (
                        <div class={`text-sm px-4 py-2 rounded-lg border ${
                          diff > 0.5 ? "bg-green-50 border-green-200 text-green-700" :
                          diff < -0.5 ? "bg-red-50 border-red-200 text-red-600" :
                          "bg-gray-50 border-gray-200 text-gray-500"
                        }`}>
                          相比 v{record().version - 1}（{prevAcc.toFixed(2)}%）
                          <span class="font-bold ml-1">
                            {diff > 0.5 ? "↑ +" : diff < -0.5 ? "↓ " : "→ "}
                            {absDiff.toFixed(2)}%
                          </span>
                          {diff > 0.5 ? " 提升" : diff < -0.5 ? " 下降" : " 持平"}
                          <Show when={record().patch?.patch_summary}>
                            <span class="ml-2 text-gray-400">| {record().patch!.patch_summary}</span>
                          </Show>
                        </div>
                      )
                    })()}
                  </Show>

                  {/* 🆕 优化细节卡片（放在显眼位置） */}
                  <Show when={record().patch?.changed_sections && record().patch!.changed_sections!.length > 0 && record().version > 0}>
                    <div class="bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 rounded-xl p-4">
                      <div class="flex items-center gap-2 mb-3">
                        <span class="text-blue-600 text-lg">📝</span>
                        <h3 class="font-semibold text-blue-800">本版优化细节</h3>
                      </div>
                      <div class="space-y-3">
                        <For each={record().patch!.changed_sections}>
                          {(section, idx) => (
                            <div class="bg-white rounded-lg border border-blue-100 p-3">
                              <div class="flex items-start gap-2">
                                <span class="w-5 h-5 rounded-full bg-blue-100 text-blue-700 text-xs font-bold flex items-center justify-center shrink-0 mt-0.5">
                                  {idx() + 1}
                                </span>
                                <div class="min-w-0">
                                  <div class="font-medium text-blue-800 text-sm">{section.section}</div>
                                  <div class="text-xs text-gray-600 mt-1">{section.reason}</div>
                                  <div class="mt-1.5 flex items-center gap-2">
                                    <span class="text-xs bg-red-50 text-red-600 px-1.5 py-0.5 rounded font-mono truncate max-w-[200px]"
                                          title={section.old}>{section.old}</span>
                                    <span class="text-gray-400 text-xs">→</span>
                                    <span class="text-xs bg-green-50 text-green-600 px-1.5 py-0.5 rounded font-mono truncate max-w-[200px]"
                                          title={section.new}>{section.new}</span>
                                  </div>
                                </div>
                              </div>
                            </div>
                          )}
                        </For>
                      </div>
                    </div>
                  </Show>

                  {/* 各区县准确率 */}
                  <Show when={record().evaluation?.per_org}>
                    <div class="bg-white rounded-xl border border-gray-200 p-4">
                      <h3 class="font-semibold mb-3">各区县准确率 <span class="text-xs text-gray-400 font-normal">（点击区县查看预测曲线）</span></h3>
                      <table class="w-full text-sm">
                        <thead>
                          <tr class="border-b border-gray-200 text-gray-500">
                            <th class="text-left py-2">区县</th>
                            <th class="text-right py-2">准确率</th>
                            <th class="text-right py-2">MAPE</th>
                          </tr>
                        </thead>
                        <tbody>
                          <For each={Object.entries(record().evaluation!.per_org!)}>
                            {([org, m]) => {
                              const orgPairs = record().evaluation?.pairs?.filter(p => p.org === org) || []
                              return (
                                <tr class="border-b border-gray-100 cursor-pointer hover:bg-blue-50 transition-colors"
                                    onClick={() => orgPairs.length > 0 && setDrillOrg({
                                      org,
                                      pairs: orgPairs.map(p => ({ month: p.month, predicted: p.predicted, actual: p.actual }))
                                    })}>
                                  <td class="py-2 font-medium text-blue-600">{org} ↗</td>
                                  <td class="text-right font-mono">{m.accuracy.toFixed(2)}%</td>
                                  <td class="text-right font-mono">{m.mape.toFixed(2)}%</td>
                                </tr>
                              )
                            }}
                          </For>
                        </tbody>
                      </table>
                    </div>
                  </Show>

                  {/* 代码补丁详情 */}
                  <Show when={record().patch?.changed_sections}>
                    <div class="bg-white rounded-xl border border-gray-200 p-4">
                      <h3 class="font-semibold mb-3">修改详情</h3>
                      <p class="text-sm text-gray-600 mb-3">{record().patch?.patch_summary}</p>
                      <For each={record().patch!.changed_sections}>
                        {(section) => (
                          <div class="bg-gray-50 rounded-lg p-3 mb-2 text-sm">
                            <div class="font-medium text-blue-700 mb-1">{section.section}</div>
                            <div class="grid grid-cols-2 gap-2 text-xs">
                              <div><span class="text-gray-400">旧:</span> <code class="bg-red-50 text-red-700 px-1">{section.old}</code></div>
                              <div><span class="text-gray-400">新:</span> <code class="bg-green-50 text-green-700 px-1">{section.new}</code></div>
                            </div>
                            <div class="text-gray-500 mt-1">原因: {section.reason}</div>
                          </div>
                        )}
                      </For>
                    </div>
                  </Show>

                  {/* Diff 视图 */}
                  <Show when={record().patch?.diff && record().patch!.diff!.length > 20}>
                    <div class="bg-white rounded-xl border border-gray-200 p-4">
                      <h3 class="font-semibold mb-3">代码差异 (Diff)</h3>
                      <pre class="bg-gray-900 text-green-400 rounded-lg p-4 text-xs overflow-x-auto max-h-64 overflow-y-auto">
                        {record().patch!.diff}
                      </pre>
                    </div>
                  </Show>
                </div>
              )}
            </Show>
          }>
            {/* 对比模式视图 */}
            <div class="bg-white rounded-xl border border-gray-200 p-6">
              <div class="flex items-center justify-between mb-4">
                <h3 class="font-semibold">版本对比</h3>
                <button onClick={() => setCompareVersions([])} class="text-xs text-gray-400 hover:text-gray-600">
                  清除选择
                </button>
              </div>
              <VersionDiff a={compA()!} b={compB()} />
              <Show when={compA() && compB()}>
                <div class="mt-4 pt-4 border-t border-gray-100">
                  <h4 class="text-sm font-semibold mb-2">准确率变化</h4>
                  <div class="text-sm">
                    <span class="text-gray-500">v{compA()!.version}: </span>
                    <span class="font-bold">{compA()!.accuracy?.toFixed(2) ?? "—"}%</span>
                    <span class="mx-2 text-gray-400">→</span>
                    <span class="text-gray-500">v{compB()!.version}: </span>
                    <span class="font-bold">{compB()!.accuracy?.toFixed(2) ?? "—"}%</span>
                    <Show when={compA()?.accuracy != null && compB()?.accuracy != null}>
                      <span class={`ml-2 font-bold ${(compB()!.accuracy! - compA()!.accuracy!) > 0 ? "text-green-600" : "text-red-500"}`}>
                        ({(compB()!.accuracy! - compA()!.accuracy!).toFixed(2)}%)
                      </span>
                    </Show>
                  </div>
                </div>
              </Show>
            </div>
          </Show>
        </div>
      </div>

      {/* 区县钻取弹窗 */}
      <Show when={drillOrg()}>
        <OrgChart org={drillOrg()!.org} pairs={drillOrg()!.pairs} onClose={() => setDrillOrg(null)} />
      </Show>
    </div>
  )
}

export default History
