import { useState } from 'react'
import { useParams, Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { formatLocalDateTime } from '../lib/dateTime'
import { displayEnum } from '../lib/displayLabel'
import type { RatingDimensions } from '../types'
import { optionLabel } from '../lib/optionLabel'
import { CollapsibleList } from '../components/CollapsibleList'
import { ExpandableText } from '../components/ExpandableText'

const DIMENSIONS: (keyof RatingDimensions)[] = [
  'requirement_fit',
  'correctness',
  'efficiency',
  'autonomy',
  'maintainability',
]

const INITIAL: RatingDimensions = {
  requirement_fit: 0,
  correctness: 0,
  efficiency: 0,
  autonomy: 0,
  maintainability: 0,
}

function valueOrDash(value: number | null | undefined, digits = 3) {
  return value === null || value === undefined ? '—' : value.toFixed(digits)
}

function moneyOrDash(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `$${value.toFixed(4)}`
}

function secondsOrDash(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `${value.toFixed(0)}s`
}

function Stars({ value, onChange }: { value: number; onChange?: (v: number) => void }) {
  return (
    <span className="stars">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          className={`star ${n <= value ? 'on' : ''}`}
          onClick={onChange ? () => onChange(n) : undefined}
          disabled={!onChange}
        >
          ★
        </button>
      ))}
    </span>
  )
}

