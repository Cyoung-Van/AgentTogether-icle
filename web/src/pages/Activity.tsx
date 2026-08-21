import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { displayEnum } from '../lib/displayLabel'
import { CollapsibleList } from '../components/CollapsibleList'

export default function Activity() {
  const { t, lang } = useLang()
  const [typeFilter, setTypeFilter] = useState('all')
  const query = useQuery({ queryKey: ['activity'], queryFn: () => api.activity() })

  if (query.isPending) return <div className="empty">{t('common.loading')}</div>
  if (query.isError) return <div className="empty error">{t('activity.failed')}</div>

  const types = ['all', ...new Set(query.data.activity.map((item: any) => item.type))]
  const filtered = typeFilter === 'all'
    ? query.data.activity
    : query.data.activity.filter((item: any) => item.type === typeFilter)

  function activityText(item: any): string {
    if (lang !== 'zh') return item.text
    if (item.type === 'mark' && item.agent_id && item.mark && item.episode_id) {
      return `${item.agent_id} 标记为「${displayEnum('mark', item.mark, lang)}」 · ${item.episode_id}`
    }
    if (item.type === 'judgment' && item.judge && item.judgment_kind) {
      return `${item.judge === 'human' ? '用户' : item.judge} 作出「${displayEnum('judgment', item.judgment_kind, lang)}」裁决`
    }
    if (item.type === 'replay' && item.agent_id && item.episode_id && item.status) {
      return `${item.agent_id} 重放 ${item.episode_id} · ${displayEnum('replay_status', item.status, lang)}`
    }
    const mark = String(item.text ?? '').match(/^(.+) marked (.+) on (.+)$/)
    if (item.type === 'mark' && mark) return `${mark[1]} 标记为「${displayEnum('mark', mark[2], lang)}」 · ${mark[3]}`
    const judgment = String(item.text ?? '').match(/^(.+) judged (.+)$/)
    if (item.type === 'judgment' && judgment) return `${judgment[1] === 'human' ? '用户' : judgment[1]} 作出「${displayEnum('judgment', judgment[2], lang)}」裁决`
    const replay = String(item.text ?? '').match(/^(.+) replayed (.+) → (.+)$/)
    if (item.type === 'replay' && replay) return `${replay[1]} 重放 ${replay[2]} · ${displayEnum('replay_status', replay[3], lang)}`
    return item.text
  }

  return (
    <div>
      <h1>{t('activity.title')}</h1>
      <div className="row" style={{ marginBottom: 10 }}>
        <span className="muted">{t('activity.filter')}:</span>
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          {types.map((type) => (
            <option key={type} value={type}>{type === 'all' ? t('activity.all') : displayEnum('activity_type', type, lang)}</option>
          ))}
        </select>
        <span className="muted">{filtered.length} {t('activity.items')}</span>
      </div>
      <CollapsibleList key={typeFilter} initialCount={12}>
        {filtered.map((item: any, index: number) => {
          const content = (
            <>
              <div className="row-meta">
                <span className="badge dim">{displayEnum('activity_type', item.type, lang)}</span>
                <span className="muted">{(item.at ?? '').slice(5, 16).replace('T', ' ')}</span>
              </div>
              <div className="line-clamp-3">{activityText(item)}</div>
            </>
          )
          // 下钻:episode 类活动可点击进入 EpisodeDetail
          return item.target === 'episode' && item.ref ? (
            <Link key={index} to={`/episodes/${item.ref}`} className="row">
              {content}
            </Link>
          ) : (
            <div key={index} className="row">{content}</div>
          )
        })}
        {filtered.length === 0 && <div className="empty">{t('activity.none')}</div>}
      </CollapsibleList>
    </div>
  )
}
