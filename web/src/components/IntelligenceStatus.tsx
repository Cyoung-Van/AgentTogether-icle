import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang } from '../i18n'

function elapsed(milliseconds: number | undefined, lang: string): string {
  const seconds = Math.max(0, Math.floor((milliseconds ?? 0) / 1000))
  if (seconds < 60) return `${seconds}${lang === 'zh' ? '秒' : 's'}`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return lang === 'zh' ? `${minutes}分${rest}秒` : `${minutes}m ${rest}s`
}

export default function IntelligenceStatus() {
  const { t, lang } = useLang()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const controlRef = useRef<HTMLDivElement>(null)
  const query = useQuery({
    queryKey: ['intelligence-status'],
    queryFn: api.intelligenceStatus,
    refetchInterval: 1_000,
    refetchIntervalInBackground: true,
    retry: 1,
  })
  const modelsQuery = useQuery({
    queryKey: ['intelligence-models'],
    queryFn: api.intelligenceModels,
    enabled: open,
  })
  const switchMutation = useMutation({
    mutationFn: ({ providerId, modelId }: { providerId: string; modelId: string }) =>
      api.selectIntelligenceModel(providerId, modelId),
    onSuccess: async () => {
      setError('')
      setOpen(false)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['intelligence-status'] }),
        queryClient.invalidateQueries({ queryKey: ['intelligence-models'] }),
        queryClient.invalidateQueries({ queryKey: ['providers'] }),
        queryClient.invalidateQueries({ queryKey: ['settings'] }),
      ])
    },
    onError: (exc: Error) => setError(exc.message),
  })

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!controlRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (query.isPending) {
    return (
      <div className="intelligence-control">
        <div className="intelligence-status is-connecting" role="status">
          <span className="intelligence-dot" />
          <span>{t('intelligence.connecting')}</span>
        </div>
      </div>
    )
  }
  if (query.isError) {
    return (
      <div className="intelligence-control">
        <div className="intelligence-status is-offline" role="status">
          <span className="intelligence-dot" />
          <span>{t('intelligence.offline')}</span>
        </div>
      </div>
    )
  }

  const status = query.data
  const active = status.active[0]
  const providerName = status.provider.display_name || status.provider.name
  const provider = status.provider.model
    ? `${providerName} / ${status.provider.model}`
    : providerName
  const last = status.last
  const title = last
    ? `${lang === 'zh' ? last.label_zh : last.label} · ${last.outcome} · ${elapsed(last.duration_ms, lang)}`
    : t('intelligence.noRecent')

  const byProvider = new Map<string, NonNullable<typeof modelsQuery.data>['models']>()
  for (const model of modelsQuery.data?.models ?? []) {
    const models = byProvider.get(model.provider) ?? []
    models.push(model)
    byProvider.set(model.provider, models)
  }

  return (
    <div className="intelligence-control" ref={controlRef}>
      <button
        type="button"
        className={`intelligence-status ${!status.provider.configured ? 'is-unconfigured' : status.state === 'busy' ? 'is-busy' : 'is-idle'}`}
        aria-expanded={open}
        aria-haspopup="menu"
        title={`${title} · ${t('intelligence.switchHint')}`}
        onClick={() => {
          setError('')
          setOpen((value) => !value)
        }}
      >
        <span className="intelligence-live" role="status" aria-live="polite">
          <span className="intelligence-dot" />
          <strong>{t('intelligence.name')}</strong>
          {!status.provider.configured ? (
            <span>{t('intelligence.unconfigured')}</span>
          ) : status.state === 'busy' && active ? (
            <>
              <span className="intelligence-operation">{lang === 'zh' ? active.label_zh : active.label}</span>
              <span className="intelligence-elapsed">{elapsed(active.duration_ms, lang)}</span>
              {status.active_count > 1 && <span className="badge">×{status.active_count}</span>}
            </>
          ) : (
            <span>{t('intelligence.idle')}</span>
          )}
        </span>
        {status.provider.configured && <span className="intelligence-provider">{provider}</span>}
        <span className="intelligence-chevron" aria-hidden="true">⌄</span>
      </button>

      {open && (
        <div className="intelligence-menu" role="menu" aria-label={t('intelligence.switchModel')}>
          <div className="intelligence-menu-header">
            <strong>{t('intelligence.switchModel')}</strong>
            <span>{t('intelligence.switchDescription')}</span>
          </div>
          {status.state === 'busy' && (
            <div className="intelligence-menu-notice">{t('intelligence.busySwitch')}</div>
          )}
          {modelsQuery.isPending && <div className="intelligence-menu-empty">{t('common.loading')}</div>}
          {modelsQuery.isError && <div className="intelligence-menu-empty error">{t('intelligence.modelsFailed')}</div>}
          {!modelsQuery.isPending && !modelsQuery.isError && byProvider.size === 0 && (
            <div className="intelligence-menu-empty">
              {t('intelligence.noModels')} <a href="/providers">{t('intelligence.openProviders')}</a>
            </div>
          )}
          {[...byProvider.entries()].map(([providerLabel, models]) => (
            <div className="intelligence-model-group" key={providerLabel}>
              <div className="intelligence-provider-label">{providerLabel}</div>
              {models.map((model) => (
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={model.selected}
                  className={`intelligence-model-option ${model.selected ? 'is-selected' : ''}`}
                  key={`${model.provider_id}/${model.model_id}`}
                  disabled={status.state === 'busy' || switchMutation.isPending}
                  title={model.model_id}
                  onClick={() => {
                    if (model.selected) {
                      setOpen(false)
                      return
                    }
                    setError('')
                    switchMutation.mutate({ providerId: model.provider_id, modelId: model.model_id })
                  }}
                >
                  <span className="intelligence-model-check" aria-hidden="true">{model.selected ? '✓' : ''}</span>
                  <span className="intelligence-model-name">{model.model}</span>
                  {model.model !== model.model_id && <span className="intelligence-model-id">{model.model_id}</span>}
                </button>
              ))}
            </div>
          ))}
          {switchMutation.isPending && <div className="intelligence-menu-notice">{t('intelligence.switching')}</div>}
          {error && <div className="intelligence-menu-notice error">{error}</div>}
        </div>
      )}
    </div>
  )
}