export default function AgentProfile() {
  const { t, lang } = useLang()
  const params = useParams<{ agentId: string }>()
  const [search] = useSearchParams()
  const agentId = search.get('agent_id') || params.agentId
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => api.agentProfile(agentId!),
    enabled: Boolean(agentId),
  })
  const directoryQuery = useQuery({ queryKey: ['agents'], queryFn: api.agents })
  const episodesQuery = useQuery({
    queryKey: ['episodes', 'ratingOptions', agentId],
    queryFn: () => api.episodes({ agent: agentId }),
    enabled: Boolean(agentId),
  })
  const [showForm, setShowForm] = useState(false)
  const [episodeId, setEpisodeId] = useState('')
  const [dimensions, setDimensions] = useState<RatingDimensions>(INITIAL)
  const [overall, setOverall] = useState(0)
  const [wouldUseAgain, setWouldUseAgain] = useState('')
  const [comment, setComment] = useState('')
  const [error, setError] = useState('')

  const ratingMutation = useMutation({
    mutationFn: () =>
      api.submitRating({
        agent_id: agentId!,
        episode_id: episodeId,
        dimensions,
        overall_preference: overall,
        would_use_again: wouldUseAgain,
        comment,
      }),
    onSuccess: () => {
      setShowForm(false)
      setError('')
      queryClient.invalidateQueries({ queryKey: ['agent', agentId] })
      queryClient.invalidateQueries({ queryKey: ['ratings'] })
    },
    onError: (exc: Error) => setError(exc.message),
  })

  if (query.isPending) return <div className="empty">{t('common.loading')}</div>
  if (query.isError) return <div className="empty error">{t('profile.notFound')}</div>
  const agent = query.data
  const metadata = directoryQuery.data?.agents.find((item) => item.agent_id === agentId)
  const description = lang === 'zh'
    ? (metadata?.description_zh || metadata?.description)
    : metadata?.description
  const rating = agent.user_rating ?? { count: 0, dimensions: null, overall_preference: null }
  const episodeById = new Map(
    (episodesQuery.data?.episodes ?? []).map((episode) => [episode.episode_id, episode]),
  )
  const episodeOptions = Array.from(new Set([
    ...episodeById.keys(),
    ...(agent.recent_marks ?? []).map((mark: any) => mark.episode_id),
  ]))

  function startForm() {
    setEpisodeId(episodeOptions[0] ?? '')
    setDimensions(INITIAL)
    setOverall(0)
    setWouldUseAgain('')
    setComment('')
    setError('')
    setShowForm(true)
  }

  async function saveRating() {
    setError('')
    if (!episodeId.trim()) {
      setError(t('rating.needEpisode'))
      return
    }
    if (Object.values(dimensions).some((v) => v === 0) || overall === 0 || !wouldUseAgain) {
      setError(t('rating.incomplete'))
      return
    }
    ratingMutation.mutate()
  }

  return (
    <div>
      <p><Link to="/agents">← {t('nav.agents')}</Link></p>
      <div className="title-row">
        <h1>{metadata?.display_name ?? agent.agent_id}</h1>
        {metadata && <span className={`badge ${metadata.linked ? '' : 'dim'}`}>{displayEnum('agent_status', metadata.status, lang)}</span>}
      </div>
      <div className="muted mono">{agent.agent_id}</div>
      <ExpandableText text={description} className="request" lines={3} threshold={220} />
      {metadata && (
        <div className="row-meta agent-capability-line">
          <span className="badge dim">{metadata.installed ? t('agents.installed') : t('agents.notInstalled')}</span>
          {metadata.execution_supported && <span className="badge">{t('agents.taskExecution')}</span>}
          {metadata.capture_supported && <span className="badge dim">{t('agents.sessionCapture')}</span>}
          {metadata.version && <span className="muted">{metadata.version}</span>}
        </div>
      )}
      <div className="cards">
        <div className="card"><div className="metric">{agent.episodes}</div><div className="label">{t('profile.episodes')}</div></div>
        <div className="card"><div className="metric">{agent.accepted}</div><div className="label">{t('profile.accepted')}</div></div>
        <div className="card"><div className="metric">{agent.replays}</div><div className="label">{t('profile.replays')}</div></div>
        <div className="card">
          <div className="metric">
            {agent.pairwise.wins}W / {agent.pairwise.losses}L / {agent.pairwise.ties}T
          </div>
          <div className="label">{t('profile.pairwise')}</div>
        </div>
        <div className="card">
          <div className="metric">
            {agent.median_duration_ms ? `${(agent.median_duration_ms / 1000).toFixed(0)}s` : '—'}
          </div>
          <div className="label">{t('profile.medianDuration')}</div>
        </div>
      </div>

      <section className="panel">
        <h2>{t('profile.measurement')}</h2>
        <p className="muted">{t('profile.measurementHint')}</p>
        <dl className="facts">
          <div>
            <dt>{t('profile.measurementStatus')}</dt>
            <dd>{t(`profile.publication.${agent.decision_profile?.measurement?.publication_status || 'insufficient_evidence'}`)}</dd>
          </div>
          <div>
            <dt>{t('profile.observedAxes')}</dt>
            <dd>{agent.decision_profile?.measurement?.observed_axis_count ?? 0} / 7</dd>
          </div>
          <div>
            <dt>{t('profile.measurementSubject')}</dt>
            <dd>{agent.decision_profile?.measurement?.subject_id || '—'}</dd>
          </div>
        </dl>
        <div className="cost-domain-table measurement-table">
          <div className="cost-domain-row cost-domain-head">
            <strong>{t('profile.axis')}</strong>
            <span>{t('profile.layerA')}</span>
            <span>{t('profile.layerB')}</span>
            <span>{t('profile.layerJ')}</span>
            <span>{t('profile.layerC')}</span>
            <span>{t('profile.axisStatus')}</span>
          </div>
          {['reasoning', 'coding', 'agentic_coding', 'mathematics', 'data_analysis', 'language', 'instruction_following'].map((axis) => {
            const row = agent.decision_profile?.measurement?.axes?.[axis] ?? {}
            const status = row.status || 'unavailable'
            return (
              <div className="cost-domain-row" key={axis}>
                <strong>{t(`profile.axis.${axis}`)}</strong>
                <span data-label={t('profile.layerA')}>{valueOrDash(row.a, 3)}</span>
                <span data-label={t('profile.layerB')}>{valueOrDash(row.b, 3)}</span>
                <span data-label={t('profile.layerJ')}>{valueOrDash(row.j, 3)}</span>
                <span data-label={t('profile.layerC')}>{valueOrDash(row.c ?? row.mean, 3)}</span>
                <span data-label={t('profile.axisStatus')}>
                  {status === 'observed' ? t('profile.axisObserved') : t('profile.noDecisionEvidence')}
                  {row.reason && status !== 'observed' ? ` · ${row.reason}` : ''}
                </span>
              </div>
            )
          })}
        </div>
      </section>

      <section className="panel">
        <h2>{t('profile.shellHistory')}</h2>
        <dl className="facts">
          <div>
            <dt>{t('agents.model')}</dt>
            <dd>{agent.evaluation?.model_identity?.model ?? t('agents.modelUnknown')}</dd>
          </div>
          <div>
            <dt>{t('profile.modelSource')}</dt>
            <dd>{agent.evaluation?.model_identity?.source ?? '—'}</dd>
          </div>
          <div>
            <dt>{t('profile.shellHistory')}</dt>
            <dd>{agent.evaluation?.score ?? '—'}</dd>
          </div>
          <div>
            <dt>{t('profile.scoreSource')}</dt>
            <dd>{agent.evaluation?.score_source ? t(`agents.scoreSource.${agent.evaluation.score_source}`) : '—'}</dd>
          </div>
          <div>
            <dt>{t('profile.localScore')}</dt>
            <dd>{agent.evaluation?.local_score ?? '—'} · n={agent.evaluation?.local_evidence_count ?? 0}</dd>
          </div>
          <div>
            <dt>{t('profile.exactModelExperience')}</dt>
            <dd>{agent.decision_profile?.coverage?.exact_model_observation_count ?? 0}</dd>
          </div>
          <div>
            <dt>{t('profile.publicPriorDomains')}</dt>
            <dd>{(agent.decision_profile?.coverage?.domains_with_public_prior ?? []).length || 0}</dd>
          </div>
          <div>
            <dt>{t('profile.externalStatus')}</dt>
            <dd>
              {agent.evaluation?.external_baseline?.status ?? '—'}
              {agent.evaluation?.external_baseline?.match_strength
                ? ` · ${t(`profile.match.${agent.evaluation.external_baseline.match_strength}`)}`
                : ''}
            </dd>
          </div>
        </dl>
        {agent.evaluation?.external_baseline && (
          <p className="muted">
            {t(`profile.externalReason.${agent.evaluation.external_baseline.match_strength || agent.evaluation.external_baseline.status}`)}
          </p>
        )}
        <CollapsibleList initialCount={3} className="external-sources">
          {(agent.evaluation?.external_baseline?.records ?? []).map((record: any, index: number) => (
            <div className="external-source" key={`${record.source_id}-${record.model}-${index}`}>
              <div className="row-meta">
                <span className="badge">{record.benchmark}</span>
                <span className="badge dim">{record.evaluated_agent || '—'} + {record.model}</span>
                <span className="badge dim">{t(`profile.match.${record.match_kind}`)}</span>
                <strong>{record.score}</strong>
              </div>
              <div className="muted">
                {record.result_date && `${t('profile.resultDate')} ${record.result_date} · `}
                {t('profile.retrievedAt')} {record.snapshot_retrieved_at?.slice(0, 10)} · {t('profile.freshUntil')} {record.fresh_until?.slice(0, 10)}
              </div>
              {record.source_url && <a href={record.source_url} target="_blank" rel="noreferrer">{t('profile.openSource')}</a>}
            </div>
          ))}
          {(agent.evaluation?.external_baseline?.records ?? []).length === 0 && (
            <p className="muted">{t('profile.noExternalMatch')}</p>
          )}
        </CollapsibleList>
        <h3>{t('profile.sourcesChecked')}</h3>
        <CollapsibleList initialCount={4} className="external-sources compact-list">
          {(agent.evaluation?.external_baseline?.sources_checked ?? []).map((source: any) => (
            <div className="external-source" key={source.source_id}>
              <div className="row-meta">
                <span className="badge dim">{source.benchmark}</span>
                <span className="muted">{source.authority}</span>
              </div>
              <div className="muted">
                {t('profile.retrievedAt')} {source.retrieved_at?.slice(0, 10)} · {t('profile.freshUntil')} {source.fresh_until?.slice(0, 10)}
              </div>
              <a href={source.source_url} target="_blank" rel="noreferrer">{t('profile.openSource')}</a>
            </div>
          ))}
        </CollapsibleList>
      </section>

      <section className="panel">
        <h2>{t('profile.decisionProfile')}</h2>
        <p className="muted">{t('profile.decisionHint')}</p>
        {agent.decision_profile?.coverage && (
          <div className="row-meta profile-coverage">
            <span className="badge">{t('profile.coverage')}: {agent.decision_profile.coverage.domain_count ?? 0}</span>
            <span className="badge dim">{t('profile.coveragePublic')}: {(agent.decision_profile.coverage.domains_with_public_prior ?? []).length}</span>
            <span className="badge dim">{t('profile.coverageExact')}: {agent.decision_profile.coverage.exact_model_observation_count ?? 0}</span>
            <span className="badge dim">{t('profile.coverageApiCost')}: {(agent.decision_profile.coverage.domains_with_api_cost ?? []).length}</span>
            <span className="badge dim">{t('profile.coverageExecutionCost')}: {(agent.decision_profile.coverage.domains_with_execution_cost ?? []).length}</span>
            <span className="badge dim">{t('profile.coverageShell')}: {agent.decision_profile.coverage.agent_shell_evidence_count ?? 0}</span>
          </div>
        )}
        {agent.decision_profile?.decision_summary?.measured_domains?.length > 0 && (
          <p className="muted">
            {t('profile.measuredDomains')}: {agent.decision_profile.decision_summary.measured_domains
              .map((domain: string) => t(`profile.domain.${domain}`))
              .join(' · ')}
          </p>
        )}
        <CollapsibleList initialCount={3} className="external-sources">
          {(agent.decision_profile?.capabilities ?? []).map((capability: any) => {
            const quality = capability.quality ?? {}
            const base = capability.base_prior ?? {}
            const local = capability.local_experience ?? {}
            const cost = capability.cost ?? {}
            const apiEquivalent = cost.api_equivalent_task_cost ?? {}
            const executionTask = cost.execution_task_cost ?? {}
            const summaryCost = executionTask.expected ?? apiEquivalent.expected
            const summaryCostKind = executionTask.expected !== null && executionTask.expected !== undefined
              ? (executionTask.cash_cost_status === 'measured' ? t('profile.localMeasured') : t('profile.executionEstimate'))
              : apiEquivalent.expected !== null && apiEquivalent.expected !== undefined
                ? t('profile.apiEquivalentEstimate')
                : t('profile.unknownCost')
            const summaryCostToAccept = cost.execution_cost_to_accept ?? cost.api_equivalent_cost_to_accept
            return (
              <div className="external-source" key={capability.domain}>
                <div className="title-row compact">
                  <h3>{t(`profile.domain.${capability.domain}`)}</h3>
                  <span className={`badge ${quality.score === null || quality.score === undefined ? 'dim' : ''}`}>
                    {quality.score === null || quality.score === undefined
                      ? t('profile.noEvidence')
                      : `${t('profile.decisionQuality')} ${valueOrDash(quality.score, 3)}`}
                  </span>
                </div>
                <dl className="facts">
                  <div>
                    <dt>{t('profile.basePrior')}</dt>
                    <dd>{base.score === null || base.score === undefined
                      ? '—'
                      : `${valueOrDash(base.score, 3)} · ${base.match_strength ?? base.status}`}</dd>
                  </div>
                  <div>
                    <dt>{t('profile.localPosterior')}</dt>
                    <dd>{local.quality === null || local.quality === undefined
                      ? '—'
                      : `${valueOrDash(local.quality, 3)} · n=${local.sample_count ?? 0}, ${t('profile.effectiveN')}=${valueOrDash(local.effective_sample_size, 2)}`}</dd>
                  </div>
                  <div>
                    <dt>{t('profile.durationP50')}</dt>
                    <dd>{secondsOrDash(local.duration_s?.p50)}</dd>
                  </div>
                  <div>
                    <dt>{t('profile.intervention')}</dt>
                    <dd>{valueOrDash(capability.intervention, 2)}</dd>
                  </div>
                  <div>
                    <dt>{t('profile.taskCostSummary')}</dt>
                    <dd>{moneyOrDash(summaryCost)} · {summaryCostKind}</dd>
                  </div>
                  <div>
                    <dt>{t('profile.costToAcceptSummary')}</dt>
                    <dd>{moneyOrDash(summaryCostToAccept)}</dd>
                  </div>
                  <div>
                    <dt>{t('profile.timeToAccept')}</dt>
                    <dd>{secondsOrDash(cost.time_to_accept_s)}</dd>
                  </div>
                </dl>
                <p className="muted">
                  {t(`profile.coverageStatus.${capability.evidence_coverage?.status ?? 'missing'}`)}
                  {capability.evidence_coverage?.missing?.length
                    ? ` · ${t('profile.coverageMissing')}: ${capability.evidence_coverage.missing.map((key: string) => t(`profile.coverageMissing.${key}`)).join(', ')}`
                    : ''}
                </p>
                {summaryCost === null || summaryCost === undefined
                  ? cost.unknown_reason && <p className="muted">{t(`profile.unknown.${cost.unknown_reason}`)}</p>
                  : null}
              </div>
            )
          })}
        </CollapsibleList>
        <p className="actions cost-detail-link">
          <Link className="button-link" to={`/cost?agent_id=${encodeURIComponent(agent.agent_id)}`}>
            {t('profile.viewCostDetails')} →
          </Link>
        </p>
      </section>

      <section className="panel">
        <div className="title-row">
          <h2>{t('rating.title')}</h2>
          {!showForm && <button className="secondary" onClick={startForm}>{t('rating.rate')}</button>}
        </div>
        {rating.count === 0 && !showForm && <p className="muted">{t('rating.none')}</p>}
        {rating.count > 0 && !showForm && (
          <dl className="facts">
            {DIMENSIONS.map((dim) => (
              <div key={dim}>
                <dt>{t(`rating.dim.${dim}`)}</dt>
                <dd><Stars value={Math.round(rating.dimensions[dim])} /></dd>
              </div>
            ))}
            <div>
              <dt>{t('rating.overall')}</dt>
              <dd><Stars value={Math.round(rating.overall_preference)} /></dd>
            </div>
            <div>
              <dt>{t('rating.count')}</dt>
              <dd>{rating.count}</dd>
            </div>
          </dl>
        )}
        {showForm && (
          <div>
            <label className="field">
              {t('rating.episode')}
              <select value={episodeId} onChange={(e) => setEpisodeId(e.target.value)}>
                {episodeOptions.length === 0 && <option value="">—</option>}
                {episodeOptions.map((id) => {
                  const episode = episodeById.get(id)
                  return <option key={id} value={id}>{episode ? `${optionLabel(episode.request)} · ${id}` : id}</option>
                })}
              </select>
            </label>
            {DIMENSIONS.map((dim) => (
              <div key={dim} className="rating-row">
                <span className="rating-label">{t(`rating.dim.${dim}`)}</span>
                <Stars value={dimensions[dim]} onChange={(v) => setDimensions({ ...dimensions, [dim]: v })} />
              </div>
            ))}
            <div className="rating-row">
              <span className="rating-label">{t('rating.overall')}</span>
              <Stars value={overall} onChange={setOverall} />
            </div>
            <label className="field">
              {t('rating.useAgain')}
              <select value={wouldUseAgain} onChange={(e) => setWouldUseAgain(e.target.value)}>
                <option value="">—</option>
                <option value="yes">{t('rating.useYes')}</option>
                <option value="maybe">{t('rating.useMaybe')}</option>
                <option value="no">{t('rating.useNo')}</option>
              </select>
            </label>
            <label className="field">
              {t('rating.comment')}
              <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
            </label>
            {error && <div className="error">{error}</div>}
            <div className="actions">
              <button onClick={saveRating} disabled={ratingMutation.isPending}>
                {ratingMutation.isPending ? t('common.loading') : t('rating.save')}
              </button>
              <button className="secondary" onClick={() => setShowForm(false)}>{t('replay.cancel')}</button>
            </div>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>{t('profile.recentMarks')}</h2>
        {agent.recent_marks.length === 0 && <p className="muted">{t('profile.noMarks')}</p>}
        <CollapsibleList initialCount={8}>
          {agent.recent_marks.map((mark: any) => (
            <div key={mark.mark_id} className="row">
              <div className="row-meta">
                <span className="badge">{mark.mark}</span>
                <span className="badge dim">{mark.episode_id}</span>
                <span className="muted">{formatLocalDateTime(mark.created_at)}</span>
              </div>
              <ExpandableText text={mark.note} className="muted" lines={3} threshold={180} />
            </div>
          ))}
        </CollapsibleList>
      </section>
      <p className="muted">{t('profile.footer')} {t('rating.footer')}</p>
    </div>
  )
}
