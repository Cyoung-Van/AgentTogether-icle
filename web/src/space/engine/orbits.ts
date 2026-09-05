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

export const CAM_REST = {
  desktop: { x: 0.06, y: 0.48, z: 6.6 },
  mobile: { x: 0.04, y: 0.4, z: 7.4 },
  fov: 48,
}

export const CENTER_RADIUS: Record<string, number> = {
  home: 1.02,
  'task-studio': 0.9,
  agents: 0.88,
}

export const SAT_RADIUS: Record<string, number> = {
  today: 0.4,
  'task-studio': 0.46,
  agents: 0.44,
  sessions: 0.36,
  settings: 0.34,
  'task-list': 0.44,
  'new-task': 0.38,
  episodes: 0.36,
  collabs: 0.34,
  'agent-dir': 0.44,
  'local-agents': 0.38,
  recommend: 0.36,
}

type OrbitTune = Omit<OrbitParams, 'b'> & { ecc: number }

const ORBIT_TABLE: Record<string, OrbitTune> = {
  today: { a: 2.55, ecc: 0.1, inc: 0.58, lan: 0.15, arg: 0.4, phase: 0.2, period: 186 },
  'task-studio': { a: 3.05, ecc: 0.09, inc: -0.42, lan: 1.85, arg: 0.9, phase: 1.55, period: 216 },
  agents: { a: 3.35, ecc: 0.11, inc: 0.62, lan: 3.2, arg: 0.2, phase: 2.85, period: 244 },
  sessions: { a: 2.85, ecc: 0.08, inc: -0.55, lan: 4.55, arg: 1.1, phase: 4.05, period: 268 },
  settings: { a: 3.2, ecc: 0.1, inc: 0.28, lan: 5.75, arg: 0.55, phase: 5.3, period: 292 },
  'task-list': { a: 2.35, ecc: 0.09, inc: 0.4, lan: 0.52, arg: 0.2, phase: 0.24, period: 192 },
  'new-task': { a: 2.75, ecc: 0.1, inc: -0.36, lan: 1.7, arg: 0.44, phase: 1.82, period: 224 },
  episodes: { a: 3.05, ecc: 0.08, inc: 0.5, lan: 2.9, arg: 0.18, phase: 3.28, period: 252 },
  collabs: { a: 3.3, ecc: 0.11, inc: -0.22, lan: 4.1, arg: 0.56, phase: 4.72, period: 278 },
  'agent-dir': { a: 2.45, ecc: 0.09, inc: 0.38, lan: 0.64, arg: 0.28, phase: 0.4, period: 200 },
  'local-agents': { a: 2.8, ecc: 0.1, inc: -0.44, lan: 2.08, arg: 0.4, phase: 2.36, period: 236 },
  recommend: { a: 3.15, ecc: 0.08, inc: 0.28, lan: 3.7, arg: 0.22, phase: 4.12, period: 270 },
}

function hashId(id: string): number {
  let h = 2166136261
  for (const ch of id) h = Math.imul(h ^ ch.charCodeAt(0), 16777619)
  return h >>> 0
}

function tuneToParams(tune: OrbitTune): OrbitParams {
  return {
    a: tune.a,
    b: tune.a * (1 - tune.ecc),
    inc: tune.inc,
    lan: tune.lan,
    arg: tune.arg,
    phase: tune.phase,
    period: tune.period,
  }
}

export function orbitFor(id: string, index: number, count: number, compact: boolean): OrbitParams {
  const listed = ORBIT_TABLE[id]
  const seed = hashId(id)
  const fallback: OrbitTune = {
    a: 2.4 + index * 0.42,
    ecc: 0.08 + (seed % 5) * 0.012,
    inc: -0.42 + (index / Math.max(count - 1, 1)) * 0.84,
    lan: (index / Math.max(count, 1)) * Math.PI * 2 + (seed % 17) * 0.04,
    arg: (seed % 11) * 0.08,
    phase: (index / Math.max(count, 1)) * Math.PI * 2 + (seed % 9) * 0.03,
    period: 168 + (seed % 9) * 14,
  }
  const base = tuneToParams(listed ?? fallback)
  const scale = compact ? 0.82 : 1
  return { ...base, a: base.a * scale, b: base.b * scale }
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
