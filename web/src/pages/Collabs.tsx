import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { displayEnum, displayStatus, templateName, templatePhase } from '../lib/displayLabel'
import { optionLabel } from '../lib/optionLabel'
import { CollapsibleList } from '../components/CollapsibleList'
import { ExpandableText } from '../components/ExpandableText'

type TemplateChild = {
  key: string
  title: string
  description: string
  optional: boolean
}
type Template = {
  template_id: string
  name: string
  children: TemplateChild[]
}

const EMPTY_TARGETS: Awaited<ReturnType<typeof api.executionTargets>> = []

export default function Collabs() {
  const { t, lang } = useLang()
  const list = useQuery({ queryKey: ['collabs'], queryFn: () => api.collabs() })
  const episodes = useQuery({ queryKey: ['episodes', 'collabOptions'], queryFn: () => api.episodes() })
  const targets = useQuery({ queryKey: ['executionTargets'], queryFn: api.executionTargets })
  const templates = useQuery({ queryKey: ['templates', 'collab'], queryFn: () => api.listTemplates() })
  const [mode, setMode] = useState('template')
  const [episodeId, setEpisodeId] = useState('')
  const [agentA, setAgentA] = useState('')
  const [agentB, setAgentB] = useState('')
  const [parallelAgents, setParallelAgents] = useState<string[]>([])
  const [templateId, setTemplateId] = useState('')
  const [phaseEnabled, setPhaseEnabled] = useState<Record<string, boolean>>({})
  const [phaseAgents, setPhaseAgents] = useState<Record<string, string>>({})
  const [recommendation, setRecommendation] = useState<any | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [running, setRunning] = useState(false)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const availableTargets = targets.data ?? EMPTY_TARGETS
  const templateList = ((templates.data?.templates ?? []) as Template[]).filter((item) => item.template_id !== 'blank')
  const selectedTemplate = templateList.find((item) => item.template_id === templateId)

  function applyTemplate(id: string) {
    setTemplateId(id)
    const template = templateList.find((item) => item.template_id === id)
    if (!template) return
    const enabled: Record<string, boolean> = {}
    const assignments: Record<string, string> = {}
    template.children.forEach((phase, index) => {
      enabled[phase.key] = !phase.optional
      if (availableTargets.length > 0) {
        assignments[phase.key] = availableTargets[index % availableTargets.length].agent_id
      }
    })
    setPhaseEnabled(enabled)
    setPhaseAgents(assignments)
  }

  useEffect(() => {
    if (!templateId && templateList.length > 0) applyTemplate(templateList[0].template_id)
    // apply only when template data first arrives; later changes stay user-controlled
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateList.length])

  useEffect(() => {
    if (!selectedTemplate || availableTargets.length === 0) return
    setPhaseAgents((current) => {
      const next = { ...current }
      selectedTemplate.children.forEach((phase, index) => {
        if (!next[phase.key] || !availableTargets.some((target) => target.agent_id === next[phase.key])) {
          next[phase.key] = availableTargets[index % availableTargets.length].agent_id
        }
      })
      return next
    })
  }, [availableTargets, selectedTemplate])

  async function chooseEpisode(id: string) {
    setEpisodeId(id)
    setRecommendation(null)
    if (!id) return
    try {
      const result = await api.collabTemplateRecommendation(id)
      const top = (result.recommendations ?? []).find((item: any) => item.template_id !== 'blank')
      if (top) {
        setRecommendation(top)
        if (mode === 'template') applyTemplate(top.template_id)
      }
    } catch (exc) {
      setError(String(exc))
    }
  }

  async function run() {
    setError('')
    setMessage('')
    setRunning(true)
    try {
      let agentList: string[] = []
      let options: { template_id?: string; assignments?: { key: string; agent: string }[] } | undefined
      if (mode === 'template') {
        const assignments = (selectedTemplate?.children ?? [])
          .filter((phase) => phaseEnabled[phase.key] || !phase.optional)
          .map((phase) => ({ key: phase.key, agent: phaseAgents[phase.key] ?? '' }))
        agentList = assignments.map((item) => item.agent).filter(Boolean)
        options = { template_id: templateId, assignments }
        if (!episodeId || !templateId || assignments.some((item) => !item.agent) || new Set(agentList).size < 2) {
          setError(t('collabs.templateFill'))
          return
        }
      } else {
        agentList = mode === 'parallel' ? parallelAgents : [agentA, agentB].filter(Boolean)
        if (!episodeId || agentList.length < 2 || new Set(agentList).size < 2) {
          setError(t('collabs.fill'))
          return
        }
      }
      const result = await api.runCollab(mode, episodeId, agentList, options)
      const collabId = result.collab?.collab_id
      if (collabId) setExpanded((current) => ({ ...current, [collabId]: true }))
      setMessage(t('collabs.startedDone'))
      await list.refetch()
    } catch (exc) {
      setError(String(exc))
    } finally {
      setRunning(false)
    }
  }

  function toggleParallelAgent(agent: string) {
    setParallelAgents((current) => (
      current.includes(agent) ? current.filter((item) => item !== agent) : [...current, agent]
    ))
  }

  return (
    <div>
      <h1>{t('collabs.title')}</h1>
      <section className="panel explainer-panel">
        <h2>{t('collabs.howTitle')}</h2>
        <ExpandableText text={t('collabs.how')} className="request" lines={3} threshold={220} />
        <div className="row-meta">
          <span className="badge dim">3 {t('collabs.basePatterns')}</span>
          <span className="badge">{templateList.length || 12} {t('collabs.taskTemplates')}</span>
          <span className="badge dim">{availableTargets.length} {t('collabs.realTargets')}</span>
        </div>
      </section>
      <div className="card selection-form">
        <div className="form-grid">
          <label className="field">
            {t('collabs.mode')}
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="template">{displayEnum('collab_mode', 'template', lang)}</option>
              <option value="handoff">{displayEnum('collab_mode', 'handoff', lang)}</option>
              <option value="parallel">{displayEnum('collab_mode', 'parallel', lang)}</option>
              <option value="review">{displayEnum('collab_mode', 'review', lang)}</option>
            </select>
          </label>
          <label className="field">
            {t('collabs.episode')}
            <select value={episodeId} onChange={(event) => chooseEpisode(event.target.value)}>
              <option value="">{t('collabs.chooseEpisode')}</option>
              {(episodes.data?.episodes ?? []).map((episode) => (
                <option key={episode.episode_id} value={episode.episode_id}>
                  {optionLabel(episode.request)} · {episode.project_id} · {episode.episode_id}
                </option>
              ))}
            </select>
          </label>
        </div>

        {mode === 'template' && (
          <div className="template-workflow-builder">
            <div className="title-row compact">
              <label className="field template-select">
                {t('collabs.taskTemplate')}
                <select value={templateId} onChange={(event) => applyTemplate(event.target.value)}>
                  {templateList.map((template) => (
                    <option key={template.template_id} value={template.template_id}>{templateName(template.template_id, template.name, lang)}</option>
                  ))}
                </select>
              </label>
              {recommendation && recommendation.template_id === templateId && (
                <span className="badge">{t('collabs.recommended')} {Math.round(recommendation.confidence * 100)}%</span>
              )}
            </div>
            <p className="muted">{t('collabs.templateHint')}</p>
            <div className="workflow-phases">
              {(selectedTemplate?.children ?? []).map((phase, index) => {
                const enabled = phaseEnabled[phase.key] || !phase.optional
                return (
                  <div key={phase.key} className={`workflow-phase${enabled ? '' : ' disabled'}`}>
                    <div className="workflow-order">{index + 1}</div>
                    <label className="workflow-check">
                      <input
                        type="checkbox"
                        checked={enabled}
                        disabled={!phase.optional}
                        onChange={(event) => setPhaseEnabled((current) => ({ ...current, [phase.key]: event.target.checked }))}
                      />
                    </label>
                    <div className="workflow-copy">
                      <strong>{templatePhase(templateId, phase.key, phase.title, phase.description, lang).title}</strong>
                      <span className="muted">{templatePhase(templateId, phase.key, phase.title, phase.description, lang).description}</span>
                      <span className="muted small">{phase.optional ? t('collabs.optionalPhase') : t('collabs.requiredPhase')}</span>
                    </div>
                    <select
                      aria-label={`${phase.title} ${t('task.agent')}`}
                      disabled={!enabled}
                      value={phaseAgents[phase.key] ?? ''}
                      onChange={(event) => setPhaseAgents((current) => ({ ...current, [phase.key]: event.target.value }))}
                    >
                      <option value="">{t('task.chooseAgent')}</option>
                      {availableTargets.map((target) => (
                        <option key={target.agent_id} value={target.agent_id}>
                          {target.display_name} · {target.kind === 'provider-model' ? t('agents.providerModel') : target.agent_id}
                        </option>
                      ))}
                    </select>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {mode === 'parallel' && (
          <div className="field">
            {t('collabs.agents')}
            <div className="choice-grid">
              {availableTargets.map((target) => (
                <label key={target.agent_id} className="choice-option">
                  <input
                    type="checkbox"
                    checked={parallelAgents.includes(target.agent_id)}
                    onChange={() => toggleParallelAgent(target.agent_id)}
                  />
                  <span>
                    <strong>{target.display_name}</strong>
                    <small>{lang === 'zh' ? (target.description_zh || target.description) : target.description}</small>
                  </span>
                </label>
              ))}
            </div>
          </div>
        )}

        {mode !== 'parallel' && mode !== 'template' && (
          <div className="form-grid">
            <label className="field">
              {mode === 'review' ? t('collabs.author') : t('collabs.agentA')}
              <select value={agentA} onChange={(event) => setAgentA(event.target.value)}>
                <option value="">—</option>
                {availableTargets.map((target) => <option key={target.agent_id} value={target.agent_id}>{target.display_name}</option>)}
              </select>
            </label>
            <label className="field">
              {mode === 'review' ? t('collabs.reviewer') : t('collabs.agentB')}
              <select value={agentB} onChange={(event) => setAgentB(event.target.value)}>
                <option value="">—</option>
                {availableTargets.filter((target) => target.agent_id !== agentA).map((target) => (
                  <option key={target.agent_id} value={target.agent_id}>{target.display_name}</option>
                ))}
              </select>
            </label>
          </div>
        )}

        {availableTargets.length < 2 && <div className="error">{t('collabs.needTargets')}</div>}
        <div className="actions">
          <button disabled={running || availableTargets.length < 2} onClick={run}>
            {running ? t('collabs.running') : t('collabs.reviewRun')}
          </button>
        </div>
        {error && <div className="error">{error}</div>}
        {message && <div className="ok">{message}</div>}
        <p className="muted">{t('collabs.hint')}</p>
      </div>

      <CollapsibleList initialCount={8}>
        {(list.data?.collabs ?? []).map((collab: any, index: number) => {
          const key = collab.collab_id ?? `${collab.created_at}-${index}`
          const open = Boolean(expanded[key])
          return (
            <div key={key} className="row">
              <div className="row-meta">
                <button className="caret-btn" aria-label={open ? t('common.collapse') : t('common.expand')}
                  onClick={() => setExpanded((current) => ({ ...current, [key]: !open }))}>
                  {open ? '▾' : '▸'}
                </button>
                <span className="badge dim">{displayEnum('collab_mode', collab.pattern ?? collab.mode, lang)}</span>
                {collab.template?.name && <span className="badge">{templateName(collab.template.template_id, collab.template.name, lang)}</span>}
                <span className="muted">{collab.episode_id}</span>
                <span className="badge">{displayStatus(collab.status, lang)}</span>
              </div>
              <div className="line-clamp-3">{collab.summary ?? collab.created_at}</div>
              {open && (
                <div className="list collab-evidence">
                  {(collab.steps ?? []).map((step: any, stepIndex: number) => (
                    <div key={`${step.role}-${step.agent}-${stepIndex}`} className="row">
                      <div className="row-meta">
                        <span className="badge dim">{displayEnum('collab_role', step.role, lang)}</span>
                        <span className="badge">{step.agent}</span>
                        <span className={step.status === 'completed' ? 'ok' : 'error'}>{displayStatus(step.status, lang)}</span>
                        <span className="muted">{(step.duration_ms / 1000).toFixed(1)}s</span>
                      </div>
                      <ExpandableText text={step.stdout_tail} className="stdout" lines={10} threshold={800} preserveWhitespace />
                      <ExpandableText text={step.stderr_tail} className="stdout error" lines={8} threshold={500} preserveWhitespace />
                    </div>
                  ))}
                  {collab.review && <ExpandableText text={`${displayEnum('collab_status', collab.review.verdict, lang)}: ${collab.review.notes ?? ''}`} className="muted" lines={4} threshold={260} />}
                  <ExpandableText text={collab.context_note} className="muted" lines={4} threshold={260} />
                </div>
              )}
            </div>
          )
        })}
        {(list.data?.collabs ?? []).length === 0 && <div className="empty">{t('collabs.none')}</div>}
      </CollapsibleList>
    </div>
  )
}
