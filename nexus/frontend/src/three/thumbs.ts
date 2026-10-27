// Small pictures of machines and of individual components, rendered once from the real
// geometry (not drawn by hand), so the pictures always match the 3D model.
import { useEffect, useState } from 'react'
import * as THREE from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { getAssembly } from './assemblies'
import { MatKey, disposeMaterial, makeMaterial } from './materials'

const cache = new Map<string, string>()
const DIR = new THREE.Vector3(0.62, 0.45, 0.7).normalize()

function renderAll(types: { type: string; drive?: any }[]) {
  const W = 200, Ht = 150
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setSize(W, Ht)
  renderer.setPixelRatio(1)
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  const pmrem = new THREE.PMREMGenerator(renderer)
  const env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
  const scene = new THREE.Scene()
  scene.environment = env
  scene.add(new THREE.HemisphereLight('#cfdcf0', '#10141a', 0.7))
  const sun = new THREE.DirectionalLight('#ffffff', 2.2)
  sun.position.set(4, 7, 5)
  scene.add(sun)
  const camera = new THREE.PerspectiveCamera(30, W / Ht, 0.05, 200)
  const holder = new THREE.Group()
  scene.add(holder)

  const shoot = (key: string, box: THREE.Box3, dir = DIR) => {
    const centre = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3())
    const radius = Math.max(size.length() / 2, 0.05)
    camera.position.copy(centre).addScaledVector(dir, (radius / Math.sin((camera.fov * Math.PI) / 360)) * 1.05)
    camera.lookAt(centre)
    renderer.render(scene, camera)
    cache.set(key, renderer.domElement.toDataURL('image/png'))
  }

  for (const { type, drive } of types) {
    if (cache.has(`asset:${type}`)) continue
    const asm = getAssembly(type, drive)
    const mats: Partial<Record<MatKey, THREE.MeshStandardMaterial>> = {}
    const groups = asm.parts.map((p) => {
      const g = new THREE.Group()
      g.position.set(...p.pos)
      for (const md of p.meshes) {
        const mesh = new THREE.Mesh(md.geo, (mats[md.mat] ||= makeMaterial(md.mat, type)))
        if (md.pivot) mesh.position.set(...md.pivot)
        g.add(mesh)
      }
      holder.add(g)
      return g
    })
    const whole = new THREE.Box3()
    asm.parts.forEach((p) => whole.union(p.box.clone().translate(new THREE.Vector3(...p.pos))))
    shoot(`asset:${type}`, whole, type === 'conveyor' ? new THREE.Vector3(0.75, 0.4, 0.55).normalize() : DIR)
    asm.parts.forEach((p, i) => {
      groups.forEach((g, j) => { g.visible = i === j })
      shoot(`part:${type}:${p.id}`, p.box.clone().translate(new THREE.Vector3(...p.pos)))
    })
    groups.forEach((g) => holder.remove(g))
    Object.values(mats).forEach((m) => m && disposeMaterial(m))
  }
  env.dispose()
  pmrem.dispose()
  renderer.dispose()
  renderer.forceContextLoss()
}

let version = 0
const listeners = new Set<() => void>()
/** Returns a lookup for thumbnails; renders them the first time they are needed. */
export function useThumbs(types: { type: string; drive?: any }[]) {
  const [, force] = useState(0)
  const key = types.map((t) => t.type).join(',')
  useEffect(() => {
    const bump = () => force((n) => n + 1)
    listeners.add(bump)
    const missing = types.filter((t) => !cache.has(`asset:${t.type}`))
    let timer: any
    if (missing.length) {
      // after first paint, so the page appears before the pictures are made
      timer = setTimeout(() => {
        try { renderAll(missing) } catch (err) { console.warn('Thumbnails could not be rendered', err) }
        version++
        listeners.forEach((l) => l())
      }, 350)
    }
    return () => { listeners.delete(bump); clearTimeout(timer) }
  }, [key])   // eslint-disable-line react-hooks/exhaustive-deps
  return { asset: (type: string) => cache.get(`asset:${type}`), part: (type: string, id: string) => cache.get(`part:${type}:${id}`), version }
}
