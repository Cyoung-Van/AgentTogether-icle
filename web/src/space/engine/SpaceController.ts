import * as THREE from 'three'
import { galaxyForPath, parentPath } from '../hierarchy'
import { clamp, easeInOutCubic, lerp } from '../motion'
import type { GalaxyLayer, PlanetNode } from '../types'
import { CAM_REST, cameraDistance, centerRadius, nodeRadius, orbitAngle, orbitFor, orbitOffset, satRadius, type OrbitParams } from './orbits'

export type PlayHooks = {
  onCovered?: () => void
  onDone?: () => void
}

type Role = 'center' | 'sat'

type Body = {
  id: string
  node: PlanetNode
  role: Role
  group: THREE.Group
  spin: THREE.Group
  mesh: THREE.Mesh
  surface: THREE.MeshStandardMaterial
  haze: THREE.MeshBasicMaterial
  radius: number
  radiusGoal: number
  orbit: OrbitParams | null
  orbitTime: number
  orbitScale: number
  speedScale: number
  magnet: THREE.Vector3
  opacity: number
  textures: THREE.Texture[]
}

type LayerMemory = {
  path: string
  anchor: THREE.Vector3
  satTimes: Record<string, number>
  magnets: Record<string, THREE.Vector3>
  cam: THREE.Vector3
  look: THREE.Vector3
}

type AnimKind = 'enter-galaxy' | 'enter-leaf' | 'return-galaxy' | 'return-leaf'

type Anim = {
  kind: AnimKind
  generation: number
  elapsed: number
  duration: number
  targetId: string
  lockedPos: THREE.Vector3
  fromCam: THREE.Vector3
  midCam: THREE.Vector3
  toCam: THREE.Vector3
  fromLook: THREE.Vector3
  toLook: THREE.Vector3
  nextPath: string
  nextGalaxy: GalaxyLayer | null
  settled: boolean
  sourcePath: string
  startRadius: number
  covered: boolean
  swapped: boolean
  hooks: PlayHooks
}

const TAU = Math.PI * 2
const CLEAR = 0x050816
const MAGNET_NDC = 0.15
const MAGNET_KEEP = 0.2
const MAGNET_MAX = 0.06
const ENTER_MS = 880
const RETURN_MS = 820

function hex(value: string): number {
  return Number.parseInt(value.slice(1), 16)
}

function seedRng(seed: number): () => number {
  let s = seed % 2147483647
  if (s <= 0) s += 2147483646
  return () => {
    s = (s * 16807) % 2147483647
    return (s - 1) / 2147483646
  }
}

function lookQuat(eye: THREE.Vector3, target: THREE.Vector3, up: THREE.Vector3, out: THREE.Quaternion) {
  lookMatrix.lookAt(eye, target, up)
  out.setFromRotationMatrix(lookMatrix)
}

const lookMatrix = new THREE.Matrix4()

export class SpaceController {
  private canvas: HTMLCanvasElement
  private renderer: THREE.WebGLRenderer
  private scene: THREE.Scene
  private camera: THREE.PerspectiveCamera
  private hemi: THREE.HemisphereLight
  private keyLight: THREE.DirectionalLight
  private fill: THREE.DirectionalLight
  private sphereGeo: THREE.SphereGeometry
  private atmosphereGeo: THREE.SphereGeometry
  private planets = new Map<string, Body>()
  private bodyCache = new Map<string, Body>()
  private routePath = '/'
  private leafId: string | null = null
  private viewHeight = 700
  private debugTrace: unknown[] = []
  private planetRoot: THREE.Group
  private farStars: THREE.Points
  private midStars: THREE.Points
  private band: THREE.Points
  private dust: THREE.Points
  private pointTex: THREE.CanvasTexture
  private nebulaRoot: THREE.Group
  private nebulae: THREE.Sprite[] = []
  private nebulaTex: THREE.CanvasTexture[] = []
  private frame = 0
  private lastTime = 0
  private time = 0
  private disposed = false
  private compact = false
  private reduce = false
  private fine = true
  private paused = false
  private workspace = false
  private galaxy: GalaxyLayer | null = null
  private generation = 0
  private anim: Anim | null = null
  private expectedPath: string | null = null
  private idleWeight = 1
  private attractedId: string | null = null
  private focusId: string | null = null
  private hoverId: string | null = null
  private pointerNdc = new THREE.Vector2(-9, -9)
  private pointerInside = false
  private anchor = new THREE.Vector3()
  private restPos = new THREE.Vector3()
  private restLook = new THREE.Vector3()
  private camPos = new THREE.Vector3()
  private camLook = new THREE.Vector3()
  private desiredQuat = new THREE.Quaternion()
  private up = new THREE.Vector3(0, 1, 0)
  private scratch = new THREE.Vector3()
  private scratch2 = new THREE.Vector3()
  private scratch3 = new THREE.Vector3()
  private orbitPoint = { x: 0, y: 0, z: 0 }
  private ndc = new THREE.Vector3()
  private raycaster = new THREE.Raycaster()
  private labels = new Map<string, HTMLElement>()
  private memory = new Map<string, LayerMemory>()
  private onAttracted: (id: string | null) => void
  private resizeObserver: ResizeObserver
  private mediaFns: Array<() => void> = []

