export function optionLabel(value: string, maxLength = 72): string {
  const compact = value.replace(/\s+/g, ' ').trim()
  if (compact.length <= maxLength) return compact
  return `${compact.slice(0, Math.max(1, maxLength - 1))}…`
}
