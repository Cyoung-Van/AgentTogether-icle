import * as React from 'react'
import { NavLink, Route, Routes, Link, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from './api/client'
import { useLang, type Lang } from './i18n'
import { getApiToken, setApiToken } from './api/client'
import Overview from './pages/Overview'
import NewTask from './pages/NewTask'
import Tasks from './pages/Tasks'
import TaskDetail from './pages/TaskDetail'
import CostCenter from './pages/CostCenter'
import Episodes from './pages/Episodes'
import EpisodeDetail from './pages/EpisodeDetail'
import Agents from './pages/Agents'
import AgentProfile from './pages/AgentProfile'
import LocalAgents from './pages/LocalAgents'
import Compare from './pages/Compare'
import Recommend from './pages/Recommend'
import Activity from './pages/Activity'
import Collabs from './pages/Collabs'
import Settings from './pages/Settings'
import Providers from './pages/Providers'
import Sessions from './pages/Sessions'
import IntelligenceStatus from './components/IntelligenceStatus'

const PRIMARY_NAV = [
  { to: '/', key: 'nav.home', end: true },
  { to: '/tasks', key: 'nav.tasks' },
  { to: '/agents', key: 'nav.agents' },
  { to: '/sessions', key: 'nav.sessions' },
  { to: '/settings', key: 'nav.settings' },
] as const

function Shell() {
  const { lang, t, setLang } = useLang()
  const location = useLocation()
  const [authRequired, setAuthRequired] = React.useState(false)
  const [token, setToken] = React.useState(getApiToken())
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 30_000 })
  React.useEffect(() => {
    const onAuthRequired = () => setAuthRequired(true)
    window.addEventListener('icle-auth-required', onAuthRequired)
    return () => window.removeEventListener('icle-auth-required', onAuthRequired)
  }, [])

  const composing = location.pathname === '/tasks/new'

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand-row">
          <div className="brand">ICLE</div>
        </div>
        <nav aria-label={t('nav.primary')}>
          {PRIMARY_NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={'end' in item ? item.end : false}
              className={({ isActive }) => (isActive ? 'nav active' : 'nav')}
            >
              {t(item.key)}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <Link to="/?learn=1" className="nav how-link">{t('how.link')}</Link>
          <button
            type="button"
            className="secondary lang-toggle"
            onClick={() => setLang((lang === 'en' ? 'zh' : 'en') as Lang)}
            title="Switch language / 切换语言"
          >
            {lang === 'en' ? '中文' : 'EN'}
          </button>
          <div className="health">
            {health.isPending
              ? t('health.connecting')
              : health.isError
                ? t('health.offline')
                : t('health.ready')}
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <IntelligenceStatus />
          {!composing && (
            <Link to="/tasks/new" className="link-button topbar-cta">
              {t('nav.newTask')}
            </Link>
          )}
        </header>
        <main className="page">
          {authRequired && (
            <div className="auth-banner">
              <strong>{t('auth.title')}</strong>
              <input
                type="password"
                value={token}
                placeholder={t('auth.placeholder')}
                onChange={(event) => setToken(event.target.value)}
              />
              <button onClick={() => { setApiToken(token); setAuthRequired(false); window.location.reload() }}>
                {t('auth.save')}
              </button>
            </div>
          )}
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/tasks/new" element={<NewTask />} />
            <Route path="/tasks" element={<Tasks />} />
            <Route path="/tasks/:taskId" element={<TaskDetail />} />
            <Route path="/episodes" element={<Episodes />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/episodes/:episodeId" element={<EpisodeDetail />} />
            <Route path="/episodes/:episodeId/compare" element={<Compare />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/agents/local" element={<LocalAgents />} />
            <Route path="/agent-profile" element={<AgentProfile />} />
            <Route path="/agents/:agentId" element={<AgentProfile />} />
            <Route path="/recommend" element={<Recommend />} />
            <Route path="/providers" element={<Providers />} />
            <Route path="/cost" element={<CostCenter />} />
            <Route path="/activity" element={<Activity />} />
            <Route path="/collabs" element={<Collabs />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<div className="empty">{t('common.notFound')}</div>} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

export default Shell
