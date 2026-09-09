import * as THREE from 'three'

type Theme = 'light' | 'dark'
type Field = THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>
type CloudKind = 'pearl' | 'cool' | 'olive' | 'lilac'

const pointVertex = `
  attribute float weight;
  attribute float radius;
  uniform float pointSize;
  varying float vWeight;
  void main() {
    vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
    float distance = max(1.0, -viewPosition.z);
    float depth = clamp(55.0 / distance, 0.4, 1.25);
    vWeight = weight * clamp(depth, 0.5, 1.0);
    gl_PointSize = clamp(pointSize * radius * depth, 14.0, 40.0);
    gl_Position = projectionMatrix * viewPosition;
  }
`

const pointFragment = `
  uniform vec3 tint;
  uniform float opacity;
  varying float vWeight;
  void main() {
    vec2 offset = gl_PointCoord - vec2(0.5);
    float radius = length(offset) * 2.0;
    if (radius > 1.0) discard;
    float core = exp(-radius * radius * 36.0);
    float halo = exp(-radius * radius * 1.8) * 0.18;
    float light = core * 1.6 + halo;
    vec3 color = mix(tint, vec3(1.0), core);
    gl_FragColor = vec4(color, opacity * vWeight * light);
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

function mixColor(a: THREE.Color, b: THREE.Color, t: number, out: THREE.Color) {
  return out.copy(a).lerp(b, THREE.MathUtils.clamp(t, 0, 1))
}

/**
 * World-fixed atmosphere. Large color fields, a few irregular clouds, then scattered light.
 * Never recenters on the current navigation layer.
 */
export class DepthBackdrop {
  readonly root = new THREE.Group()
  private readonly sky: THREE.Mesh<THREE.SphereGeometry, THREE.ShaderMaterial>
  private readonly skyColor = new THREE.Color()
  private readonly fields: Field[] = []
  private readonly clouds: THREE.Sprite[] = []
  private readonly cloudTextures: THREE.CanvasTexture[] = []
  private readonly viewport = new THREE.Vector2(1, 1)
  private nearField: Field | null = null
  private close = false
  private disposed = false

  constructor() {
    this.root.name = 'distant-space-volume'
    this.sky = this.makeSky()
    this.root.add(this.sky)

    this.makeField(18, 8, 26, 4099, 4.4, true)
    this.makeField(14, 28, 62, 819, 5.0)
    this.makeField(10, 70, 128, 3251, 5.6)

    const clouds: Array<{ x: number; y: number; z: number; w: number; h: number; r: number; kind: CloudKind; seed: number }> = [
      { x: -16, y: 10, z: -26, w: 36, h: 20, r: -0.34, kind: 'pearl', seed: 1103 },
      { x: 20, y: -11, z: -40, w: 42, h: 24, r: 0.41, kind: 'cool', seed: 2207 },
      { x: -6, y: -15, z: -54, w: 34, h: 20, r: 0.16, kind: 'olive', seed: 3311 },
      { x: 12, y: 14, z: -48, w: 46, h: 26, r: -0.18, kind: 'lilac', seed: 4417 },
      { x: -22, y: -4, z: -34, w: 38, h: 22, r: 0.28, kind: 'cool', seed: 5521 },
    ]
    for (const cloud of clouds) {
      const texture = this.makeCloudTexture(cloud.seed)
      this.cloudTextures.push(texture)
      const material = new THREE.SpriteMaterial({
        map: texture,
        transparent: true,
        depthWrite: false,
        depthTest: true,
        toneMapped: false,
        rotation: cloud.r,
        blending: THREE.NormalBlending,
      })
      material.onBeforeCompile = shader => {
        shader.uniforms.backdropViewport = { value: this.viewport }
        shader.fragmentShader = `uniform vec2 backdropViewport;\n${shader.fragmentShader}`
        shader.fragmentShader = shader.fragmentShader.replace('#include <opaque_fragment>', `
          vec2 screenUv = gl_FragCoord.xy / max(backdropViewport, vec2(1.0));
          vec2 screenEdge = min(screenUv, vec2(1.0) - screenUv);
          vec2 screenBlend = smoothstep(vec2(0.0), vec2(0.08), screenEdge);
          diffuseColor.a *= screenBlend.x * screenBlend.y;
          #include <opaque_fragment>
        `)
      }
      material.customProgramCacheKey = () => 'space-cloud-organic-v3'
      const sprite = new THREE.Sprite(material)
      sprite.name = `space-cloud-${cloud.kind}`
      sprite.position.set(cloud.x, cloud.y, cloud.z)
      sprite.scale.set(cloud.w, cloud.h, 1)
      sprite.userData.kind = cloud.kind
      sprite.userData.rest = { x: cloud.x, y: cloud.y, z: cloud.z, w: cloud.w, h: cloud.h }
      sprite.userData.phase = cloud.seed * 0.0013
      sprite.renderOrder = -32
      sprite.raycast = () => {}
      sprite.onBeforeRender = renderer => { renderer.getDrawingBufferSize(this.viewport) }
      this.clouds.push(sprite)
      this.root.add(sprite)
    }
    this.setTheme('light')
  }

  setTheme(theme: Theme) {
    this.paintSky(theme)
    const light = theme === 'light'
    for (let index = 0; index < this.fields.length; index += 1) {
      const uniforms = this.fields[index].material.uniforms
      uniforms.tint.value.set(light
        ? [0xf6f4f8, 0xf0f4f8, 0xf4f2ee][index]
        : [0xb0aaa2, 0x9aa4ae, 0xb4ac9c][index])
      uniforms.opacity.value = light ? [.16, .12, .08][index] : [.1, .07, .05][index]
      this.fields[index].userData.baseOpacity = uniforms.opacity.value
    }
    this.applyProximity()
    for (const sprite of this.clouds) {
      const kind = sprite.userData.kind as CloudKind
      sprite.material.color.set(light
        ? { pearl: 0xc8ccd2, cool: 0xb0bcc8, olive: 0xc4c4bc, lilac: 0xc4c0c8 }[kind]
        : { pearl: 0x4a4844, cool: 0x3e4852, olive: 0x4a483e, lilac: 0x423e48 }[kind])
      sprite.material.opacity = light
        ? { pearl: .34, cool: .38, olive: .3, lilac: .32 }[kind]
        : { pearl: .14, cool: .18, olive: .14, lilac: .15 }[kind]
      sprite.userData.restOpacity = sprite.material.opacity
    }
  }

  update(time: number, active: boolean) {
    const amount = active ? 1 : 0
    this.sky.material.uniforms.time.value = time
    this.sky.material.uniforms.wave.value = amount
    for (const sprite of this.clouds) {
      const rest = sprite.userData.rest
      const phase = sprite.userData.phase
      const restOpacity = sprite.userData.restOpacity ?? sprite.material.opacity
      if (!rest) continue
      const sway = amount
      const front = Math.sin(rest.y * 0.16 + time * 0.14 + phase * 0.12)
      sprite.position.set(
        rest.x + Math.sin(time * 0.07 + phase) * 2.2 * sway,
        rest.y + front * 2.4 * sway,
        rest.z,
      )
      const stretch = 1 + front * 0.12 * sway
      sprite.scale.set(rest.w * (2 - stretch), rest.h * stretch, 1)
      sprite.material.opacity = restOpacity * (1 + front * 0.26 * sway)
    }
  }

  setProximity(close: boolean) {
    this.close = close
    this.applyProximity()
  }

  private applyProximity() {
    if (!this.nearField) return
    const base = this.nearField.userData.baseOpacity ?? 0.82
    this.nearField.material.uniforms.opacity.value = this.close ? base * 1.12 : base
  }

  dispose() {
    if (this.disposed) return
    this.disposed = true
    this.sky.geometry.dispose()
    this.sky.material.dispose()
    for (const field of this.fields) {
      field.geometry.dispose()
      field.material.dispose()
    }
    for (const sprite of this.clouds) sprite.material.dispose()
    for (const texture of this.cloudTextures) texture.dispose()
    this.root.clear()
    this.root.removeFromParent()
  }

  private makeSky() {
    const geometry = new THREE.SphereGeometry(220, 48, 28)
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(geometry.attributes.position.count * 3), 3))
    const material = new THREE.ShaderMaterial({
      uniforms: {
        time: { value: 0 },
        wave: { value: 1 },
      },
      vertexShader: `
        attribute vec3 color;
        varying vec3 vColor;
        varying vec3 vDir;
        void main() {
          vColor = color;
          vDir = normalize(position);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        uniform float time;
        uniform float wave;
        varying vec3 vColor;
        varying vec3 vDir;
        void main() {
          float swell = sin(vDir.y * 12.5 + sin(vDir.x * 1.6) * 0.8 - time * 0.15);
          float drift = sin(vDir.y * 6.8 + vDir.x * 1.2 + time * 0.09);
          float crest = swell * 0.78 + drift * 0.22;
          float band = smoothstep(-0.28, 0.16, crest) * (1.0 - smoothstep(0.14, 0.9, crest));
          vec3 color = vColor * (1.0 + crest * 0.1 * wave);
          color = mix(color, vColor * vec3(0.8, 0.86, 1.04), band * 0.58 * wave);
          color = mix(color, vColor * vec3(1.08, 1.04, 0.95), max(crest, 0.0) * 0.28 * wave);
          gl_FragColor = vec4(color, 1.0);
          #include <colorspace_fragment>
        }
      `,
      side: THREE.BackSide,
      depthWrite: false,
      depthTest: false,
      toneMapped: false,
      fog: false,
    })
    const mesh = new THREE.Mesh(geometry, material)
    mesh.name = 'space-sky-volume'
    mesh.renderOrder = -50
    mesh.frustumCulled = false
    mesh.raycast = () => {}
    return mesh
  }

  private paintSky(theme: Theme) {
    const light = theme === 'light'
    const paper = new THREE.Color(light ? 0xd8d8d4 : 0x2a2824)
    const pearl = new THREE.Color(light ? 0xd4d4d0 : 0x2c2c28)
    const cool = new THREE.Color(light ? 0xb4bec8 : 0x1e2226)
    const olive = new THREE.Color(light ? 0xc8c8c2 : 0x242422)
    const lilac = new THREE.Color(light ? 0xc6c4cc : 0x242028)
    const position = this.sky.geometry.attributes.position
    const color = this.sky.geometry.attributes.color
    const mixed = this.skyColor
    for (let index = 0; index < position.count; index += 1) {
      const x = position.getX(index)
      const y = position.getY(index)
      const z = position.getZ(index)
      const length = Math.hypot(x, y, z) || 1
      const dx = x / length
      const dy = y / length
      const dz = z / length
      const starward = THREE.MathUtils.smoothstep(-dz, -0.35, 0.88)
      const rightCool = THREE.MathUtils.smoothstep(dx * 0.72 - dy * 0.28, 0.02, 0.86)
      const leftOlive = THREE.MathUtils.smoothstep(-dx * 0.7 + dy * 0.22, 0.04, 0.9)
      mixColor(paper, pearl, starward * 0.4, mixed)
      mixed.lerp(cool, rightCool * 0.58)
      mixed.lerp(olive, leftOlive * 0.32)
      mixed.lerp(lilac, (1 - starward) * leftOlive * 0.22)
      const lift = 0.92 + starward * 0.04 - rightCool * 0.04
      mixed.multiplyScalar(lift)
      color.setXYZ(index, mixed.r, mixed.g, mixed.b)
    }
    color.needsUpdate = true
  }

  private makeField(count: number, inner: number, outer: number, seed: number, size: number, near = false) {
    const rng = random(seed)
    const positions = new Float32Array(count * 3)
    const weights = new Float32Array(count)
    const radii = new Float32Array(count)
    for (let index = 0; index < count; index += 1) {
      const azimuth = rng() * Math.PI * 2
      const y = rng() * 2 - 1
      const horizontal = Math.sqrt(1 - y * y)
      const distance = inner + rng() * (outer - inner)
      let px = Math.cos(azimuth) * horizontal * distance
      let py = y * distance
      let pz = Math.sin(azimuth) * horizontal * distance
      if (pz > 6 && rng() > 0.28) pz *= -1
      positions.set([px, py, pz], index * 3)
      weights[index] = .42 + rng() * .58
      radii[index] = .55 + rng() * .6
    }
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    geometry.setAttribute('weight', new THREE.BufferAttribute(weights, 1))
    geometry.setAttribute('radius', new THREE.BufferAttribute(radii, 1))
    geometry.computeBoundingSphere()
    const material = new THREE.ShaderMaterial({
      uniforms: {
        tint: { value: new THREE.Color() },
        opacity: { value: .18 },
        pointSize: { value: size },
      },
      vertexShader: pointVertex,
      fragmentShader: pointFragment,
      transparent: true,
      depthWrite: false,
      depthTest: true,
      toneMapped: false,
      blending: THREE.AdditiveBlending,
    })
    const field = new THREE.Points(geometry, material)
    field.name = `distant-points-${inner}`
    field.renderOrder = near ? -20 : -25
    field.raycast = () => {}
    field.userData.baseOpacity = near ? .82 : .64
    if (near) this.nearField = field
    this.fields.push(field)
    this.root.add(field)
  }

  private makeCloudTexture(seed: number) {
    const size = 256
    const canvas = document.createElement('canvas')
    canvas.width = size
    canvas.height = size
    const texture = new THREE.CanvasTexture(canvas)
    texture.colorSpace = THREE.SRGBColorSpace
    const context = canvas.getContext('2d')
    if (!context) return texture
    const rng = random(seed)
    context.clearRect(0, 0, size, size)
    for (let i = 0; i < 16; i += 1) {
      const x = size * (0.16 + rng() * 0.68)
      const y = size * (0.18 + rng() * 0.64)
      const rounder = i < 6
      const rx = size * (rounder ? 0.22 + rng() * 0.26 : 0.1 + rng() * 0.2)
      const ry = size * (rounder ? 0.16 + rng() * 0.18 : 0.06 + rng() * 0.12)
      context.save()
      context.translate(x, y)
      context.rotate((rng() - 0.5) * (rounder ? 1.1 : 2.4))
      context.scale(rx, ry)
      const glow = context.createRadialGradient(0, 0, 0, 0, 0, 1)
      const peak = (rounder ? 0.28 : 0.22) + rng() * 0.2
      glow.addColorStop(0, `rgba(255,255,255,${peak})`)
      glow.addColorStop(0.4, `rgba(255,255,255,${peak * 0.46})`)
      glow.addColorStop(0.78, `rgba(255,255,255,${peak * 0.12})`)
      glow.addColorStop(1, 'rgba(255,255,255,0)')
      context.fillStyle = glow
      context.beginPath()
      context.arc(0, 0, 1, 0, Math.PI * 2)
      context.fill()
      context.restore()
    }
    texture.generateMipmaps = false
    texture.minFilter = THREE.LinearFilter
    texture.magFilter = THREE.LinearFilter
    texture.needsUpdate = true
    return texture
  }
}
