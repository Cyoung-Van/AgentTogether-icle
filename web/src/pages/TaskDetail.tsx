import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang, type Lang } from '../i18n'
import { AgentPicker } from '../components/AgentPicker'
import { TaskPath } from '../components/HowItWorks'
import { CollapsibleList } from '../components/CollapsibleList'
import { ExpandableText } from '../components/ExpandableText'
import { formatLocalDateTime } from '../lib/dateTime'
import { ensureLlm } from '../lib/ensureLlm'
import { displayEnum, displayStatus, strategyHelp, templateName, templatePhase } from '../lib/displayLabel'
import type { AgentTaskReport, TaskInfo, TaskProfile, TaskReportEntry, TaskStep } from '../types'

// Fixed standards (mirror of src/icle/task.py enums — UI-22).
const TASK_TYPES = ['CODING', 'RESEARCH', 'ANALYSIS', 'WRITING', 'PLANNING', 'DATA', 'SYSTEM_OPERATION', 'MULTIMODAL', 'OTHER']
const SUBTYPES: Record<string, string[]> = {
  CODING: ['implementation', 'debugging', 'refactor', 'review', 'testing', 'architecture', 'integration'],
  RESEARCH: ['search', 'literature', 'comparison', 'verification', 'synthesis'],
  PLANNING: ['project', 'architecture', 'workflow', 'decision', 'decomposition'],
}
const DIFFICULTIES = ['D1', 'D2', 'D3', 'D4', 'D5']
const RISKS = ['R0', 'R1', 'R2', 'R3']
const CONTEXTS = ['LOW', 'MEDIUM', 'HIGH']
const DECOMPOSITIONS = ['not_recommended', 'recommended', 'required']
const REVIEW_RECS = ['not_recommended', 'recommended', 'required']
const DURATIONS = ['short', 'medium', 'long', 'unknown']
const TOOLS = ['filesystem', 'shell', 'git', 'web', 'mcp', 'none']
const STRATEGIES = ['DIRECT', 'PLAN_FIRST', 'DECOMPOSE', 'AUTHOR_REVIEWER', 'PARALLEL_COMPARE', 'HANDOFF']
const STEP_TYPES = ['analysis', 'design', 'implementation', 'review', 'verification', 'research', 'documentation', 'other']
const CONTEXT_POLICIES = ['CLEAN', 'PROJECT_STATE', 'ARTIFACT_ONLY']
// Strategy selector help (plan §12) — shown next to the strategy picker.
const STRATEGY_HELP: Record<string, string> = {
  DIRECT: 'Task → Agent. For D1/D2: single step, no planning.',
  PLAN_FIRST: 'Task → Plan → User Approval → Agent. For D3+.',
  DECOMPOSE: 'Task → Step 1 → Step 2 → Step 3. Each step may use a different agent.',
  AUTHOR_REVIEWER: 'Agent A → Artifact → Agent B review → A revision.',
  PARALLEL_COMPARE: 'A and B in parallel → compare. For designs/decisions/high-uncertainty.',
  HANDOFF: 'Agent A → artifact-only handoff → Agent B. Never private memory transfer.',
}

export default function TaskDetail() {
  const { t, lang } = useLang()
  const { taskId } = useParams<{ taskId: string }>()
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.task(taskId!),
    enabled: Boolean(taskId),
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 2000 : false),
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['task', taskId] })

  if (query.isPending) return <div className="empty">{t('common.loading')}</div>
  if (query.isError) return <div className="empty error">{t('tasks.notFound')}</div>
  const task = query.data

  const stage = taskStage(task.status)
  const primary = new Set(primarySections(task.status))
  const sections = [
    { id: 'profile', title: t('task.profile'), node: <ProfileSection task={task} invalidate={invalidate} /> },
    { id: 'plan', title: t('task.plan'), node: <PlanSection task={task} invalidate={invalidate} /> },
    {
      id: 'run',
      title: t('task.run'),
      node: ['approved', 'running', 'failed', 'review', 'accepted'].includes(task.status)
        ? <RunSection task={task} />
        : null,
    },
    {
      id: 'report',
      title: t('report.title'),
      node: ['running', 'failed', 'review', 'accepted'].includes(task.status)
        ? <ReportSection task={task} />
        : null,
    },
    {
      id: 'evaluation',
      title: t('task.evaluation'),
      node: ['review', 'accepted'].includes(task.status)
        ? <EvaluationSection task={task} invalidate={invalidate} />
        : null,
    },
    {
      id: 'subtasks',
      title: t('task.subtasks'),
      node: !task.parent_task_id ? <SubtasksSection task={task} invalidate={invalidate} /> : null,
    },
    { id: 'cost', title: t('task.costComparison'), node: <TaskCostEstimatesSection task={task} /> },
  ]
  const current = sections.filter((section) => primary.has(section.id) && section.node)
  const later = sections.filter((section) => !primary.has(section.id) && section.node)

  return (
    <div className="task-detail">
      <Link to="/tasks" className="page-crumb">{t('nav.tasks')}</Link>
      <header className="page-hero">
        <div className="title-row">
          <h1>{task.title}</h1>
          <span className="badge">{displayStatus(task.status, lang)}</span>
          {task.archived_at && <span className="badge dim">{t('tasks.archived')}</span>}
          {task.episode_id && <Link to={`/episodes/${task.episode_id}`} className="badge">{task.episode_id}</Link>}
          <TaskLifecycleActions task={task} invalidate={invalidate} />
        </div>
        <p className="page-guide">{t(`task.guide.${stage}`)}</p>
        {task.description && (
          <ExpandableText text={task.description} className="request muted" lines={3} threshold={180} preserveWhitespace />
        )}
      </header>

      <TaskPath current={stage} />
      {stage !== 'done' && (
        <div className="next-action" role="status">
          <strong>{t('task.now')}</strong>
          <p>{t(`task.next.${stage}`)}</p>
        </div>
      )}

      <div className="stage-current">
        {current.map((section) => (
          <div key={section.id}>{section.node}</div>
        ))}
      </div>

      {later.length > 0 && (
        <div className="stage-later">
          <h2>{t('task.later')}</h2>
          {later.map((section) => (
            <details key={section.id} className="stage-fold">
              <summary>{section.title}</summary>
              {section.node}
            </details>
          ))}
        </div>
      )}
    </div>
  )
}

type TaskStage = 'profile' | 'plan' | 'run' | 'review' | 'done'

function taskStage(status: string): TaskStage {
  if (status === 'draft') return 'profile'
  if (status === 'profiled' || status === 'planned' || status === 'approved') return 'plan'
  if (status === 'running' || status === 'failed') return 'run'
  if (status === 'review') return 'review'
  return 'done'
}

function primarySections(status: string): string[] {
  switch (status) {
    case 'draft':
      return ['profile']
    case 'profiled':
    case 'planned':
    case 'approved':
      return ['plan']
    case 'running':
    case 'failed':
      return ['run', 'plan']
    case 'review':
      return ['evaluation', 'report']
    default:
      return ['report', 'evaluation']
  }
}

