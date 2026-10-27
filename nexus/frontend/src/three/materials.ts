// Material palette. Every component instance gets its own material objects, so highlighting,
// cutaway and X-ray can change one component without touching any other.
import * as THREE from 'three'
import { beltTexture } from './geom'

export type MatKey = 'paint' | 'paint2' | 'steel' | 'dark' | 'cast' | 'copper' | 'brass' | 'rubber' | 'yellow' | 'white' | 'belt' | 'vbelt' | 'crate'

/** Main and secondary paint colour per machine type. */
export const PAINT: Record<string, [string, string]> = {
  dc_motor: ['#1c62c4', '#16407e'],
  motor: ['#2a6aa6', '#1b446e'],
  cooling_fan: ['#2f6db3', '#22446f'],
  pump: ['#1d5fae', '#173f73'],
  compressor: ['#2760aa', '#c8cdd4'],
  gearbox: ['#56677b', '#3d4a5a'],
  conveyor: ['#274d86', '#1b3559'],
}

const BASE: Record<Exclude<MatKey, 'paint' | 'paint2'>, THREE.MeshStandardMaterialParameters> = {
  steel: { color: '#c7ced6', metalness: 1, roughness: 0.3 },
  dark: { color: '#262d36', metalness: 0.65, roughness: 0.5 },
  cast: { color: '#59626c', metalness: 0.55, roughness: 0.66 },
  copper: { color: '#c7793a', metalness: 1, roughness: 0.34 },
  brass: { color: '#c2a04e', metalness: 1, roughness: 0.36 },
  rubber: { color: '#15171a', metalness: 0, roughness: 0.92 },
  yellow: { color: '#e2ab1f', metalness: 0.15, roughness: 0.55 },
  white: { color: '#aeb7c1', metalness: 0.2, roughness: 0.55 },
  belt: { color: '#ffffff', metalness: 0, roughness: 0.86 },
  vbelt: { color: '#ffffff', metalness: 0, roughness: 0.9 },
  crate: { color: '#b08a5c', metalness: 0, roughness: 0.85 },
}

export function makeMaterial(key: MatKey, type: string): THREE.MeshStandardMaterial {
  const paint = PAINT[type] || PAINT.motor
  const params: THREE.MeshStandardMaterialParameters =
    key === 'paint' ? { color: paint[0], metalness: 0.35, roughness: 0.42 }
    : key === 'paint2' ? { color: paint[1], metalness: 0.35, roughness: 0.45 }
    : BASE[key]
  const m = new THREE.MeshStandardMaterial({ ...params, vertexColors: true })
  if (key === 'belt' || key === 'vbelt') {
    // a private copy of the texture, so each belt scrolls at its own speed
    const map = beltTexture(key === 'belt' ? 'conveyor' : 'vbelt').clone()
    map.needsUpdate = true
    m.map = map
    m.side = THREE.DoubleSide
  }
  return m
}

export function disposeMaterial(m: THREE.Material) {
  const map = (m as THREE.MeshStandardMaterial).map
  if (map) map.dispose()
  m.dispose()
}
