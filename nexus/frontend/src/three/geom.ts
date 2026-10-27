// Procedural geometry toolkit.
// Every component of every machine is built from these functions as ONE merged mesh per
// material, so a whole machine costs a few dozen draw calls while each component stays a
// separate, selectable object with its own origin on its own rotation axis.
import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import { TAU, openBelt } from './kinematics'

export type G = THREE.BufferGeometry
export type V3 = [number, number, number]
export type Axis = 'x' | 'y' | 'z'
const KEEP = ['position', 'normal', 'uv', 'color']

/** Vertex colour (multiplied with the material colour). Used for markings that make rotation visible. */
export function paint(g: G, color: THREE.ColorRepresentation): G {
  const c = new THREE.Color(color)
  const n = g.attributes.position.count
  const a = new Float32Array(n * 3)
  for (let i = 0; i < n; i++) { a[i * 3] = c.r; a[i * 3 + 1] = c.g; a[i * 3 + 2] = c.b }
  g.setAttribute('color', new THREE.BufferAttribute(a, 3))
  return g
}

function prep(g: G): G {
  const out = g.index ? g.toNonIndexed() : g
  for (const name of Object.keys(out.attributes)) if (!KEEP.includes(name)) out.deleteAttribute(name)
  if (!out.attributes.normal) out.computeVertexNormals()
  if (!out.attributes.uv) out.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(out.attributes.position.count * 2), 2))
  if (!out.attributes.color) paint(out, 0xffffff)
  return out
}

export function merge(list: (G | G[])[]): G {
  const flat = (list.flat() as G[]).map(prep)
  const m = flat.length === 1 ? flat[0] : mergeGeometries(flat, false)
  if (!m) throw new Error('geometry merge failed')
  m.computeBoundingBox()
  m.computeBoundingSphere()
  return m
}

// three.js cylinders and lathes are built around +Y; `orient` lays them along another axis
const orient = (g: G, axis: Axis) => (axis === 'x' ? g.rotateZ(-Math.PI / 2) : axis === 'z' ? g.rotateX(Math.PI / 2) : g)

export const box = (w: number, h: number, d: number) => new THREE.BoxGeometry(w, h, d) as G
export const at = (g: G, x = 0, y = 0, z = 0) => g.translate(x, y, z)
/** Solid cylinder along `axis`. `rEnd` gives a cone/taper towards the positive end. */
export const cyl = (r: number, len: number, axis: Axis = 'x', seg = 28, rEnd = r) => orient(new THREE.CylinderGeometry(rEnd, r, len, seg), axis)
/** Body of revolution from a profile of [radius, position along axis] points. Repeat a point for a sharp edge. */
export const lathe = (pts: [number, number][], axis: Axis = 'x', seg = 40) => orient(new THREE.LatheGeometry(pts.map(([r, h]) => new THREE.Vector2(r, h)), seg), axis)
/** Hollow cylinder (a real shell with wall thickness, so cutaway views show the inside). */
export function tube(rOut: number, rIn: number, len: number, axis: Axis = 'x', seg = 36) {
  const h = len / 2
  return lathe([[rIn, -h], [rOut, -h], [rOut, -h], [rOut, h], [rOut, h], [rIn, h], [rIn, h], [rIn, -h]], axis, seg)
}
export function torus(R: number, r: number, axis: Axis = 'x', seg = 40, tubeSeg = 12) {
  const g = new THREE.TorusGeometry(R, r, tubeSeg, seg)
  return axis === 'x' ? g.rotateY(Math.PI / 2) : axis === 'y' ? g.rotateX(Math.PI / 2) : g
}
export const sphere = (r: number) => new THREE.SphereGeometry(r, 12, 9) as G
export const capsule = (r: number, len: number, axis: Axis = 'x') => orient(new THREE.CapsuleGeometry(r, len, 10, 28), axis)

const spin = (g: G, axis: Axis, a: number) => (axis === 'x' ? g.rotateX(a) : axis === 'y' ? g.rotateY(a) : g.rotateZ(a))
/** n copies around an axis. `make` builds the copy at the 12 o'clock position (+Y; +X for a Y axis). */
export function around(axis: Axis, n: number, make: (i: number) => G, phase = 0): G[] {
  return Array.from({ length: n }, (_, i) => spin(make(i), axis, phase + (i / n) * TAU))
}
/** Offset "outwards" from an axis at the 12 o'clock position, plus a shift along the axis. */
export const out = (g: G, axis: Axis, radial: number, along = 0) =>
  axis === 'x' ? g.translate(along, radial, 0) : axis === 'z' ? g.translate(0, radial, along) : g.translate(radial, along, 0)

