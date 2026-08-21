import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { CollapsibleList } from '../components/CollapsibleList'

export default function Agents() {
  const { t, lang } = useLang()
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['agents'], queryFn: api.agents })
  const refresh = useMutation({
    mutationFn: api.refreshAgentEvaluations,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['agents'] }),
  })

  if (query.isPending) return <div className="empty">{t('common.loading')}</div>
  if (query.isError) return <div className="empty error">{t('agents.failed')}</div>

  const agents = query.data.agents
  const installed = agents.filter((agent) => agent.installed || agent.agent_type === 'provider-model').length
  const evidence = agents.filter((agent) => agent.outcomes > 0 || agent.pairwise > 0 || agent.replays > 0).length
  const executable = agents.filter((agent) => agent.execution_supported && agent.linked).length

  return (
    <div>
      <header className="page-hero">
        <div className="title-row">
          <h1>{t('nav.agents')}</h1>
          <div className="actions">
            <button
              className="secondary"
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending}
              title={t('agents.refreshHelp')}
            >
              {refresh.isPending ? t('agents.refreshing') : t('agents.refresh')}
            </button>
            <Link to="/agents/local" className="secondary link-as-button">{t('agents.manageLocal')}</Link>
          </div>
        </div>
        <p className="page-guide">{t('agents.intro')}</p>
      </header>
      <div className="cards">
        <div className="card"><div className="metric">{agents.length}</div><div className="label">{t('agents.identified')}</div></div>
        <div className="card"><div className="metric">{installed}</div><div className="label">{t('agents.installed')}</div></div>
        <div className="card"><div className="metric">{executable}</div><div className="label">{t('agents.executable')}</div></div>
        <div className="card"><div className="metric">{evidence}</div><div className="label">{t('agents.withEvidence')}</div></div>
      </div>
      <CollapsibleList initialCount={8} className="agent-directory">
        {agents.map((agent) => {
          const description = lang === 'zh'
            ? (agent.description_zh || agent.description)
            : agent.description
          const available = agent.installed || agent.agent_type === 'provider-model' || agent.status === 'historical'
          return (
            <Link
              key={agent.agent_id}
              to={`/agent-profile?agent_id=${encodeURIComponent(agent.agent_id)}`}
              className={`card agent-summary-card${available ? '' : ' unavailable'}`}
            >
              <div className="title-row compact">
                <div>
                  <strong>{agent.display_name ?? agent.agent_id}</strong>
                  <div className="muted mono">{agent.agent_id}</div>
                </div>
                <span className={`badge ${agent.linked ? '' : 'dim'}`}>
                  {agent.agent_type === 'provider-model'
                    ? t('agents.providerModel')
                    : agent.linked
                      ? t('agents.linked')
                      : agent.installed
                        ? t('agents.detected')
                        : t('agents.notInstalled')}
                </span>
              </div>
              <p className="agent-summary line-clamp-3">{description || t('agents.noDescription')}</p>
              <div className="row-meta">
                {agent.execution_supported && <span className="badge">{t('agents.taskExecution')}</span>}
                {agent.capture_supported && <span className="badge dim">{t('agents.sessionCapture')}</span>}
                {agent.version && <span className="muted">{agent.version}</span>}
              </div>
              <div className="agent-model-summary">
                <span className="badge dim">
                  {agent.model_identity?.model
                    ? `${t('agents.model')}: ${agent.model_identity.model}`
                    : t('agents.modelUnknown')}
                </span>
                {agent.external_baseline?.status === 'fresh' && (
                  <span className="badge">
                    {t('agents.externalBaseline')} {agent.external_baseline.score}
                    {agent.external_baseline.match_strength ? ` · ${t(`profile.match.${agent.external_baseline.match_strength}`)}` : ''}
                  </span>
                )}
                {(agent.external_baseline?.status === 'unavailable' || agent.external_baseline?.status === 'unresolved-model') && (
                  <span className="badge dim">{t('agents.externalCheckedNoMatch')}</span>
                )}
                {agent.external_baseline?.status === 'stale' && (
                  <span className="badge dim">{t('agents.externalStale')}</span>
                )}
                {agent.decision_profile?.strongest_domain && (
                  <span className="badge dim">
                    {t('agents.bestDomain')}: {t(`profile.domain.${agent.decision_profile.strongest_domain}`)}
                  </span>
                )}
              </div>
              <div className="agent-evidence-summary">
                <span className={`badge ${(agent.measurement?.observed_axis_count ?? 0) ? '' : 'dim'}`}>
                  {(agent.measurement?.observed_axis_count ?? 0)
                    ? `${t('profile.observedAxes')} ${agent.measurement?.observed_axis_count}/7`
                    : t('profile.noDecisionEvidence')}
                </span>
                <span className="muted">
                  {agent.outcomes} {t('agents.outcomes')} · {agent.pairwise} {t('agents.pairwise')} · {agent.replays} {t('agents.replays')}
                </span>
              </div>
            </Link>
          )
        })}
        {agents.length === 0 && <div className="empty">{t('agents.none')}</div>}
      </CollapsibleList>
      {refresh.isError && <div className="error">{t('agents.refreshFailed')}</div>}
      <p className="muted">{t('agents.footer')}</p>
    </div>
  )
}
