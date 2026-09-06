import * as THREE from 'three'

type Theme = 'light' | 'dark'
type Field = THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>

const pointVertex = `
  attribute float weight;
  attribute float radius;
  uniform float pointSize;
  varying float vWeight;
  void main() {
    vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
    float distance = max(1.0, -viewPosition.z);
    float depth = clamp(90.0 / distance, 0.5, 1.4);
    vWeight = weight * clamp(depth, 0.6, 1.0);
    gl_PointSize = clamp(pointSize * radius * depth, 0.9, 2.8);
    gl_Position = projectionMatrix * viewPosition;
  }
`

const pointFragment = `
  uniform vec3 tint;
  uniform float opacity;
  varying float vWeight;
  void main() {
    float radius = length(gl_PointCoord - vec2(0.5)) * 2.0;
    if (radius > 1.0) discard;
    float core = exp(-radius * radius * 2.7);
    float edge = 1.0 - smoothstep(0.68, 1.0, radius);
    gl_FragColor = vec4(tint, opacity * vWeight * core * edge);
    #include <colorspace_fragment>
  }
`

function random(seed: number) {
  let state = seed
  return () => {
    state = (state * 16807) % 2147483647
    return (state - 1) / 2147483646
  }
}

/** A stationary, distant volume: camera travel supplies all motion and parallax. */
export class DepthBackdrop {
  readonly root = new THREE.Group()
  private readonly fields: Field[] = []
  private readonly haze: THREE.Sprite[] = []
  private readonly hazeTexture: THREE.DataTexture
  private readonly viewport = new THREE.Vector2(1, 1)
  private disposed = false

  constructor() {
    this.root.name = 'distant-space-volume'
    this.hazeTexture = this.makeHazeTexture()

    // Separate radial shells give the tiny reference points real relative depth.
    this.makeField(240, 124, 172, 819, 1.55)
    this.makeField(144, 88, 122, 1553, 1.95)
    this.makeField(48, 62, 86, 3251, 2.25)

    const clouds = [
      { x: -64, y: 27, z: -88, w: 118, h: 54, r: -.36, warm: false },
      { x: 66, y: -32, z: -112, w: 122, h: 66, r: -.36, warm: true },
      { x: 83, y: 58, z: -51, w: 107, h: 53, r: .44, warm: false },
      { x: -75, y: -71, z: -39, w: 104, h: 56, r: .56, warm: false },
      { x: -106, y: 31, z: 52, w: 119, h: 67, r: .24, warm: true },
      { x: 101, y: -26, z: 64, w: 116, h: 54, r: -.48, warm: false },
      { x: -23, y: 78, z: 104, w: 139, h: 72, r: -.3, warm: false },
      { x: 30, y: -69, z: 110, w: 134, h: 62, r: .38, warm: true },
    ]
    for (const cloud of clouds) {
      const material = new THREE.SpriteMaterial({
        map: this.hazeTexture,
        transparent: true,
        depthWrite: false,
        depthTest: true,
        toneMapped: false,
        rotation: cloud.r,
        blending: THREE.NormalBlending,
      })
      // Blend only the distant haze into the surrounding page at the canvas edge.
      // Planet, orbit and camera rendering stay entirely independent of this mask.
      material.onBeforeCompile = shader => {
        shader.uniforms.backdropViewport = { value: this.viewport }
        shader.fragmentShader = `uniform vec2 backdropViewport;\n${shader.fragmentShader}`
        shader.fragmentShader = shader.fragmentShader.replace('#include <opaque_fragment>', `
          vec2 screenUv = gl_FragCoord.xy / max(backdropViewport, vec2(1.0));
          vec2 screenEdge = min(screenUv, vec2(1.0) - screenUv);
          vec2 screenBlend = smoothstep(vec2(0.0), vec2(0.13), screenEdge);
          diffuseColor.a *= screenBlend.x * screenBlend.y;
          #include <opaque_fragment>
        `)
      }
      material.customProgramCacheKey = () => 'depth-haze-screen-edge-v1'
      const sprite = new THREE.Sprite(material)
      sprite.name = 'distant-haze'
      sprite.position.set(cloud.x, cloud.y, cloud.z)
      sprite.scale.set(cloud.w, cloud.h, 1)
      sprite.userData.warm = cloud.warm
      sprite.renderOrder = -30
      sprite.raycast = () => {}
      sprite.onBeforeRender = renderer => { renderer.getDrawingBufferSize(this.viewport) }
      this.haze.push(sprite)
      this.root.add(sprite)
    }
    this.setTheme('light')
  }

