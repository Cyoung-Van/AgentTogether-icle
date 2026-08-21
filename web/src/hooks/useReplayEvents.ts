import { useEffect, useRef } from 'react'
import { getApiToken } from '../api/client'

export interface ReplayEvent {
  type: string
  replay_id?: string
  episode_id?: string
  status?: string
  text?: string
}

/** Subscribe to the ICLE event bus (WS /api/events). */
export function useReplayEvents(onEvent: (event: ReplayEvent) => void) {
  const handler = useRef(onEvent)
  handler.current = onEvent

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const token = getApiToken()
    const query = token ? `?token=${encodeURIComponent(token)}` : ''
    const socket = new WebSocket(`${protocol}://${window.location.host}/api/events${query}`)
    socket.onmessage = (message) => {
      try {
        handler.current(JSON.parse(message.data))
      } catch {
        /* malformed event: ignore */
      }
    }
    return () => socket.close()
  }, [])
}