export const bolts = (axis: Axis, n: number, R: number, r: number, len: number, along = 0, phase = 0) =>
  around(axis, n, () => out(cyl(r, len, axis, 6), axis, R, along), phase)

/** Cooling fins standing on a cylinder of radius r. `keep` can leave gaps (angle in degrees from 12 o'clock). */
export function fins(axis: Axis, r: number, len: number, n: number, h = 0.05, t = 0.016, keep?: (deg: number) => boolean, along = 0) {
  const all = around(axis, n, () => out(axis === 'x' ? box(len, h, t) : axis === 'z' ? box(t, h, len) : box(h, len, t), axis, r + h / 2 - 0.004, along))
  return keep ? all.filter((_, i) => keep((i / n) * 360)) : all
}

/** Spur gear along X. Tooth 0 points to +Y at zero rotation; rotating about +X moves it towards +Z. */
export function gear(teeth: number, module_: number, width: number, bore: number): G {
  const rp = (module_ * teeth) / 2, ra = rp + module_, rf = rp - 1.25 * module_, p = TAU / teeth
  const s = new THREE.Shape()
  for (let i = 0; i < teeth; i++) {
    const c = Math.PI / 2 + i * p
    const pts: [number, number][] = [[rf, c - 0.27 * p], [ra, c - 0.12 * p], [ra, c + 0.12 * p], [rf, c + 0.27 * p]]
    pts.forEach(([r, a], k) => { const x = r * Math.cos(a), y = r * Math.sin(a); if (i === 0 && k === 0) s.moveTo(x, y); else s.lineTo(x, y) })
  }
  s.closePath()
  const hole = new THREE.Path()
  hole.absarc(0, 0, bore, 0, TAU, true)
  s.holes.push(hole)
  const g = new THREE.ExtrudeGeometry(s, { depth: width, bevelEnabled: false, curveSegments: 20 })
  return g.translate(0, 0, -width / 2).rotateY(Math.PI / 2)
}

/** Pitched blades of an axial fan (axis X). Each blade is a swept plate set at `pitchDeg` to the plane of rotation. */
export function axialBlades(n: number, rHub: number, rTip: number, chord: number, pitchDeg: number, t = 0.014): G[] {
  const L = rTip - rHub
  const s = new THREE.Shape()
  s.moveTo(-chord * 0.36, 0)
  s.lineTo(chord * 0.36, 0)
  s.quadraticCurveTo(chord * 0.66, L * 0.55, chord * 0.5, L)
  s.quadraticCurveTo(0, L * 1.05, -chord * 0.44, L * 0.95)
  s.quadraticCurveTo(-chord * 0.52, L * 0.5, -chord * 0.36, 0)
  const pitch = THREE.MathUtils.degToRad(pitchDeg)
  return around('x', n, () => {
    const g = new THREE.ExtrudeGeometry(s, { depth: t, bevelEnabled: false, curveSegments: 10 })
    return g.translate(0, 0, -t / 2).rotateY(-(Math.PI / 2 - pitch)).translate(0, rHub - 0.012, 0)
  })
}

/** Backward-curved vanes of a centrifugal impeller (axis X), `width` wide along the axis. */
export function impellerVanes(n: number, rIn: number, rOut: number, width: number, sweep = 0.9, t = 0.014): G[] {
  const pts: THREE.Vector2[] = [], back: THREE.Vector2[] = []
  for (let k = 0; k <= 10; k++) {
    const u = k / 10, r = rIn + (rOut - rIn) * u, a = Math.PI / 2 - sweep * u
    const nx = Math.cos(a + Math.PI / 2), ny = Math.sin(a + Math.PI / 2)
    pts.push(new THREE.Vector2(r * Math.cos(a) + (nx * t) / 2, r * Math.sin(a) + (ny * t) / 2))
    back.unshift(new THREE.Vector2(r * Math.cos(a) - (nx * t) / 2, r * Math.sin(a) - (ny * t) / 2))
  }
  const shape = new THREE.Shape([...pts, ...back])
  return around('x', n, () => new THREE.ExtrudeGeometry(shape, { depth: width, bevelEnabled: false }).translate(0, 0, -width / 2).rotateY(Math.PI / 2))
}

