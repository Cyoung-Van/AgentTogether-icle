import type { ReactNode } from 'react'
import type { IconName } from './types'

function Svg({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true" className="planet-icon">
      {children}
    </svg>
  )
}

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

export function PlanetIcon({ name }: { name: IconName }) {
  switch (name) {
    case 'today':
      return (
        <Svg>
          <circle cx="12" cy="12" r="4.2" {...stroke} />
          <path d="M12 3.2v2.1M12 18.7v2.1M3.2 12h2.1M18.7 12h2.1M5.6 5.6l1.5 1.5M16.9 16.9l1.5 1.5M5.6 18.4l1.5-1.5M16.9 7.1l1.5-1.5" {...stroke} />
        </Svg>
      )
    case 'tasks':
      return (
        <Svg>
          <path d="M5 7h14M5 12h14M5 17h9" {...stroke} />
        </Svg>
      )
    case 'newTask':
      return (
        <Svg>
          <path d="M12 6v12M6 12h12" {...stroke} />
        </Svg>
      )
    case 'episodes':
      return (
        <Svg>
          <rect x="6" y="4.5" width="12" height="15" rx="1.6" {...stroke} />
          <path d="M9 9h6M9 12.5h6" {...stroke} />
        </Svg>
      )
    case 'collabs':
      return (
        <Svg>
          <circle cx="8" cy="9" r="2.2" {...stroke} />
          <circle cx="16" cy="15" r="2.2" {...stroke} />
          <path d="M10 10.2 14 13.8" {...stroke} />
        </Svg>
      )
    case 'agents':
      return (
        <Svg>
          <circle cx="12" cy="8" r="2.4" {...stroke} />
          <path d="M6.5 17.5c.8-2.8 2.6-4.2 5.5-4.2s4.7 1.4 5.5 4.2" {...stroke} />
        </Svg>
      )
    case 'local':
      return (
        <Svg>
          <rect x="4" y="6" width="16" height="10" rx="1.6" {...stroke} />
          <path d="M8 19h8" {...stroke} />
        </Svg>
      )
    case 'recommend':
      return (
        <Svg>
          <circle cx="12" cy="12" r="7" {...stroke} />
          <path d="M12 12 16 8.8M12 7.2V12" {...stroke} />
        </Svg>
      )
    case 'sessions':
      return (
        <Svg>
          <path d="M5.5 8.5h8.5a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H10l-3 2.2V15.5H5.5a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2z" {...stroke} />
        </Svg>
      )
    case 'activity':
      return (
        <Svg>
          <path d="M4 13h3l2.2-5 3.6 9 2.4-4H20" {...stroke} />
        </Svg>
      )
    case 'settings':
      return (
        <Svg>
          <path d="M5 8h14M5 16h14M10 8V5.8M15 16v2.2" {...stroke} />
          <circle cx="10" cy="8" r="1.6" {...stroke} />
          <circle cx="15" cy="16" r="1.6" {...stroke} />
        </Svg>
      )
    case 'providers':
      return (
        <Svg>
          <rect x="5" y="6" width="14" height="12" rx="2" {...stroke} />
          <path d="M9 10h6M9 14h4" {...stroke} />
        </Svg>
      )
    case 'cost':
      return (
        <Svg>
          <path d="M12 6v12M8.5 9.2c.7-1.2 2-1.8 3.5-1.8 2 0 3.5 1 3.5 2.6S14.2 12 12 12s-3.6.8-3.6 2.6 1.6 2.7 3.6 2.7c1.6 0 2.9-.6 3.5-1.8" {...stroke} />
        </Svg>
      )
    default:
      return null
  }
}
