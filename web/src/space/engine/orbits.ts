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
  { a: 2.35, inc: .82, lan: .12, arg: .2, phase: .35, period: 216 },
  { a: 3.35, inc: .76, lan: -.18, arg: .35, phase: 1.8, period: 232 },
  { a: 4.35, inc: .92, lan: .25, arg: -.22, phase: 3.0, period: 248 },
  { a: 5.35, inc: .78, lan: -.32, arg: .16, phase: 4.25, period: 268 },
  { a: 6.35, inc: .86, lan: .18, arg: -.12, phase: 5.4, period: 292 },
] as const

const PRIMARY_PLANETS = new Set(['today', 'task-studio', 'agents', 'sessions', 'settings'])

export function orbitFor(id: string, index: number, _count: number, _compact: boolean): OrbitParams {
  const slot = ORBIT_SLOTS[index % ORBIT_SLOTS.length]
  // A planetary system has tightly spaced moon orbits with room for its planet.
  const primary = PRIMARY_PLANETS.has(id)
  const a = primary ? slot.a : 3.6 + index * .6
  const inc = primary ? slot.inc : [1.28, 1.08, .98, .92, .86][index % 5]
  return { ...slot, a, b: a * .98, inc }
}

// Conservative envelope for every phase, including DOM labels and camera drift.
// Framing changes the camera, never the orbital path when the viewport changes.
export function cameraDistance(aspect: number, count: number, height = 700): number {
  const halfH = Math.tan(CAM_REST.fov * Math.PI / 360)
  const horizontal = Math.max(.2, aspect * .90)
  const vertical = Math.max(.5, 1 - 60 / Math.max(280, height))
  const slopeX = halfH * horizontal
  const slopeY = halfH * vertical
  const cos = { x: 0, y: 0, z: 0 }
  const sin = { x: 0, y: 0, z: 0 }
  let distance = 9

  // A tilted ellipse reaches its largest screen extent at a different phase
  // from its nearest depth. Fit their combined projection instead of adding
  // those two independent maxima, which leaves unnecessary space on desktop.
  for (let index = 0; index < Math.max(1, count); index++) {
    // Count alone cannot distinguish a primary system from its moon systems.
    for (const id of ['today', 'moon']) {
      const orbit = orbitFor(id, index, count, false)
      orbitOffset(orbit, 0, cos)
      orbitOffset(orbit, Math.PI / 2, sin)
      for (const sign of [-1, 1]) {
        // max(z + sign * x / slope) over every phase is the length of
        // its cosine/sine coefficients. The sphere term includes body
        // edges and idle drift; the viewport slopes reserve label margins.
        distance = Math.max(distance,
          Math.hypot(cos.z + sign * cos.x / slopeX, sin.z + sign * sin.x / slopeX) + .45 * Math.hypot(1, 1 / slopeX),
          Math.hypot(cos.z + sign * cos.y / slopeY, sin.z + sign * sin.y / slopeY) + .45 * Math.hypot(1, 1 / slopeY),
        )
      }
    }
  }
  return distance
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
