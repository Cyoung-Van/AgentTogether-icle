import * as THREE from 'three'
import type { PlanetPalette } from '../types'

const WIDTH = 256
const HEIGHT = 128
const TAU = Math.PI * 2
type RGB = [number, number, number]

function randomFor(id: string) {
  let state = 2166136261
  for (const character of id) state = Math.imul(state ^ character.charCodeAt(0), 16777619)
  return () => {
    state ^= state << 13
    state ^= state >>> 17
    state ^= state << 5
    return (state >>> 0) / 4294967296
  }
}

const smooth = (lo: number, hi: number, value: number) => {
  const t = Math.max(0, Math.min(1, (value - lo) / (hi - lo)))
  return t * t * (3 - 2 * t)
}

function rgb(hex: string): RGB {
  const color = new THREE.Color(hex).convertLinearToSRGB()
  return [color.r * 255, color.g * 255, color.b * 255]
}

function blend(a: RGB, b: RGB, t: number): RGB {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]
}

/** A small, cached lattice keeps each spherical sample inexpensive and smooth. */
function noiseField(random: () => number) {
  const grid = new Float32Array(32 * 32 * 32)
  for (let i = 0; i < grid.length; i++) grid[i] = random()
  return (x: number, y: number, z: number) => {
    const ix = Math.floor(x), iy = Math.floor(y), iz = Math.floor(z)
    let u = x - ix, v = y - iy, w = z - iz
    u = u * u * (3 - 2 * u)
    v = v * v * (3 - 2 * v)
    w = w * w * (3 - 2 * w)
    const x0 = ix & 31, x1 = (ix + 1) & 31
    const y0 = (iy & 31) * 32, y1 = ((iy + 1) & 31) * 32
    const z0 = (iz & 31) * 1024, z1 = ((iz + 1) & 31) * 1024
    const a = grid[z0 + y0 + x0] * (1 - u) + grid[z0 + y0 + x1] * u
    const b = grid[z0 + y1 + x0] * (1 - u) + grid[z0 + y1 + x1] * u
    const c = grid[z1 + y0 + x0] * (1 - u) + grid[z1 + y0 + x1] * u
    const d = grid[z1 + y1 + x0] * (1 - u) + grid[z1 + y1 + x1] * u
    return (a * (1 - v) + b * v) * (1 - w) + (c * (1 - v) + d * v) * w
  }
}

/**
 * Albedo only: scene lighting supplies the terminator, highlights and shadow.
 * Sampling a sphere, rather than the canvas plane, prevents a longitude seam
 * and keeps geological features continuous across both poles.
 */