/** Rim, hub and spokes. The spokes are what make a flywheel's or pulley's rotation visible. */
export function spokedWheel(axis: Axis, rRim: number, rimT: number, rimW: number, rHub: number, hubW: number, nSpokes: number, spokeW: number, spokeT: number): G[] {
  const rIn = rRim - rimT, L = rIn - rHub + 0.01
  const spoke = () => out(axis === 'x' ? box(spokeT, L, spokeW) : axis === 'z' ? box(spokeW, L, spokeT) : box(L, spokeT, spokeW), axis, (rHub + rIn) / 2)
  return [tube(rRim, rIn, rimW, axis, 48), tube(rHub, rHub * 0.45, hubW, axis, 24), ...around(axis, nSpokes, spoke)]
}

/** Deep-groove ball bearing as three separately moving pieces: fixed outer race, inner race, and the ball set. */
export function bearingParts(axis: Axis, rOut: number, rIn: number, w: number) {
  const t = (rOut - rIn) * 0.27, rPitch = (rOut + rIn) / 2, rBall = (rOut - rIn) * 0.23
  return {
    outer: tube(rOut, rOut - t, w, axis),
    inner: merge([tube(rIn + t, rIn, w, axis), paint(out(box(axis === 'x' ? w * 1.02 : 0.012, t * 0.5, axis === 'x' ? 0.012 : w * 1.02), axis, rIn + t * 0.8), 0x30363d)]),
    balls: merge([...around(axis, 9, () => out(sphere(rBall), axis, rPitch)), paint(tube(rPitch + rBall * 0.35, rPitch - rBall * 0.35, w * 0.3, axis, 30), 0xc9a24a)]),
    ballDia: rBall * 2, pitchDia: rPitch * 2,
  }
}

/**
 * Belt running around two pulleys in the XY plane (pulley axes along Z), as a closed loop with
 * real thickness. UV.u runs along the belt and repeats a whole number of times, so scrolling
 * the texture moves the belt continuously with no seam or jump.
 */