function TaskLifecycleActions({ task, invalidate }: { task: TaskInfo; invalidate: () => void }) {
  const { t } = useLang()
  const queryClient = useQueryClient()
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const action = useMutation({
    mutationFn: async (kind: 'end' | 'archive' | 'restore') => {
      if (kind === 'end') return api.endTask(task.task_id)
      if (kind === 'archive') return api.archiveTask(task.task_id)
      return api.unarchiveTask(task.task_id)
    },
    onSuccess: (_result, kind) => {
      setError('')
      setMessage(t(`tasks.${kind}Done`))
      invalidate()
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: (exc: Error) => {
      setMessage('')
      setError(exc.message)
    },
  })
  const terminal = task.status === 'accepted' || task.status === 'cancelled'
  const run = (kind: 'end' | 'archive' | 'restore') => {
    const key = kind === 'end' ? 'tasks.endConfirm' : kind === 'archive' ? 'tasks.archiveConfirm' : 'tasks.restoreConfirm'
    if (window.confirm(`${t(key)}\n\n${task.title}`)) action.mutate(kind)
  }
  return (
    <>
      {!task.archived_at && !terminal && (
        <button className="secondary mini" disabled={action.isPending || task.status === 'running'}
          title={task.status === 'running' ? t('tasks.runningCannotEnd') : t('tasks.endHelp')}
          onClick={() => run('end')}>
          {t('tasks.end')}
        </button>
      )}
      {!task.archived_at && terminal && (
        <button className="secondary mini" disabled={action.isPending} onClick={() => run('archive')}>
          {t('tasks.archive')}
        </button>
      )}
      {task.archived_at && (
        <button className="secondary mini" disabled={action.isPending} onClick={() => run('restore')}>
          {t('tasks.restore')}
        </button>
      )}
      {message && <span className="ok">{message}</span>}
      {error && <span className="error">{error}</span>}
    </>
  )
}

// ------------------------------------------------------------- profile (UI-22/23)

function profileLabel(field: string, value: string | null | undefined, lang: Lang): string {
  return displayEnum(field, value, lang)
}

function ProfileSection({ task, invalidate }: { task: TaskInfo; invalidate: () => void }) {
  const { t, lang } = useLang()
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<Partial<TaskProfile>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const mutation = useMutation({ mutationFn: () => api.setProfile(task.task_id, form), onSuccess: invalidate })
  const analyze = useMutation({
    mutationFn: () => api.analyzeTask(task.task_id, lang),
    onSuccess: invalidate,
    onError: (exc: Error) => setError(exc.message),
  })

  const profile = task.profile

  function startEdit() {
    setForm(profile ?? {})
    setEditing(true)
  }

  async function save() {
    setBusy(true)
    setError('')
    try {
      await mutation.mutateAsync()
      setEditing(false)
    } catch (exc) {
      setError(String(exc))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <div className="title-row">
        <h2>{t('task.profile')}</h2>
        {!editing && <button className="secondary" onClick={startEdit}>{t('task.editProfile')}</button>}
        {!editing && (
          <button className="secondary" disabled={analyze.isPending} onClick={() => analyze.mutate()}>
            {analyze.isPending ? t('task.aiThinking') : t('task.aiAnalyze')}
          </button>
        )}
      </div>
      {!profile && !editing && <p className="muted">{t('task.noProfile')}</p>}
      {profile && !editing && (
        <dl className="facts">
          <dt>{t('task.type')}</dt>
          <dd>{profileLabel('primary_type', profile.primary_type, lang)}{profile.subtype ? ` / ${profileLabel('subtype', profile.subtype, lang)}` : ''}</dd>
          <dt>{t('task.difficulty')}</dt><dd>{profileLabel('difficulty', profile.difficulty, lang)}</dd>
          <dt>{t('task.risk')}</dt><dd>{profileLabel('risk', profile.risk, lang)}</dd>
          <dt>{t('task.context')}</dt><dd>{profileLabel('context_requirement', profile.context_requirement, lang)}</dd>
          <dt>{t('task.tools')}</dt>
          <dd>{(profile.tool_requirement ?? []).map((tool) => profileLabel('tool_requirement', tool, lang)).join(', ') || '—'}</dd>
          <dt>{t('task.decomposition')}</dt><dd>{profileLabel('decomposition', profile.decomposition, lang)}</dd>
          <dt>{t('task.review')}</dt><dd>{profileLabel('review', profile.review, lang)}</dd>
          <dt>{t('task.duration')}</dt><dd>{profileLabel('estimated_duration', profile.estimated_duration, lang)}</dd>
          <dt>{t('task.reason')}</dt><dd>{profile.reason ?? '—'}</dd>
          <dt>{t('task.source')}</dt>
          <dd>{profileLabel('source', profile.source ?? 'manual', lang)}{profile.source === 'llm' ? ` (${lang === 'zh' ? 'AI 建议' : 'AI suggested'})` : ''}</dd>
        </dl>
      )}
      {editing && (
        <div>
          <label className="field">
            {t('task.type')}
            <select value={form.primary_type ?? ''} onChange={(e) => setForm({ ...form, primary_type: e.target.value })}>
              <option value="">—</option>
              {TASK_TYPES.map((v) => <option key={v} value={v}>{profileLabel('primary_type', v, lang)}</option>)}
            </select>
          </label>
          <label className="field">
            {t('task.subtype')}
            <select value={form.subtype ?? ''} onChange={(e) => setForm({ ...form, subtype: e.target.value })}>
              <option value="">—</option>
              {(SUBTYPES[form.primary_type ?? ''] ?? []).map((v) => <option key={v} value={v}>{profileLabel('subtype', v, lang)}</option>)}
            </select>
          </label>
          <div className="form-grid">
            <label className="field">
              {t('task.difficulty')}
              <select value={form.difficulty ?? ''} onChange={(e) => setForm({ ...form, difficulty: e.target.value })}>
                <option value="">—</option>
                {DIFFICULTIES.map((v) => <option key={v} value={v}>{displayEnum('difficulty', v, lang)}</option>)}
              </select>
            </label>
            <label className="field">
              {t('task.risk')}
              <select value={form.risk ?? ''} onChange={(e) => setForm({ ...form, risk: e.target.value })}>
                <option value="">—</option>
                {RISKS.map((v) => <option key={v} value={v}>{displayEnum('risk', v, lang)}</option>)}
              </select>
            </label>
            <label className="field">
              {t('task.context')}
              <select value={form.context_requirement ?? ''} onChange={(e) => setForm({ ...form, context_requirement: e.target.value })}>
                <option value="">—</option>
                {CONTEXTS.map((v) => <option key={v} value={v}>{profileLabel('context_requirement', v, lang)}</option>)}
              </select>
            </label>
            <label className="field">
              {t('task.decomposition')}
              <select value={form.decomposition ?? ''} onChange={(e) => setForm({ ...form, decomposition: e.target.value })}>
                <option value="">—</option>
                {DECOMPOSITIONS.map((v) => <option key={v} value={v}>{profileLabel('decomposition', v, lang)}</option>)}
              </select>
            </label>
            <label className="field">
              {t('task.review')}
              <select value={form.review ?? ''} onChange={(e) => setForm({ ...form, review: e.target.value })}>
                <option value="">—</option>
                {REVIEW_RECS.map((v) => <option key={v} value={v}>{profileLabel('review', v, lang)}</option>)}
              </select>
            </label>
            <label className="field">
              {t('task.duration')}
              <select value={form.estimated_duration ?? ''} onChange={(e) => setForm({ ...form, estimated_duration: e.target.value })}>
                <option value="">—</option>
                {DURATIONS.map((v) => <option key={v} value={v}>{profileLabel('estimated_duration', v, lang)}</option>)}
              </select>
            </label>
          </div>
          <div className="field">
            {t('task.tools')}
            <div className="tag-grid">
              {TOOLS.map((tool) => (
                <label key={tool} className="tag">
                  <input
                    type="checkbox"
                    checked={(form.tool_requirement ?? []).includes(tool)}
                    onChange={(e) => {
                      const current = form.tool_requirement ?? []
                      setForm({
                        ...form,
                        tool_requirement: e.target.checked ? [...current, tool] : current.filter((x) => x !== tool),
                      })
                    }}
                  />
                  {profileLabel('tool_requirement', tool, lang)}
                </label>
              ))}
            </div>
          </div>
          <label className="field">
            {t('task.reason')}
            <textarea rows={2} value={form.reason ?? ''} onChange={(e) => setForm({ ...form, reason: e.target.value })} />
          </label>
          {error && <div className="error">{error}</div>}
          <div className="actions">
            <button onClick={save} disabled={busy}>{t('task.saveProfile')}</button>
            <button className="secondary" onClick={() => setEditing(false)}>{t('replay.cancel')}</button>
          </div>
        </div>
      )}
    </section>
  )
}

// ------------------------------------------------------------- task cost comparison

function taskMoney(value: unknown): string {
  return typeof value === 'number' ? `$${value.toFixed(6)}` : '—'
}

function taskRate(value: unknown): string {
  return typeof value === 'number' ? `$${value.toFixed(6)}/1M` : '—'
}

function tokenCount(value: unknown): string {
  return typeof value === 'number' ? Math.round(value).toLocaleString() : '—'
}

function TaskCostEstimatesSection({ task }: { task: TaskInfo }) {
  const { t } = useLang()
  const query = useQuery({
    queryKey: ['taskCostEstimates', task.task_id, task.updated_at],
    queryFn: () => api.taskCostEstimates(task.task_id),
  })
  return (
    <section className="panel">
      <div className="title-row compact">
        <h2>{t('task.costComparison')}</h2>
        {query.data?.profile && (
          <span className="badge dim">
            {query.data.profile.primary_type} · {query.data.profile.difficulty}
          </span>
        )}
      </div>
      <p className="muted">
        {query.data?.profile_source === 'rule_preview'
          ? t('task.costRulePreview')
          : t('task.costComparisonHelp')}
      </p>
      {query.isPending && <p className="muted">{t('common.loading')}</p>}
      {query.isError && <p className="muted">{t('task.costComparisonFailed')}</p>}
      <CollapsibleList initialCount={6}>
        {(query.data?.estimates ?? []).map((estimate: any) => {
          const rates = estimate.unit_rates ?? {}
          return (
            <div className="row" key={estimate.agent_id}>
              <div className="title-row compact">
                <div>
                  <strong>{estimate.display_name}</strong>
                  <div className="mono muted">{estimate.agent_id}</div>
                </div>
                <Link className="badge dim" to={`/agent-profile?agent_id=${encodeURIComponent(estimate.agent_id)}`}>
                  {estimate.model ?? t('agents.modelUnknown')}
                </Link>
              </div>
              <div className="row-meta">
                <span className="badge dim">{t('profile.inputRate')} {taskRate(rates.input_uncached)}</span>
                <span className="badge dim">{t('profile.outputRate')} {taskRate(rates.output)}</span>
                {rates.cache_read != null && <span className="badge dim">{t('profile.cacheReadRate')} {taskRate(rates.cache_read)}</span>}
              </div>
              <div className="row-meta">
                <span className="muted">{t('task.estimatedWorkload')}: {tokenCount(estimate.workload?.input_tokens)} in / {tokenCount(estimate.workload?.output_tokens)} out</span>
                <span className="badge">{t('task.apiEquivalentEstimate')} {taskMoney(estimate.api_equivalent_cost)}</span>
                <span className={`badge ${estimate.execution_cost == null ? 'dim' : ''}`}>
                  {t('task.executionEstimate')} {taskMoney(estimate.execution_cost)}
                </span>
              </div>
              <div className="muted small">
                {estimate.execution_cost_status === 'measured'
                  ? t('task.costMeasured')
                  : estimate.execution_cost_status === 'estimated'
                    ? t('task.costEstimated')
                    : t(`profile.unknown.${estimate.unknown_reason ?? 'execution_billing_mode_unresolved'}`)}
                {estimate.pricing_source?.license ? ` · models.dev ${estimate.pricing_source.license}` : ''}
              </div>
            </div>
          )
        })}
      </CollapsibleList>
      {!query.isPending && !query.isError && (query.data?.estimates ?? []).length === 0 && (
        <p className="muted">{t('task.noCostTargets')}</p>
      )}
    </section>
  )
}

// ------------------------------------------------------------- plan (UI-24/25/26/27)

function PlanSection({ task, invalidate }: { task: TaskInfo; invalidate: () => void }) {
  const { t, lang } = useLang()
  const [strategy, setStrategy] = useState(task.plan?.strategy ?? 'PLAN_FIRST')
  const [steps, setSteps] = useState<TaskStep[]>(task.plan?.steps ?? [])
  const [planDirty, setPlanDirty] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [candidates, setCandidates] = useState<any[] | null>(null)
  // provider models for the agent picker: 'provider_id/model_id' runs the step
  // through that provider's API (see task.py _provider_executor_for)
  const providersQuery = useQuery({ queryKey: ['providers'], queryFn: api.providers })
  const localAgentsQuery = useQuery({ queryKey: ['agentDiscovery'], queryFn: api.agentDiscovery })
  const modelOptions: { value: string; label: string }[] = []
  for (const p of providersQuery.data?.providers ?? []) {
    if (p.status !== 'connected') continue
    if (!['openai-compatible', 'local'].includes(p.type)) continue
    if (p.type !== 'local' && !p.configured) continue
    for (const m of p.models) {
      modelOptions.push({ value: `${p.provider_id}/${m.id}`, label: `${p.display_name} — ${m.id}` })
    }
  }

  const save = useMutation({
    mutationFn: () => api.savePlan(task.task_id, strategy, steps),
    onSuccess: (saved) => {
      setPlanDirty(false)
      setStrategy(saved.plan?.strategy ?? strategy)
      setSteps(saved.plan?.steps ?? steps)
      invalidate()
    },
    onError: (exc: Error) => setError(exc.message),
  })
  const llm = useMutation({
    mutationFn: () => api.planLlm(task.task_id, lang),
    onSuccess: (r: any) => {
      setPlanDirty(false)
      if (r.task?.plan) {
        setStrategy(r.task.plan.strategy)
        setSteps(r.task.plan.steps ?? [])
      }
      if (r.candidates?.length > 1) setCandidates(r.candidates)
      invalidate()
    },
    onError: (exc: Error) => setError(exc.message),
  })
  const approve = useMutation({
    mutationFn: () => api.approvePlan(task.task_id),
    onSuccess: (approved) => {
      setPlanDirty(false)
      setStrategy(approved.plan?.strategy ?? strategy)
      setSteps(approved.plan?.steps ?? steps)
      invalidate()
    },
    onError: (exc: Error) => setError(exc.message),
  })
  const saveEdits = useMutation({
    mutationFn: () => api.updatePlanSteps(task.task_id, steps, strategy),
    onSuccess: (saved) => {
      setPlanDirty(false)
      setStrategy(saved.plan?.strategy ?? strategy)
      setSteps(saved.plan?.steps ?? steps)
      invalidate()
    },
    onError: (exc: Error) => setError(exc.message),
  })
  const run = useMutation({
    mutationFn: (agent?: string) => api.executeTask(task.task_id, agent),
    onSuccess: invalidate,
    onError: (exc: Error) => setError(exc.message),
  })
  const replan = useMutation({
    mutationFn: async () => {
      let failure = 'step failed'
      let progress = ''
      try {
        const latest = await api.taskRun(task.task_id)
        const failed = latest.steps.find((step) => step.status === 'failed')
        failure = failed?.stderr_tail || failed?.stdout_tail || failure
        progress = latest.steps.map((step) => `${step.step_id}: ${step.status}`).join('\n')
      } catch {
        // The backend still validates the original plan when no run is available.
      }
      return api.replanLlm(task.task_id, { failure, progress, lang })
    },
    onSuccess: (r: any) => {
      const rp = r.replan
      if (!rp) return
      const kept = (rp.kept_step_ids ?? []).map((sid: string) => {
        const orig = (task.plan?.steps ?? []).find((s) => s.step_id === sid)
        return orig ?? { step_id: sid, title: sid, description: '', type: 'other' as const, context_policy: 'CLEAN', risk: 'R0' }
      })
      const convert = (s: any) => ({
        step_id: String(s.step_id || '').split('-').at(-1) || '',
        title: s.goal || s.step_id, description: s.goal || '', type: 'other' as const,
        context_policy: s.context_policy || 'CLEAN', risk: s.risk || 'R0',
        depends_on: (s.dependencies || []).map((id: string) => id.split('-').at(-1)), required_capabilities: s.required_capabilities || [],
        expected_output: (s.expected_outputs || []).join('\n'),
        verification: s.verification?.type || '',
      })
      setSteps([...kept, ...(rp.replaced_steps ?? []).map(convert), ...(rp.new_steps ?? []).map(convert)])
      setPlanDirty(true)
      setError('')
    },
    onError: (exc: Error) => setError(exc.message),
  })

  const plan = task.plan
  const canEditPlan = !['running', 'review', 'accepted', 'cancelled'].includes(task.status)
  const canExecute = task.status === 'approved' && plan?.status === 'approved' && !planDirty

  useEffect(() => {
    if (planDirty) return
    setStrategy(task.plan?.strategy ?? 'PLAN_FIRST')
    setSteps(task.plan?.steps ?? [])
  }, [task.plan, task.updated_at, planDirty])

  // 执行前选 agent(不接 LLM 的基础功能):null = 用计划默认
  const [showAgentPicker, setShowAgentPicker] = useState(false)

  async function runWithAgent(agent: string | null) {
    setShowAgentPicker(false)
    setBusy(true)
    setError('')
    try {
      await run.mutateAsync(agent ?? undefined)
    } catch (exc) {
      setError(String(exc))
    } finally {
      setBusy(false)
    }
  }

  async function guardLlm(fn: () => void) {
    const ok = await ensureLlm(t)
    if (ok) fn()
  }

  // AI 一键分配:规则/LLM 按能力给任务+子任务分配执行 agent → 提示已分配
  const [assignMsg, setAssignMsg] = useState('')
  const [assignments, setAssignments] = useState<Record<string, string> | null>(null)
  const [assignBusy, setAssignBusy] = useState(false)
  async function assignAgent() {
    setAssignMsg('')
    setAssignments(null)
    setAssignBusy(true)
    try {
      const r = await api.assignAgents(task.task_id)
      const map = r.assignments ?? {}
      setAssignments(map)
      setAssignMsg(r.reason ?? t('task.assigned'))
    } catch (exc) {
      setError(String(exc))
    } finally {
      setAssignBusy(false)
    }
  }

  async function applyAssignments() {
    if (!assignments) return
    setAssignBusy(true)
    setError('')
    try {
      await api.applyAgentAssignments(task.task_id, assignments)
      setAssignments(null)
      setAssignMsg(t('task.assignmentApplied'))
      setPlanDirty(false)
      invalidate()
    } catch (exc) {
      setError(String(exc))
    } finally {
      setAssignBusy(false)
    }
  }

  function addStep() {
    setPlanDirty(true)
    setSteps([...steps, { step_id: '', title: '', description: '', type: 'other', recommended_agent: '', context_policy: 'CLEAN', risk: 'R0' }])
  }
  function updateStep(index: number, patch: Partial<TaskStep>) {
    setPlanDirty(true)
    setSteps(steps.map((s, i) => (i === index ? { ...s, ...patch } : s)))
  }
  function removeStep(index: number) {
    setPlanDirty(true)
    setSteps(steps.filter((_, i) => i !== index))
  }
  function moveStep(index: number, delta: -1 | 1) {
    const target = index + delta
    if (target < 0 || target >= steps.length) return
    setPlanDirty(true)
    const next = [...steps]
    ;[next[index], next[target]] = [next[target], next[index]]
    setSteps(next)
  }

  return (
    <section className="panel">
      <div className="title-row">
        <h2>{t('task.plan')}</h2>
        {!plan && <button className="secondary" disabled={llm.isPending} onClick={() => guardLlm(() => llm.mutate())}>
          {llm.isPending ? t('task.aiThinking') : t('task.aiPlan')}
        </button>}
        {plan && task.status === 'failed' && (
          <button className="secondary" disabled={replan.isPending} onClick={() => guardLlm(() => replan.mutate())}>
            {replan.isPending ? t('task.aiThinking') : t('task.aiReplan')}
          </button>
        )}
        {canExecute && (
          <>
            <button className="secondary" disabled={busy} onClick={() => setShowAgentPicker(true)}>
              {t('task.execute')}
            </button>
            <button className="secondary" disabled={busy || assignBusy} onClick={assignAgent}>
              {assignBusy ? t('task.aiThinking') : t('task.aiAssign')}
            </button>
          </>
        )}
      </div>
      {showAgentPicker && (
        <AgentPicker
          taskTitle={task.title}
          onPick={runWithAgent}
          onCancel={() => setShowAgentPicker(false)}
        />
      )}
      {assignMsg && <p className="muted">{assignMsg}</p>}
      {assignments && (
        <div className="assignment-preview">
          <div className="row-meta">
            <strong>{t('task.assigned')}</strong>
            <button className="secondary" disabled={assignBusy} onClick={applyAssignments}>{t('task.applyAssign')}</button>
          </div>
          {Object.entries(assignments).map(([targetId, agent]) => (
            <div key={targetId} className="row-meta small">
              <span className="mono">{targetId}</span><span>→</span><span className="badge">{agent}</span>
            </div>
          ))}
        </div>
      )}
      {!plan && <p className="muted">{t('task.noPlan')}</p>}

      {/* One executable route picker: candidates are filtered by backend adapters. */}
      {!plan && (
        <RouteOptions
          task={task}
          onPick={(nextSteps) => { setPlanDirty(true); setSteps(nextSteps) }}
          onStrategy={(nextStrategy) => { setPlanDirty(true); setStrategy(nextStrategy) }}
        />
      )}

      {/* Skill plan-task 候选切换(§14:Planner 给语义结构,用户选最终方案) */}
      {candidates && candidates.length > 1 && (
        <div className="candidates-bar">
          {candidates.map((c) => (
            <button key={c.candidate_id} className="secondary"
              onClick={async () => {
                try {
                  const saved = await api.updatePlanSteps(task.task_id, (c.steps ?? []).map((s: any) => ({ ...s, status: undefined })), c.execution_pattern)
                  setPlanDirty(false)
                  setStrategy(saved.plan?.strategy ?? c.execution_pattern)
                  setSteps(saved.plan?.steps ?? [])
                  invalidate()
                  setCandidates(null)
                } catch (exc) {
                  setError(String(exc))
                }
              }}>
              {t('task.candidate')} {c.candidate_id} · {displayEnum('strategy', c.execution_pattern, lang)}
              {c.summary ? ` — ${c.summary}` : ''}
            </button>
          ))}
        </div>
      )}

      <fieldset className="plan-editor-fields" disabled={Boolean(plan) && !canEditPlan}>
      <label className="field">
        {t('task.strategy')}
        <select value={strategy} onChange={(e) => { setPlanDirty(true); setStrategy(e.target.value) }}>
          {STRATEGIES.map((v) => <option key={v} value={v}>{displayEnum('strategy', v, lang)}</option>)}
        </select>
        <span className="muted strategy-help">{strategyHelp(strategy, STRATEGY_HELP[strategy], lang)}</span>
      </label>

      {/* Plan Editor (UI-26): list-style step editing */}
      <div className="list">
        {steps.map((step, index) => (
          <div key={index} className="step-editor">
            <div className="row-meta">
              <span className="badge dim">{index + 1} · {step.step_id || "new"}</span>
              <button className="mini" onClick={() => moveStep(index, -1)} disabled={index === 0}>↑</button>
              <button className="mini" onClick={() => moveStep(index, 1)} disabled={index === steps.length - 1}>↓</button>
              <button className="mini danger" onClick={() => removeStep(index)}>✕</button>
            </div>
            <input value={step.title} placeholder={t('task.stepTitle')} onChange={(e) => updateStep(index, { title: e.target.value })} />
            <input value={step.description} placeholder={t('task.stepDesc')} onChange={(e) => updateStep(index, { description: e.target.value })} />
            <div className="form-grid">
              <label className="field">
                {t('task.agent')}
                <select value={step.recommended_agent} onChange={(e) => updateStep(index, { recommended_agent: e.target.value })}>
                  <option value="">{t('task.chooseAgent')}</option>
                  {(localAgentsQuery.data ?? []).some((a) => a.status === 'linked' && a.execution_supported !== false) && (
                    <optgroup label={t('task.localAgents')}>
                      {(localAgentsQuery.data ?? [])
                        .filter((a) => a.status === 'linked' && a.execution_supported !== false)
                        .map((a) => <option key={a.agent_type} value={a.agent_type}>{a.display_name} · {a.version ?? a.agent_type}</option>)}
                    </optgroup>
                  )}
                  {modelOptions.length > 0 && (
                    <optgroup label={t('task.providerModels')}>
                      {modelOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </optgroup>
                  )}
                  {step.recommended_agent && ![
                    ...(localAgentsQuery.data ?? []).filter((a) => a.status === 'linked' && a.execution_supported !== false).map((a) => a.agent_type),
                    ...modelOptions.map((option) => option.value),
                  ].includes(step.recommended_agent) && (
                    <option value={step.recommended_agent} disabled>{step.recommended_agent} · {t('task.unavailableAgent')}</option>
                  )}
                </select>
              </label>
              <label className="field">
                {t('task.stepType')}
                <select value={step.type} onChange={(e) => updateStep(index, { type: e.target.value })}>
                  {STEP_TYPES.map((v) => <option key={v} value={v}>{displayEnum('step_type', v, lang)}</option>)}
                </select>
              </label>
              <label className="field">
                {t('task.contextPolicy')}
                <select value={step.context_policy} onChange={(e) => updateStep(index, { context_policy: e.target.value })}>
                  {CONTEXT_POLICIES.map((v) => <option key={v} value={v}>{displayEnum('context_policy', v, lang)}</option>)}
                </select>
              </label>
              <label className="field">
                {t('task.risk')}
                <select value={step.risk} onChange={(e) => updateStep(index, { risk: e.target.value })}>
                  {RISKS.map((v) => <option key={v} value={v}>{displayEnum('risk', v, lang)}</option>)}
                </select>
              </label>
            </div>
            <input value={step.verification ?? ''} placeholder={t('task.verification')} onChange={(e) => updateStep(index, { verification: e.target.value })} />
          </div>
        ))}
      </div>
      </fieldset>
      <div className="actions">
        {!plan && (
          <>
            <button
              className="secondary"
              disabled={steps.length === 0 || busy}
              onClick={() => save.mutate()}
            >
              {t('task.savePlan')}
            </button>
            <button className="secondary" onClick={addStep}>+ {t('task.addStep')}</button>
          </>
        )}
        {plan && canEditPlan && (
          <>
            <button className="secondary" onClick={addStep}>+ {t('task.addStep')}</button>
            {planDirty && (
              <button
                className="secondary"
                disabled={steps.length === 0 || saveEdits.isPending}
                onClick={() => saveEdits.mutate()}
              >
                {t('task.saveEdits')}
              </button>
            )}
          </>
        )}
        {plan && (plan.status === 'proposed' || plan.status === 'revised') && !planDirty && (
          <button disabled={approve.isPending} onClick={() => approve.mutate()}>
            {plan.status === 'revised' ? t('task.approveRevisedPlan') : t('task.approvePlan')}
          </button>
        )}
      </div>
      {error && <div className="error">{error}</div>}
      {task.project_path && <p className="muted">{lang === 'zh'
        ? '执行会复制当前文件，包括未提交修改和未跟踪文件；不复制 Git 历史。默认排除 .env、私钥及依赖目录，可通过项目根目录的 .icleignore 增加排除规则。'
        : 'Execution copies current files, including uncommitted and untracked files, without Git history. Environment files, private keys and dependency folders are excluded. Add exclusions in .icleignore at the project root.'}</p>}
      {task.status === 'approved' && <p className="muted">{t('task.reviewNote')}</p>}
    </section>
  )
}

// ------------------------------------------------------------- run (UI-29)

function RunSection({ task }: { task: TaskInfo }) {
  const { t, lang } = useLang()
  const query = useQuery({
    queryKey: ['taskRun', task.task_id],
    queryFn: () => api.taskRun(task.task_id),
    enabled: ['running', 'review', 'failed', 'accepted'].includes(task.status),
    refetchInterval: (q) => (q.state.data?.status === 'running' || task.status === 'running' ? 1500 : false),
  })
  if (!['running', 'review', 'failed', 'accepted'].includes(task.status)) return null
  if (query.isError) {
    return (
      <section className="panel">
        <h2>{t('task.run')}</h2>
        <p className="error">{t('task.noRunYet')}</p>
      </section>
    )
  }
  const run = query.data

  return (
    <section className="panel">
      <h2>{t('task.run')} {run?.run_id ?? ''}</h2>
      {!run && <p className="muted">{t('task.noRunYet')}</p>}
      {run?.workspace_init && <div className="muted">
        {run.workspace_init.snapshot_policy === 'current_worktree' && <p>
          {lang === 'zh' ? '已复制当前工作树' : 'Current working tree copied'}
          {run.workspace_init.source_commit && <> · {run.workspace_init.source_commit.slice(0, 12)}</>}
          {run.workspace_init.source_dirty && <> · {lang === 'zh' ? '含未提交文件' : 'includes uncommitted files'}</>}
        </p>}
        {run.workspace_init.reason && <p className="error">{run.workspace_init.reason}</p>}
        {!!run.workspace_init.excluded_files?.length && <details>
          <summary>{lang === 'zh' ? '未进入工作区的文件或目录' : 'Excluded files or folders'} ({run.workspace_init.excluded_files.length})</summary>
          <pre>{run.workspace_init.excluded_files.join('\n')}</pre>
        </details>}
      </div>}
      {run && (
        <CollapsibleList initialCount={8}>
          {run.steps.map((step) => (
            <div key={step.step_id} className="row">
              <div className="row-meta">
                <span className="badge dim">{step.step_id}</span>
                <span className="badge">{step.agent}</span>
                <span className={step.status === 'completed' ? 'ok' : step.status === 'failed' ? 'error' : 'muted'}>
                  {displayStatus(step.status, lang)}
                </span>
                <span className="muted">{(step.duration_ms / 1000).toFixed(1)}s</span>
              </div>
              <ExpandableText text={step.stdout_tail} className="stdout" lines={10} threshold={800} preserveWhitespace />
              <ExpandableText text={step.stderr_tail} className="stdout error" lines={8} threshold={500} preserveWhitespace />
            </div>
          ))}
        </CollapsibleList>
      )}
    </section>
  )
}

// ------------------------------------------------------------- evaluation form (J evidence)

// Numeric/boolean fields of the fixed form, in the order the agent fills them.
const REPORT_COUNTS = ['requirements_total', 'requirements_met', 'tests_total', 'tests_passed'] as const
const REPORT_USAGE = ['input_tokens', 'output_tokens', 'cache_read_tokens', 'reasoning_tokens'] as const
const REPORT_FLAGS = ['verification_ran', 'verification_passed', 'blocked'] as const

function hasConsumption(report?: AgentTaskReport | null) {
  return report?.input_tokens != null && report?.output_tokens != null && report?.duration_s != null
}

function ReportSection({ task }: { task: TaskInfo }) {
  const { t } = useLang()
  const [form, setForm] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState('')
  const enabled = ['review', 'failed', 'accepted'].includes(task.status)
  const query = useQuery({
    queryKey: ['taskReport', task.task_id],
    queryFn: () => api.taskReport(task.task_id),
    enabled,
  })
  const fill = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.fillTaskReport(task.task_id, payload),
    onSuccess: () => { setForm(null); setError(''); query.refetch() },
    onError: (exc: Error) => setError(exc.message),
  })

  if (!enabled) return null
  const view = query.data
  const consolidated = view?.consolidated
  const metrics = Object.entries(consolidated?.metrics ?? {})

  return (
    <section className="panel">
      <div className="title-row">
        <h2>{t('report.title')}</h2>
        {view && task.status !== 'accepted' && (!hasConsumption(consolidated?.report) || consolidated?.report_status !== 'observed') && (
          <button className="secondary" onClick={() => setForm({ ...view.template, ...(consolidated?.report ?? {}) })}>
            {t('report.fillManually')}
          </button>
        )}
      </div>
      <p className="muted">{t('report.explain')}</p>
      {query.isLoading && <p className="muted">{t('common.loading')}</p>}
      {view && (
        <>
          <div className="row-meta">
            <span className="badge dim">{view.report_contract_id}</span>
            <span className={consolidated?.report_status === 'observed' ? 'badge' : 'badge dim'}>
              {t(`report.source.${consolidated?.source ?? 'none'}`)}
            </span>
            <span className="badge dim">
              {view.intelligence_enabled ? t('report.intelligenceOn') : t('report.intelligenceOff')}
            </span>
          </div>
          {metrics.length > 0 ? (
            <div className="row-meta">
              {metrics.map(([id, value]) => (
                <span key={id} className="badge">{id} {value.toFixed(2)}</span>
              ))}
            </div>
          ) : (
            <p className="error">{t('report.noEvidence')}</p>
          )}
          {view && !hasConsumption(consolidated?.report) && task.status !== 'accepted' && (
            <p className="error">{t('report.consumptionRequired')}</p>
          )}
          {consolidated?.report?.summary && (
            <ExpandableText text={consolidated.report.summary} className="muted" lines={3} threshold={200} />
          )}
          <CollapsibleList initialCount={4}>
            {view.entries.map((entry) => (
              <div key={`${entry.task_id}-${entry.step_id}`} className="row">
                <div className="row-meta">
                  <span className="badge dim">{entry.step_id}</span>
                  <span className="badge">{entry.agent}</span>
                  <span className={entry.report_status === 'observed' ? 'ok' : 'error'}>
                    {t(`report.status.${entry.report_status}`)}
                  </span>
                  {entry.report_origin === 'user' && <span className="badge dim">{t('report.byUser')}</span>}
                </div>
                {entry.report && (
                  <div className="row-meta">
                    {[...REPORT_COUNTS, ...REPORT_USAGE].filter((key) => entry.report?.[key] !== undefined).map((key) => (
                      <span key={key} className="badge dim">{key} {String(entry.report?.[key])}</span>
                    ))}
                    {entry.report?.duration_s != null && (
                      <span className="badge dim">duration_s {entry.report.duration_s}</span>
                    )}
                    {REPORT_FLAGS.filter((key) => entry.report?.[key] !== undefined).map((key) => (
                      <span key={key} className="badge dim">{key} {entry.report?.[key] ? '✓' : '✗'}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </CollapsibleList>
          {view.entries.length === 0 && <p className="muted">{t('report.noRunYet')}</p>}
          <BaselineProbe task={task} entries={view.entries} onDone={() => query.refetch()} />
        </>
      )}
      {form && view && (
        <div className="stack">
          {REPORT_COUNTS.map((key) => (
            <label key={key} className="field">
              {key}
              <input
                type="number"
                min={0}
                value={String(form[key] ?? 0)}
                onChange={(event) => setForm({ ...form, [key]: Number(event.target.value) })}
              />
            </label>
          ))}
          {REPORT_USAGE.map((key) => (
            <label key={key} className="field">
              {t(`report.${key}`)}
              <input
                type="number"
                min={0}
                value={form[key] == null ? '' : String(form[key])}
                onChange={(event) => setForm({ ...form, [key]: event.target.value === '' ? null : Number(event.target.value) })}
              />
            </label>
          ))}
          <label className="field">
            {t('report.duration_s')}
            <input
              type="number"
              min={0}
              step="0.1"
              value={form.duration_s == null ? '' : String(form.duration_s)}
              onChange={(event) => setForm({ ...form, duration_s: event.target.value === '' ? null : Number(event.target.value) })}
            />
          </label>
          {REPORT_FLAGS.map((key) => (
            <label key={key} className="field">
              <input
                type="checkbox"
                checked={Boolean(form[key])}
                onChange={(event) => setForm({ ...form, [key]: event.target.checked })}
              />
              {key}
            </label>
          ))}
          <label className="field">
            summary
            <textarea
              rows={2}
              value={String(form.summary ?? '')}
              onChange={(event) => setForm({ ...form, summary: event.target.value })}
            />
          </label>
          <div className="row-meta">
            <button disabled={fill.isPending} onClick={() => fill.mutate(form)}>
              {fill.isPending ? t('common.loading') : t('report.submit')}
            </button>
            <button className="secondary" onClick={() => { setForm(null); setError('') }}>
              {t('common.cancel')}
            </button>
          </div>
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </section>
  )
}

// Matrix A has no public value for most local CLIs, so C cannot be computed.
// The probe re-runs the task on the agent's own bare model to calibrate A
// locally; the engine needs two probed tasks per axis before it trusts it.
function BaselineProbe({
  task,
  entries,
  onDone,
}: {
  task: TaskInfo
  entries: TaskReportEntry[]
  onDone: () => void
}) {
  const { t } = useLang()
  const [error, setError] = useState('')
  const agents = Array.from(new Set(entries.map((e) => e.agent).filter((a) => a && !a.includes('/'))))
  const agent = agents.length === 1 ? agents[0] : ''
  const status = useQuery({
    queryKey: ['baselineProbe', task.task_id, agent],
    queryFn: () => api.baselineProbeStatus(task.task_id, agent),
    enabled: Boolean(agent),
  })
  const probe = useMutation({
    mutationFn: () => api.runBaselineProbe(task.task_id, agent),
    onSuccess: () => { setError(''); status.refetch(); onDone() },
    onError: (exc: Error) => setError(exc.message),
  })

  if (!agent || !status.data) return null
  const { target, probed_task_count: probed, required_task_count: required, ready } = status.data

  return (
    <div className="stack" style={{ marginTop: '0.75rem' }}>
      <div className="title-row">
        <h3>{t('probe.title')}</h3>
        {target.available && (
          <button className="secondary" disabled={probe.isPending} onClick={() => probe.mutate()}>
            {probe.isPending ? t('probe.running') : t('probe.run')}
          </button>
        )}
      </div>
      <p className="muted">{t('probe.explain')}</p>
      {target.available ? (
        <div className="row-meta">
          <span className="badge">{t('probe.baseline')} {target.execution_target}</span>
          <span className={ready ? 'ok' : 'muted'}>
            {t('probe.progress')} {probed}/{required}
          </span>
          {!ready && <span className="muted">{t('probe.needMore')}</span>}
        </div>
      ) : (
        <p className="muted">{t(`probe.reason.${target.reason ?? 'unknown'}`)}</p>
      )}
      {probe.data && (
        <div className="row-meta">
          <span className="badge dim">{probe.data.run_id}</span>
          <span className={probe.data.status === 'completed' ? 'ok' : 'error'}>{probe.data.status}</span>
          {Object.entries(probe.data.report.metrics).map(([id, value]) => (
            <span key={id} className="badge dim">{id} {value.toFixed(2)}</span>
          ))}
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  )
}

// ------------------------------------------------------------- evaluation (UI-30)

function EvaluationSection({ task, invalidate }: { task: TaskInfo; invalidate: () => void }) {
  const { t } = useLang()
  const [error, setError] = useState('')
  const [selectedAgent, setSelectedAgent] = useState('')
  const [accepting, setAccepting] = useState(false)
  const episodeId = task.episode_id
  const runQuery = useQuery({
    queryKey: ['taskRun', 'evaluation', task.task_id],
    queryFn: () => api.taskRun(task.task_id),
    enabled: ['review', 'accepted'].includes(task.status),
  })
  const executionAgents = Array.from(new Set((runQuery.data?.steps ?? []).map((step) => step.agent).filter(Boolean)))
  const effectiveAgent = selectedAgent || (executionAgents.length === 1 ? executionAgents[0] : '')
  const ratings = useQuery({
    queryKey: ['ratings', task.task_id, episodeId],
    queryFn: () => api.ratings(),
    enabled: ['review', 'accepted'].includes(task.status),
  })
  const judgeLlm = useMutation({
    mutationFn: () => api.judgeTaskLlm(task.task_id, effectiveAgent),
    onSuccess: () => {
      invalidate()
      ratings.refetch()
    },
    onError: (exc: Error) => setError(exc.message),
  })

  if (!['review', 'accepted'].includes(task.status)) return null
  const relevant = (ratings.data ?? []).filter(
    (r) => r.episode_id === episodeId || r.episode_id === `task-${task.task_id}`,
  )
  const hasLlmRating = relevant.some(
    (rating) => rating.source === 'llm' && rating.agent_id === effectiveAgent,
  )

  return (
    <section className="panel">
      <div className="title-row">
        <h2>{t('task.evaluation')}</h2>
        {task.status === 'review' && (
          <button className="secondary" disabled={judgeLlm.isPending || !effectiveAgent || hasLlmRating} onClick={() => judgeLlm.mutate()}>
            {judgeLlm.isPending ? t('task.aiThinking') : t('task.llmJudge')}
          </button>
        )}
        {task.status === 'review' && (
          <button
            disabled={!effectiveAgent || accepting}
            onClick={async () => {
              setAccepting(true)
              setError('')
              try { await api.acceptTask(task.task_id, effectiveAgent); invalidate() }
              catch (exc) { setError(String(exc)) }
              finally { setAccepting(false) }
            }}
          >{accepting ? t('common.loading') : t('task.accept')}</button>
        )}
      </div>
      {executionAgents.length > 1 && (
        <label className="field">
          {t('task.agent')}
          <select value={selectedAgent} onChange={(event) => setSelectedAgent(event.target.value)}>
            <option value="">—</option>
            {executionAgents.map((agent) => <option key={agent} value={agent}>{agent}</option>)}
          </select>
        </label>
      )}
      {executionAgents.length === 0 && <p className="muted">{t('task.evalBeforeAccept')}</p>}
      {episodeId && (
        <>
          <p className="muted">
            {t('task.episodeLinked')}{' '}
            <Link to={`/episodes/${episodeId}`}>{episodeId}</Link>{' '}
            {t('task.rateVia')}{' '}
            {executionAgents.map((agent, index) => (
              <span key={agent}>
                {index > 0 ? ' / ' : ''}
                <Link to={`/agent-profile?agent_id=${encodeURIComponent(agent)}`}>{agent}</Link>
              </span>
            ))}
          </p>
          {ratings.isLoading && <p className="muted">{t('common.loading')}</p>}
          <CollapsibleList initialCount={6}>
            {relevant.map((rating) => (
              <div key={rating.rating_id} className="row">
                <div className="row-meta">
                  <span className="badge">{rating.source === 'llm' ? 'LLM' : 'User'}</span>
                  <span className="muted">{formatLocalDateTime(rating.created_at)}</span>
                </div>
                <div className="row-meta">
                  {Object.entries(rating.dimensions).map(([dim, score]) => (
                    <span key={dim} className="badge dim">{dim} {score}/5</span>
                  ))}
                  <span className="badge dim">{t('rating.overall')} {rating.overall_preference}/5</span>
                  <span className="badge dim">{rating.would_use_again}</span>
                </div>
                <ExpandableText text={rating.comment} className="muted" lines={3} threshold={180} />
              </div>
            ))}
          </CollapsibleList>
          {relevant.length === 0 && <p className="muted">{t('task.noRatings')}</p>}
        </>
      )}
      {error && <div className="error">{error}</div>}
    </section>
  )
}

// Route Options (v0.4 §30/§44): fetch executable A/B/C candidates and
// fill the Plan Editor only after the user chooses one.
function RouteOptions({
  task,
  onPick,
  onStrategy,
}: {
  task: TaskInfo
  onPick: (steps: TaskStep[]) => void
  onStrategy: (strategy: string) => void
}) {
  const { t, lang } = useLang()
  const query = useQuery({
    queryKey: ['candidates', task.task_id],
    queryFn: () => api.taskCandidates(task.task_id),
    enabled: Boolean(task.task_id),
  })
  if (query.isPending) return <div className="muted">{t('task.candidatesLoading')}</div>
  if (query.isError) return <div className="muted">{t('task.candidatesFailed')}</div>
  const candidates = query.data?.candidates ?? []

  function pick(candidate: any) {
    onStrategy(candidate.strategy)
    const steps: TaskStep[] = (candidate.steps ?? []).map((step: any, index: number) => ({
      step_id: `S${index + 1}`,
      title: step.title || `Step ${index + 1}`,
      description: step.description || '',
      type: 'other',
      recommended_agent: (candidate.agents ?? [])[index] ?? (candidate.agents ?? [])[0] ?? '',
      context_policy: 'CLEAN',
      risk: 'R1',
      expected_output: '',
      verification: '',
    }))
    onPick(steps)
  }

  return (
    <div className="panel" style={{ marginTop: 0 }}>
      <h2>{t('task.routeOptions')}</h2>
      <div className="list">
        {candidates.map((candidate: any) => (
          <div key={candidate.id} className="row">
            <div className="row-title">
              {candidate.id}. {candidate.label} <span className="badge dim">{displayEnum('strategy', candidate.strategy, lang)}</span>
            </div>
            <div className="row-meta">
              <span className="muted">{candidate.agents.join(' → ')}</span>
              {candidate.cost_quote?.expected != null && <span className="badge dim">${Number(candidate.cost_quote.expected).toFixed(4)}</span>}
              {candidate.cost_quote?.expected == null && <span className="badge dim">{t('cost.unknown')}</span>}
              {candidate.description && <span className="muted">{candidate.description}</span>}
            </div>
            <div className="actions">
              <button className="secondary" onClick={() => pick(candidate)}>{t('task.useRoute')}</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}


// ------------------------------------------------------------- subtasks (v0.5 Task Hierarchy)

type TplRow = { key?: string; title: string; description: string; optional?: boolean; checked: boolean }

// 模板列表由后端 templates.py 提供(GET /api/templates,12 固定模板 + blank)。
// 每个模板 children 带 key/optional:optional 步骤可取消勾选,任意步骤可删除。
type TplMeta = { template_id: string; name: string; children: { key: string; title: string; description: string; optional: boolean }[] }

function SubtasksSection({ task, invalidate }: { task: TaskInfo; invalidate: () => void }) {
  const { t, lang } = useLang()
  const children = useQuery({
    queryKey: ['taskChildren', task.task_id],
    queryFn: () => api.taskChildren(task.task_id),
  })
  const templates = useQuery({
    queryKey: ['templates', 'all'],
    // Keep all 12 fixed templates visible. The analyzer recommends the best
    // fit, but filtering must not make the rest appear deleted.
    queryFn: () => api.listTemplates(),
  })
  const localAgents = useQuery({ queryKey: ['agentDiscovery'], queryFn: api.agentDiscovery })
  const [showDialog, setShowDialog] = useState(false)
  const [rows, setRows] = useState<TplRow[]>([{ title: '', description: '', checked: true }])
  const [templateId, setTemplateId] = useState<string>('')
  const [recommendation, setRecommendation] = useState<any>(null)
  const [submitting, setSubmitting] = useState(false)
  const [aiBusy, setAiBusy] = useState(false)
  const [aiStage, setAiStage] = useState<'propose' | 'pick' | 'refine' | null>(null)
  const [candidates, setCandidates] = useState<any[]>([])
  const [refiningId, setRefiningId] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [error, setError] = useState('')

  async function guardLlm(fn: () => void) {
    const ok = await ensureLlm(t)
    if (ok) fn()
  }

  const create = useMutation({
    mutationFn: () => api.createTaskChildren(task.task_id, rows.filter((r) => r.checked !== false && r.title.trim()).map((r) => ({ title: r.title, description: r.description }))),
    onSuccess: () => {
      setShowDialog(false)
      setRows([{ title: '', description: '', checked: true }])
      setAiStage(null)
      invalidate()
      children.refetch()
    },
    onError: (exc: any) => setError(String(exc)),
  })

  // 阶段1:LLM 自己规划两套不同维度的拆分雏形
  async function aiPropose() {
    setAiBusy(true)
    setError('')
    setAiStage('propose')
    try {
      const r = await api.splitProposal(task.task_id, lang)
      const proposal = r.proposal
      if (proposal?.needs_split === false) {
        setError(t('task.aiNoSplit') + ': ' + (proposal.reason ?? ''))
        setAiStage(null)
        return
      }
      const cands = proposal?.candidates ?? []
      if (cands.length === 0) {
        setError(t('task.aiNoSplit'))
        setAiStage(null)
        return
      }
      setCandidates(cands)
      setAiStage('pick')
    } catch (exc) {
      setError(String(exc))
      setAiStage(null)
    } finally {
      setAiBusy(false)
    }
  }

  // 阶段2:精细化用户选中的一套雏形
  async function aiRefine(candidate: any) {
    setRefiningId(candidate.candidate_id)
    setError('')
    try {
      const r = await api.splitRefine(task.task_id, candidate, lang)
      const childrenList = r.proposal?.children ?? []
      if (childrenList.length === 0) {
        setError(t('task.aiNoSplit'))
        return
      }
      setRows(childrenList.map((c: any) => ({ title: c.title ?? '', description: c.description ?? '', checked: true })))
      setAiStage('refine')
      setShowDialog(true)
    } catch (exc) {
      setError(String(exc))
    } finally {
      setRefiningId(null)
    }
  }

  // 打开手动拆分对话框:并行拉推荐,默认选中推荐模板(置信度最高非 blank)
  async function openManualDialog() {
    setError('')
    setShowDialog(true)
    setAiStage(null)
    try {
      const [templateResult, recommendationResult] = await Promise.all([
        templates.refetch(),
        api.recommendTemplate(task.task_id),
      ])
      const recs = recommendationResult?.recommendations ?? []
      const top = recs.find((r: any) => r.template_id !== 'blank')
      if (top) setRecommendation(top)
      const pick = top?.template_id || 'blank'
      applyTemplate(pick, templateResult.data?.templates ?? [])
    } catch {
      applyTemplate('blank', [])
    }
  }

  // 应用模板到行(blank → 1 空行)
  function applyTemplate(id: string, availableTemplates?: TplMeta[]) {
    setTemplateId(id)
    const meta = availableTemplates ?? (templates.data?.templates ?? [])
    const tpl = meta.find((x: TplMeta) => x.template_id === id)
    if (!tpl || tpl.children.length === 0) {
      setRows([{ title: '', description: '', checked: true }])
      return
    }
    setRows(tpl.children.map((c: { key: string; title: string; description: string; optional: boolean }) => {
      const copy = templatePhase(id, c.key, c.title, c.description, lang)
      return { key: c.key, title: copy.title, description: copy.description, optional: c.optional, checked: true }
    }))
  }

  // 「AI 定制步骤」:LLM 按模板骨架具体化各步骤(不增删 key),回填可编辑
  async function aiCustomize() {
    if (!templateId || templateId === 'blank') return
    setAiBusy(true)
    setError('')
    try {
      const r = await api.planFromTemplate(task.task_id, templateId, lang)
      const proposal = r.proposal
      if (proposal?.fits_template === false) {
        setError(t('task.aiNoFit') + ': ' + (proposal.reason ?? ''))
        return
      }
      const childrenList = proposal?.children ?? []
      if (childrenList.length === 0) {
        setError(t('task.aiNoSplit'))
        return
      }
      setRows(childrenList.map((c: any) => {
        const copy = templatePhase(templateId, c.key, c.title, c.description, lang)
        return { key: c.key, title: copy.title, description: copy.description, optional: c.optional, checked: c.optional !== true }
      }))
      setShowDialog(true)
    } catch (exc) {
      setError(String(exc))
    } finally {
      setAiBusy(false)
    }
  }

  const list = children.data?.children ?? []
  const projection = children.data?.projection ?? { total: 0, completed: 0 }
  const treeReady = task.status === 'approved' && list.length > 0 && list.every((child: any) => child.status === 'approved')

  // 子任务执行:手动选 agent 后执行单个子任务
  const [execChild, setExecChild] = useState<{ taskId: string; title: string } | null>(null)
  const [treeBusy, setTreeBusy] = useState(false)
  const [treeMsg, setTreeMsg] = useState('')
  const [treeAgents, setTreeAgents] = useState<Record<string, string>>({})
  const executionAgents = (localAgents.data ?? []).filter(
    (agent) => agent.status === 'linked' && agent.execution_supported !== false,
  )

  async function runChild(taskId: string, agent: string | null) {
    setExecChild(null)
    try {
      await api.executeTask(taskId, agent ?? undefined)
      children.refetch()
      invalidate()
    } catch (exc) {
      setError(String(exc))
    }
  }

  // 顺序执行:父 → 子1 → 子2(可分别指定 agent)
  async function runTree() {
    setTreeBusy(true)
    setTreeMsg('')
    setError('')
    try {
      const r = await api.executeTree(task.task_id, treeAgents)
      setTreeMsg(r.status === 'completed' ? t('task.treeDone') : t('task.treeFailed') + ': ' + (r.error ?? ''))
      children.refetch()
      invalidate()
    } catch (exc) {
      setError(String(exc))
    } finally {
      setTreeBusy(false)
    }
  }

  return (
    <section className="panel">
      <div className="title-row">
        <div className="subtasks-head">
          <button className="caret-btn" aria-label={collapsed ? t('common.expand') : t('common.collapse')}
            title={collapsed ? t('common.expand') : t('common.collapse')}
            onClick={() => setCollapsed((prev) => !prev)}>
            {collapsed ? '▸' : '▾'}
          </button>
          <h2 onClick={() => setCollapsed((prev) => !prev)} style={{ cursor: 'pointer' }}>{t('task.subtasks')}</h2>
          {projection.total > 0 && (
            <span className="muted">{projection.completed} / {projection.total} {t('task.subtasksDone')}</span>
          )}
        </div>
        {projection.total > 0 && (
          <button className="secondary" disabled={treeBusy || !treeReady} onClick={runTree}>
            {treeBusy ? t('task.aiThinking') : t('task.treeRun')}
          </button>
        )}
        <button className="secondary" disabled={aiBusy} onClick={() => guardLlm(() => aiPropose())}>
          {aiBusy ? t('task.aiThinking') : t('task.aiSplit')}
        </button>
        <button className="secondary" onClick={openManualDialog}>
          {t('task.addSubtasks')}
        </button>
      </div>

      {/* error 在 section 级显示:AI 拆分失败/无需拆分时对话框未打开也能看到 */}
      {error && <div className="error">{error}</div>}

      {!collapsed && aiStage === 'pick' && (
        <div className="card">
          <h3>{t('task.aiPickTitle')}</h3>
          <p className="muted">{t('task.aiPickHint')}</p>
          <div className="split-candidates">
            {candidates.map((candidate: any) => (
              <div key={candidate.candidate_id} className="split-candidate">
                <div className="split-candidate-head">
                  <strong>{candidate.approach}</strong>
                  <button disabled={refiningId !== null}
                    onClick={() => aiRefine(candidate)}>
                    {refiningId === candidate.candidate_id ? t('task.aiThinking') : t('task.aiPickRefine')}
                  </button>
                </div>
                <p className="muted">{candidate.rationale}</p>
                <ul className="split-preview">
                  {(candidate.children ?? []).map((child: any, i: number) => (
                    <li key={i}>{child.title}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      )}

      {!collapsed && showDialog && (
        <div className="card">
          <h3>{t('task.splitTitle')}</h3>
          <p className="muted">{t('task.splitHint')}</p>
          {recommendation && (
            <div className="recommend-line">
              <span className="badge">{t('task.recommended')}</span>
              <span>{templateName(recommendation.template_id, recommendation.name, lang)}</span>
              <span className="muted">{(recommendation.confidence * 100).toFixed(0)}%</span>
            </div>
          )}
          <div className="row split-tpl">
            <label className="muted">{t('task.splitTemplate')}</label>
            <select value={templateId}
              onChange={(e) => applyTemplate(e.target.value)}>
              {(templates.data?.templates ?? []).map((tpl: TplMeta) => (
                <option key={tpl.template_id} value={tpl.template_id}>
                  {tpl.template_id === 'blank' ? t('task.tplBlank') : templateName(tpl.template_id, tpl.name, lang)}
                </option>
              ))}
            </select>
            {templateId && templateId !== 'blank' && (
              <button className="secondary" disabled={aiBusy} onClick={() => guardLlm(() => aiCustomize())}>
                {aiBusy ? t('task.aiThinking') : t('task.aiCustomize')}
              </button>
            )}
          </div>
          {rows.map((row, index) => (
            <div key={index} className="row split-row">
              <input className={row.checked === false ? 'dim' : ''} placeholder={t('task.subtaskTitle')}
                value={row.title}
                onChange={(e) => setRows(rows.map((r, i) => (i === index ? { ...r, title: e.target.value } : r)))} />
              <input className={row.checked === false ? 'dim' : ''} placeholder={t('task.subtaskDesc')}
                value={row.description}
                onChange={(e) => setRows(rows.map((r, i) => (i === index ? { ...r, description: e.target.value } : r)))} />
              {row.optional !== undefined && (
                <label className="split-check" title={row.optional ? t('task.optionalStep') : ''}>
                  <input type="checkbox" checked={row.checked}
                    onChange={(e) => setRows(rows.map((r, i) => (i === index ? { ...r, checked: e.target.checked } : r)))} />
                </label>
              )}
              <button className="secondary" disabled={rows.length <= 1}
                onClick={() => setRows(rows.filter((_, i) => i !== index))}>✕</button>
            </div>
          ))}
          <div className="actions">
            <button className="secondary"
              onClick={() => setRows([...rows, { title: '', description: '', checked: true }])}>{t('task.addRow')}</button>
            <button disabled={submitting || rows.filter((r) => r.checked !== false).every((r) => !r.title.trim())}
              onClick={async () => {
                setSubmitting(true)
                setError('')
                try { await create.mutateAsync() }
                finally { setSubmitting(false) }
              }}>
              {submitting ? t('task.submitting') : t('task.createSubtasks')}
            </button>
            <button className="secondary" onClick={() => setShowDialog(false)}>{t('common.cancel')}</button>
          </div>
        </div>
      )}

      {!collapsed && projection.total > 0 && (
        <div className="tree-agent-map panel" style={{ marginTop: 10 }}>
          <div className="muted small">{t('task.agent')} · {t('exec.planDefault')}</div>
          <div className="row-meta tree-agent-row">
            <span className="row-link row-title">{task.title}</span>
            <select
              value={treeAgents[task.task_id] ?? ''}
              onChange={(event) => setTreeAgents((prev) => ({ ...prev, [task.task_id]: event.target.value }))}
            >
              <option value="">{t('exec.planDefault')}</option>
              {executionAgents.map((agent) => <option key={agent.agent_type} value={agent.agent_type}>{agent.display_name}</option>)}
            </select>
          </div>
          {list.map((child: any) => (
            <div key={`tree-agent-${child.task_id}`} className="row-meta tree-agent-row">
              <span className="row-link row-title">{child.title}</span>
              <select
                value={treeAgents[child.task_id] ?? ''}
                onChange={(event) => setTreeAgents((prev) => ({ ...prev, [child.task_id]: event.target.value }))}
              >
                <option value="">{t('exec.planDefault')}</option>
                {executionAgents.map((agent) => <option key={agent.agent_type} value={agent.agent_type}>{agent.display_name}</option>)}
              </select>
            </div>
          ))}
        </div>
      )}
      {!collapsed && (list.length === 0 ? (
        <p className="muted">{t('task.noSubtasks')}</p>
      ) : (
        <div className="subtask-list">
          {list.map((child: any) => (
            <div key={child.task_id} className="row subtask-row">
              <Link to={`/tasks/${child.task_id}`} className="row-link subtask-link">
                <span className={`badge ${child.status === 'accepted' || child.status === 'cancelled' ? 'dim' : ''}`}>{displayStatus(child.status, lang)}</span>
                <span className="row-title">{child.title}</span>
                <span className="muted">{child.sibling_order}</span>
              </Link>
              {child.status === 'approved' && (
                <button className="secondary" onClick={() => setExecChild({ taskId: child.task_id, title: child.title })}>
                  {t('task.execute')}
                </button>
              )}
            </div>
          ))}
        </div>
      ))}
      {treeMsg && <p className="muted">{treeMsg}</p>}
      {execChild && (
        <AgentPicker
          taskTitle={execChild.title}
          onPick={(agent) => runChild(execChild.taskId, agent)}
          onCancel={() => setExecChild(null)}
        />
      )}
    </section>
  )
}
