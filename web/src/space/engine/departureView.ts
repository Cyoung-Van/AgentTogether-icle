import * as THREE from 'three'

type Obstacle = { position: THREE.Vector3; radius: number }

/** Select an exit angle using the complete outgoing system, not just its star. */
export function departureView(
  target: THREE.Vector3,
  startCamera: THREE.Vector3,
  radial: THREE.Vector3,
  distance: number,
  fov: number,
  aspect: number,
  obstacles: Obstacle[],
) {
  const start = startCamera.clone().sub(target).normalize()
  const tangent = start.clone().addScaledVector(radial, -start.dot(radial))
  if (tangent.lengthSq() < 1e-6) tangent.crossVectors(radial, new THREE.Vector3(0, 1, 0))
  if (tangent.lengthSq() < 1e-6) tangent.crossVectors(radial, new THREE.Vector3(1, 0, 0))
  tangent.normalize()
  const preferred = tangent.clone().applyAxisAngle(radial, Math.PI * .31)
  const tanY = Math.tan(fov * Math.PI / 360), tanX = tanY * aspect
  const normX = Math.sqrt(1 + tanX * tanX), normY = Math.sqrt(1 + tanY * tanY)
  const direction = new THREE.Vector3(), axis = new THREE.Vector3(), right = new THREE.Vector3(), up = new THREE.Vector3()
  const relative = new THREE.Vector3(), eye = new THREE.Vector3(), matrix = new THREE.Matrix4()
  let best: { direction: THREE.Vector3; frame: THREE.Quaternion; clearance: number; cost: number } | null = null
  const consider = (tilt: number) => {
      if (direction.angleTo(start) < .6) return
      axis.copy(radial).addScaledVector(direction, -radial.dot(direction))
      if (axis.lengthSq() < 1e-6) axis.set(0, 1, 0).addScaledVector(direction, -direction.y)
      if (axis.lengthSq() < 1e-6) axis.set(1, 0, 0).addScaledVector(direction, -direction.x)
      axis.normalize()
      eye.copy(target).addScaledVector(direction, distance)
      for (const roll of [0, .28, -.28, .6, -.6, 1, -1]) {
        if (aspect < 1) right.copy(axis)
        else right.crossVectors(axis, direction).normalize()
        right.applyAxisAngle(direction, roll)
        up.crossVectors(direction, right).normalize()
        let clearance = Infinity
        for (const obstacle of obstacles) {
          relative.copy(obstacle.position).sub(eye)
          const x = relative.dot(right), y = relative.dot(up), depth = -relative.dot(direction)
          // Signed sphere clearance from the side planes; negative means visible.
          const outside = Math.max((Math.abs(x) - depth * tanX) / normX, (Math.abs(y) - depth * tanY) / normY, -depth)
          clearance = Math.min(clearance, outside - obstacle.radius)
        }
        const cost = direction.angleTo(preferred) + Math.abs(roll) * .2 + Math.abs(tilt) * .15
        const safe = clearance > .025
        const bestSafe = best !== null && best.clearance > .025
        if (!best || (safe && !bestSafe) || (safe && bestSafe && cost < best.cost) || (!safe && !bestSafe && clearance > best.clearance)) {
          best = { direction: direction.clone(), frame: new THREE.Quaternion().setFromRotationMatrix(matrix.makeBasis(right, up, direction)), clearance, cost }
        }
      }
  }
  for (const tilt of [0, .22, -.22, .44, -.44, .66, -.66, .88, -.88, 1.1, -1.1, 1.32, -1.32, 1.5, -1.5]) {
    for (let step = 0; step < 72; step++) {
      const angle = Math.PI * .31 + step * Math.PI * 2 / 72
      direction.copy(tangent).applyAxisAngle(radial, angle).multiplyScalar(Math.cos(tilt)).addScaledVector(radial, Math.sin(tilt)).normalize()
      consider(tilt)
    }
  }
  return best!
}
