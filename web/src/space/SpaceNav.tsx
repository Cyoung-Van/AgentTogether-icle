import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { SpaceController } from './engine/SpaceController'
import { galaxyForPath, parentPath } from './hierarchy'
import { prefersReducedMotion } from './motion'

type SpaceNavValue = {
  locked: boolean
  lastFocusId: string | null
  webgl: boolean
  motionPaused: boolean
  enter: (path: string, nodeId: string) => void
  back: () => void
  home: () => void
  setMotionPaused: (value: boolean) => void
  registerController: (controller: SpaceController | null) => void
  setWebgl: (value: boolean) => void
}

const SpaceNavContext = createContext<SpaceNavValue | null>(null)

const FOCUS_KEY = 'icle-space-focus'

function writeFocus(nodeId: string) {
  sessionStorage.setItem(FOCUS_KEY, nodeId)
}

export function SpaceNavProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()
  const [locked, setLocked] = useState(false)
  const [webgl, setWebgl] = useState(true)
  const [motionPaused, setMotionPausedState] = useState(false)
  const [lastFocusId, setLastFocusId] = useState<string | null>(() => sessionStorage.getItem(FOCUS_KEY))
  const controllerRef = useRef<SpaceController | null>(null)
  const lockedRef = useRef(false)
  const pathRef = useRef(location.pathname)
  const prevPath = useRef(location.pathname)
  const tokenRef = useRef(0)
  pathRef.current = location.pathname
  lockedRef.current = locked

  const registerController = useCallback((controller: SpaceController | null) => {
    controllerRef.current = controller
    if (controller) {
      controller.setPaused(motionPaused)
      controller.syncRoute(pathRef.current)
    }
  }, [])

  const finish = useCallback((token: number) => {
    if (tokenRef.current !== token) return
    lockedRef.current = false
    setLocked(false)
  }, [])

  const setMotionPaused = useCallback((value: boolean) => {
    setMotionPausedState(value)
    controllerRef.current?.setPaused(value)
  }, [])

  const enter = useCallback((path: string, nodeId: string) => {
    if (lockedRef.current || path === pathRef.current) return
    writeFocus(nodeId)
    setLastFocusId(nodeId)
    const reduce = prefersReducedMotion()
    const controller = controllerRef.current
    if (reduce || !controller || !webgl) {
      navigate(path)
      return
    }
    tokenRef.current += 1
    const token = tokenRef.current
    lockedRef.current = true
    setLocked(true)
    controller.playEnter(nodeId, path, {
      onCovered: () => {
        if (tokenRef.current === token) navigate(path)
      },
      onDone: () => finish(token),
    })
  }, [finish, navigate, webgl])

  const retreat = useCallback((to: string) => {
    if (lockedRef.current || to === pathRef.current) return
    const reduce = prefersReducedMotion()
    const controller = controllerRef.current
    if (reduce || !controller || !webgl) {
      navigate(to)
      return
    }
    tokenRef.current += 1
    const token = tokenRef.current
    lockedRef.current = true
    setLocked(true)
    controller.playReturn(to, {
      onCovered: () => {
        if (tokenRef.current === token) navigate(to)
      },
      onDone: () => finish(token),
    })
  }, [finish, navigate, webgl])

  const back = useCallback(() => {
    const parent = parentPath(pathRef.current)
    if (!parent) return
    const fromGalaxy = galaxyForPath(pathRef.current)
    const toGalaxy = galaxyForPath(parent)
    if (fromGalaxy || toGalaxy) retreat(parent)
    else navigate(parent)
  }, [navigate, retreat])

  const home = useCallback(() => {
    if (pathRef.current === '/') return
    if (galaxyForPath(pathRef.current) || galaxyForPath('/')) retreat('/')
    else navigate('/')
  }, [navigate, retreat])

  useEffect(() => {
    const from = prevPath.current
    prevPath.current = location.pathname
    if (from === location.pathname) return
    if (lockedRef.current) return
    tokenRef.current += 1
    controllerRef.current?.syncRoute(location.pathname)
  }, [location.pathname])

  const value = useMemo<SpaceNavValue>(() => ({
    locked,
    lastFocusId,
    webgl,
    motionPaused,
    enter,
    back,
    home,
    setMotionPaused,
    registerController,
    setWebgl,
  }), [back, enter, home, lastFocusId, locked, motionPaused, registerController, setMotionPaused, webgl])

  return <SpaceNavContext.Provider value={value}>{children}</SpaceNavContext.Provider>
}

export function useSpaceNav(): SpaceNavValue {
  const value = useContext(SpaceNavContext)
  if (!value) throw new Error('useSpaceNav must be used inside SpaceNavProvider')
  return value
}