export function createPlanetSurface(id: string, palette: PlanetPalette): THREE.CanvasTexture {
  const canvas = document.createElement('canvas')
  canvas.width = WIDTH
  canvas.height = HEIGHT
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.wrapS = THREE.RepeatWrapping
  texture.wrapT = THREE.ClampToEdgeWrapping
  const context = canvas.getContext('2d')
  if (!context) return texture

  const random = randomFor(id)
  const noise = noiseField(random)
  const pixels = context.createImageData(WIDTH, HEIGHT)
  const isIce = id === 'today'
  const isMineral = id === 'task-studio' || id === 'agents'
  const isGas = id === 'sessions'
  const isMoon = !isIce && !isMineral && !isGas
  // Keep the selected family visible while allowing enough albedo contrast
  // for terrain to read on a small globe against the default light canvas.
  let low = blend(rgb(palette.c2), rgb('#718da3'), .36)
  let mid = blend(rgb(palette.c1), rgb('#c3ced3'), .22)
  let high = blend(rgb(palette.c0), rgb('#f0f0e9'), .20)
  if (id === 'task-studio') {
    low = rgb('#a29b91'); mid = rgb('#d0c4b0'); high = rgb('#ede8d9')
  } else if (id === 'agents') {
    low = rgb('#8d92a6'); mid = rgb('#c4c8d2'); high = rgb('#e9e9ec')
  } else if (isGas) {
    low = rgb('#789ca5'); mid = rgb('#b3d0d1'); high = rgb('#e6eded')
  } else if (isMoon) {
    low = blend(rgb(palette.c2), rgb('#939596'), .78)
    mid = blend(rgb(palette.c1), rgb('#bfc1c0'), .78)
    high = blend(rgb(palette.c0), rgb('#e0e0d9'), .72)
  }

  const craters = isMoon ? Array.from({ length: 14 }, () => {
    const y = random() * 2 - 1
    const angle = random() * TAU
    const radius = Math.sqrt(1 - y * y)
    const size = .075 + random() * .19
    return { x: Math.cos(angle) * radius, y, z: Math.sin(angle) * radius, sizeSquared: size * size }
  }) : []
  const longitudes = Array.from({ length: WIDTH }, (_, x) => {
    const angle = (x + .5) / WIDTH * TAU
    return [Math.cos(angle), Math.sin(angle)]
  })

  for (let py = 0; py < HEIGHT; py++) {
    const latitude = (py + .5) / HEIGHT * Math.PI
    const sy = Math.cos(latitude)
    const radius = Math.sin(latitude)
    for (let px = 0; px < WIDTH; px++) {
      const sx = longitudes[px][0] * radius
      const sz = longitudes[px][1] * radius
      const broad = noise(sx * 2.2 + 12, sy * 2.2 + 12, sz * 2.2 + 12)
      const medium = noise(sx * 5.7 + 7, sy * 5.7 + 7, sz * 5.7 + 7)
      const fine = noise(sx * 13.1 + 19, sy * 13.1 + 19, sz * 13.1 + 19)
      const terrain = broad * .62 + medium * .28 + fine * .10
      let value = smooth(.24, .76, terrain)
      let cloud = 0

      if (isIce) {
        // Uneven coastlines and a few thin cloud sheets, rather than stripes.
        const land = smooth(.46, .55, terrain)
        value = .13 + land * .69 + (fine - .5) * .10
        const polar = smooth(.73, .98, Math.abs(sy) + (medium - .5) * .14)
        cloud = Math.max(polar * .83, smooth(.68, .83, medium * .7 + fine * .3) * .55)
      } else if (isGas) {
        // Latitude flow is domain-warped in 3D: broken, soft storm bands.
        const flow = sy * 13 + (broad - .5) * 8 + (medium - .5) * 2
        const bands = Math.sin(flow) * .5 + .5
        value = .25 + bands * .43 + (fine - .5) * .10
        cloud = smooth(.67, .87, broad * .6 + medium * .4) * .45
      } else if (isMineral) {
        const ridges = 1 - Math.abs(medium * 2 - 1)
        value = smooth(.20, .86, terrain * .8 + ridges * .2)
      } else {
        // Crater relief is deliberately gentle; it never bakes a conflicting
        // light direction into the real, rotating scene's spherical lighting.
        value = .18 + value * .67
        for (const crater of craters) {
          const distanceSquared = 2 - 2 * (sx * crater.x + sy * crater.y + sz * crater.z)
          const q = distanceSquared / crater.sizeSquared
          if (q < 1.35) {
            const bowl = 1 - smooth(.12, .84, q)
            const rim = smooth(.56, .84, q) * (1 - smooth(.86, 1.35, q))
            value += rim * .16 - bowl * .16
          }
        }
      }

      value = Math.max(0, Math.min(1, value))
      const mix = value < .5 ? value * 2 : (value - .5) * 2
      const a = value < .5 ? low : mid
      const b = value < .5 ? mid : high
      const offset = (py * WIDTH + px) * 4
      for (let channel = 0; channel < 3; channel++) {
        const surface = a[channel] + (b[channel] - a[channel]) * mix
        pixels.data[offset + channel] = surface + (high[channel] - surface) * cloud
      }
      pixels.data[offset + 3] = 255
    }
  }
  context.putImageData(pixels, 0, 0)
  texture.needsUpdate = true
  return texture
}
