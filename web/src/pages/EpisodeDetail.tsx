import { useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useReplayEvents } from '../hooks/useReplayEvents'
import { useLang } from '../i18n'
import { formatLocalDateTime } from '../lib/dateTime'
import { displayEnum, displayStatus } from '../lib/displayLabel'
import { CollapsibleList } from '../components/CollapsibleList'
import { ExpandableText } from '../components/ExpandableText'

const MODES = ['CLEAN', 'PROJECT_STATE', 'SELECTED_HISTORY']
const MARKS = ['accept', 'edit', 'reject', 'redo']

const MODE_HELP: Record<string, string> = {
  CLEAN: 'replay.modeClean',
  PROJECT_STATE: 'replay.modeProject',
  SELECTED_HISTORY: 'replay.modeHistory',
}

export default function EpisodeDetail() {
  const { t, lang } = useLang()
  const { episodeId } = useParams<{ episodeId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showReplay, setShowReplay] = useState(false)
  const [agent, setAgent] = useState('')
  const [mode, setMode] = useState('CLEAN')
  const [starting, setStarting] = useState(false)
  const [liveStatus, setLiveStatus] = useState<Record<string, string>>({})
  const [liveErrors, setLiveErrors] = useState<Record<string, string>>({})
  const [selected, setSelected] = useState<string[]>([])
  const [markAgent, setMarkAgent] = useState('')
  const [markNote, setMarkNote] = useState('')
  const [markMessage, setMarkMessage] = useState('')
  const [replayError, setReplayError] = useState('')
  const [marking, setMarking] = useState(false)
  const [expandedReplay, setExpandedReplay] = useState<string | null>(null)
  const [replayDetails, setReplayDetails] = useState<Record<string, any>>({})
  const [detailBusy, setDetailBusy] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['episode', episodeId],
    queryFn: () => api.episode(episodeId!),
    enabled: Boolean(episodeId),
  })
  const targetsQuery = useQuery({ queryKey: ['replayTargets'], queryFn: api.replayTargets })

  useReplayEvents((event) => {
    if (event.episode_id !== episodeId || !event.replay_id) return
    if (event.type === 'replay.output' && event.text) {
      setLiveErrors((prev) => ({ ...prev, [event.replay_id as string]: event.text as string }))
    }
    if (event.type === 'replay.status') {
      setLiveStatus((prev) => ({ ...prev, [event.replay_id as string]: event.status as string }))
      if (event.status === 'completed' || event.status === 'failed') {
        queryClient.invalidateQueries({ queryKey: ['episode', episodeId] })
      }
    }
  })

  if (query.isPending) return <div className="empty">{t('common.loading')}</div>
  if (query.isError) return <div className="empty error">{t('detail.notFound')}</div>

  const { episode, replays } = query.data
  const replayTargets = (targetsQuery.data?.targets ?? []).filter((target) => target.available)
  const snapshot = episode.task_start.project_snapshot
  const knownReplayIds = new Set(replays.map((replay) => replay.replay_id))
  const pendingReplays = Object.entries(liveStatus).filter(([replayId]) => !knownReplayIds.has(replayId))
  const markAgents = Array.from(
    new Set([episode.source_agent_revision.agent_id, ...replays.map((r) => r.agent)]),
  )

  async function submitMark(mark: string) {
    if (!markAgent) return
    setMarking(true)
    setMarkMessage('')
    try {
      await api.mark(episodeId!, markAgent, mark, markNote)
      setMarkMessage(`${t('mark.saved')} ${mark}`)
      setMarkNote('')
      queryClient.invalidateQueries({ queryKey: ['episode', episodeId] })
      queryClient.invalidateQueries({ queryKey: ['agents'] })
    } catch (exc) {
      setMarkMessage(String(exc))
    } finally {
      setMarking(false)
    }
  }

  async function startReplay() {
    setStarting(true)
    setReplayError('')
    try {
      const result = await api.startReplay(episodeId!, agent, mode)
      setLiveStatus((prev) => ({ ...prev, [result.replay_id]: 'queued' }))
      setShowReplay(false)
    } catch (exc) {
      setReplayError(String(exc))
    } finally {
      setStarting(false)
    }
  }

  function toggleSelect(replayId: string) {
    setSelected((prev) =>
      prev.includes(replayId)
        ? prev.filter((id) => id !== replayId)
        : [...prev, replayId].slice(-2),
    )
  }

  async function toggleReplayDetail(replayId: string) {
    if (expandedReplay === replayId) {
      setExpandedReplay(null)
      return
    }
    setExpandedReplay(replayId)
    if (replayDetails[replayId]) return
    setDetailBusy(replayId)
    try {
      const result = await api.replayDetail(replayId)
      setReplayDetails((current) => ({ ...current, [replayId]: result }))
    } catch (exc) {
      setReplayDetails((current) => ({ ...current, [replayId]: { error: String(exc) } }))
    } finally {
      setDetailBusy(null)
    }
  }

  return (
    <div>
      <p><Link to="/episodes">← {t('nav.episodes')}</Link></p>
      <div className="title-row">
        <h1>{episode.episode_id}</h1>
        <button onClick={() => setShowReplay(true)}>{t('detail.replay')}</button>
        {selected.length === 2 && (
          <button
            className="secondary"
            onClick={() =>
              navigate(`/episodes/${episodeId}/compare?a=${selected[0]}&b=${selected[1]}`)
            }
          >
            {t('detail.compare')} {selected[0]} vs {selected[1]}
          </button>
        )}
      </div>
      <section className="panel explainer-panel">
        <h2>{t('detail.whatTitle')}</h2>
        <ExpandableText text={t('detail.what')} className="request" lines={3} threshold={180} />
        <div className="row-meta">
          <span className="badge dim">{t('detail.originalFrozen')}</span>
          <span className="badge dim">{t('detail.replayIsolated')}</span>
          <span className="badge dim">{t('detail.noOverwrite')}</span>
        </div>
      </section>
      <div className="row-meta">
        <span className="badge">{episode.source_agent_revision.agent_id}</span>
        <span className="badge">{episode.project_id}</span>
        <span className="muted">{formatLocalDateTime(episode.created_at)}</span>
      </div>

      <section className="panel">
        <h2>{t('detail.task')}</h2>
        <ExpandableText text={episode.task_start.original_user_request} className="request" lines={5} threshold={300} preserveWhitespace />
      </section>

      <section className="panel">
        <h2>{t('detail.startState')}</h2>
        <dl className="facts">
          <dt>{t('detail.project')}</dt><dd className="mono">{snapshot.project_path ?? '—'}</dd>
          <dt>{t('detail.gitRevision')}</dt><dd className="mono">{snapshot.git_revision?.slice(0, 10) ?? '—'}</dd>
          <dt>{t('detail.provider')}</dt><dd>{displayEnum('execution_provider', episode.task_start.execution_provider, lang)}</dd>
        </dl>
      </section>

      <section className="panel">
        <h2>{t('common.replays')} ({replays.length})</h2>
        <p className="muted">{t('detail.replayResultsHelp')}</p>
        <p className="muted">{t('detail.pickTwo')}</p>
        <CollapsibleList initialCount={8}>
          {pendingReplays.map(([replayId, status]) => (
            <div key={replayId} className="row">
              <span className="badge">{replayId}</span>
              <span className="badge">{agent}</span>
              <span className={status === 'failed' ? 'error' : 'muted'}>{displayStatus(status, lang)}</span>
              {liveErrors[replayId] && <span className="error">{liveErrors[replayId]}</span>}
            </div>
          ))}
          {replays.map((replay) => {
            const status = liveStatus[replay.replay_id] ?? replay.status
            const comparable = status === 'completed'
            const open = expandedReplay === replay.replay_id
            const detail = replayDetails[replay.replay_id]
            return (
              <div key={replay.replay_id} className="row replay-result">
                <div className="row-meta">
                  <input
                    type="checkbox"
                    disabled={!comparable}
                    checked={selected.includes(replay.replay_id)}
                    onChange={() => toggleSelect(replay.replay_id)}
                    aria-label={`${t('detail.compare')} ${replay.replay_id}`}
                  />
                  <span className="badge">{replay.replay_id}</span>
                  <span className="badge">{replay.agent}</span>
                  <span className={status === 'completed' ? 'ok' : status === 'failed' ? 'error' : 'muted'}>
                    {displayStatus(status, lang)}
                  </span>
                  <span className="muted">{displayEnum('replay_mode', replay.mode, lang)} · {(replay.duration_ms / 1000).toFixed(1)}{lang === 'zh' ? '秒' : 's'}</span>
                  <button className="secondary mini" onClick={() => toggleReplayDetail(replay.replay_id)}>
                    {open ? t('common.collapse') : t('detail.viewOutput')}
                  </button>
                </div>
                {liveErrors[replay.replay_id] && <div className="error">{liveErrors[replay.replay_id]}</div>}
                {open && (
                  <div className="replay-evidence">
                    {detailBusy === replay.replay_id && <div className="muted">{t('common.loading')}</div>}
                    {detail?.error && <div className="error">{detail.error}</div>}
                    {detail && !detail.error && (
                      <>
                        <dl className="facts compact-facts">
                          <dt>{t('replay.restore')}</dt><dd>{detail.replay?.restore?.restored ?? detail.replay?.restore?.error ?? '—'}</dd>
                          <dt>{t('replay.contextMode')}</dt><dd>{displayEnum('replay_mode', detail.replay?.context_bundle?.mode, lang)}</dd>
                          <dt>{t('detail.exitCode')}</dt><dd>{detail.replay?.exit_code ?? '—'}</dd>
                        </dl>
                        <h3>{t('compare.output')}</h3>
                        <ExpandableText text={detail.stdout_tail || t('compare.noOutput')} className="stdout" lines={12} threshold={900} preserveWhitespace />
                        {detail.stderr_tail && <><h3>{t('detail.errorOutput')}</h3><ExpandableText text={detail.stderr_tail} className="stdout error" lines={10} threshold={700} preserveWhitespace /></>}
                        <h3>{t('compare.diff')}</h3>
                        <ExpandableText text={detail.workspace_diff || t('compare.noChanges')} className="diff" lines={14} threshold={1200} preserveWhitespace />
                      </>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </CollapsibleList>
      </section>

      <section className="panel">
        <h2>{t('mark.title')}</h2>
        <label className="field">
          {t('mark.agent')}
          <select value={markAgent} onChange={(e) => setMarkAgent(e.target.value)}>
            <option value="">—</option>
            {markAgents.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
        </label>
        <div className="actions">
          {MARKS.map((mark) => (
            <button
              key={mark}
              className="secondary"
              disabled={!markAgent || marking}
              onClick={() => submitMark(mark)}
            >
              {mark}
            </button>
          ))}
        </div>
        <label className="field" style={{ marginTop: 12 }}>
          {t('mark.note')}
          <input value={markNote} onChange={(e) => setMarkNote(e.target.value)} />
        </label>
        {markMessage && <div className="muted">{markMessage}</div>}
      </section>

      {showReplay && (
        <div className="overlay" onClick={() => setShowReplay(false)}>
          <div className="dialog" onClick={(event) => event.stopPropagation()}>
            <h2>{t('detail.replay')} {episode.episode_id}</h2>
            <div className="replay-plan">
              <strong>{t('replay.beforeTitle')}</strong>
              <ol>
                <li>{t('replay.beforeRestore')}</li>
                <li>{t('replay.beforeContext')}</li>
                <li>{t('replay.beforeRun')}</li>
                <li>{t('replay.beforeSave')}</li>
              </ol>
              <p className="muted">{t('replay.budgetWarning')}</p>
            </div>
            {replayError && <div className="error">{replayError}</div>}
            <label className="field">
              {t('replay.agent')}
              <select value={agent} onChange={(event) => setAgent(event.target.value)}>
                <option value="">—</option>
                {replayTargets.map((target) => (
                  <option key={target.agent_type} value={target.agent_type}>{target.display_name} ({target.agent_type})</option>
                ))}
              </select>
            </label>
            <label className="field">
              {t('replay.contextMode')}
              <select value={mode} onChange={(event) => setMode(event.target.value)}>
                {MODES.map((m) => <option key={m} value={m}>{displayEnum('replay_mode', m, lang)}</option>)}
              </select>
              <span className="muted">{t(MODE_HELP[mode])}</span>
            </label>
            {agent && <div className="replay-confirm-summary">
              <strong>{t('replay.willRun')}</strong>: {agent} · {displayEnum('replay_mode', mode, lang)} · {t('replay.isolatedWorkspace')}
            </div>}
            <div className="actions">
              {replayTargets.length === 0 && <p className="muted">{t('replay.noAgents')}</p>}
              <button onClick={startReplay} disabled={starting || !agent}>
                {starting ? t('replay.starting') : t('replay.start')}
              </button>
              <button className="secondary" onClick={() => setShowReplay(false)}>{t('replay.cancel')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