  setTheme(theme: Theme) {
    const light = theme === 'light'
    for (let index = 0; index < this.fields.length; index += 1) {
      const uniforms = this.fields[index].material.uniforms
      uniforms.tint.value.set(light ? 0x617a93 : 0xc0cedb)
      uniforms.opacity.value = light ? [.3, .3, .19][index] : [.5, .43, .27][index]
    }
    for (const sprite of this.haze) {
      const warm = sprite.userData.warm
      sprite.material.color.set(light
        ? (warm ? 0xcbbca7 : 0x9eb6ce)
        : (warm ? 0x695d50 : 0x4b6c8a))
      sprite.material.opacity = light ? (warm ? .16 : .23) : (warm ? .09 : .19)
    }
  }

  dispose() {
    if (this.disposed) return
    this.disposed = true
    for (const field of this.fields) {
      field.geometry.dispose()
      field.material.dispose()
    }
    for (const sprite of this.haze) sprite.material.dispose()
    this.hazeTexture.dispose()
    this.root.clear()
    this.root.removeFromParent()
  }

  private makeField(count: number, inner: number, outer: number, seed: number, size: number) {
    const rng = random(seed)
    const positions = new Float32Array(count * 3)
    const weights = new Float32Array(count)
    const radii = new Float32Array(count)
    for (let index = 0; index < count; index += 1) {
      const azimuth = rng() * Math.PI * 2
      const y = rng() * 2 - 1
      const horizontal = Math.sqrt(1 - y * y)
      const distance = inner + rng() * (outer - inner)
      positions.set([
        Math.cos(azimuth) * horizontal * distance,
        y * distance,
        Math.sin(azimuth) * horizontal * distance,
      ], index * 3)
      weights[index] = .48 + rng() * .52
      radii[index] = .7 + rng() * .55
    }
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    geometry.setAttribute('weight', new THREE.BufferAttribute(weights, 1))
    geometry.setAttribute('radius', new THREE.BufferAttribute(radii, 1))
    geometry.computeBoundingSphere()
    const material = new THREE.ShaderMaterial({
      uniforms: {
        tint: { value: new THREE.Color() },
        opacity: { value: .3 },
        pointSize: { value: size },
      },
      vertexShader: pointVertex,
      fragmentShader: pointFragment,
      transparent: true,
      depthWrite: false,
      depthTest: true,
      toneMapped: false,
      blending: THREE.NormalBlending,
    })
    const field = new THREE.Points(geometry, material)
    field.name = `distant-points-${inner}`
    field.renderOrder = -25
    field.raycast = () => {}
    this.fields.push(field)
    this.root.add(field)
  }

  private makeHazeTexture() {
    const size = 96
    const data = new Uint8Array(size * size * 4)
    for (let y = 0; y < size; y += 1) {
      for (let x = 0; x < size; x += 1) {
        const u = (x + .5) / size * 2 - 1
        const v = (y + .5) / size * 2 - 1
        const radius = Math.hypot(u, v)
        const edge = Math.max(0, 1 - radius * radius)
        // Smooth at both center and edge; no bright nucleus or visible sprite boundary.
        const alpha = Math.exp(-radius * radius * 3.8) * edge * edge
        const offset = (y * size + x) * 4
        data[offset] = 255
        data[offset + 1] = 255
        data[offset + 2] = 255
        data[offset + 3] = Math.round(alpha * 255)
      }
    }
    const texture = new THREE.DataTexture(data, size, size, THREE.RGBAFormat)
    texture.minFilter = THREE.LinearFilter
    texture.magFilter = THREE.LinearFilter
    texture.generateMipmaps = false
    texture.needsUpdate = true
    return texture
  }
}
