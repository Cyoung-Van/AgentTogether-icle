import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

// Wait for the existing query to render its section. Navigation never calls a
// business mutation or replaces the page's QueryClient/cache.
export function useWorkspaceSection(ready: boolean) {
  const { hash, key } = useLocation()
  useEffect(() => {
    if (!ready || !hash) return
    const section = document.getElementById(hash.slice(1))
    if (!section) return
    section.scrollIntoView({ block: 'start', behavior: 'instant' })
    section.focus({ preventScroll: true })
  }, [ready, hash, key])
  return hash.slice(1)
}
