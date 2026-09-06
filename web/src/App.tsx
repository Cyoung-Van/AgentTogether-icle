import * as React from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'
import { getApiToken, setApiToken } from './api/client'
import { useLang } from './i18n'
import Activity from './pages/Activity'
import AgentProfile from './pages/AgentProfile'
import Agents from './pages/Agents'
import Collabs from './pages/Collabs'
import Compare from './pages/Compare'
import CostCenter from './pages/CostCenter'
import EpisodeDetail from './pages/EpisodeDetail'
import Episodes from './pages/Episodes'
import LocalAgents from './pages/LocalAgents'
import NewTask from './pages/NewTask'
import Overview from './pages/Overview'
import Providers from './pages/Providers'
import Recommend from './pages/Recommend'
import Sessions from './pages/Sessions'
import Settings from './pages/Settings'
import TaskDetail from './pages/TaskDetail'
import Tasks from './pages/Tasks'
import { fieldForPath } from './space/hierarchy'
import SpaceBackground from './space/SpaceBackground'
import SpaceChrome from './space/SpaceChrome'
import { SpaceNavProvider } from './space/SpaceNav'
import SpaceScene from './space/SpaceScene'
import './space/space.css'

function WorkspaceRoutes() {
  const { t } = useLang()
  return (
    <div className="space-workspace">
      <div className="page">
        <Routes>
          <Route path="/today" element={<Overview />} />
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
      </div>
    </div>
  )
}

function SpaceStage() {
  const location = useLocation()
  if (fieldForPath(location.pathname)) return null
  return <WorkspaceRoutes />
}

function Shell() {
  const { t } = useLang()
  const location = useLocation()
  const field = fieldForPath(location.pathname)
  const [authRequired, setAuthRequired] = React.useState(false)
  const [token, setToken] = React.useState(getApiToken())

  React.useEffect(() => {
    const onAuthRequired = () => setAuthRequired(true)
    window.addEventListener('icle-auth-required', onAuthRequired)
    return () => window.removeEventListener('icle-auth-required', onAuthRequired)
  }, [])

  return (
    <div className="space-app">
      <SpaceBackground />
      <SpaceNavProvider>
        <SpaceChrome />
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
        <div className={`space-main ${field ? 'is-field' : 'is-workspace'}`}>
          <SpaceScene />
          <SpaceStage />
        </div>
      </SpaceNavProvider>
    </div>
  )
}

export default Shell
