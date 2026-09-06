import { test } from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { orbitFor, orbitOffset, orbitAngle, CAM_REST, satRadius } from '../src/space/engine/orbits.ts'
import { HOME_GALAXY, ALL_GALAXIES, navigationPath, trailForPath, parentPath, galaxyForPath } from '../src/space/hierarchy.ts'

for (const layer of ALL_GALAXIES) {
  test(`${layer.path}: each child returns to its real parent; leaves stay workspaces`, () => {
    for (const node of layer.children) assert.equal(parentPath(node.path), layer.path)
    assert.equal(galaxyForPath('/tasks'), null)
  })
  test(`${layer.path}: deterministic closed orbits retain depth and never collide over 300 seconds`, () => {
    const nodes = layer.children
    const orbits = nodes.map((n, i) => orbitFor(n.id, i, nodes.length, false))
    for (const [i, o] of orbits.entries()) {
      assert.deepEqual(o, orbitFor(nodes[i].id, i, nodes.length, false))
      assert.ok(o.period >= 120 && o.period <= 300)
      const start = new THREE.Vector3(), end = new THREE.Vector3()
      orbitOffset(o, orbitAngle(o, 0), start)
      orbitOffset(o, orbitAngle(o, o.period), end)
      assert.ok(start.distanceTo(end) < 1e-10)
    }
    for (let t = 0; t <= 300; t += 0.25) {
      const positions = orbits.map(o => { const p = new THREE.Vector3(); orbitOffset(o, orbitAngle(o, t), p); return p })
      for (let i = 0; i < positions.length; i++) for (let j = i + 1; j < positions.length; j++) {
        assert.ok(positions[i].distanceTo(positions[j]) > satRadius(nodes[i].id) + satRadius(nodes[j].id), `collision at ${t}: ${nodes[i].id}/${nodes[j].id}`)
      }
    }
  })
}
test('portrait camera fits the entire orbit, including planet edge', () => {
  const camera = new THREE.PerspectiveCamera(CAM_REST.fov, 462 / 500, .18, 260)
  camera.position.set(CAM_REST.mobile.x, CAM_REST.mobile.y, CAM_REST.mobile.z)
  camera.lookAt(0, 0, 0); camera.updateMatrixWorld()
  for (const [i, node] of HOME_GALAXY.children.entries()) {
    const o = orbitFor(node.id, i, 5, true)
    for (let t = 0; t < o.period; t += .5) {
      const p = new THREE.Vector3(); orbitOffset(o, orbitAngle(o, t), p)
      const n = p.clone().project(camera)
      assert.ok(Math.abs(n.x) < .85 && Math.abs(n.y) < .85, `${node.id} offscreen at ${t}: ${n.toArray()}`)
    }
  }
})

import { SpaceController } from '../src/space/engine/SpaceController.ts'
const box = { left: 0, top: 100, right: 1000, bottom: 800, width: 1000, height: 700 }
const canvas = () => ({ dataset: {}, getContext: () => null, getBoundingClientRect: () => box })
globalThis.window = { matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }), addEventListener() {}, removeEventListener() {}, devicePixelRatio: 1 }
globalThis.document = { hidden: false, createElement: canvas }
globalThis.ResizeObserver = class { observe() {} disconnect() {} }
globalThis.requestAnimationFrame = () => 1
globalThis.cancelAnimationFrame = () => {}
function controller() {
  const renderer = { setClearColor() {}, setPixelRatio() {}, setSize() {}, render() {}, dispose() {}, shadowMap: {}, info: { memory: {}, render: {} } }
  const c = new SpaceController(canvas(), { onAttracted() {}, createRenderer: () => renderer })
  let now = c.lastTime
  c.step = (frames = 1) => { for (let i = 0; i < frames; i++) { now += 1000 / 120; c.tick(now) } }
  c.syncRoute('/'); c.step(20)
  return c
}
function finish(c) { let budget = 200; while(c.inspect().anim && --budget) c.step(); assert.ok(budget > 0) }
function enter(c, id, path) { c.playEnter(id, path, { onCovered: () => c.syncRoute(path) }); finish(c) }
function back(c, path) { c.playReturn(path, { onCovered: () => c.syncRoute(path) }); finish(c) }

