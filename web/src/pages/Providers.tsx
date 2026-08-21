import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { formatLocalDateTime } from '../lib/dateTime'
import { displayEnum } from '../lib/displayLabel'
import type { ProviderInfo, ProviderModel } from '../types'
import { CollapsibleList } from '../components/CollapsibleList'

const TYPES = ['openai-compatible', 'local', 'custom']
const ROLES = ['extraction', 'judging', 'planning', 'routing', 'general']
const CUSTOM_PROVIDER = '__custom_provider__'

const STATUS_LABEL: Record<string, string> = {
  connected: 'ok',
  unavailable: 'error',
  misconfigured: 'muted',
}

export default function Providers() {
  const { t, lang } = useLang()
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['providers'], queryFn: api.providers })
  const [editing, setEditing] = useState<ProviderInfo | 'new' | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [actionError, setActionError] = useState('')

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['providers'] })

  const testMutation = useMutation({
    mutationFn: (id: string) => api.testProvider(id),
    onSuccess: invalidate,
    onError: (exc: Error) => setActionError(exc.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteProvider(id),
    onSuccess: invalidate,
    onError: (exc: Error) => setActionError(exc.message),
  })

  const selectMutation = useMutation({
    mutationFn: ({ id, model }: { id: string; model: string }) =>
      api.updateProvider(id, { selected_model: model }),
    onSuccess: invalidate,
    onError: (exc: Error) => setActionError(exc.message),
  })

  function selectModel(id: string, model: string) {
    setActionError('')
    selectMutation.mutate({ id, model })
  }

  async function runTest(id: string) {
    setBusyId(id)
    setActionError('')
    try {
      await testMutation.mutateAsync(id)
    } catch (exc) {
      setActionError(String(exc))
    } finally {
      setBusyId(null)
    }
  }

  if (query.isPending) return <div className="empty">{t('common.loading')}</div>
  if (query.isError) return <div className="empty error">{t('providers.failed')}</div>

  return (
    <div>
      <div className="title-row">
        <h1>{t('providers.title')}</h1>
        <button onClick={() => setEditing('new')}>{t('providers.add')}</button>
      </div>
      {actionError && <div className="error">{actionError}</div>}
      {query.data.providers.length === 0 && (
        <div className="empty">{t('providers.none')}</div>
      )}
      <CollapsibleList initialCount={6}>
        {query.data.providers.map((provider) => (
          <div key={provider.provider_id} className="row">
            <div className="row-title">
              {provider.display_name}{' '}
              <span className={`badge ${provider.configured ? '' : 'dim'}`}>
                {provider.configured ? t('providers.configured') : t('providers.notConfigured')}
              </span>
            </div>
            <div className="row-meta">
              <span className={STATUS_LABEL[provider.status] ?? 'muted'}>
                {provider.status === 'connected'
                  ? t('providers.status.connected')
                  : provider.status === 'unavailable'
                    ? t('providers.status.unavailable')
                    : t('providers.status.misconfigured')}
              </span>
              <span className="badge dim">{displayEnum('provider_type', provider.type, lang)}</span>
              <span className="muted mono">{provider.base_url || '—'}</span>
              <span className="muted">{provider.roles.map((role) => displayEnum('provider_role', role, lang)).join('、') || '—'}</span>
              {provider.last_checked_at && (
                <span className="muted">{t('providers.checkedAt')} {formatLocalDateTime(provider.last_checked_at)}</span>
              )}
            </div>
            {/* model selector: one chip per discovered model, click to switch.
                Selected model is persisted via PATCH (LobeChat-style active state). */}
            <CollapsibleList initialCount={8} className="model-chips">
              {provider.models.length === 0 ? (
                <span className="muted">{t('providers.noModels')}</span>
              ) : (
                [...provider.models]
                  .sort((a, b) => Number(b.id === provider.selected_model) - Number(a.id === provider.selected_model))
                  .map((model) => {
                  const active = provider.selected_model === model.id
                  return (
                    <button
                      key={model.id}
                      className={`chip ${active ? 'active' : ''}`}
                      disabled={!['openai-compatible', 'local'].includes(provider.type)}
                      onClick={() => selectModel(provider.provider_id, model.id)}
                    >
                      {model.alias || model.id}
                    </button>
                  )
                })
              )}
            </CollapsibleList>
            <div className="actions">
              {['openai-compatible', 'local'].includes(provider.type) && (
                <button
                  className="secondary"
                  disabled={busyId === provider.provider_id || testMutation.isPending}
                  onClick={() => runTest(provider.provider_id)}
                >
                  {busyId === provider.provider_id ? t('providers.testing') : t('providers.test')}
                </button>
              )}
              {testMutation.data && testMutation.data.provider_id === provider.provider_id && (
                <span className={testMutation.data.ok ? 'ok' : 'error'}>
                  {testMutation.data.ok
                    ? `${t('providers.testOk')} ${testMutation.data.latency_ms}ms`
                    : `${t('providers.testFail')}${testMutation.data.error ? `: ${testMutation.data.error}` : ''}`}
                  {testMutation.data.models_synced && <> · {t('providers.synced')}</>}
                </span>
              )}
              <button className="secondary" onClick={() => setEditing(provider)}>{t('providers.edit')}</button>
              <button
                className="secondary"
                onClick={() => {
                  if (window.confirm(t('providers.deleteConfirm'))) {
                    deleteMutation.mutate(provider.provider_id)
                  }
                }}
              >
                {t('providers.delete')}
              </button>
            </div>
          </div>
        ))}
      </CollapsibleList>
      {editing && (
        <ProviderDialog
          provider={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            invalidate()
          }}
        />
      )}
      <p className="muted">{t('providers.footer')}</p>
    </div>
  )
}

