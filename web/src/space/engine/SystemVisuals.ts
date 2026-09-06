import * as THREE from 'three'
import type { GalaxyLayer } from '../types'
import { orbitAngle, orbitFor, orbitOffset, type OrbitParams } from './orbits'

type Theme = 'light' | 'dark'
type Phase = { orbitTime: number; orbitScale: number; opacity: number }
type Trail = {
  id: string
  orbit: OrbitParams
  span: number
  ribbon: THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial>
  dust: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>
  samples: Array<{ t: number; radial: number; lift: number }>
  lastTime: number
  lastScale: number
}

const SEGMENTS = 112
const DUST_COUNT = 18
const vertexShader = `
  attribute float weight;
  attribute float across;
  uniform float systemScale;
  varying float vWeight;
  varying float vAcross;
  varying float vDistance;
  void main() {
    vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
    vWeight = weight;
    vAcross = across;
    vDistance = max(0.1, -viewPosition.z / systemScale);
    gl_Position = projectionMatrix * viewPosition;
  }
`
const fragmentShader = `
  uniform vec3 tint;
  uniform float opacity;
  uniform float bodyOpacity;
  varying float vWeight;
  varying float vAcross;
  varying float vDistance;
  void main() {
    float edge = exp(-vAcross * vAcross * 4.0) * (1.0 - smoothstep(0.8, 1.0, abs(vAcross)));
    float depth = clamp(pow(18.0 / vDistance, 1.3), 0.2, 1.0);
    float alpha = opacity * bodyOpacity * vWeight * edge * depth;
    if (alpha < 0.002) discard;
    gl_FragColor = vec4(tint, alpha);
    #include <colorspace_fragment>
  }
`
const dustVertexShader = `
  attribute float weight;
  uniform float systemScale;
  uniform float pointSize;
  varying float vWeight;
  void main() {
    vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
    float distance = max(0.1, -viewPosition.z / systemScale);
    float depth = clamp(18.0 / distance, 0.2, 1.0);
    vWeight = weight * depth;
    gl_PointSize = clamp(pointSize * depth, 0.65, 2.0);
    gl_Position = projectionMatrix * viewPosition;
  }
`
const dustFragmentShader = `
  uniform vec3 tint;
  uniform float opacity;
  uniform float bodyOpacity;
  varying float vWeight;
  void main() {
    float radius = length(gl_PointCoord - vec2(0.5)) * 2.0;
    if (radius > 1.0) discard;
    gl_FragColor = vec4(tint, opacity * bodyOpacity * vWeight * exp(-radius * radius * 4.0));
    #include <colorspace_fragment>
  }
`

/** Subtle, open orbital traces. Body clocks remain the sole source of motion. */
export class SystemVisuals {
  readonly root = new THREE.Group()
  private theme: Theme = 'light'
  private systemKey = ''
  private contextual = false
  private scale = 1
  private trails: Trail[] = []

  constructor() { this.root.name = 'orbital-traces' }

  setTheme(theme: Theme) {
    this.theme = theme
    const light = theme === 'light'
    for (const trail of this.trails) {
      const line = trail.ribbon.material.uniforms
      const dust = trail.dust.material.uniforms
      line.tint.value.setHex(light ? 0x607c97 : 0x9bc7df)
      line.opacity.value = light ? .25 : .36
      dust.tint.value.copy(line.tint.value)
      dust.opacity.value = light ? .11 : .17
      dust.pointSize.value = light ? 1.7 : 2.0
    }
  }

  setContext(isContext: boolean) {
    this.contextual = isContext
    // Ancestral guides are not physical bodies. Keeping them in a close-up
    // creates screen-spanning scaffolding, so only the current system has traces.
    for (const trail of this.trails) {
      trail.ribbon.visible = !isContext
      trail.dust.visible = !isContext
    }
  }

  setSystem(layer: GalaxyLayer, center: THREE.Vector3, scale: number) {
    this.root.position.copy(center)
    this.root.scale.setScalar(scale)
    this.scale = scale
    const key = `${layer.id}:${layer.children.map(child => child.id).join(',')}`
    if (key !== this.systemKey) {
      this.clearGeometry()
      this.systemKey = key
      layer.children.forEach((child, index) => this.addTrail(child.id, orbitFor(child.id, index, layer.children.length, false), index))
      this.setTheme(this.theme)
      this.setContext(this.contextual)
    }
    for (const trail of this.trails) {
      trail.ribbon.material.uniforms.systemScale.value = scale
      trail.dust.material.uniforms.systemScale.value = scale
    }
  }

  update(_dt: number, _animate: boolean, phases?: ReadonlyMap<string, Phase>) {
    if (this.contextual) return
    for (const trail of this.trails) {
      const body = phases?.get(trail.id)
      if (!body) continue
      trail.ribbon.material.uniforms.bodyOpacity.value = body.opacity
      trail.dust.material.uniforms.bodyOpacity.value = body.opacity
      if (body.orbitTime !== trail.lastTime || body.orbitScale !== trail.lastScale) {
        this.positionTrail(trail, body.orbitTime, body.orbitScale)
      }
    }
  }

