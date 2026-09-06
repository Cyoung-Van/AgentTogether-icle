import { useTheme } from '../theme'
import { useLocation } from 'react-router-dom'
import { isGalaxyPath } from './hierarchy'

// Broad illumination anchors the composition; real parallax lives in the scene.
export default function SpaceBackground() {
  const { theme } = useTheme()
  const { pathname } = useLocation()
  return (
    <div className="space-sky" data-theme={theme} data-view={isGalaxyPath(pathname) ? 'system' : 'workspace'} aria-hidden="true">
      <div className="space-sky-far" />
      <div className="space-sky-near" />
    </div>
  )
}
