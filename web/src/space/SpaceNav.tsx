import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { SpaceController } from './engine/SpaceController'
import { galaxyForPath, parentPath, navigationPath } from './hierarchy'
import { SpaceNavContext, type SpaceNavValue } from './SpaceNavContext'

const FOCUS_KEY = 'icle-space-focus'

function writeFocus(nodeId: string) {
  sessionStorage.setItem(FOCUS_KEY, nodeId)
}

export function SpaceNavProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()
  const currentPath = navigationPath(location)
  const [locked, setLocked] = useState(false)
  const [webgl, setWebgl] = useState(true)
  const [motionPaused, setMotionPausedState] = useState(false)
  const [lastFocusId, setLastFocusId] = useState<string | null>(() => sessionStorage.getItem(FOCUS_KEY))
  const controllerRef = useRef<SpaceController | null>(null)
  const lockedRef = useRef(false)
  const pathRef = useRef(currentPath)
  const previousKey = useRef(location.key)
  const locationRef = useRef(location)
  locationRef.current = location
  const pendingRef = useRef<{ token: number; to: string; committed: boolean; key?: string } | null>(null)
  const pausedRef = useRef(false)
  const tokenRef = useRef(0)
  pathRef.current = currentPath
  lockedRef.current = locked

  const registerController = useCallback((controller: SpaceController | null) => {
    controllerRef.current = controller
    if (controller) {
      controller.setPaused(pausedRef.current)
      controller.syncRoute(pathRef.current)
    }
  }, [])

  const finish = useCallback((token: number) => {
    if (tokenRef.current !== token) return
    lockedRef.current = false
    setLocked(false)
  }, [])

  const setMotionPaused = useCallback((value: boolean) => {
    pausedRef.current = value
    setMotionPausedState(value)
    controllerRef.current?.setPaused(value)
  }, [])

  const travel = useCallback((to: string, nodeId?: string) => {
    if (lockedRef.current || to === pathRef.current) return
    const from = locationRef.current
    const token = ++tokenRef.current
    const previous = from.state?.spaceParent as { path: string; key: string } | undefined
    const canPop = !nodeId && previous?.path === to
    pendingRef.current = { token, to, committed: false, key: canPop ? previous.key : undefined }
    const commit = () => {
      if (tokenRef.current !== token) return
      pendingRef.current!.committed = true
      if (canPop) navigate(-1)
      else navigate(to, { state: { spaceTransition: token, ...(nodeId ? { spaceParent: { path: navigationPath(from), key: from.key } } : {}) } })
    }
    if (nodeId) { writeFocus(nodeId); setLastFocusId(nodeId) }
    const controller = controllerRef.current
    if (!controller || !webgl) { commit(); return }
    lockedRef.current = true
    setLocked(true)
    const hooks = { onCovered: commit, onDone: () => finish(token) }
    if (nodeId) controller.playEnter(nodeId, to, hooks)
    else controller.playReturn(to, hooks)
  }, [finish, navigate, webgl])

  const enter = useCallback((path: string, nodeId: string) => travel(path, nodeId), [travel])
  const retreat = useCallback((path: string) => travel(path), [travel])

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
    if (previousKey.current === location.key) return
    previousKey.current = location.key
    const pending = pendingRef.current
    const ownCommit = pending?.committed && pending.to === currentPath &&
      (pending.key ? pending.key === location.key : location.state?.spaceTransition === pending.token)
    if (!ownCommit) {
      tokenRef.current += 1
      lockedRef.current = false
      setLocked(false)
    }
    controllerRef.current?.syncRoute(currentPath, !ownCommit)
    pendingRef.current = null
  }, [location.key, currentPath, location.state])


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
