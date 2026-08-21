import { type CSSProperties, useState } from 'react'
import { useLang } from '../i18n'

export function ExpandableText({
  text,
  lines = 4,
  threshold = 240,
  className = '',
  preserveWhitespace = false,
}: {
  text: string | null | undefined
  lines?: number
  threshold?: number
  className?: string
  preserveWhitespace?: boolean
}) {
  const { t } = useLang()
  const [expanded, setExpanded] = useState(false)
  const value = text ?? ''
  const isLong = value.length > threshold || value.split('\n').length > lines

  if (!value) return null

  return (
    <div className={`expandable-text ${preserveWhitespace ? 'preserve-whitespace' : ''} ${className}`.trim()}>
      <div
        className={isLong && !expanded ? 'line-clamp' : ''}
        style={!expanded ? { '--clamp-lines': lines } as CSSProperties : undefined}
      >
        {value}
      </div>
      {isLong && (
        <button
          type="button"
          className="text-expand-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? t('common.showLess') : t('common.showFullText')}
        </button>
      )}
    </div>
  )
}
