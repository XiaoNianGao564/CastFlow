/**
 * WebSearch - 真实网络搜索模块
 *
 * 通过 Python 子进程调用搜狗搜索获取实时网页内容。
 * 由于网络限制（国际站点不可达），使用搜狗（sogou.com）作为搜索引擎。
 * 搜狗是中国第二大搜索引擎，对电力负荷预测等中文内容覆盖良好。
 *
 * 如果搜狗也不可达，回退到 Bing 搜索（如果有 VPN/代理）或 LLM 模拟。
 */

import { execSync } from "child_process"
import { writeFileSync, unlinkSync, mkdtempSync } from "fs"
import { join } from "path"
import { tmpdir } from "os"

export interface WebSearchResult {
  title: string
  snippet: string
  url: string
}

export class WebSearchEngine {
  private cache = new Map<string, { results: WebSearchResult[]; timestamp: number }>()
  private readonly CACHE_TTL = 30 * 60 * 1000 // 30 min cache

  /**
   * 搜索网络，返回结构化结果
   */
  async search(query: string, maxResults: number = 5): Promise<WebSearchResult[]> {
    // 检查缓存
    const cached = this.cache.get(query)
    if (cached && Date.now() - cached.timestamp < this.CACHE_TTL) {
      return cached.results.slice(0, maxResults)
    }

    // 执行搜索
    const results = await this.searchSogou(query, maxResults)

    // 缓存
    if (results.length > 0) {
      this.cache.set(query, { results, timestamp: Date.now() })
    }

    return results
  }

  /**
   * 搜索并返回合并摘要文本（用于注入 LLM prompt）
   */
  async searchAsText(query: string, maxResults: number = 3): Promise<string> {
    const results = await this.search(query, maxResults)
    if (results.length === 0) return ""

    return results.map((r, i) =>
      `[${i + 1}] ${r.title}\n   ${r.snippet}\n   来源: ${r.url}`
    ).join("\n\n")
  }

  /** 清空缓存 */
  clearCache() {
    this.cache.clear()
  }

  // ======= 搜狗搜索实现 =======
  private async searchSogou(query: string, maxResults: number): Promise<WebSearchResult[]> {
    const tmpDir = mkdtempSync(join(tmpdir(), "castflow-search-"))
    const scriptFile = join(tmpDir, "search.py")

    const pyCode = `
import re, json, sys

try:
    import requests
    r = requests.get(
        "https://www.sogou.com/web",
        params={"query": ${JSON.stringify(query)}},
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        timeout=15,
    )
    html = r.text
except Exception as e:
    print(json.dumps({"error": str(e)}, ensure_ascii=False))
    sys.exit(0)

results = []
# 搜狗搜索结果解析
# 结果在 <div class="vrwrap"> 或 <div class="rb"> 中
# 标题在 <h3> / <a> 中，摘要为 <p class="str-info"> 或 <div class="str-text">

# 方法1: 匹配标准结果块
for block in re.findall(
    r'<div class="vrwrap[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
    html, re.DOTALL
):
    title_match = re.search(r'<h3[^>]*>.*?<a[^>]*>(.*?)</a>', block, re.DOTALL)
    snippet_match = re.search(
        r'<p class="str-info[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL
    )
    url_match = re.search(r'<a[^>]*href="(https?://[^"]+)"', block)
    title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else ""
    snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip() if snippet_match else ""
    url = url_match.group(1) if url_match else ""
    if title:
        results.append({"title": title, "snippet": snippet[:300], "url": url})

# 方法2: 备用匹配
if not results:
    for block in re.findall(
        r'<div class="rb[^"]*"[^>]*>(.*?)</div>\s*<div class="fb">',
        html, re.DOTALL
    ):
        title_match = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        url_match = re.search(r'href="(https?://[^"]+)"', block)
        title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else ""
        snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip() if snippet_match else ""
        url = url_match.group(1) if url_match else ""
        if title:
            results.append({"title": title, "snippet": snippet[:300], "url": url})

# 方法3: 纯标题匹配（兜底）
if not results:
    for block in re.findall(r'<h3[^>]*>(.*?)</h3>', html, re.DOTALL):
        a_match = re.search(r'<a[^>]*>(.*?)</a>', block)
        if a_match:
            title = re.sub(r'<[^>]+>', '', a_match.group(1)).strip()
            if title:
                results.append({"title": title, "snippet": "", "url": ""})

print(json.dumps(results[:${maxResults * 2}], ensure_ascii=False))
`

    writeFileSync(scriptFile, pyCode, "utf-8")

    try {
      const output = execSync(`python "${scriptFile}"`, {
        encoding: "utf-8",
        timeout: 20000,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
        maxBuffer: 5 * 1024 * 1024,
      }).trim()

      const parsed = JSON.parse(output)
      if (parsed.error) {
        console.warn(`[WebSearch] Sogou search failed: ${parsed.error}`)
        return []
      }
      return (parsed as WebSearchResult[]).slice(0, maxResults)
    } catch (e: any) {
      console.warn(`[WebSearch] Search error: ${e.message || e}`)
      return []
    } finally {
      try { unlinkSync(scriptFile); unlinkSync(tmpDir) } catch {}
    }
  }
}
