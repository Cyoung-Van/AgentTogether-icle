import { api } from '../api/client'

/** LLM 可用性守卫(exec_04):无 LLM 时所有必需 LLM 的功能点击后提示接入。 */
export async function ensureLlm(t: (key: string) => string): Promise<boolean> {
  try {
    const data = await api.providers()
    const hasConnected = (data.providers ?? []).some(
      (p: any) =>
        p.status === 'connected'
        && ['openai-compatible', 'local'].includes(p.type)
        && p.configured !== false
        && (p.models?.length ?? 0) > 0,
    )
    if (hasConnected) return true
  } catch {
    // providers 拉取失败按无 LLM 处理(提示)
  }
  // eslint-disable-next-line no-alert
  window.alert(t('exec.needLlm'))
  return false
}
