import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { formatLocalDateTime } from '../lib/dateTime'
import { displayEnum } from '../lib/displayLabel'

const BUDGET_OPTIONS = [1, 3, 5, 10, 20, 50, 100]

export default function Settings() {
  const { t, lang, setLang } = useLang()
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['settings'], queryFn: api.settings })
  const [replayPerDay, setReplayPerDay] = useState(5)
  const [defaultLanguage, setDefaultLanguage] = useState<'zh' | 'en'>('zh')
  const [valuePolicy, setValuePolicy] = useState('balanced')
  const [modelChoice, setModelChoice] = useState('')
  const [modelSaving, setModelSaving] = useState(false)
  const [budgetEnabled, setBudgetEnabled] = useState(false)
  const [monthlyBudget, setMonthlyBudget] = useState('')
  const [warningPercent, setWarningPercent] = useState(80)
  const [saved, setSaved] = useState(false)
  const [pricingRefreshing, setPricingRefreshing] = useState(false)
  const [pricingMessage, setPricingMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!query.data) return
    const data = query.data
    setReplayPerDay(Number(data.replay_per_day) || 5)
    setDefaultLanguage(data.default_language === 'en' ? 'en' : 'zh')
    setValuePolicy(data.value_policy || 'balanced')
    setModelChoice(data.intelligence_selection ? `${data.intelligence_selection.provider_id}/${data.intelligence_selection.model_id}` : '')
    setBudgetEnabled(data.monthly_cost_budget_usd != null)
    setMonthlyBudget(data.monthly_cost_budget_usd == null ? '' : String(data.monthly_cost_budget_usd))
    setWarningPercent(Number(data.cost_warning_percent) || 80)
  }, [query.data])

  if (query.isPending) return <div className="empty">{t('common.loading')}</div>
  if (query.isError) return <div className="empty error">{t('settings.failed')}</div>
  const settings = query.data
  const budgetStatus = settings.cost_budget ?? {}

  async function selectModel(value: string) {
    if (!value) return
    const [providerId, ...modelParts] = value.split('/')
    const modelId = modelParts.join('/')
    setModelSaving(true)
    setError('')
    try {
      await api.selectIntelligenceModel(providerId, modelId)
      setModelChoice(value)
      queryClient.invalidateQueries({ queryKey: ['settings'] })
      queryClient.invalidateQueries({ queryKey: ['intelligence-status'] })
      queryClient.invalidateQueries({ queryKey: ['intelligence-models'] })
    } catch (exc) {
      setError(String(exc))
    } finally {
      setModelSaving(false)
    }
  }

  async function refreshPricing() {
    setPricingRefreshing(true)
    setPricingMessage('')
    setError('')
    try {
      const result = await api.refreshPricingCatalog()
      setPricingMessage(result.changed ? t('settings.pricingUpdated') : t('settings.pricingUnchanged'))
      queryClient.invalidateQueries({ queryKey: ['settings'] })
      queryClient.invalidateQueries({ queryKey: ['agents'] })
      queryClient.invalidateQueries({ queryKey: ['agent'] })
    } catch (exc) {
      setError(String(exc))
    } finally {
      setPricingRefreshing(false)
    }
  }

  async function save() {
    setError('')
    setSaved(false)
    try {
      const next = await api.updateSettings({
        replay_per_day: replayPerDay,
        default_language: defaultLanguage,
        value_policy: valuePolicy,
        cost_budget_enabled: budgetEnabled,
        monthly_cost_budget_usd: budgetEnabled ? Number(monthlyBudget) : null,
        cost_warning_percent: warningPercent,
      })
      setSaved(true)
      queryClient.setQueryData(['settings'], next)
      if (defaultLanguage !== lang) setLang(defaultLanguage)
      queryClient.invalidateQueries({ queryKey: ['intelligence-models'] })
      setTimeout(() => setSaved(false), 2500)
    } catch (exc) {
      setError(String(exc))
    }
  }

  return (
    <div>
      <header className="page-hero">
        <h1>{t('settings.title')}</h1>
        <p className="page-guide">{t('settings.localOnly')}</p>
      </header>

      <section>
        <div className="section-head">
          <h2>{t('settings.places')}</h2>
        </div>
        <p className="page-guide">{t('settings.placesHelp')}</p>
        <div className="grouped-box">
          <Link to="/providers" className="grouped-row">
            <span>{t('nav.providers')}</span>
            <span className="chevron" aria-hidden="true">›</span>
          </Link>
          <Link to="/agents/local" className="grouped-row">
            <span>{t('nav.localAgents')}</span>
            <span className="chevron" aria-hidden="true">›</span>
          </Link>
          <Link to="/cost" className="grouped-row">
            <span>{t('nav.cost')}</span>
            <span className="chevron" aria-hidden="true">›</span>
          </Link>
          <Link to="/episodes" className="grouped-row">
            <span>{t('nav.episodes')}</span>
            <span className="chevron" aria-hidden="true">›</span>
          </Link>
          <Link to="/activity" className="grouped-row">
            <span>{t('nav.activity')}</span>
            <span className="chevron" aria-hidden="true">›</span>
          </Link>
          <Link to="/collabs" className="grouped-row">
            <span>{t('nav.collabs')}</span>
            <span className="chevron" aria-hidden="true">›</span>
          </Link>
          <Link to="/recommend" className="grouped-row">
            <span>{t('nav.recommend')}</span>
            <span className="chevron" aria-hidden="true">›</span>
          </Link>
        </div>
      </section>

      <section className="panel">
        <h2>{t('settings.general')}</h2>
        <div className="form-grid">
          <label className="field">
            {t('settings.language')}
            <select value={defaultLanguage} onChange={(event) => setDefaultLanguage(event.target.value as 'zh' | 'en')}>
              <option value="zh">中文</option>
              <option value="en">English</option>
            </select>
          </label>
          <label className="field">
            {t('settings.valuePolicy')}
            <select value={valuePolicy} onChange={(event) => setValuePolicy(event.target.value)}>
              {['quality_first', 'balanced', 'cost_first', 'speed_first'].map((policy) => (
                <option key={policy} value={policy}>{displayEnum('policy', policy, lang)}</option>
              ))}
            </select>
          </label>
        </div>
        <p className="muted">{t('settings.valuePolicyHelp')}</p>
      </section>

      <section className="panel">
        <h2>{t('settings.intelligenceSettings')}</h2>
        <label className="field">
          {t('settings.intelligenceModel')}
          <select value={modelChoice} onChange={(event) => void selectModel(event.target.value)} disabled={modelSaving || !(settings.intelligence_models?.length)}>
            <option value="">{settings.intelligence_models?.length ? t('settings.chooseModel') : t('settings.noModels')}</option>
            {(settings.intelligence_models ?? []).map((model: any) => (
              <option key={`${model.provider_id}/${model.model_id}`} value={`${model.provider_id}/${model.model_id}`}>
                {model.provider} · {model.model} ({model.model_id})
              </option>
            ))}
          </select>
        </label>
        <p className="muted">{t('settings.intelligenceModelHelp')}</p>
      </section>

      <section className="panel">
        <div className="title-row">
          <h2>{t('settings.pricingCatalog')}</h2>
          <button className="secondary" disabled={pricingRefreshing} onClick={() => void refreshPricing()}>
            {pricingRefreshing ? t('settings.pricingRefreshing') : t('settings.pricingRefresh')}
          </button>
        </div>
        <p className="muted">{t('settings.pricingHelp')}</p>
        <dl className="facts">
          <dt>{t('settings.pricingSource')}</dt>
          <dd><a href={settings.pricing_catalog.repository} target="_blank" rel="noreferrer">{settings.pricing_catalog.name}</a></dd>
          <dt>{t('settings.pricingLicense')}</dt>
          <dd><a href={settings.pricing_catalog.license_url} target="_blank" rel="noreferrer">{settings.pricing_catalog.license}</a></dd>
          <dt>{t('settings.pricingSchedule')}</dt><dd>{t('settings.pricingScheduleValue')}</dd>
          <dt>{t('settings.pricingRetrieved')}</dt><dd>{formatLocalDateTime(settings.pricing_catalog.retrieved_at)}</dd>
          <dt>{t('settings.pricingCoverage')}</dt><dd>{settings.pricing_catalog.providers ?? 0} / {settings.pricing_catalog.priced_models ?? 0}</dd>
          <dt>{t('settings.pricingFallback')}</dt>
          <dd>
            {settings.pricing_catalog.fallback?.available
              ? `${settings.pricing_catalog.fallback.priced_models ?? 0} LiteLLM`
              : '—'}
          </dd>
          <dt>SHA-256</dt><dd className="mono">{settings.pricing_catalog.content_hash ?? '—'}</dd>
        </dl>
        {settings.pricing_catalog.last_error && <p className="muted">{t('settings.pricingLastError')}: {settings.pricing_catalog.last_error}</p>}
        {pricingMessage && <p className="ok">{pricingMessage}</p>}
      </section>

      <section className="panel">
        <h2>{t('settings.replay')}</h2>
        <div className="form-grid">
          <label className="field">
            {t('settings.dailyBudget')}
            <select value={replayPerDay} onChange={(event) => setReplayPerDay(Number(event.target.value))}>
              {[...new Set([...BUDGET_OPTIONS, replayPerDay])].sort((a, b) => a - b).map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
          <div className="field">
            <span>{t('settings.replayToday')}</span>
            <strong>{settings.replay_used_today} / {settings.replay_per_day}</strong>
            <span className="muted">{t('settings.replayRemaining')} {settings.replay_remaining_today}</span>
          </div>
        </div>
        <p className="muted">{t('settings.replayHelp')}</p>
      </section>

      <section className="panel">
        <h2>{t('settings.costBudget')}</h2>
        <label className="choice-option">
          <input type="checkbox" checked={budgetEnabled} onChange={(event) => setBudgetEnabled(event.target.checked)} />
          <span><strong>{t('settings.enableCostBudget')}</strong><small>{t('settings.costBudgetHelp')}</small></span>
        </label>
        {budgetEnabled && <div className="form-grid" style={{ marginTop: 12 }}>
          <label className="field">
            {t('settings.monthlyBudget')}
            <input type="number" min="0.01" step="0.01" value={monthlyBudget} onChange={(event) => setMonthlyBudget(event.target.value)} placeholder="10.00" />
          </label>
          <label className="field">
            {t('settings.warningPercent')}
            <input type="number" min="1" max="100" value={warningPercent} onChange={(event) => setWarningPercent(Number(event.target.value))} />
          </label>
        </div>}
        <div className="row-meta" style={{ marginTop: 12 }}>
          <span className="badge dim">{t('settings.spentThisMonth')} ${Number(budgetStatus.spent ?? 0).toFixed(4)}</span>
          {budgetStatus.budget != null && <span className="badge dim">/ ${Number(budgetStatus.budget).toFixed(2)}</span>}
          {budgetStatus.warning && <span className="badge">{t('settings.budgetWarning')}</span>}
        </div>
      </section>

      <section className="panel">
        <h2>{t('settings.providers')}</h2>
        <dl className="facts">
          <dt>{t('settings.intelligence')}</dt><dd>{settings.intelligence_provider}</dd>
          <dt>{t('settings.models')}</dt><dd>{settings.intelligence_models?.length ?? 0}</dd>
          <dt>{t('settings.providerCount')}</dt><dd>{settings.providers?.connected ?? 0} / {settings.providers?.total ?? 0} {t('settings.connected')}</dd>
          <dt>{t('settings.execution')}</dt><dd>{settings.execution_provider}</dd>
        </dl>
        <div className="actions">
          <Link className="secondary link-as-button" to="/providers">{t('settings.manageProviders')}</Link>
        </div>
      </section>

      <section className="panel">
        <h2>{t('settings.data')}</h2>
        <div className="cards">
          <div className="card"><div className="metric">{settings.data?.tasks ?? 0}</div><div className="label">{t('settings.tasks')}</div></div>
          <div className="card"><div className="metric">{settings.data?.episodes ?? 0}</div><div className="label">{t('settings.episodes')}</div></div>
          <div className="card"><div className="metric">{settings.data?.cost_records ?? 0}</div><div className="label">{t('settings.costRecords')}</div></div>
        </div>
        <p className="muted">{t('settings.dataHelp')}</p>
      </section>

      <section className="panel">
        <h2>{t('settings.safety')}</h2>
        <div className="row-meta">
          <span className="badge dim">✓ {t('settings.approvalRequired')}</span>
          <span className="badge dim">✓ {t('settings.appendOnly')}</span>
          <span className="badge dim">✓ {t('settings.secretsMasked')}</span>
          <span className="badge dim">✓ {t('settings.replayIsolated')}</span>
        </div>
      </section>

      {error && <div className="error">{error}</div>}
      <div className="actions">
        <button onClick={save}>{saved ? t('settings.saved') : t('settings.save')}</button>
      </div>
    </div>
  )
}
