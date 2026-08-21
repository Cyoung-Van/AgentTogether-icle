function parsed(value?: string | null): Date | null {
  if (!value) return null
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
  const date = new Date(hasTimezone ? value : `${value}Z`)
  return Number.isNaN(date.getTime()) ? null : date
}

function two(value: number): string {
  return String(value).padStart(2, '0')
}

/** Stable compact display using the browser/computer's current local timezone. */
export function formatLocalDateTime(value?: string | null): string {
  const date = parsed(value)
  if (!date) return value || '—'
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}`
}

/** Stable local calendar date using the browser/computer's current timezone. */
export function formatLocalDate(value?: string | null): string {
  const date = parsed(value)
  if (!date) return value || '—'
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())}`
}
