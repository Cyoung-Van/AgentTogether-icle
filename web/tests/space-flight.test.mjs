import { test } from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { SpaceController } from '../src/space/engine/SpaceController.ts'
import { HOME_GALAXY } from '../src/space/hierarchy.ts'

// These tests exercise the real animation loop with a renderer stub. Visibility
// is checked against an independently constructed camera frustum, rather than
// accepting the controller's transition diagnostics as proof of geometry.
let viewport = { width: 1440, height: 762 }
const canvas = () => ({
  dataset: {},
  getContext: () => null,
  getBoundingClientRect: () => ({ left: 0, top: 0, right: viewport.width, bottom: viewport.height, ...viewport }),
})
globalThis.window = {
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  addEventListener() {}, removeEventListener() {}, devicePixelRatio: 1,
}
globalThis.document = { hidden: false, createElement: canvas }
globalThis.ResizeObserver = class { observe() {} disconnect() {} }
globalThis.requestAnimationFrame = () => 1
globalThis.cancelAnimationFrame = () => {}

function controller(width, height, phase) {
  viewport = { width, height }
  const renderer = {
    setClearColor() {}, setPixelRatio() {}, setSize() {}, render() {}, dispose() {},
    shadowMap: {}, info: { memory: {}, render: {} },
  }
  const c = new SpaceController(canvas(), { onAttracted() {}, createRenderer: () => renderer })
  let now = c.lastTime
  c.step = () => { now += 1000 / 120; c.tick(now) }
  c.syncRoute('/')
  for (const body of c.planets.values()) if (body.orbit) body.orbitTime = phase
  c.step()
  return c
}

function intersectsCamera(c, body) {
  c.camera.updateMatrixWorld()
  body.group.updateWorldMatrix(true, false)
  const matrix = new THREE.Matrix4().multiplyMatrices(c.camera.projectionMatrix, c.camera.matrixWorldInverse)
  const frustum = new THREE.Frustum().setFromProjectionMatrix(matrix)
  const scale = body.group.getWorldScale(new THREE.Vector3())
  const sphere = new THREE.Sphere(body.group.getWorldPosition(new THREE.Vector3()), Math.max(scale.x, scale.y, scale.z))
  return frustum.intersectsSphere(sphere)
}

function finish(c, inspectFrame = () => {}) {
  let frames = 0
  while (c.anim && frames++ < 480) { c.step(); inspectFrame() }
  assert.ok(frames < 480, 'animation must finish within four seconds')
}

