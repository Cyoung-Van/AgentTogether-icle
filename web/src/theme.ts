import { useSyncExternalStore } from 'react'

export type Theme = 'light' | 'dark'
const STORAGE_KEY = 'agenttogether.theme'
const listeners = new Set<() => void>()
let currentTheme: Theme = 'light'

export function initializeTheme() {
  try {
    currentTheme = window.localStorage.getItem(STORAGE_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    currentTheme = 'light'
  }
  document.documentElement.dataset.theme = currentTheme
}

function setTheme(theme: Theme) {
  if (theme === currentTheme) return
  currentTheme = theme
  document.documentElement.dataset.theme = theme
  try {
    window.localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // The current session can still change appearance when storage is unavailable.
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, () => currentTheme, () => 'light' as Theme)
  return { theme, setTheme }
}
