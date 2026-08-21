import { useState } from 'react'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { displayStatus } from '../lib/displayLabel'
import { ExpandableText } from '../components/ExpandableText'

const TAGS = [
  'correctness', 'completeness', 'style', 'speed',
  'cost', 'initiative', 'understanding', 'maintainability',
]

export default function Compare() {
  const { t, lang } = useLang()
  const { episodeId } = useParams<{ episodeId: string }>()
  const [params] = useSearchParams()
  const a = params.get('a') ?? ''
  const b = params.get('b') ?? ''
  const [kind, setKind] = useState<string | null>(null)
  const [tags, setTags] = useState<string[]>([])
  const [note, setNote] = useState('')
  const [saved, setSaved] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const KINDS = [
    { value: 'prefer_a', label: t('compare.preferA') },
    { value: 'tie', label: t('compare.tie') },
    { value: 'prefer_b', label: t('compare.preferB') },
    { value: 'inconclusive', label: t('compare.inconclusive') },
  ]

  const query = useQuery({
    queryKey: ['compare', a, b],
    queryFn: () => api.compare(a, b),
    enabled: Boolean(a && b),
  })

  if (!a || !b) return <div className="empty">{t('compare.pickFirst')}</div>
  if (query.isPending) return <div className="empty">{t('common.loading')}</div>
  if (query.isError) return <div className="empty error">{t('compare.failed')}</div>

  const { a: left, b: right } = query.data

  function Side({ data, label }: { data: typeof left; label: string }) {
    return (
      <div className="compare-side">
        <div className="row-meta">
          <span className="badge">{label}</span>
          <span className="badge">{data.replay.target_agent_revision.agent_id}</span>
          <span className={data.replay.status === 'completed' ? 'ok' : 'error'}>
            {displayStatus(data.replay.status, lang)}
          </span>
          <span className="muted">{(data.replay.duration_ms / 1000).toFixed(1)}s</span>
        </div>
        <h3>{t('compare.diff')}</h3>
        <ExpandableText text={data.workspace_diff || t('compare.noChanges')} className="diff" lines={14} threshold={1200} preserveWhitespace />
        <h3>{t('compare.output')}</h3>
        <ExpandableText text={data.stdout_tail || t('compare.noOutput')} className="stdout" lines={12} threshold={900} preserveWhitespace />
      </div>
    )
  }

  function toggleTag(tag: string) {
    setTags((prev) => (prev.includes(tag) ? prev.filter((x) => x !== tag) : [...prev, tag]))
  }

  async function submit() {
    if (!kind || submitting) return
    setSubmitting(true)
    setError('')
    try {
      const result = await api.judge({ pair: [a, b], kind, reason_tags: tags, note })
      setSaved(result.judgment.judgment_id)
    } catch (exc) {
      setError(String(exc))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div>
      <p><Link to={`/episodes/${episodeId}`}>← {episodeId}</Link></p>
      <h1>{t('compare.title')}</h1>
      <div className="compare">
        <Side data={left} label={`A · ${a}`} />
        <Side data={right} label={`B · ${b}`} />
      </div>

      <section className="panel">
        <h2>{t('compare.judgment')}</h2>
        <div className="actions">
          {KINDS.map((k) => (
            <button
              key={k.value}
              className={kind === k.value ? '' : 'secondary'}
              onClick={() => setKind(k.value)}
            >
              {k.label}
            </button>
          ))}
        </div>
        <div className="tag-grid">
          {TAGS.map((tag) => (
            <label key={tag} className="tag">
              <input type="checkbox" checked={tags.includes(tag)} onChange={() => toggleTag(tag)} />
              {tag}
            </label>
          ))}
        </div>
        <input
          placeholder={t('compare.note')}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
        {error && <div className="error">{error}</div>}
        <div className="actions">
          <button onClick={submit} disabled={!kind || Boolean(saved) || submitting}>
            {saved ? `✓ ${saved}` : submitting ? t('common.loading') : t('compare.save')}
          </button>
        </div>
      </section>
    </div>
  )
}