test('real loop: same mesh becomes center, leaf opens workspace, two returns restore frozen parent phases', () => {
  const c = controller()
  const original = c.planets.get('task-studio')
  const uuid = original.group.uuid
  c.playEnter('task-studio', '/space/tasks', { onCovered: () => c.syncRoute('/space/tasks') })
  c.step(18)
  const saved = c.memory.get('/').satTimes
  const position = c.anim.lockedPos.clone()
  finish(c)
  assert.equal(c.planets.get('task-studio').group.uuid, uuid)
  assert.equal(c.planets.get('task-studio').role, 'center')
  assert.ok(c.planets.get('task-studio').group.position.distanceTo(position) < 1e-9)
  const p = c.planets.get('task-studio').group.position.clone().project(c.camera)
  assert.ok(Math.hypot(p.x, p.y) < 1e-8)
  enter(c, 'task-list', '/tasks')
  assert.equal(c.inspect().workspace, true)
  assert.equal(c.inspect().path, '/tasks')
  back(c, '/space/tasks')
  assert.equal(c.planets.get('task-studio').group.uuid, uuid)
  assert.equal(c.planets.get('task-studio').role, 'center')
  c.step(300) // time spent in child must not advance root phases
  back(c, '/')
  assert.equal(c.planets.get('task-studio').role, 'sat')
  assert.equal(c.planets.get('task-studio').group.uuid, uuid)
  for (const [id, time] of Object.entries(saved)) assert.ok(Math.abs(c.planets.get(id).orbitTime - time) < .002, `${id} phase changed`)
  assert.ok(c.planets.get('task-studio').group.position.distanceTo(position) < .002)
  c.dispose()
})

test('camera aims at the locked target during its arc and never crosses its surface', () => {
  const c = controller()
  c.playEnter('task-studio', '/space/tasks', {})
  const locked = c.anim.lockedPos.clone(), dist = c.camPos.distanceTo(locked)
  while (c.anim && c.anim.elapsed / c.anim.duration < .33) {
    c.step()
    assert.ok(c.camPos.distanceTo(locked) <= dist + 1e-7)
    assert.ok(c.camPos.distanceTo(locked) > c.planets.get('task-studio').radius * 3)
  }
  const p = locked.clone().project(c.camera)
  assert.ok(Math.hypot(p.x, p.y) < 1e-7)
  while(c.anim) {
    c.step()
    assert.ok(c.camPos.distanceTo(locked) > c.planets.get('task-studio').radius * 3)
    const centered = locked.clone().project(c.camera)
    assert.ok(Math.hypot(centered.x, centered.y) < 1e-7)
  }
  assert.ok(c.camPos.distanceTo(locked) < dist * .55)
  c.dispose()
})

test('external route interruption prevents stale transition callbacks and releases picking', () => {
  const c = controller(); let old = 0
  c.playEnter('task-studio', '/space/tasks', { onCovered: () => old++, onDone: () => old++ })
  c.step(25); c.syncRoute('/space/agents', true); c.step(140)
  assert.equal(c.inspect().path, '/space/agents'); assert.equal(old, 0); assert.equal(c.inspect().anim, null)
  c.dispose()
})

test('direct leaf refresh returns to a deterministic parent without fabricated travel', () => {
  const c = controller(); c.memory.clear(); c.syncRoute('/tasks', true)
  c.playReturn('/space/tasks', {})
  assert.equal(c.inspect().anim, null); assert.equal(c.galaxy.path, '/space/tasks')
  c.dispose()
})

test('pause/reduced motion freezes orbits; reduced navigation keeps hierarchy', () => {
  const c = controller(); c.setPaused(true)
  const times = c.inspect().bodies.map(b => b.orbitTime); c.step(600)
  assert.deepEqual(c.inspect().bodies.map(b => b.orbitTime), times)
  c.reduce = true; enter(c, 'task-studio', '/space/tasks'); assert.equal(c.inspect().anim, null)
  back(c, '/'); assert.equal(c.galaxy.path, '/')
  c.dispose()
})

import { cameraDistance, centerRadius } from '../src/space/engine/orbits.ts'
test('all complete cycles fit portrait and desktop; center occultation never hides an entrance', () => {
  for (const [width,height] of [[320,380], [462,446], [1440,762]]) {
    for (const layer of ALL_GALAXIES) {
      const camera = new THREE.PerspectiveCamera(CAM_REST.fov,width/height,.18,260)
      const scale = layer.parent ? .15 : 1
      camera.position.z = cameraDistance(width/height,layer.children.length,height) * scale
      camera.lookAt(0,0,0); camera.updateMatrixWorld()
      let minDepth=Infinity, maxDepth=-Infinity
      for(let t=0;t<=300;t+=.25) {
        for(const [i,node] of layer.children.entries()) {
          const o=orbitFor(node.id,i,layer.children.length,false), p=new THREE.Vector3()
          orbitOffset(o,orbitAngle(o,t),p,scale); minDepth=Math.min(minDepth,p.z); maxDepth=Math.max(maxDepth,p.z)
          const n=p.clone().project(camera), d=camera.position.z-p.z
          const rp=satRadius(node.id)*scale/(d*Math.tan(CAM_REST.fov*Math.PI/360))
          assert.ok(Math.abs(n.x)+rp/camera.aspect<.99 && Math.abs(n.y)+rp<.99, `${width} ${node.id} leaves viewport at ${t}`)
          const radius = layer.parent ? satRadius(layer.center.id) : centerRadius(layer.center.id)
          const centerPx=radius/(camera.position.z*Math.tan(CAM_REST.fov*Math.PI/360))
          assert.ok(Math.hypot(n.x*camera.aspect,n.y)>centerPx+rp, `${node.id} hidden by center at ${t}`)
        }
      }
      assert.ok(maxDepth-minDepth>1.5*scale,'depth discarded')
    }
  }
})

