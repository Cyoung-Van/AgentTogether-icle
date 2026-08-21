import { Link, useSearchParams } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { useLang } from '../i18n'

const STORAGE_KEY = 'icle-how'

type Setup = {
  agentReady: boolean
  providerReady: boolean
  hasWork: boolean
}

export function HowItWorks({ agentReady, providerReady, hasWork }: Setup) {
  const { t } = useLang()
  const [params, setParams] = useSearchParams()
  const forced = params.get('learn') === '1'
  const [open, setOpen] = useState(() => {
    if (forced || !hasWork) return true
    return localStorage.getItem(STORAGE_KEY) !== 'collapsed'
  })

  useEffect(() => {
    if (forced) setOpen(true)
  }, [forced])

  function collapse() {
    setOpen(false)
    localStorage.setItem(STORAGE_KEY, 'collapsed')
    if (forced) {
      params.delete('learn')
      setParams(params, { replace: true })
    }
  }

  function expand() {
    setOpen(true)
    localStorage.removeItem(STORAGE_KEY)
  }

  const steps = [
    { n: '1', title: t('how.step1'), body: t('how.step1Body') },
    { n: '2', title: t('how.step2'), body: t('how.step2Body') },
    { n: '3', title: t('how.step3'), body: t('how.step3Body') },
    { n: '4', title: t('how.step4'), body: t('how.step4Body') },
  ]
  const checks = [
    {
      done: agentReady,
      title: t('how.checkAgent'),
      help: t('how.checkAgentHelp'),
      to: '/agents/local',
      action: t('how.checkAgentAction'),
    },
    {
      done: providerReady,
      title: t('how.checkProvider'),
      help: t('how.checkProviderHelp'),
      to: '/providers',
      action: t('how.checkProviderAction'),
    },
    {
      done: hasWork,
      title: t('how.checkTask'),
      help: t('how.checkTaskHelp'),
      to: '/tasks/new',
      action: t('nav.newTask'),
    },
  ]
  const next = checks.find((item) => !item.done)

  return (
    <section className="how" aria-labelledby="how-title">
      <div className="section-head">
        <h2 id="how-title">{t('how.title')}</h2>
        {open ? (
          <button type="button" className="text-button" onClick={collapse}>{t('how.hide')}</button>
        ) : (
          <button type="button" className="text-button" onClick={expand}>{t('how.show')}</button>
        )}
      </div>
      <p className="page-guide">{t('how.what')}</p>
      {!open && next && (
        <p className="how-next-line">
          {t('how.nextIs')} <Link to={next.to}>{next.action}</Link>
        </p>
      )}
      {open && (
        <>
          <ol className="how-path">
            {steps.map((step) => (
              <li key={step.n}>
                <span className="how-n" aria-hidden="true">{step.n}</span>
                <strong>{step.title}</strong>
                <span>{step.body}</span>
              </li>
            ))}
          </ol>
          <div className="grouped-box how-checks">
            {checks.map((item) => (
              <div key={item.title} className={`grouped-row how-check ${item.done ? 'is-done' : 'is-todo'}`}>
                <div>
                  <div className="row-title">
                    <span className="how-mark" aria-hidden="true">{item.done ? '✓' : '○'}</span>
                    {item.title}
                  </div>
                  <p className="muted">{item.help}</p>
                </div>
                {!item.done && (
                  <Link to={item.to} className="link-as-button">{item.action}</Link>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  )
}

export function TaskPath({ current }: { current: 'profile' | 'plan' | 'run' | 'review' | 'done' }) {
  const { t } = useLang()
  const items = [
    { id: 'profile', label: t('how.step1') },
    { id: 'plan', label: t('how.step2') },
    { id: 'run', label: t('how.step3') },
    { id: 'review', label: t('how.step4') },
  ] as const
  const index = current === 'done' ? items.length : items.findIndex((item) => item.id === current)
  return (
    <ol className="task-path" aria-label={t('how.title')}>
      {items.map((item, i) => {
        const state = current === 'done' || i < index ? 'done' : i === index ? 'now' : 'todo'
        return (
          <li key={item.id} className={`task-path-item is-${state}`}>
            <span className="how-n" aria-hidden="true">{i + 1}</span>
            <span>{item.label}</span>
          </li>
        )
      })}
    </ol>
  )
}
