import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { formatLocalDateTime } from '../lib/dateTime'
import { CollapsibleList } from '../components/CollapsibleList'

function money(value: unknown, digits = 4): string {
  return typeof value === 'number' ? `$${value.toFixed(digits)}` : '—'
}

function rate(value: unknown): string {
  return typeof value === 'number' ? `$${value.toFixed(6)} / 1M` : '—'
}

function value(value: unknown, digits = 3): string {
  return typeof value === 'number' ? value.toFixed(digits) : '—'
}

function clock(ms: unknown): string {
  if (typeof ms !== 'number' || ms <= 0) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds - minutes * 60
  if (minutes < 60) return `${minutes}m ${rest.toFixed(0)}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

function Bucket({ title, entries, calls, unknownLabel }: { title: string; entries: Record<string, any>; calls: string; unknownLabel: string }) {
  const rows = Object.entries(entries ?? {}).sort((a, b) => Number(b[1].total ?? 0) - Number(a[1].total ?? 0))
  return (
    <section className="panel">
      <h2>{title}</h2>
      {rows.length === 0 && <p className="muted">—</p>}
      <CollapsibleList initialCount={8} className="compact-list">
        {rows.map(([key, entry]) => (
        <div key={key} className="row-meta compact-list-row" style={{ padding: '6px 0' }}>
          <span className="badge dim">{key}</span>
          <span className="muted">{entry.known_count ?? 0} {calls}</span>
          {(entry.unknown_count ?? 0) > 0 && <span className="muted">{entry.unknown_count} {unknownLabel}</span>}
          <span className="muted">{clock(entry.duration_ms)}</span>
          <span className="mono">{money(entry.total)}</span>
        </div>
        ))}
      </CollapsibleList>
    </section>
  )
}

function AgentCostDetails({ agent, t }: { agent: any; t: (key: string) => string }) {
  const profile = agent?.decision_profile ?? {}
  const capabilities = profile.capabilities ?? []
  const pricedCapability = capabilities.find((item: any) => item.cost?.unit_rates)
  const referenceCost = pricedCapability?.cost ?? capabilities[0]?.cost ?? {}
  const rates = referenceCost.unit_rates ?? {}
  const pricingSource = referenceCost.pricing_source ?? {}
  const route = profile.route ?? {}

  return (
    <>
      <div className="row-meta cost-model-identity">
        <strong>{route.agent ?? agent.agent_id}</strong>
        <span className="badge dim">{route.provider ?? '—'} / {route.model ?? '—'}</span>
        <Link to={`/agent-profile?agent_id=${encodeURIComponent(agent.agent_id)}`}>{t('cost.openEvaluation')}</Link>
      </div>

      <h3>{t('cost.tokenRates')}</h3>
      <div className="cost-rate-grid">
        <div><span>{t('profile.inputRate')}</span><strong>{rate(rates.input_uncached)}</strong></div>
        <div><span>{t('profile.cacheReadRate')}</span><strong>{rate(rates.cache_read ?? rates.input_cached)}</strong></div>
        <div><span>{t('profile.cacheWriteRate')}</span><strong>{rate(rates.cache_write)}</strong></div>
        <div><span>{t('profile.outputRate')}</span><strong>{rate(rates.output)}</strong></div>
        <div><span>{t('profile.reasoningRate')}</span><strong>{rate(rates.reasoning)}</strong></div>
      </div>

      <dl className="facts compact-facts cost-source-facts">
        <div>
          <dt>{t('profile.catalogBillingMode')}</dt>
          <dd>{t(`profile.billing.${referenceCost.catalog_billing_mode ?? referenceCost.billing_mode ?? 'UNKNOWN'}`)}</dd>
        </div>
        <div>
          <dt>{t('profile.executionBillingMode')}</dt>
          <dd>{t(`profile.billing.${referenceCost.execution_billing_mode ?? 'UNKNOWN'}`)}</dd>
        </div>
        <div>
          <dt>{t('profile.pricingSource')}</dt>
          <dd className="mono">{referenceCost.pricing_snapshot ?? '—'}</dd>
        </div>
        <div>
          <dt>{t('profile.pricingRevision')}</dt>
          <dd className="mono">{pricingSource.revision ?? '—'}</dd>
        </div>
        <div>
          <dt>{t('profile.pricingCatalog')}</dt>
          <dd>
            {pricingSource.url
              ? <a href={pricingSource.url} target="_blank" rel="noreferrer">{pricingSource.license ? `models.dev · ${pricingSource.license}` : t(`profile.pricingKind.${pricingSource.kind}`)}</a>
              : '—'}
          </dd>
        </div>
      </dl>

      <h3>{t('cost.domainCosts')}</h3>
      <p className="muted">{t('cost.domainCostsHelp')}</p>
      <div className="cost-domain-table" role="table" aria-label={t('cost.domainCosts')}>
        <div className="cost-domain-row cost-domain-head" role="row">
          <span>{t('cost.domain')}</span>
          <span>{t('cost.quality')}</span>
          <span>{t('profile.apiEquivalentCost')}</span>
          <span>{t('profile.executionTaskCost')}</span>
          <span>{t('profile.localTaskCost')}</span>
          <span>{t('profile.apiEquivalentCostToAccept')}</span>
          <span>{t('profile.costToAccept')}</span>
        </div>
        {capabilities.map((capability: any) => {
          const cost = capability.cost ?? {}
          const apiEquivalent = cost.api_equivalent_task_cost ?? {}
          const execution = cost.execution_task_cost ?? {}
          const local = capability.local_experience?.known_cost ?? {}
          return (
            <div className="cost-domain-row" role="row" key={capability.domain}>
              <strong>{t(`profile.domain.${capability.domain}`)}</strong>
              <span data-label={t('cost.quality')}>{value(capability.quality?.score)}</span>
              <span data-label={t('task.apiEquivalentEstimate')}>{money(apiEquivalent.expected)}</span>
              <span data-label={t('task.executionEstimate')}>
                {money(execution.expected)}
                <small>{t(`profile.billing.${execution.billing_mode ?? cost.execution_billing_mode ?? 'UNKNOWN'}`)}</small>
              </span>
              <span data-label={t('profile.localTaskCost')}>{money(local.p50)}</span>
              <span data-label={t('profile.apiEquivalentCostToAccept')}>{money(cost.api_equivalent_cost_to_accept)}</span>
              <span data-label={t('profile.costToAccept')}>{money(cost.execution_cost_to_accept)}</span>
            </div>
          )
        })}
      </div>
      {capabilities.length === 0 && <p className="muted">{t('cost.noAgentCostProfile')}</p>}
      {referenceCost.unknown_reason && (
        <p className="muted">{t(`profile.unknown.${referenceCost.unknown_reason}`)}</p>
      )}
    </>
  )
}

export default function CostCenter() {
  const { t } = useLang()
  const [search, setSearch] = useSearchParams()
  const query = useQuery({ queryKey: ['cost'], queryFn: api.costCenter })
  const agentsQuery = useQuery({ queryKey: ['agents'], queryFn: api.agents })
  const agents = agentsQuery.data?.agents ?? []
  const requestedAgentId = search.get('agent_id') ?? ''
  const selectedAgentId = agents.some((item) => item.agent_id === requestedAgentId)
    ? requestedAgentId
    : agents[0]?.agent_id ?? ''
  const agentQuery = useQuery({
    queryKey: ['agent', selectedAgentId],
    queryFn: () => api.agentProfile(selectedAgentId),
    enabled: Boolean(selectedAgentId),
  })

  useEffect(() => {
    if (!agentsQuery.isSuccess || !selectedAgentId || requestedAgentId === selectedAgentId) return
    const next = new URLSearchParams(search)
    next.set('agent_id', selectedAgentId)
    setSearch(next, { replace: true })
  }, [agentsQuery.isSuccess, requestedAgentId, search, selectedAgentId, setSearch])

  if (query.isPending) return <div className="empty">{t('common.loading')}</div>
  if (query.isError) return <div className="empty error">{t('cost.failed')}</div>
  const data: any = query.data
  const budget = data.budget ?? {}
  const comparison = data.estimated_vs_actual ?? {}

  function selectAgent(agentId: string) {
    const next = new URLSearchParams(search)
    if (agentId) next.set('agent_id', agentId)
    else next.delete('agent_id')
    setSearch(next)
  }

  return (
    <div>
      <h1>{t('cost.title')}</h1>
      <div className="cards">
        <div className="card"><div className="metric">{money(data.total_spend)}</div><div className="label">{t('cost.totalSpend')}</div></div>
        <div className="card"><div className="metric">{clock(data.total_duration_ms)}</div><div className="label">{t('cost.totalTime')}</div></div>
        <div className="card"><div className="metric">{data.known_cost_count ?? 0}</div><div className="label">{t('cost.known')}</div></div>
        <div className="card"><div className="metric">{data.unknown_cost_count ?? 0}</div><div className="label">{t('cost.unknown')}</div></div>
        <div className="card"><div className="metric">{data.total_actuals ?? 0}</div><div className="label">{t('cost.totalActuals')}</div></div>
      </div>

      <section className="panel cost-agent-details">
        <div className="title-row cost-detail-heading">
          <div>
            <h2>{t('cost.agentModelDetails')}</h2>
            <p className="muted">{t('cost.agentModelDetailsHelp')}</p>
          </div>
          <label className="field compact-field">
            {t('cost.chooseAgentModel')}
            <select value={selectedAgentId} onChange={(event) => selectAgent(event.target.value)}>
              {agents.length === 0 && <option value="">{t('cost.noAgents')}</option>}
              {agents.map((item) => (
                <option key={item.agent_id} value={item.agent_id}>
                  {item.display_name ?? item.agent_id}{item.model_id ? ` · ${item.model_id}` : ''}
                </option>
              ))}
            </select>
          </label>
        </div>
        {(agentsQuery.isPending || agentQuery.isPending) && <p className="muted">{t('common.loading')}</p>}
        {(agentsQuery.isError || agentQuery.isError) && <p className="error">{t('cost.agentDetailsFailed')}</p>}
        {agentQuery.data && <AgentCostDetails agent={agentQuery.data} t={t} />}
      </section>

      <section className="panel">
        <h2>{t('cost.budget')}</h2>
        {budget.budget == null
          ? <p className="muted">{t('settings.costBudgetHelp')}</p>
          : <div className="row-meta">
            <span className="badge dim">{money(budget.spent)} / {money(budget.budget)}</span>
            <span className="muted">{budget.warning ? t('settings.budgetWarning') : `${money(budget.remaining)} ${t('settings.replayRemaining')}`}</span>
          </div>}
      </section>

      <section className="panel">
        <h2>{t('cost.estimatedActual')}</h2>
        <div className="row-meta">
          <span className="badge dim">{t('cost.estimated')} {money(comparison.estimated_total)}</span>
          <span className="badge dim">{t('cost.actual')} {money(comparison.actual_total)}</span>
          <span className="muted">{comparison.count ?? 0} {t('cost.calls')}</span>
        </div>
        <CollapsibleList initialCount={8} className="compact-list">
        {[...(comparison.records ?? [])].reverse().map((item: any, index: number) => (
          <div key={`${item.task_id ?? 'system'}-${item.step_id ?? index}`} className="row-meta" style={{ padding: '6px 0' }}>
            <span className="muted">{item.task_id ?? 'system'} {item.step_id ?? ''}</span>
            <span className="mono">{money(item.estimated)} → {money(item.actual)}</span>
            <span className="muted">{item.error == null ? '—' : `${(Number(item.error) * 100).toFixed(1)}%`}</span>
          </div>
        ))}
        </CollapsibleList>
      </section>

      <Bucket title={t('cost.byAgent')} entries={data.by_agent} calls={t('cost.calls')} unknownLabel={t('cost.unknown')} />
      <Bucket title={t('cost.byModel')} entries={data.by_model} calls={t('cost.calls')} unknownLabel={t('cost.unknown')} />
      <Bucket title={t('cost.byProject')} entries={data.by_project} calls={t('cost.calls')} unknownLabel={t('cost.unknown')} />
      <Bucket title={t('cost.byTaskType')} entries={data.by_task_type} calls={t('cost.calls')} unknownLabel={t('cost.unknown')} />
      <Bucket title={t('cost.byOperation')} entries={data.by_operation} calls={t('cost.calls')} unknownLabel={t('cost.unknown')} />

      <section className="panel">
        <h2>{t('cost.recent')}</h2>
        {(data.recent ?? []).length === 0 && <p className="muted">{t('cost.empty')}</p>}
        <CollapsibleList initialCount={12} className="compact-list">
        {(data.recent ?? []).map((item: any, index: number) => (
          <div key={`${item.created_at}-${index}`} className="row-meta" style={{ padding: '7px 0', borderBottom: '1px solid var(--border)' }}>
            <span className="muted">{formatLocalDateTime(item.created_at)}</span>
            <span className="badge dim">{item.operation ?? item.scope}</span>
            <span className="mono">{item.provider}/{item.model}</span>
            <span className="muted">{clock(item.duration_ms)}</span>
            <span className={item.cash_cost == null ? 'muted' : 'mono'}>{item.cash_cost == null ? `${t('cost.unknown')} · ${item.reason ?? '—'}` : money(item.cash_cost)}</span>
          </div>
        ))}
        </CollapsibleList>
      </section>
      <p className="muted">{t('cost.footer')}</p>
    </div>
  )
}