test('projected DOM labels stay separate during the entire orbital cycle', () => {
  const c=controller()
  for(const layer of ALL_GALAXIES) {
    for(const [width,height] of [[320,380],[462,446],[1440,762]]) {
      c.camera.aspect=width/height; c.viewHeight=height
      c.syncRoute(layer.path,true); c.applyRest(true); c.camera.updateMatrixWorld()
      const labels=new Map([...c.planets.values()].map(b=>[b.id,{offsetWidth:b.role==='center'?150:110,offsetHeight:b.role==='center'?62:44,style:{},classList:{toggle(){}}}]))
      c.canvas.getBoundingClientRect=()=>({left:0,top:0,right:width,bottom:height,width,height})
      c.setLabels(labels)
      for(let t=0;t<=300;t+=.5) {
        for(const b of c.planets.values()) if(b.orbit) {
          b.orbitTime=t
          orbitOffset(b.orbit,orbitAngle(b.orbit,t),b.group.position,c.systemScale)
          b.group.position.applyQuaternion(c.systemFrame).add(c.anchor)
        }
        c.updateLabels()
        const boxes=[...labels].map(([id,l])=>{const [x,y]=l.style.transform.match(/translate3d\(([^p]+)px, ([^p]+)px/).slice(1).map(Number);return{id,x,y,w:l.offsetWidth,h:l.offsetHeight}})
        for(let i=0;i<boxes.length;i++) for(let j=i+1;j<boxes.length;j++) {
          const a=boxes[i],b=boxes[j]
          assert.ok(!(Math.abs(a.x-b.x)<(a.w+b.w)/2 && a.y<b.y+b.h && a.y+a.h>b.y), `${width} ${layer.path} label collision ${a.id}/${b.id} t=${t}`)
        }
      }
    }
  }
  c.dispose()
})

test('same navigation rules cover the agent cluster and every root leaf', () => {
  const c=controller()
  for(const main of HOME_GALAXY.children) {
    enter(c,main.id,main.path)
    const galaxy=galaxyForPath(main.path)
    if(galaxy) {
      assert.equal(c.planets.get(main.id).role,'center')
      for(const child of galaxy.children) {
        enter(c,child.id,child.path); assert.equal(c.workspace,true)
        back(c,main.path); assert.equal(c.galaxy.path,main.path)
      }
    } else assert.equal(c.workspace,true)
    back(c,'/'); assert.equal(c.galaxy.path,'/'); assert.equal(c.workspace,false)
  }
  c.dispose()
})

test('fast duplicate clicks cannot skip a layer; background resume cannot jump clocks', () => {
  const c=controller(); let first=0,second=0
  c.playEnter('task-studio','/space/tasks',{onCovered:()=>first++})
  c.playEnter('agents','/space/agents',{onCovered:()=>second++})
  finish(c); assert.equal(first,1); assert.equal(second,0); assert.equal(c.galaxy.path,'/space/tasks')
  const before=c.planets.get('task-list').orbitTime
  document.hidden=true; c.tick(c.lastTime+360000); document.hidden=false
  c.tick(c.lastTime+1000/60)
  assert.ok(c.planets.get('task-list').orbitTime-before<.02)
  c.dispose()
})

test('cached GPU bodies are bounded by the real navigation tree and disposed together', () => {
  const c=controller()
  for(let i=0;i<5;i++) { enter(c,'task-studio','/space/tasks'); back(c,'/') }
  assert.equal(c.bodyCache.size,10)
  let surfaces=0, textures=0
  for(const b of c.bodyCache.values()) {
    b.surface.addEventListener('dispose',()=>surfaces++)
    for(const t of b.textures)t.addEventListener('dispose',()=>textures++)
  }
  c.dispose(); assert.equal(surfaces,10); assert.equal(textures,10); assert.equal(c.bodyCache.size,0)
})

test('reduced-motion entry resolves directly to final sizes without residual growth', () => {
  const c=controller(); c.reduce=true
  enter(c,'task-studio','/space/tasks')
  assert.equal(c.planets.get('task-studio').radius,satRadius('task-studio'))
  assert.equal(c.planets.get('task-studio').radiusGoal,satRadius('task-studio'))
  c.dispose()
})

for (const id of ['today','sessions','settings']) {
  test(`${id}: root entrance owns a real subgalaxy and preserves the legacy workspace`, () => {
    const rootNode=HOME_GALAXY.children.find(n=>n.id===id)
    assert.equal(rootNode.path,`/space/${id}`)
    const galaxy=galaxyForPath(rootNode.path)
    assert.ok(galaxy && galaxy.children.length>=3 && galaxy.children.length<=5)
    assert.equal(galaxy.center,rootNode)
    assert.equal(parentPath(`/${id}`),rootNode.path)
    assert.equal(galaxyForPath(`/${id}`),null)
    for(const child of galaxy.children) {
      assert.equal(parentPath(child.path),rootNode.path)
      assert.equal(galaxyForPath(child.path),null)
    }
  })
}


test('known section URLs retain their parent, while unknown fragments keep legacy behavior', () => {
  assert.equal(navigationPath({pathname:'/settings',hash:'#budgets'}),'/settings#budgets')
  assert.equal(navigationPath({pathname:'/settings',hash:'#unknown'}),'/settings')
  assert.deepEqual(trailForPath('/sessions#task-preparation').map(n=>n.path),['/','/space/sessions','/sessions#task-preparation'])
  assert.equal(parentPath('/cost'),'/settings#budgets')
})

test('section siblings on the same pathname have independent identity and return snapshots', () => {
  const c=controller()
  enter(c,'settings','/space/settings')
  const id=c.planets.get('settings').group.uuid
  for(const [node,path] of [['settings-general','/settings#general'],['settings-budgets','/settings#budgets']]) {
    enter(c,node,path); assert.equal(c.inspect().path,path)
    back(c,'/space/settings'); assert.equal(c.planets.get('settings').group.uuid,id)
    assert.equal(c.inspect().path,'/space/settings')
  }
  c.dispose()
})

test('idle preparation warms future satellites without changing the visible system and reuses them during entry', () => {
  const callbacks = new Map()
  let sequence = 0, uploads = 0, compiles = 0
  window.requestIdleCallback = callback => { callbacks.set(++sequence, callback); return sequence }
  window.cancelIdleCallback = id => callbacks.delete(id)
  const c = controller()
  c.renderer.initTexture = () => uploads++
  c.renderer.compile = () => compiles++
  try {
    const visible = c.inspect().visibleBodyIds
    const original = c.inspect().bodies
    c.queueWarmup(HOME_GALAXY)
    while (callbacks.size) {
      const [id, callback] = callbacks.entries().next().value
      callbacks.delete(id); callback()
    }
    assert.ok(uploads > 0)
    assert.equal(compiles, uploads)
    assert.deepEqual(c.inspect().visibleBodyIds, visible)
    assert.deepEqual(c.inspect().bodies, original)
    const ids = ['today-overview', 'today-needs', 'today-active', 'today-resources']
    const prepared = new Map(ids.map(id => [id, c.bodyCache.get(id).group.uuid]))
    assert.ok(ids.every(id => !c.bodyCache.get(id).group.visible))
    const cacheSize = c.bodyCache.size
    enter(c, 'today', '/space/today')
    assert.equal(c.bodyCache.size, cacheSize, 'entry creates no new body resources')
    for (const id of ids) {
      assert.equal(c.planets.get(id).group.uuid, prepared.get(id))
      assert.equal(c.planets.get(id).radius, c.planets.get(id).radiusGoal)
    }
    const textures = [...c.bodyCache.values()].flatMap(body => body.textures)
    let released = 0
    textures.forEach(texture => texture.addEventListener('dispose', () => released++))
    c.dispose()
    assert.equal(released, textures.length)
  } finally {
    if (!c.disposed) c.dispose()
    delete window.requestIdleCallback
    delete window.cancelIdleCallback
  }
})

test('background preparation never runs during camera flight and pending work is canceled on disposal', () => {
  const callbacks = new Map()
  let sequence = 0, uploads = 0
  window.requestIdleCallback = callback => { callbacks.set(++sequence, callback); return sequence }
  window.cancelIdleCallback = id => callbacks.delete(id)
  const c = controller()
  c.renderer.initTexture = () => uploads++
  c.renderer.compile = () => {}
  try {
    c.queueWarmup(HOME_GALAXY)
    c.playEnter('today', '/space/today', {})
    const [id, callback] = callbacks.entries().next().value
    callbacks.delete(id); callback()
    assert.equal(uploads, 0, 'flight does not share its frame budget with warmup')
    assert.equal(callbacks.size, 1, 'warmup is deferred rather than discarded')
    c.dispose()
    assert.equal(callbacks.size, 0)
    assert.equal(c.warmQueue.length, 0)
  } finally {
    if (!c.disposed) c.dispose()
    delete window.requestIdleCallback
    delete window.cancelIdleCallback
  }
})
