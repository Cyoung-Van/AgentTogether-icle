import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useWorkspaceSection } from '../space/useWorkspaceSection'
import { HowItWorks } from '../components/HowItWorks'
import { useLang } from '../i18n'
import { formatLocalDateTime } from '../lib/dateTime'
import { displayEnum, displayStatus } from '../lib/displayLabel'

export default function Overview() {
  const { t, lang } = useLang()
  const [params] = useSearchParams()
  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const overview = useQuery({ queryKey: ['overview'], queryFn: api.overview })
  const intel = useQuery({ queryKey: ['intelligence-status'], queryFn: api.intelligenceStatus })

  useWorkspaceSection(health.isSuccess && overview.isSuccess)

  if (health.isPending || overview.isPending) return <div className="empty">{t('common.loading')}</div>
  if (health.isError || overview.isError || !overview.data) return <div className="empty error">{t('overview.apiUnreachable')}</div>

  const data = overview.data
  const agents = data.local_agents
  const active = data.active ?? []
  const needsYou = data.needs_you ?? []
  const cost = data.cost ?? { today: 0, this_week: 0 }
  const recommendations = data.recommendations ?? []
  const empty = needsYou.length === 0 && active.length === 0
  const today = new Date().toLocaleDateString(lang === 'zh' ? 'zh-CN' : 'en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  })
  const learn = params.get('learn') === '1'

  return (
    <div className="home">
      <header className="page-hero">
        <p className="page-kicker">{today}</p>
        <h1>{empty || learn ? t('home.welcome') : t('home.title')}</h1>
        <p className="page-guide">{empty ? t('home.emptyGuide') : t('home.guide')}</p>
      </header>

      <HowItWorks
        agentReady={agents.ready > 0}
        providerReady={Boolean(intel.data?.provider?.configured)}
        hasWork={!empty}
      />

      {empty && (
        <section className="hero-card">
          <h2>{t('home.startTitle')}</h2>
          <p>{t('home.startHelp')}</p>
          <div className="actions">
            <Link to="/tasks/new" className="link-button">{t('nav.newTask')}</Link>
            <Link to="/sessions" className="link-as-button">{t('home.fromSession')}</Link>
          </div>
        </section>
      )}

      <section id="needs-attention" tabIndex={-1} className="workspace-section" aria-labelledby="needs-heading">
          <div className="section-head">
            <h2 id="needs-heading">{t('overview.needsYou')}</h2>
            <span className="count">{needsYou.length}</span>
          </div>
          <p className="page-guide">{t(needsYou.length ? 'home.needsHelp' : 'space.section.noNeeds')}</p>
          <div className="grouped-box">
            {needsYou.map((task: any) => (
              <Link key={task.task_id} to={`/tasks/${task.task_id}`} className="grouped-row">
                <div>
                  <div className="row-title line-clamp-2">{task.title}</div>
                  <div className="row-meta">
                    <span className="badge">{task.status === 'review' ? t('overview.review') : displayStatus(task.status, lang)}</span>
                    {task.status === 'review' && <span className="muted">{t('overview.rateOrAccept')}</span>}
                  </div>
                </div>
                <span className="chevron" aria-hidden="true">›</span>
              </Link>
            ))}
          </div>
      </section>

      <section id="active-work" tabIndex={-1} className="workspace-section" aria-labelledby="active-heading">
          <div className="section-head">
            <h2 id="active-heading">{t('home.continue')}</h2>
            <Link to="/tasks" className="text-link">{t('home.allTasks')}</Link>
          </div>
          <p className="page-guide">{t(active.length ? 'home.continueHelp' : 'space.section.noActive')}</p>
          <div className="grouped-box">
            {active.slice(0, 6).map((task: any) => (
              <Link key={task.task_id} to={`/tasks/${task.task_id}`} className="grouped-row">
                <div>
                  <div className="row-title line-clamp-2">{task.title}</div>
                  <div className="row-meta">
                    <span className="badge dim">{displayStatus(task.status, lang)}</span>
                    {task.strategy && <span className="muted">{displayEnum('strategy', task.strategy, lang)}</span>}
                    <span className="muted">{formatLocalDateTime(task.updated_at)}</span>
                  </div>
                </div>
                <span className="chevron" aria-hidden="true">›</span>
              </Link>
            ))}
          </div>
      </section>

      <section id="resources" tabIndex={-1} className="workspace-section" aria-labelledby="resources-heading">
      <h2 id="resources-heading">{t('space.section.resources')}</h2>
      <footer className="home-quiet">
        <Link to="/agents/local">{agents.ready} {t('home.agentsReady')}</Link>
        <Link to="/cost">${cost.today.toFixed(2)} {t('home.spentToday')}</Link>
        {recommendations.length > 0 && (
          <Link to="/recommend">{recommendations.length} {t('home.recs')}</Link>
        )}
      </footer>
      </section>
    </div>
  )
}
