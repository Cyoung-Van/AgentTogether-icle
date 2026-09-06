// Sessions: 捕获会话浏览 + 会话 → 项目任务(手动 / LLM 批量)
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { useWorkspaceSection } from '../space/useWorkspaceSection'
import { api } from '../api/client'
import { useLang, type Lang } from '../i18n'
import { displayContent } from '../lib/pretty'
import { displayEnum } from '../lib/displayLabel'
import { ProjectPicker } from '../components/ProjectPicker'
import { CollapsibleList } from '../components/CollapsibleList'
import { ExpandableText } from '../components/ExpandableText'

interface SessionSummary {
  session_id: string
  agent_id: string
  events: number
  last_event?: string
}
interface SessionEvent {
  seq: number
  kind: string
  content: string
  ts?: string
}

function kindLabel(kind: string, lang: Lang): string {
  const map: Record<string, [string, string]> = {
    user: ['你', 'You'],
    assistant: ['AI', 'AI'],
    tool_call: ['工具', 'Tool'],
    tool_result: ['工具结果', 'Tool result'],
    failure: ['失败', 'Error'],
    meta: ['系统', 'System'],
    usage: ['用量', 'Usage'],
    artifact: ['产物', 'Artifact'],
  }
  const pair = map[kind] ?? [kind, kind]
  return lang === 'zh' ? pair[0] : pair[1]
}

