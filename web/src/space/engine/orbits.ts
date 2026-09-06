import type { PlanetNode } from '../types'

export type OrbitParams = {
  a: number
  b: number
  inc: number
  lan: number
  arg: number
  phase: number
  period: number
}

// Stable, non-intersecting radial shells; independent planes, phase and local clocks.
export const CAM_REST = {
  desktop: { x: 0, y: 0, z: 17.5 },
  mobile: { x: 0, y: 0, z: 20 },
  fov: 48,
}
export const CENTER_RADIUS: Record<string, number> = { home: 1.12, 'task-studio': 1.02, agents: 1.02, today: 1.02, sessions: 1.02, settings: 1.02 }
export const SAT_RADIUS: Record<string, number> = { 'task-studio': .4, agents: .38 }
export const ORBIT_SLOTS = [
  { a: 2.35, inc: 1.20, lan: .12, arg: .2, phase: .35, period: 216 },
  { a: 3.35, inc: 1.34, lan: -.18, arg: .35, phase: 1.8, period: 232 },
  { a: 4.35, inc: 1.13, lan: .25, arg: -.22, phase: 3.0, period: 248 },
  { a: 5.35, inc: 1.39, lan: -.32, arg: .16, phase: 4.25, period: 268 },
  { a: 6.35, inc: 1.26, lan: .18, arg: -.12, phase: 5.4, period: 292 },
] as const

export function orbitFor(_id: string, index: number, _count: number, _compact: boolean): OrbitParams {
  const slot = ORBIT_SLOTS[index % ORBIT_SLOTS.length]
  return { ...slot, b: slot.a * .98 }
}

// Conservative envelope for every phase, including DOM labels and camera drift.
// Framing changes the camera, never the orbital path when the viewport changes.
export function cameraDistance(aspect: number, count: number, height = 700): number {
  const radius = ORBIT_SLOTS[Math.max(0, count - 1) % ORBIT_SLOTS.length].a
  const halfH = Math.tan(CAM_REST.fov * Math.PI / 360)
  const horizontal = Math.max(.2, aspect * .90)
  const vertical = Math.max(.5, 1 - 60 / Math.max(280, height))
  return Math.max(9, (radius + .45) / (halfH * Math.min(horizontal, vertical)) + radius * .44)
}

export function centerRadius(id: string): number {
  return CENTER_RADIUS[id] ?? 0.72
}

export function satRadius(id: string): number {
  return SAT_RADIUS[id] ?? 0.32
}

export function nodeRadius(node: PlanetNode, role: 'center' | 'sat'): number {
  return role === 'center' ? centerRadius(node.id) : satRadius(node.id)
}

export function orbitAngle(params: OrbitParams, orbitTime: number): number {
  return params.phase + (Math.PI * 2 * orbitTime) / params.period
}

export function orbitOffset(
  params: OrbitParams,
  theta: number,
  out: { x: number; y: number; z: number },
  radiusScale = 1,
) {
  const lx = params.a * radiusScale * Math.cos(theta)
  const lz = params.b * radiusScale * Math.sin(theta)
  const cA = Math.cos(params.arg)
  const sA = Math.sin(params.arg)
  const x1 = lx * cA + lz * sA
  const z1 = -lx * sA + lz * cA
  const cI = Math.cos(params.inc)
  const sI = Math.sin(params.inc)
  const y2 = -z1 * sI
  const z2 = z1 * cI
  const cL = Math.cos(params.lan)
  const sL = Math.sin(params.lan)
  out.x = x1 * cL - y2 * sL
  out.y = x1 * sL + y2 * cL
  out.z = z2
}