function ProviderDialog({
  provider,
  onClose,
  onSaved,
}: {
  provider: ProviderInfo | null
  onClose: () => void
  onSaved: () => void
}) {
  const { t, lang } = useLang()
  const presetsQuery = useQuery({
    queryKey: ['providerPresets'],
    queryFn: api.providerPresets,
  })
  const [activeProvider, setActiveProvider] = useState<ProviderInfo | null>(provider)
  const [presetId, setPresetId] = useState('')
  const [displayName, setDisplayName] = useState(provider?.display_name ?? '')
  const [type, setType] = useState(provider?.type ?? 'openai-compatible')
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [roles, setRoles] = useState<string[]>(provider?.roles ?? ['general'])
  const [availableModels, setAvailableModels] = useState<ProviderModel[]>(provider?.models ?? [])
  const [selectedModels, setSelectedModels] = useState<string[]>((provider?.models ?? []).map((model) => model.id))
  const [error, setError] = useState('')
  const [discovering, setDiscovering] = useState(false)
  const [saving, setSaving] = useState(false)
  // Auto-detection state: after the user pastes an API key, the backend
  // matches its prefix against known providers and we prefill the form.
  const [detectResult, setDetectResult] = useState<{
    detected: boolean
    suggested: { display_name: string; type: string; base_url: string } | null
    candidates: { display_name: string; type: string; base_url: string }[]
    hint: string
    warning?: string | null
  } | null>(null)
  const detectTimer = useRef<number | null>(null)

  useEffect(() => {
    if (!activeProvider || presetId || !presetsQuery.data) return
    const match = presetsQuery.data.presets.find((preset) => (
      preset.type === activeProvider.type && preset.base_url === activeProvider.base_url
    ))
    setPresetId(match?.id ?? CUSTOM_PROVIDER)
  }, [activeProvider, presetId, presetsQuery.data])

  const selectedPreset = presetsQuery.data?.presets.find((preset) => preset.id === presetId)

  function onKeyChange(value: string) {
    setApiKey(value)
    if (detectTimer.current) window.clearTimeout(detectTimer.current)
    if (value.trim().length < 8) {
      setDetectResult(null)
      return
    }
    // Debounce so we don't hammer the API while typing; prefill only fields
    // the user hasn't already filled (prev || suggested).
    detectTimer.current = window.setTimeout(async () => {
      try {
        const result = await api.detectProvider(value.trim())
        setDetectResult(result)
        if (result.detected && result.suggested) {
          applyCandidate(result.suggested)
        }
      } catch {
        /* detection is best-effort; manual entry always works */
      }
    }, 400)
  }

  function applyCandidate(candidate: { display_name: string; type: string; base_url: string }) {
    const preset = presetsQuery.data?.presets.find((item) => item.type === candidate.type && item.base_url === candidate.base_url)
    setPresetId(preset?.id ?? CUSTOM_PROVIDER)
    setDisplayName(candidate.display_name)
    setType(candidate.type)
    setBaseUrl(candidate.base_url)
  }

  // LobeChat-style preset picker: choose a provider → base_url/type/name auto-fill.
  function applyPreset(nextPresetId: string) {
    setPresetId(nextPresetId)
    if (nextPresetId === CUSTOM_PROVIDER && !activeProvider) {
      setDisplayName('')
      setType('openai-compatible')
      setBaseUrl('')
      return
    }
    const preset = presetsQuery.data?.presets.find((p) => p.id === nextPresetId)
    if (!preset) return
    setDisplayName(preset.display_name)
    setType(preset.type)
    setBaseUrl(preset.base_url)
  }

  function toggleRole(role: string) {
    setRoles((prev) =>
      prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role],
    )
  }

  function toggleModel(modelId: string) {
    setSelectedModels((current) => (
      current.includes(modelId) ? current.filter((id) => id !== modelId) : [...current, modelId]
    ))
  }

  async function discover() {
    if (!activeProvider) return
    setDiscovering(true)
    setError('')
    try {
      const result = await api.providerModels(activeProvider.provider_id)
      if (result.ok) {
        setAvailableModels(result.models)
        setSelectedModels(result.models.map((model) => model.id))
      } else {
        setError(result.error ?? t('providers.discoverFail'))
      }
    } catch (exc) {
      setError(String(exc))
    } finally {
      setDiscovering(false)
    }
  }

  async function save() {
    setSaving(true)
    setError('')
    try {
      // Selected discovered models form the provider whitelist; an empty list
      // triggers discovery after the connection is saved.
      const body = {
        display_name: displayName,
        type,
        base_url: baseUrl,
        roles,
        models: selectedModels.map((id) => availableModels.find((model) => model.id === id) ?? { id }),
        api_key: apiKey || null,
      }
      let saved = activeProvider
        ? await api.updateProvider(activeProvider.provider_id, body)
        : await api.createProvider(body)
      setActiveProvider(saved)
      // Auto-configure after save (Open WebUI: "Once the connection is saved,
      // the provider's models are pulled automatically"): test the connection,
      // and when the manual list is empty, discover models and store them.
      if (body.models.length === 0) {
        try {
          const found = await api.providerModels(saved.provider_id)
          if (found.ok && found.models.length) {
            saved = await api.updateProvider(saved.provider_id, { models: found.models })
            setActiveProvider(saved)
          }
        } catch {
          /* discovery is best-effort; the connection may still be usable */
        }
      }
      if (type === 'custom') {
        // Custom adapters have no protocol-level test yet; saving the config is
        // still useful and keeps the provider explicitly marked for manual verification.
        onSaved()
        return
      }
      const tested = await api.testProvider(saved.provider_id)
      if (!tested.ok) {
        setError(t('providers.savedButTestFail') + (tested.error ? `: ${tested.error}` : ''))
        return
      }
      onSaved()
    } catch (exc) {
      setError(String(exc))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog wide" onClick={(event) => event.stopPropagation()}>
        <h2>{activeProvider ? t('providers.edit') : t('providers.add')}</h2>
        <label className="field">
          {t('providers.preset')}
          <select value={presetId} onChange={(e) => applyPreset(e.target.value)}>
            <option value="">{t('providers.choosePreset')}</option>
            {presetsQuery.data?.presets.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.display_name}
                {preset.requires_key === false ? ` (${t('providers.noKey')})` : ''}
              </option>
            ))}
            <option value={CUSTOM_PROVIDER}>{t('providers.presetManual')}</option>
          </select>
          <span className="muted strategy-help">{t('providers.presetHelp')}</span>
        </label>
        {presetId && presetId !== CUSTOM_PROVIDER ? (
          <dl className="facts compact-facts provider-summary">
            <dt>{t('providers.displayName')}</dt><dd>{displayName}</dd>
            <dt>{t('providers.type')}</dt><dd>{displayEnum('provider_type', type, lang)}</dd>
            <dt>{t('providers.baseUrl')}</dt>            <dd className="mono">{baseUrl || '—'}</dd>
            {t(`providers.note.${presetId}`) !== `providers.note.${presetId}` && (
              <>
                <dt>{t('providers.splitNote')}</dt>
                <dd>{t(`providers.note.${presetId}`)}</dd>
              </>
            )}
          </dl>
        ) : presetId === CUSTOM_PROVIDER ? (
          <>
            <label className="field">
              {t('providers.displayName')}
              <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </label>
            <label className="field">
              {t('providers.type')}
              <select value={type} onChange={(e) => setType(e.target.value)}>
                {TYPES.map((value) => (
                  <option key={value} value={value}>{displayEnum('provider_type', value, lang)}</option>
                ))}
              </select>
            </label>
            <label className="field">
              {t('providers.baseUrl')}
              <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…" />
            </label>
          </>
        ) : null}
        {selectedPreset?.requires_key !== false && (
          <label className="field">
            {t('providers.apiKey')}
            <input
              type="password"
              value={apiKey}
              onChange={(e) => onKeyChange(e.target.value)}
              placeholder={activeProvider?.configured ? t('providers.keyPlaceholder') : t('providers.keyPh')}
            />
            {activeProvider?.configured && <span className="ok">{t('providers.configured')}</span>}
          </label>
        )}
        {detectResult?.detected && (
          <div className="detect-hint">
            <span className="ok">{t('providers.detected')} {detectResult.suggested?.display_name}</span>
            {detectResult.warning && <div className="muted">{t('providers.detectKimiSplit')}</div>}
            {detectResult.candidates.length > 1 && (
              <span className="muted">
                {' '}{t('providers.detectSwitch')}{' '}
                {detectResult.candidates.map((candidate) => (
                  <button key={candidate.base_url} className="mini" onClick={() => applyCandidate(candidate)}>
                    {candidate.display_name}
                  </button>
                ))}
              </span>
            )}
          </div>
        )}
        <div className="field">
          {t('providers.roles')}
          <div className="tag-grid">
            {ROLES.map((role) => (
              <label key={role} className="tag">
                <input
                  type="checkbox"
                  checked={roles.includes(role)}
                  onChange={() => toggleRole(role)}
                />
                {displayEnum('provider_role', role, lang)}
              </label>
            ))}
          </div>
        </div>
        <div className="field">
          {t('providers.modelsTitle')}
          {availableModels.length > 0 ? (
            <div className="choice-grid model-choice-grid">
              {availableModels.map((model) => (
                <label key={model.id} className="choice-option">
                  <input
                    type="checkbox"
                    checked={selectedModels.includes(model.id)}
                    onChange={() => toggleModel(model.id)}
                  />
                  <span><strong>{model.alias || model.id}</strong><small>{model.id}</small></span>
                </label>
              ))}
            </div>
          ) : (
            <span className="muted">{t('providers.modelsAuto')}</span>
          )}
          <span className="muted strategy-help">{t('providers.modelsHelp')}</span>
          <div className="actions" style={{ marginTop: 8 }}>
            <button className="secondary" disabled={!activeProvider || discovering || !['openai-compatible', 'local'].includes(type)} onClick={discover}>
              {discovering ? t('providers.discovering') : t('providers.discover')}
            </button>
          </div>
        </div>
        <p className="muted">{t('providers.autoConfig')}</p>
        {error && <div className="error">{error}</div>}
        <div className="actions">
          <button onClick={save} disabled={saving || !displayName.trim()}>
            {saving ? t('common.loading') : activeProvider ? t('providers.save') : t('providers.saveConnect')}
          </button>
          <button className="secondary" onClick={onClose}>{t('replay.cancel')}</button>
        </div>
      </div>
    </div>
  )
}
