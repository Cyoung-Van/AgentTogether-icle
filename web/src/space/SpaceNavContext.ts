import { createContext, useContext } from 'react'
import type { SpaceController } from './engine/SpaceController'

export type SpaceNavValue = {
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

export const SpaceNavContext = createContext<SpaceNavValue | null>(null)

export function useSpaceNav(): SpaceNavValue {
  const value = useContext(SpaceNavContext)
  if (!value) throw new Error('useSpaceNav must be used inside SpaceNavProvider')
  return value
}
