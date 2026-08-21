import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang, type Lang } from '../i18n'
import { formatLocalDateTime } from '../lib/dateTime'
import { displayEnum } from '../lib/displayLabel'
import type { TaskInfo } from '../types'
import { CollapsibleList } from '../components/CollapsibleList'

const TERMINAL = new Set(['accepted', 'cancelled'])

function statusLabel(status: string, lang: Lang): string {
  const map: Record<string, [string, string]> = {
    draft: ['草稿', 'draft'],
    profiled: ['已建档', 'profiled'],
    planned: ['已规划', 'planned'],
    approved: ['已批准', 'approved'],
    running: ['执行中', 'running'],
    review: ['待评审', 'review'],
    accepted: ['已完成', 'accepted'],
    failed: ['失败', 'failed'],
    cancelled: ['已结束', 'ended'],
    needs_input: ['待输入', 'needs input'],
    revised: ['已修订', 'revised'],
  }
  const pair = map[status] ?? [status, status]
  return lang === 'zh' ? pair[0] : pair[1]
}

type Tab = 'active' | 'all' | 'archived'

export default function Tasks() {
  const { t, lang } = useLang()
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('active')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const query = useQuery({ queryKey: ['tasks'], queryFn: api.tasks })
  const refresh = () => qc.invalidateQueries({ queryKey: ['tasks'] })

  const lifecycle = useMutation({
    mutationFn: async ({ task, action }: { task: TaskInfo; action: 'end' | 'archive' | 'restore' }) => {
      if (action === 'end') return api.endTask(task.task_id)
      if (action === 'archive') return api.archiveTask(task.task_id)
      return api.unarchiveTask(task.task_id)
    },
    onSuccess: (_result, vars) => {
      setError('')
      setMessage(t(`tasks.${vars.action}Done`))
      refresh()
    },
    onError: (exc: Error) => {
      setMessage('')
      setError(exc.message)
    },
  })

  if (query.isPending) return <div className="empty">{t('common.loading')}</div>
  if (query.isError) return <div className="empty error">{t('tasks.failed')}</div>

  const tasks = [...query.data.tasks].sort((a, b) =>
    (b.updated_at ?? '').localeCompare(a.updated_at ?? ''),
  )
  const active = tasks.filter((task) => !task.archived_at && !TERMINAL.has(task.status))
  const current = tasks.filter((task) => !task.archived_at)
  const archived = tasks.filter((task) => Boolean(task.archived_at))
  const shown = tab === 'active' ? active : tab === 'archived' ? archived : current

  function perform(task: TaskInfo, action: 'end' | 'archive' | 'restore') {
    const prompt = action === 'end'
      ? t('tasks.endConfirm')
      : action === 'archive'
        ? t('tasks.archiveConfirm')
        : t('tasks.restoreConfirm')
    if (!window.confirm(`${prompt}\n\n${task.title}`)) return
    setError('')
    setMessage('')
    lifecycle.mutate({ task, action })
  }

  const shownIds = new Set(shown.map((task) => task.task_id))
  const childrenMap = new Map<string, TaskInfo[]>()
  for (const task of shown) {
    if (task.parent_task_id && shownIds.has(task.parent_task_id)) {
      const list = childrenMap.get(task.parent_task_id) ?? []
      list.push(task)
      childrenMap.set(task.parent_task_id, list)
    }
  }
  const roots = shown.filter((task) => !task.parent_task_id || !shownIds.has(task.parent_task_id))

  const renderRow = (task: TaskInfo, child = false) => {
    const terminal = TERMINAL.has(task.status)
    const hasChildren = (childrenMap.get(task.task_id) ?? []).length > 0
    const isCollapsed = expanded[task.task_id] === false
    const pendingThis = lifecycle.isPending && lifecycle.variables?.task.task_id === task.task_id
    return (
      <div key={task.task_id} className={`row task-row${child ? ' task-row-child' : ''}`}>
        {!child && hasChildren && (
          <button className="caret-btn"
            aria-label={isCollapsed ? t('common.expand') : t('common.collapse')}
            title={isCollapsed ? t('common.expand') : t('common.collapse')}
            onClick={() => setExpanded((prev) => ({ ...prev, [task.task_id]: isCollapsed }))}>
            {isCollapsed ? '▸' : '▾'}
          </button>
        )}
        <Link to={`/tasks/${task.task_id}`} className="row-link">
          <div className="row-title line-clamp-2">
            {child && <span className="muted">└ </span>}
            {task.title}
          </div>
          <div className="row-meta">
            <span className={`badge ${terminal ? 'dim' : ''}`}>{statusLabel(task.status, lang)}</span>
            {task.archived_at && <span className="badge dim">{t('tasks.archived')}</span>}
            {task.profile && (
              <span className="badge dim">
                {displayEnum('primary_type', task.profile.primary_type, lang)} / {displayEnum('difficulty', task.profile.difficulty, lang)} / {displayEnum('risk', task.profile.risk, lang)}
              </span>
            )}
            {task.strategy && <span className="badge dim">{displayEnum('strategy', task.strategy, lang)}</span>}
            {(task.step_count ?? 0) > 0 && <span className="badge dim">{task.step_count} {lang === 'zh' ? '步' : 'steps'}</span>}
            {task.episode_id && <span className="badge dim">{task.episode_id}</span>}
            <span className="muted mono">{task.project_id}</span>
            <span className="muted">{formatLocalDateTime(task.updated_at)}</span>
          </div>
        </Link>
        <div className="row-actions">
          {!task.archived_at && !terminal && (
            <button className="secondary mini" disabled={pendingThis || task.status === 'running'}
              title={task.status === 'running' ? t('tasks.runningCannotEnd') : t('tasks.endHelp')}
              onClick={() => perform(task, 'end')}>
              {t('tasks.end')}
            </button>
          )}
          {!task.archived_at && terminal && (
            <button className="secondary mini" disabled={pendingThis} onClick={() => perform(task, 'archive')}>
              {t('tasks.archive')}
            </button>
          )}
          {task.archived_at && (
            <button className="secondary mini" disabled={pendingThis} onClick={() => perform(task, 'restore')}>
              {t('tasks.restore')}
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div>
      <header className="page-hero">
        <h1>{t('tasks.title')}</h1>
        <p className="page-guide">{t('tasks.guide')}</p>
      </header>
      <div className="tabs" role="tablist">
        <button aria-selected={tab === 'active'} className={`tab ${tab === 'active' ? 'tab-active' : ''}`} onClick={() => setTab('active')}>
          {t('tasks.tabActive')} ({active.length})
        </button>
        <button aria-selected={tab === 'all'} className={`tab ${tab === 'all' ? 'tab-active' : ''}`} onClick={() => setTab('all')}>
          {t('tasks.tabAll')} ({current.length})
        </button>
        <button aria-selected={tab === 'archived'} className={`tab ${tab === 'archived' ? 'tab-active' : ''}`} onClick={() => setTab('archived')}>
          {t('tasks.tabArchived')} ({archived.length})
        </button>
      </div>
      <p className="muted tab-explanation">
        {tab === 'active' ? t('tasks.activeHelp') : tab === 'all' ? t('tasks.allHelp') : t('tasks.archivedHelp')}
        {tab === 'all' && active.length === current.length && current.length > 0 ? ` ${t('tasks.sameBecauseAllActive')}` : ''}
      </p>
      {message && <div className="ok">{message}</div>}
      {error && <div className="error">{error}</div>}
      {shown.length === 0 && (
        <div className="hero-card">
          <p>{t(`tasks.none.${tab}`)}</p>
          {tab !== 'archived' && <Link to="/tasks/new" className="link-button">{t('nav.newTask')}</Link>}
        </div>
      )}
      <CollapsibleList key={tab} initialCount={10}>
        {roots.map((task) => (
          <Fragment key={task.task_id}>
            {renderRow(task)}
            {expanded[task.task_id] === false ? null :
              (childrenMap.get(task.task_id) ?? [])
                .sort((a, b) => (a.sibling_order ?? 0) - (b.sibling_order ?? 0))
                .map((child) => renderRow(child, true))}
          </Fragment>
        ))}
      </CollapsibleList>
    </div>
  )
}