  constructor(
    canvas: HTMLCanvasElement,
    hooks: {
      onAttracted: (id: string | null) => void
      createRenderer?: (canvas: HTMLCanvasElement) => THREE.WebGLRenderer
    },
  ) {
    this.canvas = canvas
    this.onAttracted = hooks.onAttracted
    this.compact = window.matchMedia('(max-width: 760px)').matches
    this.reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    this.fine = window.matchMedia('(hover: hover) and (pointer: fine)').matches

    this.renderer = hooks.createRenderer?.(canvas) ?? new THREE.WebGLRenderer({
      canvas,
      antialias: !this.compact,
      alpha: true,
      powerPreference: 'low-power',
      stencil: false,
    })
    this.renderer.setClearColor(CLEAR, 0)
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5))
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.renderer.toneMapping = THREE.NoToneMapping
    this.renderer.shadowMap.enabled = false

    this.scene = new THREE.Scene()
    this.camera = new THREE.PerspectiveCamera(CAM_REST.fov, 1, 0.18, 260)
    this.applyRest(true)

    this.hemi = new THREE.HemisphereLight(0x3a5478, 0x0a0c12, 0.9)
    this.keyLight = new THREE.DirectionalLight(0xf2f6fb, 1.18)
    this.keyLight.position.set(-4.2, 5.4, 6.2)
    this.fill = new THREE.DirectionalLight(0x9eb4d0, 0.28)
    this.fill.position.set(5.2, -1.4, -3.4)
    this.scene.add(this.hemi, this.keyLight, this.fill)

    this.sphereGeo = new THREE.SphereGeometry(1, 48, 32)
    this.atmosphereGeo = new THREE.SphereGeometry(1.025, 32, 20)
    this.planetRoot = new THREE.Group()
    this.nebulaRoot = new THREE.Group()
    this.scene.add(this.planetRoot, this.nebulaRoot)

    this.pointTex = this.makePointSprite()
    const stars = this.makeStars()
    this.farStars = stars.far
    this.midStars = stars.mid
    this.band = stars.band
    this.dust = stars.dust
    this.scene.add(this.farStars, this.midStars, this.band, this.dust)
    this.makeNebulae()

    this.resize()
    this.resizeObserver = new ResizeObserver(() => this.resize())
    this.resizeObserver.observe(canvas.parentElement ?? canvas)
    this.bindMedia()
    window.addEventListener('pointermove', this.onWindowPointer)
    this.lastTime = performance.now()
    this.frame = requestAnimationFrame(this.tick)
  }

  inspect() {
    return {
      path: this.routePath,
      layer: this.galaxy?.path ?? null,
      progress: this.anim ? this.anim.elapsed / this.anim.duration : null,
      paused: this.paused,
      reduced: this.reduce,
      renderer: { memory: this.renderer.info.memory, render: this.renderer.info.render },
      memory: [...this.memory].map(([path, m]) => ({ path, times: m.satTimes })),
      workspace: this.workspace,
      anim: this.anim?.kind ?? null,
      anchor: this.anchor.toArray(),
      cam: this.camPos.toArray(),
      look: this.camLook.toArray(),
      bodies: [...this.planets.values()].map((body) => ({
        id: body.id,
        uuid: body.group.uuid,
        spin: body.spin.rotation.y,
        opacity: body.opacity,
        speed: body.speedScale,
        projected: body.group.position.clone().project(this.camera).toArray(),
        role: body.role,
        x: +body.group.position.x.toFixed(3),
        y: +body.group.position.y.toFixed(3),
        z: +body.group.position.z.toFixed(3),
        orbitTime: +body.orbitTime.toFixed(3),
        radius: +body.radius.toFixed(3),
      })),
    }
  }

  dispose() {
    this.disposed = true
    cancelAnimationFrame(this.frame)
    this.resizeObserver.disconnect()
    for (const off of this.mediaFns) off()
    window.removeEventListener('pointermove', this.onWindowPointer)
    this.clearPlanets()
    this.sphereGeo.dispose()
    this.atmosphereGeo.dispose()
    this.disposePoints(this.farStars)
    this.disposePoints(this.midStars)
    this.disposePoints(this.band)
    this.disposePoints(this.dust)
    this.disposeNebulae()
    this.pointTex.dispose()
    this.renderer.dispose()
    const global = window as unknown as { __icleSpace?: SpaceController }
    if (global.__icleSpace === this) delete global.__icleSpace
  }

  setLabels(labels: Map<string, HTMLElement>) {
    this.labels = labels
  }

  setHover(id: string | null) { this.hoverId = id }

  setFocus(id: string | null) {
    this.focusId = id
    if (id) this.setAttracted(id)
  }

  setPaused(value: boolean) {
    this.paused = value
  }

  get generationValue() {
    return this.generation
  }

  syncRoute(pathname: string, external = false) {
    if (!external && this.anim && this.expectedPath === pathname) { this.routePath = pathname; return }
    if (this.anim) this.cancelAnim()
    else if (!this.workspace && this.galaxy?.path !== pathname) this.rememberLayer()
    if (!external && this.workspace && this.routePath === pathname) return
    this.routePath = pathname
    const next = galaxyForPath(pathname)
    if (!external && next && this.galaxy?.path === pathname && !this.workspace) return
    if (next) {
      const mem = this.memory.get(next.path)
      this.applyGalaxy(next, { anchor: mem?.anchor ?? new THREE.Vector3(), times: mem?.satTimes, magnets: mem?.magnets })
      for (const body of this.planets.values()) {
        body.radius = body.radiusGoal
        body.group.scale.setScalar(body.radius)
      }
      this.applyRest(true)
      this.idleWeight = 0
    } else {
      this.workspace = true
      this.leafId = null
      this.planetRoot.visible = false
    }
    this.focusId = null
    this.setAttracted(null)
    this.expectedPath = null
  }

  playEnter(nodeId: string, nextPath: string, hooks: PlayHooks) {
    if (this.anim) return
    const body = this.planets.get(nodeId)
    if (this.reduce || !body || body.role !== 'sat') {
      this.rememberLayer()
      this.syncRoute(nextPath)
      hooks.onCovered?.(); hooks.onDone?.()
      return
    }
    this.generation += 1
    this.debugTrace = []
    // Freeze the actual rendered target, including its small magnetic displacement.
    // Other bodies decelerate for the first 100 ms before the parent snapshot is taken.
    const lockedPos = body.group.position.clone()
    const nextGalaxy = galaxyForPath(nextPath)
    const distance = cameraDistance(this.camera.aspect, nextGalaxy?.children.length ?? 1, this.viewHeight)
    const toCam = lockedPos.clone().add(nextGalaxy ? new THREE.Vector3(0, 0, distance) : this.leafOffset(body.radius))
    this.anim = {
      kind: nextGalaxy ? 'enter-galaxy' : 'enter-leaf', generation: this.generation,
      elapsed: 0, duration: ENTER_MS / 1000, targetId: nodeId, lockedPos,
      fromCam: this.camPos.clone(), fromLook: this.camLook.clone(),
      midCam: new THREE.Vector3(), toCam, toLook: lockedPos.clone(),
      nextPath, nextGalaxy, sourcePath: this.routePath, startRadius: body.radius,
      settled: false, covered: false, swapped: false, hooks,
    }
    this.expectedPath = nextPath
    this.focusId = null
  }

  playReturn(nextPath: string, hooks: PlayHooks) {
    if (this.anim) return
    const nextGalaxy = galaxyForPath(nextPath)
    const mem = this.memory.get(nextPath)
    // A refreshed/deep-linked page has no visual history. Use the deterministic parent.
    if (this.reduce || (this.workspace && !this.leafId) || !nextGalaxy || !mem || parentPath(this.routePath) !== nextPath) {
      this.syncRoute(nextPath)
      hooks.onCovered?.(); hooks.onDone?.()
      return
    }
    if (!this.workspace) this.rememberLayer()
    this.generation += 1
    this.debugTrace = []
    const targetId = this.workspace ? this.leafId : this.galaxy?.center.id
    const target = targetId ? this.planets.get(targetId) : undefined
    this.anim = {
      kind: this.workspace ? 'return-leaf' : 'return-galaxy', generation: this.generation,
      elapsed: 0, duration: RETURN_MS / 1000, targetId: targetId ?? '',
      lockedPos: target?.group.position.clone() ?? this.anchor.clone(),
      fromCam: this.camPos.clone(), midCam: this.arcMid(this.camPos, mem.cam), toCam: mem.cam.clone(),
      fromLook: this.camLook.clone(), toLook: mem.look.clone(),
      nextPath, nextGalaxy, sourcePath: this.routePath, startRadius: target?.radius ?? .32,
      settled: true, covered: false, swapped: false, hooks,
    }
    this.expectedPath = nextPath
    this.focusId = null
  }

  pick(clientX: number, clientY: number): string | null {
    if (this.workspace || this.anim) return null
    const box = this.canvas.getBoundingClientRect()
    const ndc = new THREE.Vector2(
      ((clientX - box.left) / box.width) * 2 - 1,
      -((clientY - box.top) / box.height) * 2 + 1,
    )
    this.raycaster.setFromCamera(ndc, this.camera)
    const meshes = [...this.planets.values()].filter((body) => body.opacity > 0.2).map((body) => body.mesh)
    const hit = this.raycaster.intersectObjects(meshes, false)[0]
    if (!hit) return null
    for (const [id, body] of this.planets) {
      if (body.mesh === hit.object) return id
    }
    return null
  }

  private tick = (now: number) => {
    if (this.disposed) return
    this.frame = requestAnimationFrame(this.tick)
    if (document.hidden) {
      this.lastTime = now
      return
    }
    let dt = (now - this.lastTime) / 1000
    this.lastTime = now
    if (dt > 0.05) dt = 1 / 60
    if (!this.paused && !this.reduce) this.time += dt
    const tracedAnim = this.anim
    this.updateAnim(dt)
    this.updateMagnet(dt)
    this.updateBodies(dt)
    this.updateDust(dt)
    this.updateCamera(dt)
    this.camera.updateMatrixWorld()
    this.planetRoot.updateMatrixWorld(true)
    this.updateLabels()
    if (import.meta.env?.DEV) {
      const state = this.inspect()
      this.canvas.dataset.spaceState = JSON.stringify(state)
      if (tracedAnim) {
        this.debugTrace.push({ ...state, progress: Math.min(1, tracedAnim.elapsed / tracedAnim.duration) })
        if (!this.anim) this.canvas.dataset.spaceTransition = JSON.stringify(this.debugTrace)
      }
    }
    this.renderer.render(this.scene, this.camera)
  }

  private updateAnim(dt: number) {
    const anim = this.anim
    if (!anim) {
      this.idleWeight = lerp(this.idleWeight, this.reduce || this.workspace || this.paused ? 0 : 1, 1 - Math.exp(-dt * 3))
      return
    }
    anim.elapsed += dt
    const t = clamp(anim.elapsed / anim.duration, 0, 1)
    this.idleWeight *= Math.exp(-dt * 25)
    const entering = anim.kind.startsWith('enter')
    if (entering) {
      if (!anim.settled && t >= .12) {
        this.rememberLayer()
        const parent = this.memory.get(anim.sourcePath)
        if (parent) { parent.cam.copy(anim.fromCam); parent.look.copy(anim.fromLook) }
        anim.settled = true
      }
      // First aim and reposition at constant target distance. Only then dolly in.
      const aim = easeInOutCubic(clamp((t - .08) / .25, 0, 1))
      const approach = easeInOutCubic(clamp((t - .33) / .67, 0, 1))
      const startDir = anim.fromCam.clone().sub(anim.lockedPos)
      const startDistance = startDir.length()
      const endDir = anim.toCam.clone().sub(anim.lockedPos).normalize()
      const dir = startDir.normalize().lerp(endDir, aim).normalize()
      const centered = anim.lockedPos.clone().addScaledVector(dir, startDistance)
      this.camPos.copy(centered)
      if (t > .33) {
        const mid = this.arcMid(centered, anim.toCam)
        this.bezier(this.camPos, centered, mid, anim.toCam, approach)
      }
      this.camLook.copy(anim.fromLook).lerp(anim.lockedPos, aim)
      const target = this.planets.get(anim.targetId)
      if (target) {
        target.group.position.copy(anim.lockedPos)
        target.radiusGoal = lerp(anim.startRadius, anim.nextGalaxy ? centerRadius(anim.targetId) : anim.startRadius * 1.12, approach)
      }
      if (!anim.swapped) {
        const fade = 1 - easeInOutCubic(clamp((t - .25) / .27, 0, 1))
        for (const body of this.planets.values()) if (body.id !== anim.targetId) this.setOpacity(body, fade)
      }
      if (anim.nextGalaxy && !anim.swapped && t >= .52) {
        const memory = this.memory.get(anim.nextPath)
        this.applyGalaxy(anim.nextGalaxy, { anchor: anim.lockedPos, keepId: anim.targetId, times: memory?.satTimes, fadeInNew: true })
        anim.swapped = true
      }
      if (anim.swapped) {
        const appear = easeInOutCubic(clamp((t - .52) / .48, 0, 1))
        for (const body of this.planets.values()) if (body.role === 'sat') {
          this.setOpacity(body, appear)
          body.orbitScale = lerp(.9, 1, appear)
        }
      } else if (!anim.nextGalaxy && target) {
        this.setOpacity(target, 1 - easeInOutCubic(clamp((t - .72) / .28, 0, 1)))
      }
    } else {
      const k = easeInOutCubic(t)
      this.bezier(this.camPos, anim.fromCam, anim.midCam, anim.toCam, k)
      this.camLook.copy(anim.fromLook).lerp(anim.toLook, k)
      if (!anim.swapped) {
        const fade = 1 - easeInOutCubic(clamp(t / .28, 0, 1))
        for (const body of this.planets.values()) if (body.id !== anim.targetId) {
          this.setOpacity(body, fade)
          if (body.role === 'sat') body.orbitScale = lerp(.9, 1, fade)
        }
      }
      if (!anim.swapped && t >= .28 && anim.nextGalaxy) {
        const mem = this.memory.get(anim.nextPath)!
        this.applyGalaxy(anim.nextGalaxy, { anchor: mem.anchor, times: mem.satTimes, magnets: mem.magnets, keepId: anim.targetId, fadeInNew: true })
        anim.swapped = true
        if (anim.kind === 'return-leaf') {
          anim.covered = true
          anim.hooks.onCovered?.()
        }
      }
      if (anim.swapped) {
        const appear = easeInOutCubic(clamp((t - .28) / .72, 0, 1))
        for (const body of this.planets.values()) {
          this.setOpacity(body, body.id === anim.targetId ? 1 : appear)
          body.orbitScale = 1
        }
      }
    }
    if (t >= 1 && this.anim === anim && this.generation === anim.generation) {
      if (anim.kind === 'enter-leaf') {
        this.workspace = true
        this.leafId = anim.targetId
        this.planetRoot.visible = false
      }
      for (const body of this.planets.values()) body.speedScale = 0
      this.routePath = anim.nextPath
      this.anim = null
      this.expectedPath = null
      this.idleWeight = 0
      if (!anim.covered) anim.hooks.onCovered?.()
      anim.hooks.onDone?.()
    }
  }

  private updateMagnet(dt: number) {
    if (this.anim || this.paused || this.reduce) return
    if (this.workspace || this.reduce || this.paused) {
      if (!this.focusId && this.attractedId) this.setAttracted(null)
      for (const body of this.planets.values()) {
        body.magnet.lerp(this.scratch.set(0, 0, 0), 1 - 0.86 ** (dt * 60))
        if (!this.focusId) body.speedScale = lerp(body.speedScale, 1, 0.08)
      }
      return
    }
    let bestId: string | null = this.attractedId
    let best = Infinity
    const canHover = this.fine && this.pointerInside
    if (canHover) {
      for (const body of this.planets.values()) {
        if (body.role !== 'sat') continue
        this.ndc.copy(body.group.position).project(this.camera)
        const dist = Math.hypot(this.pointerNdc.x - this.ndc.x, this.pointerNdc.y - this.ndc.y)
        const keep = this.attractedId === body.id ? MAGNET_KEEP : MAGNET_NDC
        if (dist < keep && dist < best) {
          best = dist
          bestId = body.id
        }
      }
      if (best === Infinity && !this.focusId) bestId = null
    } else if (!this.focusId) {
      bestId = null
    }
    if (this.hoverId && this.planets.get(this.hoverId)?.role === 'sat') bestId = this.hoverId
    if (this.focusId) {
      const focused = this.planets.get(this.focusId)
      if (focused?.role === 'sat') bestId = this.focusId
    }
    this.setAttracted(bestId)
    for (const body of this.planets.values()) {
      const hold = body.role === 'sat' && body.id === bestId
      body.speedScale = lerp(body.speedScale, hold ? 0 : 1, 1 - Math.exp(-dt * 6))
      if (!hold || body.role === 'center' || this.focusId === body.id) {
        body.magnet.lerp(this.scratch.set(0, 0, 0), 1 - 0.88 ** (dt * 60))
        continue
      }
      this.ndc.copy(body.group.position).project(this.camera)
      const dx = this.pointerNdc.x - this.ndc.x
      const dy = this.pointerNdc.y - this.ndc.y
      const dist = Math.hypot(dx, dy) || 1
      const pull = clamp(1 - dist / MAGNET_NDC, 0, 1) ** 1.2
      this.scratch.set(this.ndc.x, this.ndc.y, this.ndc.z)
      this.scratch2.set(this.ndc.x + dx * 0.32 * pull, this.ndc.y + dy * 0.32 * pull, this.ndc.z)
      this.scratch.unproject(this.camera)
      this.scratch2.unproject(this.camera)
      this.scratch3.copy(this.scratch2).sub(this.scratch)
      if (this.scratch3.length() > MAGNET_MAX) this.scratch3.setLength(MAGNET_MAX)
      body.magnet.lerp(this.scratch3, 1 - 0.82 ** (dt * 60))
    }
  }

  private updateBodies(dt: number) {
    for (const body of this.planets.values()) {
      body.radius = lerp(body.radius, body.radiusGoal, 1 - 0.9 ** (dt * 60))
      body.group.scale.setScalar(body.radius)
      if (body.role === 'center') {
        body.group.position.copy(this.anchor)
        if (this.anim?.targetId === body.id && (this.anim.kind === 'enter-galaxy' || this.anim.kind === 'enter-leaf')) {
          body.group.position.copy(this.anim.lockedPos)
        }
      } else if (body.orbit) {
        const settling = this.anim?.kind.startsWith('enter') && !this.anim.settled && body.id !== this.anim.targetId
        if ((!this.anim || settling) && !this.reduce && !this.paused && !this.workspace) {
          if (settling) body.speedScale *= Math.exp(-dt * 35)
          body.orbitTime += dt * body.speedScale
        }
        if (!this.anim || this.anim.swapped || settling) {
          const theta = orbitAngle(body.orbit, body.orbitTime)
          orbitOffset(body.orbit, theta, this.orbitPoint, body.orbitScale)
          body.group.position.set(
            this.anchor.x + this.orbitPoint.x + body.magnet.x,
            this.anchor.y + this.orbitPoint.y + body.magnet.y,
            this.anchor.z + this.orbitPoint.z + body.magnet.z,
          )
        }
      }
      if (this.anim?.kind.startsWith('enter') && this.anim.targetId === body.id) body.group.position.copy(this.anim.lockedPos)
      if (!this.reduce && !this.paused) body.spin.rotation.y += (body.role === 'center' ? 0.045 : 0.07) * dt
      const highlighted = this.focusId === body.id || this.hoverId === body.id || this.attractedId === body.id
      const breath = body.role === 'center' && !this.reduce && !this.paused ? Math.sin(this.time * .7) * .025 : 0
      body.surface.emissiveIntensity = lerp(body.surface.emissiveIntensity, .20 + breath + (highlighted ? .12 : 0), 1 - Math.exp(-dt * 8))
    }
  }

  private updateDust(dt: number) {
    if (this.reduce || this.paused) return
    this.dust.rotation.y += dt * .0005
  }

  private updateCamera(_dt: number) {
    if (!this.anim && !this.workspace && !this.paused) {
      this.restFromAnchor()
      this.camPos.copy(this.restPos)
      this.camLook.copy(this.restLook)
      if (!this.reduce && !this.paused && this.idleWeight > 0.01 && !this.workspace) {
        const w = this.idleWeight
        this.camPos.x += Math.sin(this.time / 18 * TAU) * 0.045 * w
        this.camPos.y += Math.cos(this.time / 23 * TAU) * 0.028 * w

      }
    }
    this.camera.position.copy(this.camPos)
    lookQuat(this.camPos, this.camLook, this.up, this.desiredQuat)
    this.camera.quaternion.copy(this.desiredQuat)
  }

  private updateLabels() {
    const box = this.canvas.getBoundingClientRect()
    const tan = Math.tan(this.camera.fov * Math.PI / 360)
    const bodies = [...this.planets.values()].map(body => {
      const p = body.group.position.clone().project(this.camera)
      const depth = body.group.position.clone().applyMatrix4(this.camera.matrixWorldInverse).z * -1
      return { body, x: box.left + (p.x + 1) * box.width / 2, y: box.top + (1 - p.y) * box.height / 2,
        r: body.radius * box.height / (2 * tan * Math.max(.2, depth)), depth }
    }).sort((a, b) => Number(b.body.role === 'center') - Number(a.body.role === 'center') || a.depth - b.depth)
    const rects: Array<{ x: number; y: number; w: number; h: number }> = []
    for (const item of bodies) {
      const el = this.labels.get(item.body.id)
      if (!el) continue
      const w = el.offsetWidth || 124, h = Math.max(44, el.offsetHeight)
      const center = item.body.role === 'center'
      const candidates = center ? [[0, item.r + 12]] : [
        [0, item.r + 8], [0, -item.r - h - 8], [item.r + w / 2 + 8, -h / 2], [-item.r - w / 2 - 8, -h / 2],
        [0, item.r + 56], [0, -item.r - h - 56],
      ]
      if (!center) {
        // Extra nearby placements are needed for narrow panes and coincident
        // projections. These move only the DOM label, never the 3D body or orbit.
        for (let ring = 1; ring <= 5; ring += 1) {
          for (let j = 0; j < 12; j += 1) {
            const a = j * TAU / 12
            candidates.push([Math.cos(a) * (item.r + w * .55 + ring * 20), Math.sin(a) * (item.r + h + ring * 22) - h / 2])
          }
        }
      }
      let chosen = { x: item.x, y: item.y + item.r + 8, w, h }, best = Infinity
      for (const [dx, dy] of candidates) {
        const r = { x: clamp(item.x + dx, box.left + w / 2 + 6, box.right - w / 2 - 6),
          y: clamp(item.y + dy, box.top + 4, box.bottom - h - 4), w, h }
        let penalty = 0
        for (const other of rects) {
          if (Math.abs(r.x - other.x) < (r.w + other.w) / 2 + 6 && r.y < other.y + other.h + 5 && r.y + r.h + 5 > other.y) penalty += 1000
        }
        for (const other of bodies) {
          if (other.body === item.body) continue
          const nearX = clamp(other.x, r.x - w / 2, r.x + w / 2)
          const nearY = clamp(other.y, r.y, r.y + h)
          if (Math.hypot(other.x - nearX, other.y - nearY) < other.r + 6) penalty += 100
        }
        if (penalty < best) { best = penalty; chosen = r }
        if (best === 0) break
      }
      if (best >= 1000 && !center) {
        // Rare compact-view conjunction: search free DOM space, keeping all labels
        // operable rather than hiding an entrance or moving its orbital position.
        let nearest = Infinity
        for (let y = box.top + 4; y <= box.bottom - h - 4; y += 4) {
          for (let x = box.left + w / 2 + 6; x <= box.right - w / 2 - 6; x += 4) {
            if (rects.some(r => Math.abs(x - r.x) < (w + r.w) / 2 + 2 && y < r.y + r.h + 2 && y + h + 2 > r.y)) continue
            if (bodies.some(b => Math.hypot(b.x - clamp(b.x, x - w / 2, x + w / 2), b.y - clamp(b.y, y, y + h)) < b.r + 4)) continue
            const distance = Math.hypot(x - item.x, y - item.y - item.r - 8)
            if (distance < nearest) { chosen = { x, y, w, h }; nearest = distance }
          }
        }
      }
      rects.push(chosen)
      // Labels are placed beside, never through, a foreground sphere. Every entry
      // remains a keyboard target even during a brief physical occultation.
      const visible = !this.workspace && !this.anim && this.planetRoot.visible && item.body.opacity > .35
      el.style.transform = `translate3d(${chosen.x}px, ${chosen.y}px, 0) translate(-50%, 0)`
      el.style.opacity = visible ? '1' : '0'
      el.style.pointerEvents = visible ? 'auto' : 'none'
      el.style.zIndex = center ? '5' : '4'
      el.classList.toggle('is-attracted', this.attractedId === item.body.id || this.focusId === item.body.id)
    }
  }

  private rememberLayer() {
    if (!this.galaxy || this.workspace) return
    const satTimes: Record<string, number> = {}
    for (const body of this.planets.values()) {
      if (body.role === 'sat') satTimes[body.id] = body.orbitTime
    }
    this.memory.set(this.galaxy.path, {
      path: this.galaxy.path,
      anchor: this.anchor.clone(),
      satTimes,
      magnets: Object.fromEntries([...this.planets.values()].filter(b => b.role === 'sat').map(b => [b.id, b.magnet.clone()])),
      cam: this.camPos.clone(),
      look: this.camLook.clone(),
    })
  }

  private applyGalaxy(
    layer: GalaxyLayer,
    opts: {
      anchor: THREE.Vector3
      times?: Record<string, number>
      magnets?: Record<string, THREE.Vector3>
      keepId?: string
      fadeInNew?: boolean
    },
  ) {
    this.galaxy = layer
    this.workspace = false
    this.planetRoot.visible = true
    this.anchor.copy(opts.anchor)
    const wanted = new Set<string>([layer.center.id, ...layer.children.map((node) => node.id)])
    for (const [id, body] of [...this.planets]) {
      if (!wanted.has(id)) {
        body.group.visible = false
        this.planets.delete(id)
      }
    }
    const center = this.ensureBody(layer.center, 'center', opts.fadeInNew)
    center.role = 'center'
    center.magnet.set(0, 0, 0)
    this.setOpacity(center, opts.fadeInNew && opts.keepId !== center.id ? 0 : 1)
    center.orbit = null
    center.radiusGoal = centerRadius(layer.center.id)
    center.group.position.copy(this.anchor)
    if (opts.keepId === center.id) this.setOpacity(center, 1)
    layer.children.forEach((node, index) => {
      const body = this.ensureBody(node, 'sat', opts.fadeInNew)
      body.role = 'sat'
      body.orbit = orbitFor(node.id, index, layer.children.length, this.compact)
      body.orbitTime = opts.times?.[node.id] ?? 0
      body.radiusGoal = satRadius(node.id)
      body.speedScale = 0
      body.magnet.copy(opts.magnets?.[node.id] ?? new THREE.Vector3())
      if (opts.fadeInNew && opts.keepId !== node.id) {
        body.orbitScale = 0.76
        this.setOpacity(body, 0)
      } else {
        body.orbitScale = 1
        this.setOpacity(body, 1)
      }
      if (opts.keepId === node.id) {
        body.orbitScale = 1
        this.setOpacity(body, 1)
      }
      const theta = orbitAngle(body.orbit, body.orbitTime)
      orbitOffset(body.orbit, theta, this.orbitPoint, body.orbitScale)
      body.group.position.set(
        this.anchor.x + this.orbitPoint.x + body.magnet.x,
        this.anchor.y + this.orbitPoint.y + body.magnet.y,
        this.anchor.z + this.orbitPoint.z + body.magnet.z,
      )
    })
    this.restFromAnchor()
  }

  private ensureBody(node: PlanetNode, role: Role, fadeInNew?: boolean): Body {
    const existing = this.bodyCache.get(node.id)
    if (existing) {
      existing.group.visible = true
      this.planets.set(node.id, existing)
      return existing
    }
    const body = this.makePlanet(node, role)
    this.planets.set(body.id, body)
    this.bodyCache.set(body.id, body)
    this.planetRoot.add(body.group)
    if (fadeInNew) this.setOpacity(body, 0)
    return body
  }

  private makePlanet(node: PlanetNode, role: Role): Body {
    const albedo = this.makeAlbedo(node.palette.c0, node.palette.c1, node.palette.c2, node.id)
    const surface = new THREE.MeshStandardMaterial({
      map: albedo,
      color: 0xf7f8fa,
      roughness: 0.74,
      metalness: 0.04,
      emissive: new THREE.Color(node.palette.c1).multiplyScalar(0.16),
      emissiveIntensity: 0.28,
      transparent: true,
      opacity: 1,
    })
    const mesh = new THREE.Mesh(this.sphereGeo, surface)
    const haze = new THREE.MeshBasicMaterial({
      color: hex(node.palette.c0),
      transparent: true,
      opacity: 0.025,
      side: THREE.BackSide,
      depthWrite: false,
    })
    const atmo = new THREE.Mesh(this.atmosphereGeo, haze)
    const spin = new THREE.Group()
    spin.add(mesh, atmo)
    const group = new THREE.Group()
    const radius = nodeRadius(node, role)
    group.scale.setScalar(radius)
    group.add(spin)
    return {
      id: node.id,
      node,
      role,
      group,
      spin,
      mesh,
      surface,
      haze,
      radius,
      radiusGoal: radius,
      orbit: null,
      orbitTime: 0,
      orbitScale: 1,
      speedScale: 1,
      magnet: new THREE.Vector3(),
      opacity: 1,
      textures: [albedo],
    }
  }

  private setOpacity(body: Body, opacity: number) {
    const value = clamp(opacity, 0, 1)
    body.opacity = value
    body.surface.opacity = value
    body.surface.transparent = value < 0.98
    body.surface.depthWrite = value > 0.88
    body.haze.opacity = 0.025 * value
  }

  private makeAlbedo(light: string, mid: string, dark: string, id: string): THREE.CanvasTexture {
    const canvas = document.createElement('canvas')
    canvas.width = 256
    canvas.height = 128
    const ctx = canvas.getContext('2d')
    const tex = new THREE.CanvasTexture(canvas)
    if (!ctx) return tex
    const seed = [...id].reduce((sum, ch) => sum + ch.charCodeAt(0), 0)
    const fill = ctx.createLinearGradient(0, 0, 0, 128)
    fill.addColorStop(0, light)
    fill.addColorStop(0.4, mid)
    fill.addColorStop(1, mid)
    ctx.fillStyle = fill
    ctx.fillRect(0, 0, 256, 128)
    for (let i = 0; i < 16; i += 1) {
      const y = (seed * (i + 3) * 17) % 128
      ctx.fillStyle = `rgba(255,255,255,${0.03 + (i % 3) * 0.015})`
      ctx.fillRect(0, y, 256, 4 + (i % 4) * 2)
    }
    ctx.fillStyle = `${dark}33`
    for (let i = 0; i < 9; i += 1) {
      ctx.beginPath()
      ctx.ellipse((seed * (i + 1) * 13) % 256, (seed * (i + 5) * 19) % 128, 22, 9, 0, 0, TAU)
      ctx.fill()
    }
    tex.colorSpace = THREE.SRGBColorSpace
    tex.wrapS = THREE.RepeatWrapping
    tex.needsUpdate = true
    return tex
  }

  private makePointSprite(): THREE.CanvasTexture {
    const canvas = document.createElement('canvas')
    canvas.width = 64
    canvas.height = 64
    const ctx = canvas.getContext('2d')
    if (ctx) {
      const glow = ctx.createRadialGradient(32, 32, 0, 32, 32, 32)
      glow.addColorStop(0, 'rgba(255,255,255,1)')
      glow.addColorStop(0.1, 'rgba(236,242,255,0.92)')
      glow.addColorStop(0.22, 'rgba(196,214,255,0.28)')
      glow.addColorStop(0.48, 'rgba(150,176,230,0.06)')
      glow.addColorStop(1, 'rgba(255,255,255,0)')
      ctx.fillStyle = glow
      ctx.fillRect(0, 0, 64, 64)
    }
    const tex = new THREE.CanvasTexture(canvas)
    tex.colorSpace = THREE.SRGBColorSpace
    tex.generateMipmaps = false
    tex.minFilter = THREE.LinearFilter
    tex.magFilter = THREE.LinearFilter
    tex.needsUpdate = true
    return tex
  }

  private makeStars() {
    const haloN = this.compact ? 900 : 2100
    const midN = this.compact ? 600 : 1400
    const diskN = this.compact ? 700 : 1500
    const dustN = this.compact ? 8 : 12
    const far = this.haloPoints(haloN, 2026, 60, 150, 0.55, 0.88)
    const mid = this.haloPoints(midN, 5501, 18, 54, 0.16, 0.78)
    const band = this.diskPoints(diskN, 3111)
    const dust = this.haloPoints(dustN, 4099, 14, 22, 0.04, 0.08)
    const dustVel = new Float32Array(dustN)
    const rng = seedRng(4099)
    for (let i = 0; i < dustN; i += 1) dustVel[i] = (rng() - 0.5) * 0.06
    far.renderOrder = -8
    mid.renderOrder = -6
    band.renderOrder = -5
    dust.renderOrder = -3
    return { far, mid, band, dust, dustVel }
  }

  private makeNebulaTex(color: string): THREE.CanvasTexture {
    const canvas = document.createElement('canvas')
    canvas.width = 256
    canvas.height = 256
    const ctx = canvas.getContext('2d')
    if (ctx) {
      const rgb = hex(color)
      const r = (rgb >> 16) & 255
      const g = (rgb >> 8) & 255
      const b = rgb & 255
      const glow = ctx.createRadialGradient(128, 128, 8, 128, 128, 128)
      glow.addColorStop(0, `rgba(${r},${g},${b},0.85)`)
      glow.addColorStop(0.35, `rgba(${r},${g},${b},0.32)`)
      glow.addColorStop(1, 'rgba(0,0,0,0)')
      ctx.fillStyle = glow
      ctx.fillRect(0, 0, 256, 256)
    }
    const tex = new THREE.CanvasTexture(canvas)
    tex.colorSpace = THREE.SRGBColorSpace
    tex.generateMipmaps = false
    tex.minFilter = THREE.LinearFilter
    tex.needsUpdate = true
    return tex
  }

  private makeNebulae() {
    const spots = [
      { x: -11, y: 7.2, z: -8, sx: 9, sy: 6.2, c: '#4a74c4', a: 0.38 },
      { x: 12, y: 5.6, z: -10, sx: 8, sy: 5.4, c: '#3d5f98', a: 0.3 },
      { x: -10, y: -5.8, z: -7, sx: 7.2, sy: 5, c: '#2f4d7a', a: 0.28 },
      { x: 11, y: -6.4, z: -12, sx: 8.4, sy: 5.8, c: '#6a8fd0', a: 0.26 },
    ]
    for (const spot of spots) {
      const tex = this.makeNebulaTex(spot.c)
      this.nebulaTex.push(tex)
      const mat = new THREE.SpriteMaterial({
        map: tex,
        transparent: true,
        opacity: spot.a,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      })
      const sprite = new THREE.Sprite(mat)
      sprite.position.set(spot.x, spot.y, spot.z)
      sprite.scale.set(spot.sx, spot.sy, 1)
      sprite.renderOrder = -12
      this.nebulae.push(sprite)
      this.nebulaRoot.add(sprite)
    }
  }

  private disposeNebulae() {
    for (const sprite of this.nebulae) {
      this.nebulaRoot.remove(sprite)
      const mat = sprite.material
      mat.map = null
      mat.dispose()
    }
    for (const tex of this.nebulaTex) tex.dispose()
    this.nebulae = []
    this.nebulaTex = []
  }

  private haloPoints(
    count: number,
    seed: number,
    r0 = 70,
    r1 = 190,
    size = 0.2,
    opacity = 0.48,
  ): THREE.Points {
    const rng = seedRng(seed)
    const pos = new Float32Array(count * 3)
    const col = new Float32Array(count * 3)
    for (let i = 0; i < count; i += 1) {
      const theta = rng() * TAU
      const phi = Math.acos(2 * rng() - 1)
      const r = r0 + rng() * (r1 - r0)
      pos[i * 3] = r * Math.sin(phi) * Math.cos(theta)
      pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta)
      pos[i * 3 + 2] = r * Math.cos(phi)
      const tint = 0.55 + rng() * 0.45
      col[i * 3] = 0.74 * tint
      col[i * 3 + 1] = 0.8 * tint
      col[i * 3 + 2] = 0.96 * tint
    }
    return this.starCloud(pos, col, size, opacity)
  }

  private diskPoints(count: number, seed: number): THREE.Points {
    const rng = seedRng(seed)
    const pos = new Float32Array(count * 3)
    const col = new Float32Array(count * 3)
    const tilt = 0.72
    const cy = Math.cos(tilt)
    const sy = Math.sin(tilt)
    for (let i = 0; i < count; i += 1) {
      const core = i < count * 0.22
      const r = core ? 2.4 + rng() ** 1.1 * 6.2 : 14 + rng() ** 1.35 * 72
      const theta = rng() * TAU
      const thick = (core ? 3.6 : 9.5) + r * 0.04
      const y0 = (rng() + rng() + rng() - 1.5) * thick * 0.58
      const x0 = r * Math.cos(theta)
      const z0 = r * Math.sin(theta)
      pos[i * 3] = x0
      pos[i * 3 + 1] = y0 * cy - z0 * sy
      pos[i * 3 + 2] = y0 * sy + z0 * cy
      const warm = core ? 0.1 : rng() * 0.06
      const tint = (core ? 0.78 : 0.48) + rng() * 0.3
      col[i * 3] = (0.8 + warm) * tint
      col[i * 3 + 1] = 0.82 * tint
      col[i * 3 + 2] = (core ? 0.92 : 1) * tint
    }
    return this.starCloud(pos, col, 0.14, 0.5)
  }

  private starCloud(pos: Float32Array, col: Float32Array, size: number, opacity: number): THREE.Points {
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
    geo.setAttribute('color', new THREE.BufferAttribute(col, 3))
    const mat = new THREE.PointsMaterial({
      size,
      sizeAttenuation: true,
      map: this.pointTex,
      vertexColors: true,
      transparent: true,
      opacity,
      depthWrite: false,
      alphaTest: 0.04,
      blending: THREE.AdditiveBlending,
    })
    return new THREE.Points(geo, mat)
  }

  private disposePoints(points: THREE.Points) {
    points.geometry.dispose()
    const mat = points.material as THREE.PointsMaterial
    mat.map = null
    mat.dispose()
    this.scene.remove(points)
  }

  private disposeBody(body: Body) {
    this.planetRoot.remove(body.group)
    body.surface.dispose()
    body.haze.dispose()
    for (const tex of body.textures) tex.dispose()
  }

  private clearPlanets() {
    for (const body of this.bodyCache.values()) this.disposeBody(body)
    this.planets.clear()
    this.bodyCache.clear()
  }

  private camOffset() {
    return new THREE.Vector3(0, 0, cameraDistance(this.camera.aspect, this.galaxy?.children.length ?? 5, this.viewHeight))
  }

  private leafOffset(radius: number) {
    const pose = this.compact ? CAM_REST.mobile : CAM_REST.desktop
    const dist = Math.max(pose.z * 0.42, radius * 5.2)
    return new THREE.Vector3(0, 0.1, dist)
  }

  private restFromAnchor() {
    const offset = this.camOffset()
    this.restPos.copy(this.anchor).add(offset)
    this.restLook.copy(this.anchor)
  }

  private applyRest(snap: boolean) {
    this.restFromAnchor()
    if (snap) {
      this.camPos.copy(this.restPos)
      this.camLook.copy(this.restLook)
      this.camera.position.copy(this.camPos)
      this.camera.lookAt(this.camLook)
    }
  }

  private arcMid(from: THREE.Vector3, to: THREE.Vector3) {
    const mid = from.clone().lerp(to, 0.46)
    const along = this.scratch.copy(to).sub(from)
    this.scratch2.crossVectors(along, this.up)
    if (this.scratch2.lengthSq() < 0.0001) this.scratch2.set(1, 0, 0)
    mid.addScaledVector(this.scratch2.normalize(), 0.72)
    mid.y += 0.22
    return mid
  }

  private bezier(out: THREE.Vector3, a: THREE.Vector3, b: THREE.Vector3, c: THREE.Vector3, t: number) {
    this.scratch.copy(a).lerp(b, t)
    this.scratch2.copy(b).lerp(c, t)
    out.copy(this.scratch).lerp(this.scratch2, t)
  }

  private cancelAnim() {
    this.generation += 1
    this.anim = null
    this.expectedPath = null
  }

  private setAttracted(id: string | null) {
    if (this.attractedId === id) return
    this.attractedId = id
    this.onAttracted(id)
  }

  private resize = () => {
    const box = this.canvas.getBoundingClientRect()
    const w = Math.max(1, box.width)
    const h = Math.max(1, box.height)
    this.viewHeight = h
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(w, h, false)
    this.compact = w <= 760
    // A resize invalidates screen framing, but not saved orbital phases.
    for (const [path, memory] of this.memory) {
      const count = galaxyForPath(path)?.children.length ?? 5
      memory.cam.copy(memory.anchor).add(new THREE.Vector3(0, 0, cameraDistance(w / h, count, h)))
      memory.look.copy(memory.anchor)
    }
    if (this.anim) {
      const interrupted = this.anim
      this.cancelAnim()
      this.syncRoute(interrupted.nextPath)
      if (!interrupted.covered) interrupted.hooks.onCovered?.()
      interrupted.hooks.onDone?.()
    }
  }

  private bindMedia() {
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const pointer = window.matchMedia('(hover: hover) and (pointer: fine)')
    const onMotion = () => {
      this.reduce = motion.matches
      if (this.reduce && this.anim) {
        const interrupted = this.anim
        this.cancelAnim()
        this.syncRoute(interrupted.nextPath)
        if (!interrupted.covered) interrupted.hooks.onCovered?.()
        interrupted.hooks.onDone?.()
      }
    }
    const onPointer = () => {
      this.fine = pointer.matches
    }
    motion.addEventListener('change', onMotion)
    pointer.addEventListener('change', onPointer)
    this.mediaFns.push(() => motion.removeEventListener('change', onMotion))
    this.mediaFns.push(() => pointer.removeEventListener('change', onPointer))
  }

  private onWindowPointer = (event: PointerEvent) => {
    const box = this.canvas.getBoundingClientRect()
    this.pointerInside = event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom
    this.pointerNdc.set(
      ((event.clientX - box.left) / box.width) * 2 - 1,
      -((event.clientY - box.top) / box.height) * 2 + 1,
    )
  }
}
