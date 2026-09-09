import * as THREE from 'three'
import type { GalaxyLayer } from '../types'

type Theme = 'light' | 'dark'

/** Orbits stay invisible. The class remains so layer memory and camera work unchanged. */
export class SystemVisuals {
  readonly root = new THREE.Group()

  constructor() {
    this.root.name = 'orbital-traces'
    this.root.visible = false
  }

  setTheme(_theme: Theme) {}
  setContext(_isContext: boolean) {}
  setSystem(_layer: GalaxyLayer, center: THREE.Vector3, _scale: number) {
    this.root.position.copy(center)
  }
  update(_dt: number, _animate: boolean, _phases?: ReadonlyMap<string, { orbitTime: number; orbitScale: number; opacity: number }>) {}
  dispose() {
    this.root.removeFromParent()
  }
}
