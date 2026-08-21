// prettyContent:把捕获会话的 content 转成自然语言展示。
// capture 按设计保留原始 wire(append-only raw,不做理解),展示层负责提取：
// - 已是纯文本 → 原样
// - JSON wire(如 kimi 整行 wire、claude thinking 结构)→ 递归提取 text/content/message/input/thinking
// 提取不到 → 返回原样(绝不显示空串)。

export function prettyContent(raw: string): string {
  if (!raw) return raw
  const s = raw.trim()
  if (!s || !(s[0] === '{' || s[0] === '[')) return raw
  let data: unknown
  try {
    data = JSON.parse(s)
  } catch {
    return raw
  }
  const extracted = extractText(data)
  return extracted || raw
}

function extractText(data: unknown): string {
  if (typeof data === 'string') {
    const t = data.trim()
    if (t) return t
    return ''
  }
  if (Array.isArray(data)) {
    for (const item of data) {
      const t = extractText(item)
      if (t) return t
    }
    return ''
  }
  if (data && typeof data === 'object') {
    const obj = data as Record<string, unknown>
    for (const key of ['text', 'content', 'message', 'input', 'user_message', 'thinking']) {
      if (key in obj) {
        const t = extractText(obj[key])
        if (t) return t
      }
    }
  }
  return ''
}

// displayContent:展示层最终文案。
// 1. 先 prettyContent 提取自然语言;
// 2. 把系统注入的噪音块(<environment_context>/<turn_aborted>/<instructions>)
//    折叠成跟随界面语言的统一简短标签(会话要么全中文要么全英文);
// 3. 用户真实指令(不含系统块)保持原样,绝不修改。
export function displayContent(raw: string, lang: 'en' | 'zh'): string {
  const text = prettyContent(raw)
  const labels = lang === 'zh'
    ? { env: '[环境上下文]', abort: '[用户中断本轮]', instr: '[系统指令]' }
    : { env: '[environment context]', abort: '[turn aborted]', instr: '[system instructions]' }
  let out = text
  out = out.replace(/<environment_context>[\s\S]*?<\/environment_context>/g, labels.env)
  out = out.replace(/<turn_aborted>[\s\S]*?<\/turn_aborted>/g, labels.abort)
  out = out.replace(/<(?:INSTRUCTIONS|instructions)>[\s\S]*?<\/(?:INSTRUCTIONS|instructions)>/g, labels.instr)
  return out
}
