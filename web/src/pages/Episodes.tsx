import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { formatLocalDate } from '../lib/dateTime'
import { optionLabel } from '../lib/optionLabel'
import { CollapsibleList } from '../components/CollapsibleList'
import { ExpandableText } from '../components/ExpandableText'

export default function Episodes() {
  const { t } = useLang()
  const [episodeId, setEpisodeId] = useState('')
  const [agent, setAgent] = useState('')
  const [project, setProject] = useState('')
  const all = useQuery({ queryKey: ['episodes'], queryFn: () => api.episodes() })
  const query = useQuery({
    queryKey: ['episodes', episodeId, agent, project],
    queryFn: async () => {
      if (episodeId) {
        const episode = (all.data?.episodes ?? []).find((item) => item.episode_id === episodeId)
        return { episodes: episode ? [episode] : [], total: episode ? 1 : 0 }
      }
      return api.episodes({ agent: agent || undefined, project: project || undefined })
    },
  })
  const agents = [...new Set((all.data?.episodes ?? []).map((e) => e.agent))]
  const projects = [...new Set((all.data?.episodes ?? []).map((e) => e.project_id))]

  const totalReplays = (all.data?.episodes ?? []).reduce((sum, item) => sum + item.replays, 0)

  return (
    <div>
      <h1>{t('nav.episodes')}</h1>
      <section className="panel explainer-panel">
        <h2>{t('episodes.whatTitle')}</h2>
        <ExpandableText text={t('episodes.what')} className="request" lines={3} threshold={180} />
        <div className="experience-flow">
          <span>{t('episodes.flow.accepted')}</span><b>→</b>
          <span>{t('episodes.flow.replay')}</span><b>→</b>
          <span>{t('episodes.flow.compare')}</span><b>→</b>
          <span>{t('episodes.flow.learn')}</span>
        </div>
        <div className="row-meta">
          <span className="badge">{(all.data?.episodes ?? []).length} Episodes</span>
          <span className="badge dim">{totalReplays} {t('common.replays')}</span>
          <span className="muted">{t('episodes.safe')}</span>
        </div>
      </section>
      <div className="toolbar">
        <select value={episodeId} onChange={(event) => {
          setEpisodeId(event.target.value)
          if (event.target.value) {
            setAgent('')
            setProject('')
          }
        }}>
          <option value="">{t('episodes.allTasks')}</option>
          {(all.data?.episodes ?? []).map((episode) => (
            <option key={episode.episode_id} value={episode.episode_id}>
              {optionLabel(episode.request)} · {episode.episode_id}
            </option>
          ))}
        </select>
        <select value={agent} onChange={(event) => setAgent(event.target.value)}>
          <option value="">{t('episodes.allAgents')}</option>
          {agents.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <select value={project} onChange={(event) => setProject(event.target.value)}>
          <option value="">{t('episodes.allProjects')}</option>
          {projects.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </div>
      {query.isPending && <div className="empty">{t('common.loading')}</div>}
      {query.isError && <div className="empty error">{t('episodes.failed')}</div>}
      {query.data && query.data.total === 0 && <div className="empty">{t('episodes.noMatch')}</div>}
      <CollapsibleList key={`${episodeId}:${agent}:${project}`} initialCount={10}>
        {query.data?.episodes.map((episode) => (
          <Link to={`/episodes/${episode.episode_id}`} key={episode.episode_id} className="row">
            <div className="row-title line-clamp-2">{episode.request}</div>
            <p className="muted episode-purpose">{t('episodes.cardPurpose')}</p>
            <div className="row-meta">
              <span className="badge">{episode.agent}</span>
              <span className="badge">{episode.project_id}</span>
              <span className="muted">
                {episode.replays} {t('common.replays')} · {formatLocalDate(episode.created_at)}
              </span>
            </div>
          </Link>
        ))}
      </CollapsibleList>
    </div>
  )
}
