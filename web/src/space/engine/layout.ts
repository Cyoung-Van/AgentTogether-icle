import type { PlanetNode, SpaceField } from '../types'

export type SpiralParams = {
  a: number
  b: number
  theta0: number
  step: number
  tiltX: number
  tiltZ: number
  shiftX: number
  shiftY: number
  depth: number
}

export type PlanetPose = {
  id: string
  node: PlanetNode
  x: number
  y: number
  z: number
  radius: number
  theta: number
  r: number
}

export const SPIRAL_DESKTOP: SpiralParams = {
  a: 2.55,
  b: 0.09,
  theta0: -0.58,
  step: 1.3,
  tiltX: -0.12,
  tiltZ: 0.03,
  shiftX: 0,
  shiftY: 0,
  depth: 0.18,
}

export const SPIRAL_MOBILE: SpiralParams = {
  a: 2.05,
  b: 0.11,
  theta0: -0.42,
  step: 1.3,
  tiltX: -0.1,
  tiltZ: 0.02,
  shiftX: 0,
  shiftY: 0,
  depth: 0.14,
}

export const CAMERA_REST = {
  desktop: { x: 0.04, y: 0.16, z: 9.2, lookX: 0, lookY: 0.06, lookZ: 0 },
  mobile: { x: 0, y: 0.12, z: 10.1, lookX: 0, lookY: 0.04, lookZ: 0 },
  fov: 40,
}

export const FIELD_ORDER: Record<string, string[]> = {
  home: ['task-studio', 'today', 'agents', 'sessions', 'settings'],
  tasks: ['task-list', 'new-task', 'episodes', 'collabs'],
  agents: ['agent-dir', 'local-agents', 'recommend'],
}

const RADIUS: Record<string, number> = {
  'task-studio': 0.34,
  today: 0.27,
  agents: 0.27,
  sessions: 0.24,
  settings: 0.23,
  'task-list': 0.32,
  'new-task': 0.26,
  episodes: 0.24,
  collabs: 0.23,
  'agent-dir': 0.32,
  'local-agents': 0.26,
  recommend: 0.24,
}

export function spiralParamsFor(count: number, compact: boolean): SpiralParams {
  const base = compact ? SPIRAL_MOBILE : SPIRAL_DESKTOP
  if (count <= 3) {
    return { ...base, a: base.a * 1.08, step: base.step * 1.2 }
  }
  if (count === 4) {
    return { ...base, a: base.a * 1.04, step: 1.45 }
  }
  return base
}

export function planetRadius(id: string): number {
  return RADIUS[id] ?? 0.26
}

export function layoutField(field: SpaceField, compact: boolean, aspect = 16 / 9): PlanetPose[] {
  const order = FIELD_ORDER[field.id] ?? field.planets.map((node) => node.id)
  const nodes = order
    .map((id) => field.planets.find((node) => node.id === id))
    .filter((node): node is PlanetNode => Boolean(node))
  const params = spiralParamsFor(nodes.length, compact)
  return fitPoses(
    nodes.map((node, index) => poseForIndex(node, index, params)),
    compact,
    aspect,
  )
}

export function poseForIndex(node: PlanetNode, index: number, params: SpiralParams): PlanetPose {
  const theta = params.theta0 + index * params.step
  const r = params.a * Math.exp(params.b * (theta - params.theta0))
  const lx = r * Math.cos(theta) + params.shiftX
  const ly = r * Math.sin(theta) + params.shiftY
  const cz = Math.cos(params.tiltZ)
  const sz = Math.sin(params.tiltZ)
  const x1 = lx * cz - ly * sz
  const y1 = lx * sz + ly * cz
  const cy = Math.cos(params.tiltX)
  const sy = Math.sin(params.tiltX)
  return {
    id: node.id,
    node,
    x: x1,
    y: y1 * cy,
    z: y1 * sy + r * params.depth,
    radius: planetRadius(node.id),
    theta,
    r,
  }
}

function viewFrame(compact: boolean, aspect: number, count: number) {
  const cam = compact ? CAMERA_REST.mobile : CAMERA_REST.desktop
  const halfH = cam.z * Math.tan((CAMERA_REST.fov * Math.PI) / 360)
  const halfW = halfH * Math.max(aspect, 0.72)
  const widen = count <= 3 ? 0.54 : count === 4 ? 0.58 : 0.6
  const tallen = count <= 3 ? 0.54 : count === 4 ? 0.58 : 0.62
  return {
    width: halfW * 2 * (compact ? Math.min(widen + 0.04, 0.72) : widen),
    height: halfH * 2 * (compact ? Math.min(tallen + 0.04, 0.7) : tallen),
    cx: cam.lookX,
    cy: cam.lookY + (compact ? 0.08 : 0.16),
  }
}

function fitPoses(poses: PlanetPose[], compact: boolean, aspect: number): PlanetPose[] {
  if (poses.length === 0) return poses
  const frame = viewFrame(compact, aspect, poses.length)
  const box = bounds(poses)
  const scaleX = frame.width / box.width
  const scaleY = frame.height / box.height
  const fitted = poses.map((pose) => ({
    ...pose,
    x: (pose.x - box.midX) * scaleX + frame.cx,
    y: (pose.y - box.midY) * scaleY + frame.cy,
    z: pose.z * Math.min(scaleX, scaleY),
    r: pose.r * Math.min(scaleX, scaleY),
  }))
  return separate(fitted, compact ? 1.15 : 1.7)
}

function bounds(poses: PlanetPose[]) {
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  for (const pose of poses) {
    minX = Math.min(minX, pose.x - pose.radius)
    maxX = Math.max(maxX, pose.x + pose.radius)
    minY = Math.min(minY, pose.y - pose.radius * 1.65)
    maxY = Math.max(maxY, pose.y + pose.radius)
  }
  return {
    width: Math.max(maxX - minX, 0.01),
    height: Math.max(maxY - minY, 0.01),
    midX: (minX + maxX) / 2,
    midY: (minY + maxY) / 2,
  }
}

function separate(poses: PlanetPose[], minGap: number): PlanetPose[] {
  const next = poses.map((pose) => ({ ...pose }))
  for (let iter = 0; iter < 8; iter += 1) {
    for (let i = 0; i < next.length; i += 1) {
      for (let j = i + 1; j < next.length; j += 1) {
        const a = next[i]
        const b = next[j]
        const dx = b.x - a.x
        const dy = b.y - a.y
        const dist = Math.hypot(dx, dy)
        const need = a.radius + b.radius + minGap
        if (dist >= need || dist < 1e-4) continue
        const push = ((need - dist) / dist) * 0.5
        a.x -= dx * push
        a.y -= dy * push
        b.x += dx * push
        b.y += dy * push
      }
    }
  }
  return next
}

export function sampleSpiral(params: SpiralParams, turns = 5, samples = 64): Array<{ x: number; y: number; z: number }> {
  const points: Array<{ x: number; y: number; z: number }> = []
  for (let i = 0; i < samples; i += 1) {
    const t = i / Math.max(samples - 1, 1)
    const theta = params.theta0 + t * params.step * turns
    const r = params.a * Math.exp(params.b * (theta - params.theta0))
    const lx = r * Math.cos(theta) + params.shiftX
    const ly = r * Math.sin(theta) + params.shiftY
    const cz = Math.cos(params.tiltZ)
    const sz = Math.sin(params.tiltZ)
    const x1 = lx * cz - ly * sz
    const y1 = lx * sz + ly * cz
    points.push({
      x: x1,
      y: y1 * Math.cos(params.tiltX),
      z: y1 * Math.sin(params.tiltX) + r * params.depth,
    })
  }
  return points
}
