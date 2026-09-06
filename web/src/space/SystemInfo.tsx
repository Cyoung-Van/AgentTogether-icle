import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang } from '../i18n'
import type { GalaxyLayer, PlanetNode } from './types'

type SystemInfoProps = {
  galaxy: GalaxyLayer
  motionPaused: boolean
}

function entranceCount(nodes: PlanetNode[]): number {
  return nodes.reduce((count, node) => (
    count + (node.children?.length ? entranceCount(node.children) : 1)
  ), 0)
}

export default function SystemInfo({ galaxy, motionPaused }: SystemInfoProps) {
  const { lang, t } = useLang()
  const zh = lang === 'zh'
  // Subscribe to the existing header status; this surface never fetches or polls.
  const query = useQuery({
    queryKey: ['intelligence-status'],
    queryFn: api.intelligenceStatus,
    enabled: false,
  })
  const health = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    enabled: false,
  })
  const isRoot = galaxy.parent === null
  const count = galaxy.children.length
  const entrances = entranceCount(galaxy.children)
  const status = query.data
  const statusKnown = Boolean(status && !query.isError && !status.provider.error)
  const activeCount = statusKnown && status
    ? Math.max(status.active_count, status.active.length)
    : null
  const connectionState = health.isError
    ? 'offline'
    : health.data?.status === 'ok' ? 'online' : 'pending'
  const connectionText = connectionState === 'online'
    ? (zh ? '本地已连接' : 'Local connected')
    : connectionState === 'offline'
      ? (zh ? '连接不可用' : 'Disconnected')
      : (zh ? '连接待同步' : 'Connection pending')
  const motionText = motionPaused
    ? (zh ? '轨道已暂停' : 'Orbits paused')
    : (zh ? '轨道运行中' : 'Orbits moving')
  const model = statusKnown && status?.provider.configured ? status.provider.model : ''
  let state = 'unknown'
  let statusText = zh ? '状态待同步' : 'Status pending'

  if (query.isError || status?.provider.error) {
    statusText = zh ? '状态不可用' : 'Unavailable'
  } else if (status) {
    if (status.active.length > 0 || status.active_count > 0) {
      state = 'busy'
      statusText = zh ? `${activeCount} 项处理中` : `${activeCount} active`
    } else if (!status.provider.configured) {
      state = 'unconfigured'
      statusText = zh ? '智能层未配置' : 'LLM not set'
    } else if (status.state === 'busy') {
      state = 'busy'
      statusText = zh ? '智能处理中' : 'AI working'
    } else {
      state = 'idle'
      statusText = zh ? '智能空闲' : 'AI idle'
    }
  }

  const title = isRoot ? (zh ? '协作中枢' : 'Together') : t(galaxy.titleKey)
  const classText = isRoot ? (zh ? '恒星系' : 'STELLAR') : (zh ? '行星系' : 'PLANETARY')
  const compactSummary = isRoot
    ? (zh ? `${entrances} 个工作入口` : `${entrances} entrances`)
    : (zh ? `${count} 个工作入口` : `${count} entrances`)

  return (
    <div
      className={`system-info-content ${isRoot ? 'is-root' : 'is-planet'}`}
      data-motion={motionPaused ? 'paused' : 'running'}
      data-entrances={entrances}
      data-main-count={count}
    >
      {isRoot && (
        <div className="system-info-mini" aria-label={zh ? `${count} 个主功能，${entrances} 个工作入口` : `${count} main areas, ${entrances} work entrances`}>
          <strong>{count}<span>·</span>{entrances}</strong>
          <span>{zh ? '功能·入口' : 'AREAS·LINKS'}</span>
        </div>
      )}
      <div className="system-info-compact">
        {isRoot ? (
          <>
            <span className="system-info-eyebrow">{zh ? '协作中枢' : 'TOGETHER'}</span>
            <strong className="system-info-count">{String(count).padStart(2, '0')}<small>{zh ? '主功能' : 'areas'}</small></strong>
            <span className="system-info-summary">{compactSummary}</span>
            <span className={`system-info-connection is-${connectionState}`}>{connectionText}</span>
          </>
        ) : (
          <>
            <span className="system-info-eyebrow">{classText}</span>
            <strong className="system-info-name">{title}</strong>
            <span className="system-info-summary">{compactSummary}</span>
            <span className={`system-info-status is-${state}`}>{statusText}</span>
          </>
        )}
      </div>

      <div className="system-info-expanded">
        <span className="system-info-eyebrow">{classText}</span>
        <strong className="system-info-name">{title}</strong>
        <dl className="system-info-metrics">
          <div className="system-info-metric">
            <dt>{isRoot ? (zh ? '主功能' : 'Main areas') : (zh ? '工作入口' : 'Entrances')}</dt>
            <dd className="system-info-count">{String(count).padStart(2, '0')}</dd>
          </div>
          <div className="system-info-metric">
            <dt>{isRoot ? (zh ? '工作入口' : 'Entrances') : (zh ? '全局 AI 运行' : 'Global AI active')}</dt>
            <dd className="system-info-count">{isRoot ? String(entrances).padStart(2, '0') : activeCount === null ? '—' : String(activeCount).padStart(2, '0')}</dd>
          </div>
        </dl>
        {!isRoot && <p className="system-info-purpose">{t(galaxy.center.blurbKey)}</p>}
        <div className="system-info-telemetry">
          <span className={`system-info-status is-${state}`}>{statusText}</span>
          <span className={`system-info-connection is-${connectionState}`}>{connectionText}</span>
        </div>
        {model && (
          <div className="system-info-model" title={model}>
            <span>{zh ? '模型' : 'MODEL'}</span>
            <span>{model.length > 28 ? `${model.slice(0, 27)}…` : model}</span>
          </div>
        )}
        <span className="system-info-motion">{motionText}</span>
      </div>
    </div>
  )
}
