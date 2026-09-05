export const ENTER_MS = 820
export const EXIT_MS = 680
export const PRESS_MS = 90
export const HANDOFF_MS = 560

export function easeOutExpo(t: number): number {
  return t >= 1 ? 1 : 1 - 2 ** (-10 * t)
}

export function easeOutQuad(t: number): number {
  return 1 - (1 - t) ** 2
}

export function easeOutCubic(t: number): number {
  return 1 - (1 - t) ** 3
}

export function easeOutBack(t: number): number {
  const c1 = 1.20158
  const c3 = c1 + 1
  return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2
}

export function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function hasFinePointer(): boolean {
  return window.matchMedia('(hover: hover) and (pointer: fine)').matches
}

export function isCompactViewport(): boolean {
  return window.matchMedia('(max-width: 760px)').matches
}

export function easeOutQuint(t: number): number {
  return 1 - (1 - t) ** 5
}

export function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}