export function beltLoop(a: { x: number; y: number; r: number }, b: { x: number; y: number; r: number }, width: number, thickness: number, tile: number) {
  const { phi, gamma, straight, length } = openBelt(a, b)
  type P = { x: number; y: number; nx: number; ny: number; s: number }
  const path: P[] = []
  let s = 0
  const push = (x: number, y: number, nx: number, ny: number, ds: number) => { s += ds; path.push({ x, y, nx, ny, s }) }
  const nu = phi + gamma, nl = phi - gamma
  // carrying run A -> B
  push(a.x + a.r * Math.cos(nu), a.y + a.r * Math.sin(nu), Math.cos(nu), Math.sin(nu), 0)
  const runSeg = Math.max(2, Math.ceil(straight / 0.5))
  for (let i = 1; i <= runSeg; i++) { const u = i / runSeg; push(a.x + a.r * Math.cos(nu) + (b.x + b.r * Math.cos(nu) - a.x - a.r * Math.cos(nu)) * u, a.y + a.r * Math.sin(nu) + (b.y + b.r * Math.sin(nu) - a.y - a.r * Math.sin(nu)) * u, Math.cos(nu), Math.sin(nu), straight / runSeg) }
  // wrap around B
  const arcB = 2 * gamma, nB = Math.max(8, Math.ceil(arcB / 0.2))
  for (let i = 1; i <= nB; i++) { const ang = nu - (arcB * i) / nB; push(b.x + b.r * Math.cos(ang), b.y + b.r * Math.sin(ang), Math.cos(ang), Math.sin(ang), (b.r * arcB) / nB) }
  // return run B -> A
  for (let i = 1; i <= runSeg; i++) { const u = i / runSeg; push(b.x + b.r * Math.cos(nl) + (a.x + a.r * Math.cos(nl) - b.x - b.r * Math.cos(nl)) * u, b.y + b.r * Math.sin(nl) + (a.y + a.r * Math.sin(nl) - b.y - b.r * Math.sin(nl)) * u, Math.cos(nl), Math.sin(nl), straight / runSeg) }
  // wrap around A
  const arcA = TAU - 2 * gamma, nA = Math.max(8, Math.ceil(arcA / 0.2))
  for (let i = 1; i <= nA; i++) { const ang = nl - (arcA * i) / nA; push(a.x + a.r * Math.cos(ang), a.y + a.r * Math.sin(ang), Math.cos(ang), Math.sin(ang), (a.r * arcA) / nA) }

  const repeat = Math.max(1, Math.round(length / tile))
  const pos: number[] = [], nor: number[] = [], uv: number[] = []
  const w = width / 2
  const quad = (p: number[][], n: number[][], t: number[][]) => { for (const i of [0, 1, 2, 0, 2, 3]) { pos.push(...p[i]); nor.push(...n[i]); uv.push(...t[i]) } }
  for (let j = 0; j < path.length - 1; j++) {
    const p = path[j], q = path[j + 1]
    const u0 = (p.s / length) * repeat, u1 = (q.s / length) * repeat
    const o = (k: P, z: number) => [k.x, k.y, z], inn = (k: P, z: number) => [k.x - k.nx * thickness, k.y - k.ny * thickness, z]
    // outer face (seen from outside the loop), inner face, and the two edges
    quad([o(p, -w), o(p, w), o(q, w), o(q, -w)], [[p.nx, p.ny, 0], [p.nx, p.ny, 0], [q.nx, q.ny, 0], [q.nx, q.ny, 0]], [[u0, 0], [u0, 1], [u1, 1], [u1, 0]])
    quad([inn(p, w), inn(p, -w), inn(q, -w), inn(q, w)], [[-p.nx, -p.ny, 0], [-p.nx, -p.ny, 0], [-q.nx, -q.ny, 0], [-q.nx, -q.ny, 0]], [[u0, 1], [u0, 0], [u1, 0], [u1, 1]])
    quad([inn(p, w), inn(q, w), o(q, w), o(p, w)], [[0, 0, 1], [0, 0, 1], [0, 0, 1], [0, 0, 1]], [[u0, 0.02], [u1, 0.02], [u1, 0.02], [u0, 0.02]])
    quad([inn(q, -w), inn(p, -w), o(p, -w), o(q, -w)], [[0, 0, -1], [0, 0, -1], [0, 0, -1], [0, 0, -1]], [[u1, 0.02], [u0, 0.02], [u0, 0.02], [u1, 0.02]])
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
  g.setAttribute('normal', new THREE.Float32BufferAttribute(nor, 3))
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2))
  paint(g, 0xffffff)
  g.computeBoundingBox()
  g.computeBoundingSphere()
  return { geo: g as G, length, repeat, tile: length / repeat }
}

/** Bent pipe through a list of points. */
export function pipe(points: V3[], r: number): G {
  const curve = new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p)), false, 'catmullrom', 0.05)
  return new THREE.TubeGeometry(curve, points.length * 10, r, 10)
}

// ---- textures --------------------------------------------------------------
const textures: Record<string, THREE.Texture> = {}
/** Belt surface with regular transverse marks, so movement is visible. */
export function beltTexture(kind: 'conveyor' | 'vbelt'): THREE.Texture {
  if (!textures[kind]) {
    const c = document.createElement('canvas')
    c.width = 256; c.height = 64
    const g = c.getContext('2d')!
    g.fillStyle = kind === 'conveyor' ? '#23272d' : '#16181b'
    g.fillRect(0, 0, 256, 64)
    if (kind === 'conveyor') {
      g.fillStyle = '#2f353d'; g.fillRect(0, 0, 10, 64)                       // cleat line
      g.strokeStyle = '#3b434d'; g.lineWidth = 5; g.beginPath(); g.moveTo(96, 12); g.lineTo(150, 32); g.lineTo(96, 52); g.stroke()   // direction chevron
      g.fillStyle = '#1b1e23'; for (let x = 20; x < 256; x += 24) g.fillRect(x, 0, 1, 64)
    } else {
      g.fillStyle = '#30343a'; for (let x = 0; x < 256; x += 32) g.fillRect(x, 0, 12, 64)
    }
    const t = new THREE.CanvasTexture(c)
    t.wrapS = THREE.RepeatWrapping
    t.colorSpace = THREE.SRGBColorSpace
    t.anisotropy = 4
    textures[kind] = t
  }
  return textures[kind]
}
