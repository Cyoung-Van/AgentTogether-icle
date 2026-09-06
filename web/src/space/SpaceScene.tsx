import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useLang } from '../i18n'
import { useTheme } from '../theme'
import { SpaceController } from './engine/SpaceController'
import { galaxyForPath } from './hierarchy'
import SpaceFallback from './SpaceFallback'
import SystemInfo from './SystemInfo'
import { useSpaceNav } from './SpaceNavContext'

export default function SpaceScene() {
  const location = useLocation()
  const { t, lang } = useLang()
  const { theme } = useTheme()
  const { enter, back, locked, lastFocusId, motionPaused, setMotionPaused, registerController, setWebgl, webgl } = useSpaceNav()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const hostRef = useRef<HTMLDivElement>(null)
  const labelsRef = useRef<Map<string, HTMLElement>>(new Map())
  const centerInfoRef = useRef<HTMLDivElement>(null)
  const controllerRef = useRef<SpaceController | null>(null)
  const [attractedId, setAttractedId] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const galaxy = galaxyForPath(location.pathname)

  useEffect(() => {
    const canvas = canvasRef.current
    const host = hostRef.current
    if (!canvas || !host) return
    let controller: SpaceController
    try {
      controller = new SpaceController(canvas, {
        onAttracted: (id) => setAttractedId(id),
      })
    } catch (error) {
      console.error('SpaceController failed to start', error)
      setFailed(true)
      setWebgl(false)
      return
    }
    controllerRef.current = controller
    registerController(controller)
    controller.setLabels(labelsRef.current)
    controller.setCenterInfo(centerInfoRef.current)
    return () => {
      registerController(null)
      controllerRef.current = null
      controller.dispose()
    }
  }, [registerController, setWebgl])

  useEffect(() => {
    controllerRef.current?.setLabels(labelsRef.current)
    controllerRef.current?.setCenterInfo(centerInfoRef.current)
  }, [galaxy])

  useEffect(() => {
    controllerRef.current?.setTheme(theme)
  }, [theme])

  if (failed || !webgl) {
    return galaxy ? <SpaceFallback galaxy={galaxy} /> : null
  }

  const nodes = galaxy ? [galaxy.center, ...galaxy.children] : []

  return (
    <div ref={hostRef} lang={lang === 'zh' ? 'zh-CN' : 'en'} className={`space-scene ${galaxy ? 'is-field' : 'is-workspace'}`} aria-hidden={!galaxy}>
      <canvas
        ref={canvasRef}
        className="space-webgl"
        onPointerUp={(event) => {
          if (locked || !galaxy) return
          if (Math.hypot(event.movementX, event.movementY) > 6) return
          const id = controllerRef.current?.pick(event.clientX, event.clientY)
          if (!id) return
          if (id === galaxy.center.id) {
            if (galaxy.parent) back()
            return
          }
          const node = galaxy.children.find((item) => item.id === id)
          if (node) enter(node.path, node.id)
        }}
      />
      {galaxy && (
        <div ref={centerInfoRef} className="space-body-info" data-system-id={galaxy.id} aria-hidden={locked}>
          <SystemInfo galaxy={galaxy} motionPaused={motionPaused} />
        </div>
      )}
      {galaxy && (
        <div className="planet-field-copy">
          <p className="planet-field-kicker">{t(galaxy.id === 'home' ? 'space.stellarSystem' : 'space.planetarySystem')}</p>
          <h1>{galaxy.id === 'home' ? t('space.systemTitle') : t(galaxy.titleKey)}</h1>
          <p className="planet-field-description">{t(galaxy.id === 'home' ? 'space.stellarHint' : 'space.planetaryHint')}</p>
          <button
            type="button"
            className="space-pause"
            onClick={() => setMotionPaused(!motionPaused)}
          >
            {motionPaused ? t('space.resumeMotion') : t('space.pauseMotion')}
          </button>
        </div>
      )}
      {nodes.map((node) => {
        const isCenter = galaxy?.center.id === node.id
        const canReturn = Boolean(isCenter && galaxy?.parent)
        const label = t(node.titleKey)
        const parentName = galaxy?.parent ? t(galaxy.parent.titleKey) : ''
        const blurb = t(node.blurbKey)
        const showBlurb = !isCenter && (attractedId === node.id || lastFocusId === node.id)
        const returnHint = `${t('space.returnPrefix')}${parentName}`
        if (isCenter && !canReturn) {
          return (
            <div
              key={node.id}
              data-node-id={node.id}
              className="space-label is-center is-root"
              ref={(el) => {
                if (el) labelsRef.current.set(node.id, el)
                else labelsRef.current.delete(node.id)
              }}
            >
              <span className="space-label-name">{t('space.brand')}</span>
              <span className="space-label-here">{t('space.centralStar')}</span>
            </div>
          )
        }
        return (
          <button
            key={node.id}
            type="button"
            className={`space-label ${isCenter ? 'is-center' : ''}`}
            ref={(el) => {
              if (el) labelsRef.current.set(node.id, el)
              else labelsRef.current.delete(node.id)
            }}
            aria-label={canReturn ? `${label}. ${t('space.youAreHere')}. ${returnHint}` : `${t('space.enterPrefix')}${label}`}
            disabled={locked}
            data-node-id={node.id}
            onFocus={() => controllerRef.current?.setFocus(node.id)}
            onBlur={() => controllerRef.current?.setFocus(null)}
            onPointerEnter={() => controllerRef.current?.setHover(node.id)}
            onPointerLeave={() => controllerRef.current?.setHover(null)}
            onClick={() => {
              if (canReturn) back()
              else enter(node.path, node.id)
            }}
          >
            {!isCenter && <span className="space-label-name">{label}</span>}
            {canReturn ? (
              <><span className="space-label-here">{t('space.youAreHere')}</span><span className="space-label-return">{returnHint}</span></>
            ) : (
              <span className={`space-label-blurb ${showBlurb ? 'is-on' : ''}`}>{blurb}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}
