import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { displayEnum, displayStatus } from '../lib/displayLabel'
import { optionLabel } from '../lib/optionLabel'
import { CollapsibleList } from '../components/CollapsibleList'
import { ExpandableText } from '../components/ExpandableText'

export default function Recommend() {
  const { t, lang } = useLang()
  const [taskId, setTaskId] = useState('')
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const tasks = useQuery({ queryKey: ['tasks', 'recommendOptions'], queryFn: api.tasks })
  const agents = useQuery({ queryKey: ['agents', 'recommendOptions'], queryFn: api.agents })
  const selectedTask = (tasks.data?.tasks ?? []).find((item) => item.task_id === taskId)
  const taskText = selectedTask ? `${selectedTask.title}\n${selectedTask.description}`.trim() : ''
  const similar = useQuery({
    queryKey: ['similar', taskId, taskText],
    queryFn: () => api.similar(taskText, selectedTask?.project_id),
    enabled: taskText.length > 3,
  })

  function agentLabel(agentId: string): string {
    return agents.data?.agents.find((item) => item.agent_id === agentId)?.display_name ?? agentId
  }

  function recommendationReason(value: any): string {
    const domain = t(`profile.domain.${value.domain}`)
    return value.recommended
      ? `${t('recommend.domain')} ${domain} · ${t('recommend.resultAgent')} ${agentLabel(value.recommended)} · ${t('recommend.policyScore')} ${value.policy_order?.[0]?.score ?? value.ranking?.[0]?.score ?? '—'}`
      : `${t('recommend.domain')} ${domain} · ${t('recommend.noDomainEvidence')}`
  }

  function evidenceSummary(entry: any): string {
    const parts = [
      `${t('recommend.exactModelSamples')} ${entry.local_evidence_count ?? 0}`,
      `${t('recommend.agentShellSamples')} ${entry.agent_shell_evidence_count ?? 0}`,
    ]
    if (entry.external_baseline?.score != null) {
      parts.push(`${t('recommend.externalPrior')} ${entry.external_baseline.score}`)
    }
    if (entry.median_duration_ms != null) {
      parts.push(`${t('recommend.completedP50')} ${(Number(entry.median_duration_ms) / 1000).toFixed(1)}s`)
    }
    if (entry.model_identity?.model) parts.push(`${t('agents.model')}: ${entry.model_identity.model}`)
    return parts.join(' · ')
  }

  async function submit() {
    setLoading(true)
    setError('')
    try {
      setResult(await api.recommend(taskText, selectedTask?.project_id, selectedTask?.profile))
    } catch (exc) {
      setError(String(exc))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <h1>{t('recommend.title')}</h1>
      <div className="panel">
        <label className="field">
          {t('recommend.task')}
          <select value={taskId} onChange={(event) => { setTaskId(event.target.value); setResult(null) }}>
            <option value="">{t('recommend.chooseTask')}</option>
            {(tasks.data?.tasks ?? []).map((item) => (
              <option key={item.task_id} value={item.task_id}>
                {optionLabel(item.title)} · {item.project_id} · {displayStatus(item.status, lang)}
              </option>
            ))}
          </select>
        </label>
        {selectedTask && (
          <dl className="facts compact-facts">
            <dt>{t('recommend.project')}</dt><dd>{selectedTask.project_id}</dd>
            <dt>{t('newtask.taskDesc')}</dt><dd><ExpandableText text={selectedTask.description || '—'} lines={3} threshold={200} /></dd>
          </dl>
        )}
        {error && <div className="error">{error}</div>}
        <div className="actions">
          <button onClick={submit} disabled={loading || taskText.length < 4}>
            {loading ? t('recommend.thinking') : t('recommend.go')}
          </button>
        </div>
      </div>

      {result && (
        <section className="panel">
          <h2>{t('recommend.result')}</h2>
          <div className="rec-top">
            <span className={`badge big ${result.recommended ? '' : 'dim'}`}>
              {result.recommended ? agentLabel(result.recommended) : t('recommend.insufficient')}
            </span>
            <span className="badge dim">{t(`profile.domain.${result.domain}`)}</span>
            <span className="muted">{displayEnum('policy', result.policy, lang)}</span>
          </div>
          <ExpandableText text={recommendationReason(result)} className="request" lines={4} threshold={260} />
          {result.policy_order?.[0]?.decision?.axes_used?.length > 0 && (
            <div className="cost-domain-table measurement-table">
              <div className="cost-domain-row cost-domain-head">
                <strong>{t('profile.axis')}</strong>
                <span>{t('recommend.policyScore')}</span>
                <span>{t('profile.axisStatus')}</span>
              </div>
              {(result.policy_order ?? result.ranking ?? []).slice(0, 3).map((entry: any) => (
                (entry.decision?.axes_used ?? []).map((item: any) => (
                  <div className="cost-domain-row" key={`${entry.agent}-${item.axis}`}>
                    <strong>{agentLabel(entry.agent)} · {t(`profile.axis.${item.axis}`)}</strong>
                    <span data-label={t('recommend.policyScore')}>{Number(item.value).toFixed(3)}</span>
                    <span data-label={t('profile.axisStatus')}>{item.source}</span>
                  </div>
                ))
              ))}
            </div>
          )}
          <h3>{t('recommend.policyOrder')}</h3>
          <CollapsibleList initialCount={8}>
            {(result.policy_order ?? result.ranking ?? []).map((entry: any) => (
              <div key={entry.agent} className="row">
                <div className="row-meta">
                  <span className="badge">{agentLabel(entry.agent)}</span>
                  {agentLabel(entry.agent) !== entry.agent && <span className="muted mono">{entry.agent}</span>}
                  <span className="badge dim">{displayEnum('confidence', entry.confidence, lang)}</span>
                  {entry.exploration && <span className="badge dim">{t('recommend.explorationCandidate')}</span>}
                  <span className="muted">
                    {entry.score === null ? t('recommend.insufficient') : `${t('recommend.policyScore')} ${entry.score}`}
                  </span>
                </div>
                <ExpandableText text={evidenceSummary(entry)} className="muted" lines={3} threshold={240} />
              </div>
            ))}
          </CollapsibleList>
        </section>
      )}

      {similar.data && similar.data.length > 0 && (
        <section className="panel">
          <h2>{t('recommend.similar')}</h2>
          <CollapsibleList initialCount={8}>
            {similar.data.map((item: any) => (
              <Link key={item.episode_id} to={`/episodes/${item.episode_id}`} className="row">
                <div className="row-meta">
                  <span className="badge">{item.episode_id}</span>
                  <span className="badge dim">{(item.score * 100).toFixed(0)}%</span>
                </div>
                <div className="muted line-clamp-3">{item.request}</div>
              </Link>
            ))}
          </CollapsibleList>
        </section>
      )}
    </div>
  )
}
