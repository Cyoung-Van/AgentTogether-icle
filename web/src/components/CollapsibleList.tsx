import { Children, type ReactNode, useState } from 'react'
import { useLang } from '../i18n'

export function CollapsibleList({
  children,
  initialCount = 8,
  className = 'list',
}: {
  children: ReactNode
  initialCount?: number
  className?: string
}) {
  const { t } = useLang()
  const [expanded, setExpanded] = useState(false)
  const items = Children.toArray(children)
  const hidden = Math.max(0, items.length - initialCount)

  return (
    <div className={className}>
      {expanded ? items : items.slice(0, initialCount)}
      {hidden > 0 && (
        <div className="collapse-control">
          <button
            type="button"
            className="secondary collapse-toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? t('common.showLess') : `${t('common.showMore')} (${hidden})`}
          </button>
        </div>
      )}
    </div>
  )
}
