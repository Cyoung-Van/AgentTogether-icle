import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { displayEnum } from '../lib/displayLabel'
import type { DetectedAgent } from '../types'
import { CollapsibleList } from '../components/CollapsibleList'
import { ExpandableText } from '../components/ExpandableText'

/** Local Agents (v0.4 §12): scan local agent CLIs, link what you actually use.
 *  Discovery is read-only — no login/chat/install ever runs here.
 */
export default function LocalAgents() {
  const { t, lang } = useLang()
  const queryClient = useQueryClient()
  const discovery = useQuery({ queryKey: ['agentDiscovery'], queryFn: api.agentDiscovery })
  const doctor = useQuery({ queryKey: ['agentDoctor'], queryFn: api.agentDoctor })
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState('')

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['agentDiscovery'] })
    queryClient.invalidateQueries({ queryKey: ['agentDoctor'] })
  }

  const link = useMutation({
    mutationFn: (agentType: string) => api.linkAgent(agentType),
    onSuccess: invalidate,
  })
  const unlink = useMutation({
    mutationFn: (agentType: string) => api.unlinkAgent(agentType),
    onSuccess: invalidate,
  })
  const linkAll = useMutation({
    mutationFn: () => api.linkAllAgents(),
    onSuccess: invalidate,
    onError: (exc: Error) => setActionError(exc.message),
  })
  const capture = useMutation({
    mutationFn: (agentType: string) => api.captureAgent(agentType),
    onSuccess: invalidate,
  })
  const [captureResult, setCaptureResult] = useState<Record<string, string>>({})

  if (discovery.isPending || doctor.isPending) return <div className="empty">{t('common.loading')}</div>
  if (discovery.isError || doctor.isError) return <div className="empty error">{t('localagents.failed')}</div>

  const agents = discovery.data
  const summary = doctor.data.summary

  async function toggle(agent: DetectedAgent) {
    setBusy(agent.agent_type)
    setActionError('')
    try {
      if (agent.status === 'linked') await unlink.mutateAsync(agent.agent_type)
      else await link.mutateAsync(agent.agent_type)
    } catch (exc) {
      setActionError(String(exc))
    } finally {
      setBusy(null)
    }
  }

  async function runCapture(agent: DetectedAgent) {
    setBusy(agent.agent_type)
    try {
      const result = await capture.mutateAsync(agent.agent_type)
      setCaptureResult((prev) => ({
        ...prev,
        [agent.agent_type]: `${t('localagents.fetchDone')} ${result.sessions?.length ?? result.sessions ?? ''} / ${result.new_events ?? result.total_new ?? 0}`,
      }))
    } catch (exc) {
      setCaptureResult((prev) => ({ ...prev, [agent.agent_type]: String(exc) }))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div>
      <div className="title-row">
        <h1>{t('localagents.title')}</h1>
        <button className="secondary" onClick={() => {
          setActionError('')
          Promise.all([discovery.refetch(), doctor.refetch()]).catch((exc) => setActionError(String(exc)))
        }}>{t('localagents.scan')}</button>
        <button className="secondary" disabled={linkAll.isPending} onClick={() => { setActionError(''); linkAll.mutate() }}> 
          {t('localagents.linkAll')}
        </button>
      </div>
      {actionError && <div className="error">{actionError}</div>}
      <div className="cards">
        <div className="card"><div className="metric">{summary.total}</div><div className="label">{t('localagents.detected')}</div></div>
        <div className="card"><div className="metric">{summary.ready}</div><div className="label">{t('localagents.ready')}</div></div>
        <div className="card"><div className="metric">{summary.needs_attention}</div><div className="label">{t('localagents.attention')}</div></div>
      </div>

      <CollapsibleList initialCount={8}>
        {agents.map((agent) => {
          const health = doctor.data.agents.find((row) => row.agent_type === agent.agent_type)
          const ready = agent.status === 'runnable' || agent.status === 'linked'
          return (
            <div key={agent.agent_type} className="row">
              <div className="row-title">
                {ready ? '✓' : '○'} {agent.display_name}{' '}
                <span className={`badge ${agent.status === 'linked' ? '' : 'dim'}`}>
                  {agent.status === 'linked'
                    ? t('localagents.linked')
                    : agent.status === 'runnable'
                      ? t('localagents.runnable')
                      : agent.status === 'unavailable'
                        ? t('localagents.notFound')
                        : displayEnum('agent_status', agent.status, lang)}
                </span>
              </div>
              <div className="row-meta">
                <span className="muted mono">{agent.executable_path ?? '—'}</span>
                {agent.version && <span className="badge dim">{agent.version}</span>}
                {agent.native_home && <span className="muted mono">{agent.native_home}</span>}
                {agent.session_capture_count > 0 && (
                  <span className="muted">{agent.session_capture_count} {t('localagents.sessions')}</span>
                )}
                <span className="badge dim">
                  {agent.execution_supported
                    ? t('localagents.execution')
                    : agent.capture_supported
                      ? t('localagents.captureOnly')
                      : t('localagents.adapterReady')}
                </span>
              </div>
              {/* Doctor health row (§13) */}
              {health && (
                <div className="row-meta muted">
                  <span>{t('localagents.exe')} {health.checks.executable ? '✓' : '✗'}</span>
                  <span>{t('localagents.ver')} {health.checks.version ? '✓' : '✗'}</span>
                  <span>{t('localagents.home')} {health.checks.native_home ? '✓' : '—'}</span>
                  <span>{t('localagents.capture')} {health.checks.session_capture ? '✓' : '—'}</span>
                  <span>{t('localagents.acp')} —</span>
                </div>
              )}
              {agent.probe_errors.map((error, index) => (
                <ExpandableText key={index} text={error} className="error" lines={3} threshold={220} />
              ))}
              {captureResult[agent.agent_type] && (
                <div className="muted">{captureResult[agent.agent_type]}</div>
              )}
              {agent.status !== 'unavailable' && (
                <div className="actions">
                  <button className="secondary" disabled={busy === agent.agent_type} onClick={() => toggle(agent)}>
                    {agent.status === 'linked' ? t('localagents.unlink') : t('localagents.link')}
                  </button>
                  {agent.agent_type in {
                    claude: true, codex: true, hermes: true, kimi: true, opencode: true, pi: true,
                  } && (
                    <button className="secondary" disabled={busy === agent.agent_type} onClick={() => runCapture(agent)}>
                      {t('localagents.fetch')}
                    </button>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </CollapsibleList>
      <p className="muted">{t('localagents.footer')}</p>
    </div>
  )
}
