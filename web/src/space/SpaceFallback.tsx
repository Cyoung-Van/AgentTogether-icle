import { useLang } from '../i18n'
import { useSpaceNav } from './SpaceNavContext'
import type { GalaxyLayer } from './types'

export default function SpaceFallback({ galaxy }: { galaxy: GalaxyLayer }) {
  const { t } = useLang()
  const { enter, back, locked, lastFocusId } = useSpaceNav()
  const parentName = galaxy.parent ? t(galaxy.parent.titleKey) : ''
  return (
    <div className="space-fallback">
      <p className="planet-field-kicker">AgentTogether</p>
      <h1>{galaxy.id === 'home' ? t('space.explore') : t(galaxy.titleKey)}</h1>
      {galaxy.parent && (
        <button type="button" className="space-fallback-item" disabled={locked} onClick={back}>
          <strong>{t(galaxy.center.titleKey)}</strong>
          <span>{`${t('space.returnPrefix')}${parentName}`}</span>
        </button>
      )}
      <div className="space-fallback-list">
        {galaxy.children.map((node) => (
          <button
            key={node.id}
            type="button"
            className="space-fallback-item"
            disabled={locked}
            autoFocus={lastFocusId === node.id}
            onClick={() => enter(node.path, node.id)}
          >
            <strong>{t(node.titleKey)}</strong>
            <span>{t(node.blurbKey)}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