  dispose() {
    this.clearGeometry()
    this.root.removeFromParent()
    this.systemKey = ''
  }

  private addTrail(id: string, orbit: OrbitParams, index: number) {
    const geometry = new THREE.BufferGeometry()
    const positions = new Float32Array((SEGMENTS + 1) * 6)
    const weights = new Float32Array((SEGMENTS + 1) * 2)
    const sides = new Float32Array((SEGMENTS + 1) * 2)
    const indices: number[] = []
    for (let i = 0; i <= SEGMENTS; i++) {
      const t = i / SEGMENTS
      const head = Math.min(1, t / .075)
      const weight = head * head * (3 - 2 * head) * Math.pow(1 - t, 1.65)
      weights[i * 2] = weights[i * 2 + 1] = weight
      sides[i * 2] = -1
      sides[i * 2 + 1] = 1
      if (i < SEGMENTS) { const n = i * 2; indices.push(n, n + 1, n + 2, n + 1, n + 3, n + 2) }
    }
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3).setUsage(THREE.DynamicDrawUsage))
    geometry.setAttribute('weight', new THREE.BufferAttribute(weights, 1))
    geometry.setAttribute('across', new THREE.BufferAttribute(sides, 1))
    geometry.setIndex(indices)
    geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(), orbit.a * 1.2)
    const uniforms = () => ({ tint: { value: new THREE.Color() }, opacity: { value: 0 }, bodyOpacity: { value: 1 }, systemScale: { value: this.scale } })
    const ribbon = new THREE.Mesh(geometry, new THREE.ShaderMaterial({
      uniforms: uniforms(), vertexShader, fragmentShader,
      transparent: true, depthWrite: false, depthTest: true, side: THREE.DoubleSide, toneMapped: false,
    }))
    ribbon.name = `orbit-trace-${id}`
    const samples = Array.from({ length: DUST_COUNT }, (_, i) => ({
      t: .05 + ((i * .61803398875 + index * .137) % 1) * .86,
      radial: Math.sin(i * 7.19 + index * 2.3) * .022,
      lift: Math.sin(i * 4.31 + index) * .014,
    }))
    const dustGeometry = new THREE.BufferGeometry()
    dustGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(DUST_COUNT * 3), 3).setUsage(THREE.DynamicDrawUsage))
    dustGeometry.setAttribute('weight', new THREE.BufferAttribute(new Float32Array(samples.map((s, i) => Math.pow(1 - s.t, 1.4) * (.25 + (i % 4) * .18))), 1))
    dustGeometry.boundingSphere = geometry.boundingSphere.clone()
    const dust = new THREE.Points(dustGeometry, new THREE.ShaderMaterial({
      uniforms: { ...uniforms(), pointSize: { value: 1.7 } },
      vertexShader: dustVertexShader, fragmentShader: dustFragmentShader,
      transparent: true, depthWrite: false, depthTest: true, toneMapped: false,
    }))
    dust.name = `orbital-dust-${id}`
    const trail: Trail = { id, orbit, ribbon, dust, samples, span: (100 + index * 7) * Math.PI / 180, lastTime: NaN, lastScale: NaN }
    this.trails.push(trail)
    this.root.add(ribbon, dust)
    this.positionTrail(trail, 0, 1)
  }

  private positionTrail(trail: Trail, time: number, radiusScale: number) {
    const head = orbitAngle(trail.orbit, time)
    const positions = trail.ribbon.geometry.getAttribute('position') as THREE.BufferAttribute
    const weights = trail.ribbon.geometry.getAttribute('weight') as THREE.BufferAttribute
    const point = { x: 0, y: 0, z: 0 }
    for (let i = 0; i <= SEGMENTS; i++) {
      const angle = head - trail.span * i / SEGMENTS
      const halfWidth = .060 * Math.sqrt(weights.getX(i * 2)) / trail.orbit.a
      for (let side = 0; side < 2; side++) {
        orbitOffset(trail.orbit, angle, point, radiusScale * (1 + (side ? 1 : -1) * halfWidth))
        positions.setXYZ(i * 2 + side, point.x, point.y, point.z)
      }
    }
    positions.needsUpdate = true
    const dust = trail.dust.geometry.getAttribute('position') as THREE.BufferAttribute
    trail.samples.forEach((sample, i) => {
      orbitOffset(trail.orbit, head - trail.span * sample.t, point, radiusScale * (1 + sample.radial / trail.orbit.a))
      dust.setXYZ(i, point.x, point.y + sample.lift, point.z)
    })
    dust.needsUpdate = true
    trail.lastTime = time
    trail.lastScale = radiusScale
  }

  private clearGeometry() {
    for (const trail of this.trails) {
      for (const object of [trail.ribbon, trail.dust]) {
        object.geometry.dispose()
        object.material.dispose()
        object.removeFromParent()
      }
    }
    this.trails = []
  }
}
