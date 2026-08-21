import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { displayEnum } from '../lib/displayLabel'

/**
 * AgentPicker:执行前选择执行 agent(不接 LLM 的基础功能)。
 * 列表 = 本机已链接 agent(discovery),每个附带小字介绍
 * (display_name + version + capabilities + native_home 摘要)。
 * 「用计划默认」= 不覆盖,按 plan steps 里已定的 agent 执行。
 */
export function AgentPicker({
  onPick,
  onCancel,
  taskTitle,
}: {
  onPick: (agent: string | null) => void
  onCancel: () => void
  taskTitle?: string
}) {
  const { t, lang } = useLang()
  const agents = useQuery({ queryKey: ['agentDiscovery'], queryFn: api.agentDiscovery })
  const [picked, setPicked] = useState<string | null>(null)
  const list = (agents.data ?? []).filter((a) => a.status === 'linked' && a.execution_supported !== false)

  return (
    <div className="card agent-picker">
      <h3 className="line-clamp-2" title={taskTitle}>{t('exec.pickAgent')}{taskTitle ? ` — ${taskTitle}` : ''}</h3>
      <div className="list">
        {list.length === 0 && <p className="muted">{t('exec.noAgents')}</p>}
        {list.map((agent) => (
          <button
            key={agent.agent_type}
            className={`row agent-option${picked === agent.agent_type ? ' picked' : ''}`}
            onClick={() => setPicked(agent.agent_type)}
          >
            <div className="row-title">
              <strong>{agent.display_name ?? agent.agent_type}</strong>
              <span className="badge dim">{displayEnum('agent_type', agent.agent_type, lang)}</span>
              {agent.version && <span className="muted small">{agent.version}</span>}
            </div>
            <div className="muted small agent-desc">
              {(agent.capabilities ?? []).join(' · ') || t('exec.noDesc')}
            </div>
            <div className="muted small">{agent.native_home ?? agent.executable_path ?? ''}</div>
          </button>
        ))}
      </div>
      <div className="actions">
        <button className="secondary" onClick={() => onPick(null)}>
          {t('exec.planDefault')}
        </button>
        <button disabled={!picked} onClick={() => onPick(picked)}>{t('exec.useAgent')}</button>
        <button className="secondary" onClick={onCancel}>{t('common.cancel')}</button>
      </div>
    </div>
  )
}