function fmtTime(ts?: string): string {
  if (!ts) return ''
  const num = /^\d+$/.test(ts) ? parseInt(ts, 10) : Date.parse(ts)
  if (Number.isNaN(num)) return ''
  return new Date(num).toLocaleTimeString('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}

function AnalysisProgress({ label, events }: { label: string; events?: number }) {
  const { t } = useLang()
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    const started = Date.now()
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return (
    <div className="analysis-progress" role="status" aria-live="polite">
      <div className="progress-spinner" />
      <div>
        <strong>{label}</strong>
        <div className="muted">
          {events ? `${events} ${t('sessions.events')} · ` : ''}
          {elapsed}s · {t('sessions.progressHint')}
        </div>
      </div>
    </div>
  )
}

export default function Sessions() {
  const { t, lang } = useLang()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const invalidate = () => qc.invalidateQueries({ queryKey: ['sessions'] })

  const list = useQuery<{ agents?: Record<string, SessionSummary[]>; total_events?: number }>({
    queryKey: ['sessions'],
    queryFn: api.listSessions,
  })
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [openAgent, setOpenAgent] = useState<string | null>(null)
  const [openSession, setOpenSession] = useState<string | null>(null)
  const [detail, setDetail] = useState<SessionEvent[] | null>(null)
  const [form, setForm] = useState({ title: '', request: '', project_id: 'default', project_path: '' })
  const [taskPreview, setTaskPreview] = useState<any | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [message, setMessage] = useState<{ kind: string; text: string } | null>(null)
  const [analysis, setAnalysis] = useState<Record<string, any>>({})
  const [analysisBusy, setAnalysisBusy] = useState<string | null>(null)
  const [editReq, setEditReq] = useState<{ key: string; cid: string; text: string } | null>(null)
  const [proposalBusy, setProposalBusy] = useState<string | null>(null)
  const statsQuery = useQuery({ queryKey: ['skillStats'], queryFn: api.skillStats })

  const fb = (mode: string, action: string, sessionId: string, candidateId: string, detail = '') => {
    api.skillFeedback({ skill: 'icle-task-intelligence', mode, action, session_id: sessionId, candidate_id: candidateId, detail })
      .then(() => qc.invalidateQueries({ queryKey: ['skillStats'] }))
      .catch(() => {})
  }

  const analyze = useMutation({
    mutationFn: ({ agent, session }: { agent: string; session: string }) =>
      api.analyzeSession(agent, session, lang),
    onSuccess: (r: any, vars) => {
      setAnalysis((prev) => ({ ...prev, [`${vars.agent}/${vars.session}`]: r.analysis }))
    },
    onError: (exc: any) => setMessage({ kind: 'err', text: String(exc) }),
  })

  const createTask = useMutation({
    mutationFn: () => api.createTaskFromSessions({
      sessions: taskPreview.picks,
      title: form.title,
      description: form.request,
      project_id: form.project_id,
      project_path: form.project_path,
    }),
    onSuccess: (result: any) => {
      setMessage({ kind: 'ok', text: t('sessions.taskCreated') })
      setTaskPreview(null)
      setSelected({})
      invalidate()
      if (result.task?.task_id) navigate(`/tasks/${result.task.task_id}`)
    },
    onError: (exc: any) => setMessage({ kind: 'err', text: String(exc) }),
  })
  const extract = useMutation({
    mutationFn: (picks: any[]) => api.extractTasks({ sessions: picks, project_id: form.project_id || 'default', lang }),
    onSuccess: (r: any) => {
      const proposed: Record<string, any> = {}
      for (const item of r.proposals ?? []) {
        proposed[`${item.agent_id}/${item.session_id}`] = item.analysis
      }
      setAnalysis((current) => ({ ...current, ...proposed }))
      setMessage({
        kind: 'ok',
        text: `${t('sessions.extracted')} ${r.proposal_count ?? 0}${r.errors?.length ? ` · ${r.errors.length} ${t('sessions.failed')}` : ''}`,
      })
    },
    onError: (exc: any) => setMessage({ kind: 'err', text: String(exc) }),
  })

  async function openTaskPreview(picks: { agent_id: string; session_id: string }[]) {
    setPreviewBusy(true)
    setMessage(null)
    try {
      const result = await api.previewSessionTask(picks)
      const preview = result.preview
      setTaskPreview({ ...preview, picks })
      setForm((current) => ({
        ...current,
        title: preview.title ?? '',
        request: preview.description ?? '',
      }))
    } catch (exc) {
      setMessage({ kind: 'err', text: String(exc) })
    } finally {
      setPreviewBusy(false)
    }
  }

  async function openDetail(agent: string, sessionId: string) {
    setOpenAgent(agent)
    setOpenSession(sessionId)
    try {
      const r = await api.sessionDetail(agent, sessionId)
      setDetail(r.events ?? [])
    } catch (exc) {
      setDetail(null)
      setMessage({ kind: 'err', text: String(exc) })
    }
  }

  function togglePick(agent: string, sessionId: string) {
    const key = `${agent}/${sessionId}`
    setSelected((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const selectedPicks = Object.entries(selected)
    .filter(([, v]) => v)
    .map(([key]) => {
      const [agent_id, session_id] = key.split('/')
      return { agent_id, session_id }
    })

  const section = useWorkspaceSection(list.isSuccess)

  if (list.isPending) return <div className="empty">{t('common.loading')}</div>
  if (list.isError) return <div className="empty error">{t('sessions.failed')}</div>
  const agents = list.data?.agents ?? {}

  return (
    <div className="page">
      <header className="page-hero">
        <h1>{t('sessions.title')}</h1>
        <p className="page-guide">{t('sessions.hint')}</p>
      </header>
      <nav className="session-workflow-tabs" aria-label={t('nav.sessions')}>
        <Link to="/sessions#browse" aria-current={section === 'browse' ? 'page' : undefined}>{t('space.section.browse')}</Link>
        <Link to="/sessions#task-preparation" aria-current={section === 'task-preparation' ? 'page' : undefined}>{t('space.section.sessionTask')}</Link>
        <Link to="/sessions#analysis-preparation" aria-current={section === 'analysis-preparation' ? 'page' : undefined}>{t('space.section.sessionAnalysis')}</Link>
      </nav>
      <section id="task-preparation" tabIndex={-1} className="panel workspace-section" aria-labelledby="session-task-heading" hidden={section === 'analysis-preparation'}>
        <h2 id="session-task-heading">{t('space.section.sessionTask')}</h2>
        <p className="page-guide">{t('space.section.sessionTaskHint')}</p>
        <button
          className="secondary"
          disabled={selectedPicks.length === 0 || previewBusy}
          onClick={() => openTaskPreview(selectedPicks)}
        >
          {previewBusy ? t('common.loading') : `${t('sessions.previewTask')} (${selectedPicks.length})`}
        </button>
        <Link to="#browse" className="session-pick-link">{t('space.section.backToList')}</Link>
      </section>
      <section id="analysis-preparation" tabIndex={-1} className="panel workspace-section" aria-labelledby="session-analysis-heading" hidden={section === 'task-preparation'}>
        <h2 id="session-analysis-heading">{t('space.section.sessionAnalysis')}</h2>
        <p className="page-guide">{t('space.section.sessionAnalysisHint')}</p>
        <button
          className="secondary"
          disabled={selectedPicks.length === 0 || !form.project_id.trim() || extract.isPending || analysisBusy !== null}
          onClick={() => {
            if (!window.confirm(`${t('sessions.extractConfirm')} (${selectedPicks.length})`)) return
            extract.mutate(selectedPicks)
          }}
        >
          {extract.isPending ? t('sessions.analyzing') : `${t('sessions.extractBtn')} (${selectedPicks.length})`}
        </button>
        <Link to="#browse" className="session-pick-link">{t('space.section.backToList')}</Link>
      </section>
      <p className="page-guide">{t('sessions.pickHelp')}</p>
      <div className="session-project-picker">
        <ProjectPicker
          projectId={form.project_id}
          projectPath={form.project_path}
          onChange={(projectId, projectPath) => setForm((current) => ({ ...current, project_id: projectId, project_path: projectPath }))}
        />
      </div>
      {statsQuery.data?.stats?.length > 0 && (
        <div className="skill-stats">
          {(statsQuery.data.stats as any[]).map((s) => (
            <span key={`${s.skill}/${s.mode}`} className="muted">
              {t('sessions.skillEval')} {displayEnum('skill_mode', s.mode, lang)}: {s.accept}/{s.edit}/{s.reject}
              {s.accept_rate != null ? ` · ${t('sessions.acceptRate')} ${Math.round(s.accept_rate * 100)}%` : ''}
            </span>
          ))}
        </div>
      )}
      {message && <div className={message.kind === 'ok' ? 'ok' : 'error'}>{message.text}</div>}
      {extract.isPending && <AnalysisProgress label={t('sessions.batchProgress')} />}
      {taskPreview && (
        <div className="card session-task-preview">
          <div className="title-row">
            <h2>{t('sessions.createPreview')} · {taskPreview.count} {t('sessions.sessionsUnit')}</h2>
            <span className="badge dim">{t('sessions.notCreated')}</span>
          </div>
          <div className="preview-sources">
            {(taskPreview.sources ?? []).map((source: any) => (
              <div key={`${source.agent_id}/${source.session_id}`} className="muted">
                <span className="badge dim">{source.agent_id}</span>{' '}
                <span className="mono">{source.session_id}</span> · {source.summary}
              </div>
            ))}
          </div>
          <label className="field">
            {t('newtask.taskTitle')}
            <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </label>
          <label className="field">
            {t('newtask.taskDesc')}
            <textarea rows={6} value={form.request} onChange={(e) => setForm({ ...form, request: e.target.value })} />
          </label>
          <p className="muted">{t('sessions.confirmHint')}</p>
          <div className="actions">
            <button disabled={createTask.isPending || !form.title.trim() || !form.project_id.trim()}
              onClick={() => createTask.mutate()}>
              {createTask.isPending ? t('common.loading') : t('sessions.confirmCreate')}
            </button>
            <button className="secondary" disabled={createTask.isPending} onClick={() => setTaskPreview(null)}>{t('common.cancel')}</button>
          </div>
        </div>
      )}

      <section id="browse" tabIndex={-1} className="workspace-section" aria-labelledby="session-browse-heading">
      <h2 id="session-browse-heading">{t('space.section.browse')}</h2>
      <p className="page-guide">{t('space.section.chooseSessions')}</p>
      {Object.entries(agents).map(([agent, sessions]) => {
        const isCollapsed = collapsed[agent] !== false
        return (
        <div key={agent} className="card">
          <button
            type="button"
            className="agent-head"
            aria-expanded={!isCollapsed}
            onClick={() => setCollapsed((prev) => ({ ...prev, [agent]: !(prev[agent] ?? true) }))}
            title={isCollapsed ? t('sessions.expand') : t('sessions.collapse')}
          >
            <span className="caret">{isCollapsed ? '▸' : '▾'}</span>
            <span className="agent-head-title">{agent}</span>
            <span className="muted">{sessions.length} {t('sessions.sessionsUnit')}</span>
          </button>
          {!isCollapsed && <CollapsibleList initialCount={8} className="session-list">{(sessions as SessionSummary[]).map((s) => {
            const key = `${agent}/${s.session_id}`
            const open = openAgent === agent && openSession === s.session_id
            return (
              <div key={key} className="session-row">
                <label className="muted">
                  <input type="checkbox" checked={!!selected[key]}
                    onChange={() => togglePick(agent, s.session_id)} /> {t('sessions.pick')}
                </label>
                <span className="mono session-id line-clamp-2" title={s.session_id}>{s.session_id}</span>
                <span className="muted">{s.events} {t('sessions.events')}</span>
                <button className="secondary" onClick={() => {
                  if (open) {
                    setOpenAgent(null)
                    setOpenSession(null)
                    setDetail(null)
                  } else {
                    openDetail(agent, s.session_id)
                  }
                }}>
                  {open ? t('common.collapse') : t('sessions.view')}
                </button>
                <button className="secondary" disabled={previewBusy}
                  onClick={() => openTaskPreview([{ agent_id: agent, session_id: s.session_id }])}>
                  {t('sessions.previewOne')}
                </button>
                <button className="secondary" disabled={analysisBusy !== null || extract.isPending}
                  onClick={async () => {
                    setAnalysisBusy(key)
                    try { await analyze.mutateAsync({ agent, session: s.session_id }) }
                    finally { setAnalysisBusy(null) }
                  }}>
                  {analysisBusy === key ? t('sessions.analyzing') : t('sessions.analyze')}
                </button>
                {analysisBusy === key && <AnalysisProgress label={t('sessions.singleProgress')} events={s.events} />}
                {open && (
                  <CollapsibleList initialCount={20} className="timeline">
                    {detail?.map((ev) => {
                      const text = displayContent(ev.content, lang)
                      const isSystem = ev.kind === 'meta' || text.startsWith('[')
                      const bubbleClass = isSystem ? 'bubble-meta'
                        : ev.kind === 'user' ? 'bubble-user'
                        : ev.kind === 'assistant' ? 'bubble-assistant'
                        : 'bubble-meta'
                      return (
                        <div key={ev.seq} className={`bubble ${bubbleClass}`}>
                          <div className="bubble-head">
                            <span className="bubble-who">{kindLabel(ev.kind, lang)}</span>
                            <span className="bubble-seq">#{ev.seq}</span>
                            {!isSystem && <span className="bubble-ts">{fmtTime(ev.ts)}</span>}
                          </div>
                          <ExpandableText text={text} className="prewrap" lines={6} threshold={400} preserveWhitespace />
                        </div>
                      )
                    })}
                  </CollapsibleList>
                )}
                {analysis[key] && (
                  <div className="analysis-panel">
                    <div className="analysis-head">
                      <span>{t('sessions.analysisTitle')}</span>
                      {analysis[key].status === 'insufficient_evidence' && (
                        <span className="muted">{t('sessions.insufficient')}: {analysis[key].reason}</span>
                      )}
                    </div>
                    {(analysis[key].tasks ?? []).map((task: any) => {
                      const akey = `${agent}/${s.session_id}/${task.candidate_id}`
                      // 接受 → 创建 Task(带 TaskProfile,§31 调用链)→ 跳转规划页
                      const accept = async (request: string, action: 'accept' | 'edit' = 'accept') => {
                        const accepted = { ...task, title: task.title, goal: request || task.goal }
                        setProposalBusy(akey)
                        try {
                          const r = await api.createTaskFromProposal(agent, s.session_id, {
                            task: accepted,
                            provenance: analysis[key]?.provenance ?? null,
                            project_id: form.project_id || 'default',
                            project_path: form.project_path,
                          })
                          fb('analyze-session', action, s.session_id, task.candidate_id, request)
                          setMessage({ kind: 'ok', text: t('sessions.taskCreated') })
                          setAnalysis((prev) => ({ ...prev, [key]: { ...prev[key], tasks: (prev[key]?.tasks ?? []).filter((x: any) => x.candidate_id !== task.candidate_id) } }))
                          invalidate()
                          navigate(`/tasks/${r.task?.task_id}`)
                        } catch (exc) {
                          setMessage({ kind: 'err', text: String(exc) })
                        } finally {
                          setProposalBusy(null)
                        }
                      }
                      return (
                        <div key={akey} className="proposal-card">
                          <div className="proposal-title">{task.title}</div>
                          <div className="proposal-meta">
                            {task.task_type && <span className="badge">{displayEnum('primary_type', task.task_type, lang)}</span>}
                            {task.difficulty && <span className="badge dim">{displayEnum('difficulty', task.difficulty, lang)}</span>}
                            {task.risk && <span className="badge dim">{displayEnum('risk', task.risk, lang)}</span>}
                            {task.confidence?.boundary != null && (
                              <span className="muted">{t('sessions.confidence')} {Math.round(task.confidence.boundary * 100)}%</span>
                            )}
                            <span className="muted">{task.boundaries?.start_event} → {task.boundaries?.end_event}</span>
                          </div>
                          <div className="prewrap muted">{task.goal}</div>
                          {task.original_request !== task.final_request && (
                            <div className="prewrap intent">
                              <span className="muted">{t('sessions.original')}: {task.original_request}</span>
                              <span className="muted">{t('sessions.final')}: {task.final_request}</span>
                            </div>
                          )}
                          {task.failures?.length > 0 && (
                            <div className="muted">{t('sessions.failures')}: {task.failures.join(' · ')}</div>
                          )}
                          {editReq && editReq.key === akey ? (
                            <div className="row">
                              <input value={editReq.text}
                                onChange={(e) => setEditReq({ ...editReq, text: e.target.value })} />
                              <button disabled={proposalBusy === akey} onClick={() => { accept(editReq.text, 'edit'); setEditReq(null) }}>{t('sessions.accept')}</button>
                              <button className="secondary" onClick={() => setEditReq(null)}>{t('common.cancel')}</button>
                            </div>
                          ) : (
                            <div className="actions">
                              <button disabled={proposalBusy === akey} onClick={() => accept(task.final_request || task.original_request || task.title)}>
                                {proposalBusy === akey ? t('common.loading') : t('sessions.accept')}
                              </button>
                              <button className="secondary"
                                onClick={() => setEditReq({ key: akey, cid: task.candidate_id, text: task.final_request || task.original_request || task.title })}>
                                {t('sessions.edit')}
                              </button>
                              <button className="secondary"
                                onClick={() => { fb('analyze-session', 'reject', s.session_id, task.candidate_id); setAnalysis((prev) => ({ ...prev, [key]: { ...prev[key], tasks: (prev[key]?.tasks ?? []).filter((x: any) => x.candidate_id !== task.candidate_id) } })) }}>
                                {t('sessions.reject')}
                              </button>
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}</CollapsibleList>}
        </div>
        )
      })}
      {Object.keys(agents).length === 0 && (
        <div className="hero-card">
          <h2>{t('sessions.emptyTitle')}</h2>
          <p>{t('sessions.empty')}</p>
          <Link to="/agents/local" className="link-button">{t('nav.localAgents')}</Link>
        </div>
      )}
      </section>
    </div>
  )
}
