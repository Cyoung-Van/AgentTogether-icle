import { Link, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import IntelligenceStatus from '../components/IntelligenceStatus'
import { api } from '../api/client'
import { useLang, type Lang } from '../i18n'
import { isComposingPath, parentPath, trailForPath, navigationPath } from './hierarchy'
import { useSpaceNav } from './SpaceNavContext'

export default function SpaceChrome() {
  const { lang, t, setLang } = useLang()
  const location = useLocation()
  const { back, home, locked } = useSpaceNav()
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 30_000 })
  const path = navigationPath(location)
  const trail = trailForPath(path)
  const parent = parentPath(path)
  const composing = isComposingPath(location.pathname)

  return (
    <header className="space-chrome">
      <div className="space-chrome-nav">
        {parent ? (
          <button type="button" className="space-back" onClick={back} disabled={locked}>
            {t('space.back')}
          </button>
        ) : (
          <span className="space-brand">AgentTogether</span>
        )}
        <nav className="space-trail" aria-label={t('space.location')}>
          {trail.map((item, index) => {
            const last = index === trail.length - 1
            return (
              <span key={`${item.path}-${index}`} className="space-trail-item">
                {index > 0 && <span className="space-trail-sep" aria-hidden="true">/</span>}
                {last ? (
                  <span aria-current="page">{t(item.titleKey)}</span>
                ) : (
                  <Link to={item.path}>{t(item.titleKey)}</Link>
                )}
              </span>
            )
          })}
        </nav>
      </div>
      <div className="space-chrome-tools">
        <IntelligenceStatus />
        {location.pathname !== '/' && (
          <button type="button" className="secondary space-home" onClick={home} disabled={locked}>
            {t('space.home')}
          </button>
        )}
        {!composing && (
          <Link to="/tasks/new" className="link-button space-new-task">
            {t('nav.newTask')}
          </Link>
        )}
        <Link to="/today?learn=1" className="space-how">{t('how.link')}</Link>
        <button
          type="button"
          className="secondary lang-toggle"
          onClick={() => setLang((lang === 'en' ? 'zh' : 'en') as Lang)}
          title="Switch language / 切换语言"
        >
          {lang === 'en' ? '中文' : 'EN'}
        </button>
        <div className="health">
          {health.isPending ? t('health.connecting') : health.isError ? t('health.offline') : t('health.ready')}
        </div>
      </div>
    </header>
  )
}