for (const [width, height] of [[1440, 762], [390, 640]]) {
  for (const phase of [0, 49, 147]) {
    for (const node of HOME_GALAXY.children) {
      test(`${node.path}, ${width}×${height}, orbit ${phase}s: a solid star exits through camera rotation and returns in place`, () => {
        const c = controller(width, height, phase)
        try {
          const source = c.planets.get('home')
          const target = c.planets.get(node.id)
          const outgoing = [...c.planets.values()].filter(body => body.id !== node.id)
          const targetRadius = target.radius
          const targetUuid = target.group.uuid
          const targetPosition = target.group.position.clone()
          const sourcePosition = source.group.position.clone()
          const firstCamera = c.camPos.clone()
          const firstQuaternion = c.camera.quaternion.clone()
          const firstDistance = c.camPos.distanceTo(targetPosition)
          assert.equal(intersectsCamera(c, source), true, 'the departing star starts in view')
          let maxRotation = 0
          let visibleFrames = 0
          let exited = false
          let previousCamera = firstCamera.clone()
          let previousQuaternion = firstQuaternion.clone()
          let covered = 0, done = 0
          c.playEnter(node.id, node.path, {
            onCovered: () => { covered++; c.syncRoute(node.path) },
            onDone: () => done++,
          })
          finish(c, () => {
            for (const body of outgoing) {
              assert.equal(body.group.visible, true, 'entry never forcibly hides an outgoing body')
              assert.equal(body.opacity, 1, 'entry never fades an outgoing body')
              assert.equal(body.surface.opacity, 1)
            }
            const inView = intersectsCamera(c, source)
            assert.equal(source.opacity, 1, 'the departing star must never dissolve')
            assert.equal(source.surface.opacity, 1, 'the star material must stay opaque')
            assert.ok(source.group.position.distanceTo(sourcePosition) < 1e-8, 'the star stays in its world position')
            if (!source.group.visible) assert.equal(inView, false, 'a star may be hidden only after its whole sphere exits')
            if (inView && source.group.visible) visibleFrames++
            if (!inView) exited = true
            assert.equal(target.group.uuid, targetUuid, 'the selected planet keeps its mesh')
            assert.ok(Math.abs(target.radius - targetRadius) < 1e-8, 'zoom changes distance, not physical planet size')
            assert.ok(target.group.position.distanceTo(targetPosition) < 1e-8, 'the selected planet stays anchored throughout flight')
            assert.ok(c.camPos.distanceTo(targetPosition) > target.radius * 3, 'camera stays clear of the target surface')
            assert.ok(c.camPos.toArray().every(Number.isFinite), 'camera positions stay finite')
            if (c.anim) assert.ok(Math.abs(c.up.dot(c.camPos.clone().sub(c.camLook).normalize())) < 1e-8, 'transported camera up remains perpendicular to the sightline')
            assert.ok(c.camPos.distanceTo(previousCamera) < Math.max(.9, firstDistance * .08), 'camera translation has no frame discontinuity')
            assert.ok(c.camera.quaternion.angleTo(previousQuaternion) < .18, 'camera rotation has no frame discontinuity')
            maxRotation = Math.max(maxRotation, c.camera.quaternion.angleTo(firstQuaternion))
            previousCamera.copy(c.camPos)
            previousQuaternion.copy(c.camera.quaternion)
          })
          assert.ok(visibleFrames > 2, 'the original star stays physically present during the start of travel')
          assert.equal(exited, true, 'rotation carries the original star out of frame')
          assert.equal(intersectsCamera(c, source), false, 'the final frame excludes the original star sphere')
          assert.ok(maxRotation > .35, 'travel includes a noticeable three-dimensional camera rotation')
          assert.ok(c.camPos.distanceTo(targetPosition) < firstDistance * .55, 'travel also approaches the selected system')
          assert.equal(c.inspect().path, node.path)
          assert.equal(c.departures.size, outgoing.length, 'outgoing bodies remain in the 3D scene')
          for (const body of outgoing) assert.equal(intersectsCamera(c, body), false, `${body.id} exits geometrically through the camera angle`)
          assert.equal(c.planets.get(node.id).role, 'center')
          assert.equal(covered, 1)
          assert.equal(done, 1)

          const parentMemory = c.memory.get('/')
          const savedTimes = { ...parentMemory.satTimes }
          const savedCamera = parentMemory.cam.clone()
          const savedLook = parentMemory.look.clone()
          previousQuaternion.copy(c.camera.quaternion)
          c.playReturn('/', { onCovered: () => c.syncRoute('/') })
          finish(c, () => {
            assert.equal(source.opacity, 1, 'returning star must also stay solid')
            assert.equal(source.surface.opacity, 1)
            if (intersectsCamera(c, source)) assert.equal(source.group.visible, true, 'the returning star is visible when geometry enters the frame')
            assert.ok(c.camera.quaternion.angleTo(previousQuaternion) < .18, 'return rotation has no frame discontinuity')
            if (c.anim) assert.ok(Math.abs(c.up.dot(c.camPos.clone().sub(c.camLook).normalize())) < 1e-8, 'return camera up remains perpendicular to the sightline')
            previousQuaternion.copy(c.camera.quaternion)
          })
          assert.equal(c.inspect().path, '/')
          assert.equal(c.departures.size, 0, 'return clears detached local moon details')
          assert.equal([...c.systemVisuals.values()].filter(v => v.root.visible).length, 1, 'only the current orbital system remains active after return')
          assert.equal(c.planets.get(node.id).role, 'sat')
          assert.equal(c.planets.get(node.id).group.uuid, targetUuid)
          assert.ok(c.planets.get(node.id).group.position.distanceTo(targetPosition) < .002)
          for (const [id, saved] of Object.entries(savedTimes)) {
            assert.ok(Math.abs(c.planets.get(id).orbitTime - saved) < .002, `${id} restores its parent orbital phase`)
          }
          assert.ok(c.camPos.distanceTo(savedCamera) < .003, 'return restores the saved camera position')
          assert.ok(c.camLook.distanceTo(savedLook) < .003, 'return restores the saved camera target')
          assert.equal(intersectsCamera(c, source), true)
          assert.equal(source.group.visible, true)
        } finally {
          c.dispose()
        }
      })
    }
  }
}
