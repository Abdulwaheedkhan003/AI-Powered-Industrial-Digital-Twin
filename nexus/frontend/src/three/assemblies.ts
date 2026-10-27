// Machine assemblies, defined as data.
//
// Every machine is a list of genuinely separate components. Each component has
//   * a stable id, and the component-library entry that describes it (`lib`)
//   * its assembled position (`pos`), with the origin ON its own rotation axis
//   * a curated explosion vector (`explode`): where it goes in the exploded view
//   * one or more meshes, each with its own motion rule
//
// Motion is never animated freely. Every moving mesh takes its angle from ONE master
// shaft angle per machine through a fixed ratio, so gears stay in mesh, a belt runs at the
// surface speed of its pulleys, and a piston follows its crank.
//
// These are conceptual models built for illustration. They are not manufacturer geometry.
import { stagedK } from './kinematics'
import * as THREE from 'three'
import { G, V3, around, at, axialBlades, bearingParts, beltLoop, bolts, box, capsule, cyl, fins, gear, impellerVanes, lathe, merge, out, paint, pipe, spokedWheel, torus, tube } from './geom'
import { cageRatio, gearRatio, meshOffsets } from './kinematics'
import type { MatKey } from './materials'

export type Motion =
  | { kind: 'rotate'; axis: 'x' | 'y' | 'z'; ratio: number; phase?: number; wobble?: boolean }
  | { kind: 'piston'; r: number; l: number; ratio: number }
  | { kind: 'rod'; r: number; l: number; ratio: number }
  | { kind: 'belt'; radius: number; ratio: number; tile: number }
export type MeshDef = { geo: G; mat: MatKey; pivot?: V3; motion?: Motion }
export type PartDef = {
  id: string; lib: string; name?: string; pos: V3; explode: V3; order: number; meshes: MeshDef[]
  rank?: number          // 0 = leaves first in the staged explosion, 1 = last
  cut?: boolean          // sectioned in the cutaway view
  enclosure?: boolean    // becomes translucent in the X-ray view
  state: string          // how this component moves, in words
  box: THREE.Box3        // bounding box relative to `pos`
}
export type AssemblyDef = {
  type: string; parts: PartDef[]
  maxRatio: number                              // fastest element relative to the master shaft
  master: string                                // which shaft the speed reading refers to
  cutPlanes: { n: V3; c: number }[]             // the cutaway removes the region on the negative side of ALL planes
  views: { assembled: V3; exploded: V3 }        // default camera directions
  lift: number                                  // how far the assembly rises when exploded (keeps parts above the floor)
  items?: { part: string; count: number; spacing: number; start: number; span: number; y: number; radius: number; ratio: number }
  flow?: { from: V3; to: V3 }[]                 // flow-direction arrows (illustration only)
}

const DARK = 0x30363d
const M = (geo: G | (G | G[])[], mat: MatKey, extra: Partial<MeshDef> = {}): MeshDef => ({ geo: merge(Array.isArray(geo) ? geo : [geo]), mat, ...extra })
const sharp = (pts: [number, number][]) => pts.flatMap((p, i) => (i === 0 || i === pts.length - 1 ? [p] : [p, p])) as [number, number][]

const STATE: Record<Motion['kind'], string> = { rotate: 'Rotating', piston: 'Reciprocating', rod: 'Swinging and reciprocating', belt: 'Travelling' }
function part(id: string, lib: string, pos: V3, explode: V3, meshes: MeshDef[], opts: Partial<PartDef> = {}): PartDef {
  const b = new THREE.Box3()
  for (const m of meshes) {
    const mb = m.geo.boundingBox!.clone()
    if (m.pivot) mb.translate(new THREE.Vector3(...m.pivot))
    b.union(mb)
  }
  const moving = meshes.filter((m) => m.motion)
  const state = moving.length === 0 ? 'Stationary' : moving.length === meshes.length ? STATE[moving[0].motion!.kind] : `${STATE[moving[0].motion!.kind]} inside a stationary part`
  return { id, lib, pos, explode, meshes, order: 0, state, box: b, ...opts }
}
function bearing(id: string, lib: string, name: string, pos: V3, explode: V3, axis: 'x' | 'z', rOut: number, rIn: number, w: number, ratio: number): PartDef {
  const b = bearingParts(axis, rOut, rIn, w)
  const p = part(id, lib, pos, explode, [
    M(b.outer, 'steel'),
    M(b.inner, 'steel', { motion: { kind: 'rotate', axis, ratio } }),
    M(b.balls, 'steel', { motion: { kind: 'rotate', axis, ratio: ratio * cageRatio(b.ballDia, b.pitchDia) } }),
  ], { name })
  p.state = 'Outer race stationary; inner race turns with the shaft; balls and cage turn at about 40% of shaft speed'
  return p
}
const finish = (def: Omit<AssemblyDef, 'lift'>): AssemblyDef => {
  def.parts.forEach((p, i) => { p.order = i })
  const drop = Math.max(0, ...def.parts.map((p) => -(p.pos[1] + p.box.min.y + p.explode[1])))
  return { ...def, lift: drop + 0.05 }
}
const QUARTER = (h: number) => [{ n: [0, 0, -1] as V3, c: 0 }, { n: [0, -1, 0] as V3, c: h }]

// ---------------------------------------------------------------------------------- motor
function motor(type: string): AssemblyDef {
  const H = 0.62
  const spin: Motion = { kind: 'rotate', axis: 'x', ratio: 1, wobble: true }
  const shield = () => merge([
    lathe(sharp([[0.064, -0.04], [0.135, -0.04], [0.135, -0.015], [0.385, -0.015], [0.385, -0.04], [0.405, -0.04], [0.405, 0.02], [0.3, 0.045], [0.14, 0.045], [0.12, 0.085], [0.064, 0.085], [0.064, -0.04]])),
    ...around('x', 6, () => at(box(0.02, 0.16, 0.014), 0.05, 0.22, 0)),
  ])
  return finish({
    type, maxRatio: 1, master: 'the motor shaft', cutPlanes: QUARTER(H),
    views: { assembled: [0.6, 0.4, 0.69], exploded: [0.24, 0.4, 0.88] },
    parts: [
      part('base', 'base', [0, 0, 0], [0, -0.5, 0], [
        M([at(box(1.2, 0.05, 0.92), 0, 0.025, 0), at(box(1.0, 0.1, 0.13), 0, 0.1, 0.3), at(box(1.0, 0.1, 0.13), 0, 0.1, -0.3)], 'cast'),
        M([[0.42, 0.3], [0.42, -0.3], [-0.42, 0.3], [-0.42, -0.3]].map(([x, z]) => at(cyl(0.024, 0.03, 'y', 6), x, 0.16, z)), 'steel'),
      ]),
      part('housing', 'housing', [0, H, 0], [0, 0, 0], [
        M([tube(0.4, 0.356, 1.0), ...fins('x', 0.4, 0.92, 30, 0.05, 0.016, (d) => !(d > 150 && d < 210) && !(d < 14 || d > 346)),
          at(box(0.86, 0.22, 0.13), 0, -0.37, 0.3), at(box(0.86, 0.22, 0.13), 0, -0.37, -0.3), at(box(0.3, 0.04, 0.3), 0.05, 0.415, 0)], 'paint'),
        M([at(torus(0.05, 0.014, 'z', 20, 8), -0.32, 0.46, 0), paint(at(box(0.2, 0.004, 0.11), 0.1, 0.0, 0.0).rotateX(Math.PI / 3).translate(0, 0.228, 0.394), 0xd9dee4)], 'steel'),
      ], { cut: true, enclosure: true }),
      part('stator', 'stator', [0, H, 0], [1.15, 0, 0], [
        M(tube(0.352, 0.236, 0.6), 'dark'),
        M([at(torus(0.292, 0.055, 'x'), 0.335), at(torus(0.292, 0.055, 'x'), -0.335), ...around('x', 24, () => at(box(0.6, 0.016, 0.03), 0, 0.243, 0))], 'copper'),
      ], { cut: true }),
      part('rotor', 'rotor', [0, H, 0], [-1.55, 0, 0], [
        M([paint(cyl(0.222, 0.58, 'x', 40), 0xb4bbc3), ...around('x', 20, () => paint(at(box(0.57, 0.006, 0.016), 0, 0.2215, 0), DARK)),
          at(tube(0.21, 0.12, 0.035), 0.3075), at(tube(0.21, 0.12, 0.035), -0.3075),
          ...around('x', 12, () => at(box(0.03, 0.05, 0.01), 0.34, 0.165, 0)), ...around('x', 12, () => at(box(0.03, 0.05, 0.01), -0.34, 0.165, 0))], 'steel', { motion: spin }),
      ]),
      part('shaft', 'shaft', [0, H, 0], [-1.75, 0, 0], [
        M([lathe(sharp([[0, -0.8], [0.042, -0.8], [0.042, -0.6], [0.06, -0.6], [0.06, -0.5], [0.076, -0.5], [0.076, 0.5], [0.06, 0.5], [0.06, 0.62], [0.05, 0.62], [0.05, 1.0], [0, 1.0]]), 'x', 28),
          paint(at(box(0.24, 0.022, 0.028), 0.8, 0.055, 0), DARK)], 'steel', { motion: spin }),
      ]),
      bearing('bearing_de', 'bearing_de', 'Bearing (drive end)', [0.56, H, 0], [1.44, 0, 0], 'x', 0.118, 0.06, 0.07, 1),
      bearing('bearing_nde', 'bearing_nde', 'Bearing (non-drive end)', [-0.56, H, 0], [-2.39, 0, 0], 'x', 0.118, 0.06, 0.07, 1),
      part('end_shield_de', 'end_shield', [0.535, H, 0], [1.865, 0, 0], [M(shield(), 'paint'), M(bolts('x', 4, 0.37, 0.018, 0.03, 0.035, 0.6), 'steel')], { name: 'End shield (drive end)', cut: true }),
      part('end_shield_nde', 'end_shield', [-0.535, H, 0], [-2.815, 0, 0], [M(shield().rotateY(Math.PI), 'paint'), M(bolts('x', 4, 0.37, 0.018, 0.03, -0.035, 0.6), 'steel')], { name: 'End shield (non-drive end)', cut: true }),
      part('cooling_fan', 'cooling_fan', [-0.74, H, 0], [-3.11, 0, 0], [
        M([cyl(0.065, 0.09), at(cyl(0.21, 0.012, 'x', 32), 0.04), ...around('x', 9, () => at(box(0.075, 0.15, 0.012), -0.005, 0.135, 0))], 'white', { motion: spin }),
      ]),
      part('fan_cowl', 'fan_cowl', [-0.8, H, 0], [-3.65, 0, 0], [
        M([tube(0.43, 0.41, 0.36), ...[0.08, 0.16, 0.24, 0.32, 0.4].map((r) => at(torus(r, 0.008, 'x', 36, 6), -0.18)), ...around('x', 8, () => at(box(0.012, 0.4, 0.012), -0.18, 0.2, 0))], 'dark'),
      ], { cut: true, enclosure: true }),
      part('terminal_box', 'terminal_box', [0.05, H + 0.52, 0], [0, 0.5, 0], [
        M(box(0.3, 0.2, 0.3), 'paint'),
        M([at(box(0.33, 0.025, 0.33), 0, 0.11, 0), at(cyl(0.035, 0.08, 'z', 12), 0, -0.02, 0.19), at(cyl(0.045, 0.02, 'z', 6), 0, -0.02, 0.16)], 'cast'),
        M([[0.13, 0.13], [0.13, -0.13], [-0.13, 0.13], [-0.13, -0.13]].map(([x, z]) => at(cyl(0.012, 0.012, 'y', 6), x, 0.128, z)), 'steel'),
      ]),
      part('coupling', 'coupling', [0.93, H, 0], [2.07, 0, 0], [
        M([cyl(0.095, 0.14), at(cyl(0.165, 0.035, 'x', 32), 0.055)], 'cast', { motion: spin }),
        M(bolts('x', 6, 0.125, 0.016, 0.02, 0.08), 'steel', { motion: spin }),
        M(at(box(0.038, 0.02, 0.05), 0.055, 0.166, 0), 'yellow', { motion: spin }),
      ]),
    ],
  })
}

// ---------------------------------------------------------------------------------- cooling fan
function coolingFan(): AssemblyDef {
  const H = 1.05
  const spin: Motion = { kind: 'rotate', axis: 'x', ratio: 1, wobble: true }
  return finish({
    type: 'cooling_fan', maxRatio: 1, master: 'the fan shaft', cutPlanes: QUARTER(H),
    views: { assembled: [0.74, 0.3, 0.6], exploded: [0.3, 0.38, 0.87] },
    parts: [
      part('stand', 'stand', [0, 0, 0], [0, -0.6, 0], [
        M([at(box(1.3, 0.08, 0.12), 0, 0.04, 0.55), at(box(1.3, 0.08, 0.12), 0, 0.04, -0.55), at(box(0.12, 0.08, 1.22), 0.5, 0.04, 0), at(box(0.12, 0.08, 1.22), -0.5, 0.04, 0),
          at(box(0.42, 0.46, 0.07), 0, 0.29, 0.52), at(box(0.42, 0.46, 0.07), 0, 0.29, -0.52)], 'dark'),
      ], { name: 'Mounting stand' }),
      part('casing', 'casing', [0, H, 0], [0, 0, 0], [
        M([tube(0.78, 0.745, 0.5, 'x', 56), at(tube(0.85, 0.745, 0.03, 'x', 56), 0.25), at(tube(0.85, 0.745, 0.03, 'x', 56), -0.25)], 'paint'),
        M([...bolts('x', 12, 0.81, 0.016, 0.045, 0.25), ...bolts('x', 12, 0.81, 0.016, 0.045, -0.25)], 'steel'),
      ], { name: 'Outer housing', cut: true, enclosure: true }),
      part('motor', 'motor', [-0.3, H, 0], [-1.2, 0, 0], [
        M([cyl(0.19, 0.46), ...fins('x', 0.19, 0.4, 16, 0.03, 0.012), at(cyl(0.2, 0.03), -0.245)], 'paint2'),
        M(around('x', 3, () => at(box(0.05, 0.57, 0.03), 0, 0.47, 0)), 'dark'),
      ], { name: 'Drive motor and mounting arms' }),
      bearing('bearing_de', 'bearing_de', 'Bearing (drive end)', [-0.04, H, 0], [-0.81, 0, 0], 'x', 0.078, 0.035, 0.045, 1),
      part('shaft', 'shaft', [0.13, H, 0], [0.49, 0, 0], [M([cyl(0.035, 0.42, 'x', 20), paint(at(box(0.14, 0.018, 0.02), 0.1, 0.04, 0), DARK)], 'steel', { motion: spin })], { name: 'Motor shaft' }),
      part('hub', 'hub', [0.18, H, 0], [0.97, 0, 0], [M([cyl(0.17, 0.2, 'x', 32), at(cyl(0.17, 0.16, 'x', 32, 0.03), 0.18), paint(at(box(0.05, 0.012, 0.05), 0, 0.171, 0), 0xe2ab1f)], 'white', { motion: spin })]),
      part('blades', 'blades', [0.18, H, 0], [1.62, 0, 0], [M(axialBlades(6, 0.17, 0.715, 0.36, 32), 'steel', { motion: spin })]),
      part('grille', 'grille', [0.46, H, 0], [2.0, 0, 0], [
        M([...[0.15, 0.27, 0.39, 0.51, 0.63, 0.75].map((r) => torus(r, 0.009, 'x', 48, 6)), torus(0.8, 0.014, 'x', 56, 8), cyl(0.15, 0.012, 'x', 24), ...around('x', 8, () => at(box(0.012, 0.66, 0.012), 0, 0.47, 0))], 'dark'),
      ]),
    ],
  })
}

// ---------------------------------------------------------------------------------- pump
function pump(): AssemblyDef {
  const H = 0.62
  const spin: Motion = { kind: 'rotate', axis: 'x', ratio: 1 }
  return finish({
    type: 'pump', maxRatio: 1, master: 'the pump shaft', cutPlanes: QUARTER(H),
    views: { assembled: [0.56, 0.36, 0.75], exploded: [0.2, 0.4, 0.9] },
    flow: [{ from: [1.95, H, 0], to: [1.45, H, 0] }, { from: [0.95, H + 1.05, -0.34], to: [0.95, H + 1.55, -0.34] }],
    parts: [
      part('base', 'base', [0, 0, 0], [0, -0.5, 0], [
        M([at(box(2.9, 0.1, 0.8), -0.05, 0.05, 0), at(box(0.95, 0.14, 0.5), -0.85, 0.17, 0), at(box(0.32, 0.22, 0.3), 0.42, 0.21, 0), at(box(0.24, 0.12, 0.4), 0.95, 0.16, 0)], 'dark'),
      ], { name: 'Baseplate' }),
      part('motor', 'motor', [-0.85, H, 0], [-1.15, 0, 0], [
        M([cyl(0.34, 1.0, 'x', 36), ...fins('x', 0.34, 0.9, 26, 0.045, 0.014, (d) => !(d > 150 && d < 210) && !(d < 16 || d > 344)), at(cyl(0.345, 0.05, 'x', 36), 0.5),
          at(tube(0.36, 0.34, 0.3), -0.62), at(cyl(0.36, 0.02, 'x', 36), -0.77), at(box(0.8, 0.12, 0.5), 0, -0.33, 0), at(box(0.26, 0.16, 0.26), 0, 0.4, 0)], 'paint'),
        M([at(cyl(0.04, 0.2, 'x', 18), 0.6), paint(at(box(0.12, 0.018, 0.022), 0.6, 0.045, 0), DARK)], 'steel', { motion: spin }),
      ]),
      part('coupling', 'coupling', [0, H, 0], [-0.9, 0, 0], [
        M([at(cyl(0.09, 0.12), -0.085), at(cyl(0.15, 0.03, 'x', 32), -0.02), at(cyl(0.15, 0.03, 'x', 32), 0.02), at(cyl(0.09, 0.12), 0.085)], 'cast', { motion: spin }),
        M(bolts('x', 6, 0.115, 0.016, 0.09, 0), 'steel', { motion: spin }),
        M(at(box(0.07, 0.02, 0.05), 0, 0.151, 0), 'yellow', { motion: spin }),
      ]),
      part('bearing_housing', 'bearing_housing', [0.42, H, 0], [-0.72, 0, 0], [
        M([lathe(sharp([[0.05, -0.24], [0.13, -0.24], [0.13, -0.2], [0.19, -0.2], [0.19, 0.2], [0.13, 0.2], [0.13, 0.24], [0.05, 0.24], [0.05, -0.24]])), at(box(0.3, 0.26, 0.26), 0, -0.26, 0), at(cyl(0.03, 0.05, 'y', 12), 0, 0.21, 0)], 'cast'),
      ], { cut: true, enclosure: true }),
      part('shaft', 'shaft', [0.405, H, 0], [0.445, 0, 0], [M([cyl(0.045, 1.05, 'x', 20), paint(at(box(0.1, 0.02, 0.024), -0.44, 0.05, 0), DARK), paint(at(box(0.5, 0.004, 0.012), 0.1, 0.046, 0), DARK)], 'steel', { motion: spin })], { name: 'Pump shaft' }),
      part('seal', 'seal', [0.74, H, 0], [1.06, 0, 0], [M([tube(0.1, 0.047, 0.05), at(tube(0.085, 0.047, 0.03), -0.04)], 'steel'), M([-0.01, 0.0, 0.01].map((x) => at(torus(0.07, 0.006, 'x', 24, 6), x - 0.04)), 'dark')]),
      part('back_plate', 'back_plate', [0.8, H, 0], [1.35, 0, 0], [M(tube(0.5, 0.1, 0.04, 'x', 48), 'paint'), M(bolts('x', 8, 0.46, 0.016, 0.03, -0.03), 'steel')], { cut: true }),
      part('impeller', 'impeller', [0.93, H, 0], [1.72, 0, 0], [
        M([at(cyl(0.38, 0.016, 'x', 44), -0.062), at(tube(0.38, 0.17, 0.014, 'x', 44), 0.062), tube(0.085, 0.045, 0.14), ...impellerVanes(6, 0.11, 0.375, 0.11)], 'brass', { motion: { ...spin, wobble: true } }),
      ]),
      part('volute', 'volute', [0.95, H, 0], [2.4, 0, 0], [
        M([tube(0.52, 0.47, 0.3, 'x', 52), at(tube(0.52, 0.2, 0.035, 'x', 52), 0.165), at(tube(0.56, 0.5, 0.03, 'x', 52), -0.15), at(tube(0.14, 0.11, 0.6, 'y', 28), 0, 0.66, -0.34)], 'paint'),
      ], { cut: true, enclosure: true }),
      part('flange_in', 'flange_in', [1.25, H, 0], [2.8, 0, 0], [M([tube(0.2, 0.165, 0.28), at(tube(0.28, 0.165, 0.035), 0.14)], 'paint'), M(bolts('x', 8, 0.24, 0.016, 0.03, 0.165), 'steel')], { cut: true }),
      part('flange_out', 'flange_out', [0.95, H + 0.975, -0.34], [2.4, 0.45, 0], [M(tube(0.22, 0.11, 0.035, 'y', 28), 'paint'), M(bolts('y', 6, 0.175, 0.014, 0.03, 0.025), 'steel')]),
    ],
  })
}

// ---------------------------------------------------------------------------------- compressor
function compressor(): AssemblyDef {
  const cx = -0.45, cy = 1.27, r = 0.09, l = 0.3
  const rFly = 0.372, rPul = 0.102                       // belt surface radii on flywheel and motor pulley
  const fast = -(rFly / rPul)                            // motor pulley: same direction as the flywheel, faster
  const crank: Motion = { kind: 'rotate', axis: 'z', ratio: -1 }
  const belt = beltLoop({ x: cx, y: cy, r: rFly }, { x: 0.5, y: 1.25, r: rPul }, 0.032, 0.014, 0.12)
  return finish({
    type: 'compressor', maxRatio: rFly / rPul, master: 'the crankshaft', cutPlanes: [{ n: [0, 0, -1], c: 0 }],
    views: { assembled: [0.4, 0.3, 0.87], exploded: [0.8, 0.36, 0.48] },
    parts: [
      part('tank', 'tank', [0, 0.55, 0], [0, 0, 0], [
        M([capsule(0.42, 1.5), at(torus(0.425, 0.012, 'x', 40, 6), 0.5), at(torus(0.425, 0.012, 'x', 40, 6), -0.5)], 'paint2'),
        M([at(box(1.7, 0.04, 0.62), 0, 0.44, 0), at(cyl(0.05, 0.03, 'z', 20), 0.8, 0.18, 0.37), at(cyl(0.02, 0.06, 'y', 8), 0, -0.44, 0)], 'dark'),
      ]),
      part('feet', 'base', [0, 0, 0], [0, -0.45, 0], [M([at(box(0.14, 0.3, 0.7), 0.6, 0.15, 0), at(box(0.14, 0.3, 0.7), -0.6, 0.15, 0), at(box(0.2, 0.02, 0.8), 0.6, 0.01, 0), at(box(0.2, 0.02, 0.8), -0.6, 0.01, 0)], 'dark')], { name: 'Tank feet' }),
      part('crankcase', 'crankcase', [cx, cy, 0], [0, 0, 0], [M([box(0.46, 0.46, 0.4), at(box(0.56, 0.03, 0.5), 0, -0.245, 0)], 'cast')], { cut: true, enclosure: true }),
      part('crankshaft', 'crankshaft', [cx, cy, 0], [0, 0, 1.02], [
        M([at(cyl(0.035, 0.75, 'z', 18), 0, 0, 0.155), at(cyl(0.11, 0.025, 'z', 28), 0, -0.015, 0.06), at(cyl(0.11, 0.025, 'z', 28), 0, -0.015, -0.06), at(cyl(0.03, 0.12, 'z', 16), 0, r, 0)], 'steel', { motion: crank }),
      ]),
      part('connecting_rod', 'connecting_rod', [cx, cy, 0], [0, 0.42, 0], [
        M([tube(0.055, 0.032, 0.05, 'z', 24), at(box(0.045, l - 0.085, 0.028), 0, l / 2, 0), at(tube(0.04, 0.021, 0.05, 'z', 20), 0, l, 0)], 'steel', { motion: { kind: 'rod', r, l, ratio: 1 } }),
      ]),
      part('piston', 'piston', [cx, cy + r + l, 0], [0, 0.75, 0], [
        M([at(cyl(0.112, 0.16, 'y', 32), 0, 0.02, 0), paint(at(tube(0.1135, 0.1, 0.012, 'y', 32), 0, 0.075, 0), DARK), paint(at(tube(0.1135, 0.1, 0.012, 'y', 32), 0, 0.05, 0), DARK)], 'white', { motion: { kind: 'piston', r, l, ratio: 1 } }),
        M(cyl(0.02, 0.2, 'z', 12), 'steel', { motion: { kind: 'piston', r, l, ratio: 1 } }),
      ]),
      part('cylinder', 'cylinder', [cx, 1.73, 0], [0, 1.15, 0], [
        M([tube(0.15, 0.116, 0.44, 'y', 40), ...Array.from({ length: 8 }, (_, i) => at(tube(0.22, 0.145, 0.012, 'y', 40), 0, -0.19 + i * 0.052, 0))], 'cast'),
      ], { cut: true, enclosure: true }),
      part('cylinder_head', 'cylinder_head', [cx, 2.0, 0], [0, 1.5, 0], [
        M([box(0.36, 0.09, 0.36), ...Array.from({ length: 5 }, (_, i) => at(box(0.34, 0.05, 0.012), 0, 0.07, -0.12 + i * 0.06)), at(cyl(0.035, 0.1), 0.2, 0, -0.1)], 'cast'),
        M(pipe([[0.24, 0, -0.1], [0.5, 0, -0.2], [0.62, -0.25, -0.24], [0.62, -0.75, -0.24], [0.62, -0.98, -0.24]], 0.022), 'copper'),
      ]),
      part('intake_filter', 'intake_filter', [cx - 0.27, 2.0, 0], [-0.5, 1.5, 0], [M([cyl(0.085, 0.12, 'x', 20), at(cyl(0.03, 0.08), 0.09)], 'dark')]),
      bearing('bearing_de', 'bearing_de', 'Main bearing', [cx, cy, 0.205], [0, 0, 0.42], 'z', 0.075, 0.035, 0.04, -1),
      part('flywheel', 'flywheel', [cx, cy, 0.42], [0, 0, 1.58], [
        M(spokedWheel('z', 0.36, 0.055, 0.07, 0.075, 0.1, 5, 0.05, 0.03), 'paint', { motion: crank }),
        M(at(box(0.05, 0.03, 0.074), 0, 0.345, 0), 'yellow', { motion: crank }),
      ]),
      part('motor_pulley', 'motor_pulley', [0.5, 1.25, 0.42], [0, 0, 1.58], [M(spokedWheel('z', 0.09, 0.03, 0.06, 0.035, 0.07, 4, 0.03, 0.02), 'steel', { motion: { kind: 'rotate', axis: 'z', ratio: fast } })]),
      part('vbelt', 'vbelt', [0, 0, 0.42], [0, 0, 2.08], [M(belt.geo, 'vbelt', { motion: { kind: 'belt', radius: rFly, ratio: 1, tile: belt.tile } })]),
      part('motor', 'motor', [0.5, 1.25, 0], [0.9, 0, 0], [
        M([at(cyl(0.19, 0.5, 'z', 30), 0, 0, -0.03), ...fins('z', 0.19, 0.44, 18, 0.03, 0.012, (d) => !(d > 150 && d < 210), -0.03), at(box(0.34, 0.06, 0.4), 0, -0.22, -0.03)], 'paint'),
        M(at(cyl(0.03, 0.26, 'z', 14), 0, 0, 0.33), 'steel', { motion: { kind: 'rotate', axis: 'z', ratio: fast } }),
      ]),
    ],
  })
}

// ---------------------------------------------------------------------------------- gearbox
function gearbox(): AssemblyDef {
  const H = 0.62, m = 0.025, z1 = 14, z2 = 34, zi = -0.36, zo = 0.24
  const k = 1 / gearRatio(z1, z2)                        // input turns the other way, faster by z2/z1
  const off = meshOffsets(z1, z2, Math.PI / 2)           // the wheel lies towards +Z from the pinion
  const input: Motion = { kind: 'rotate', axis: 'x', ratio: k, phase: off.a }
  const output: Motion = { kind: 'rotate', axis: 'x', ratio: 1, phase: off.b }
  const wall = H - 0.12
  return finish({
    type: 'gearbox', maxRatio: z2 / z1, master: 'the output shaft', cutPlanes: [{ n: [-1, 0, 0], c: 0.07 }],
    views: { assembled: [0.82, 0.4, 0.42], exploded: [0.68, 0.48, 0.55] },
    parts: [
      part('housing_lower', 'housing_lower', [0, 0, 0], [0, -0.6, 0], [
        M([at(box(0.44, 0.04, 1.44), 0, 0.1, 0.06), at(box(0.04, wall, 1.44), 0.2, 0.12 + wall / 2, 0.06), at(box(0.04, wall, 1.44), -0.2, 0.12 + wall / 2, 0.06),
          at(box(0.44, wall, 0.04), 0, 0.12 + wall / 2, -0.64), at(box(0.44, wall, 0.04), 0, 0.12 + wall / 2, 0.76),
          at(box(0.62, 0.08, 0.16), 0, 0.04, -0.5), at(box(0.62, 0.08, 0.16), 0, 0.04, 0.62), at(box(0.06, 0.03, 1.52), 0.25, H - 0.015, 0.06), at(box(0.06, 0.03, 1.52), -0.25, H - 0.015, 0.06)], 'paint'),
      ], { cut: true, enclosure: true }),
      part('housing_cover', 'housing_cover', [0, 0, 0], [0, 1.05, 0], [
        M([at(box(0.44, 0.04, 1.44), 0, 1.11, 0.06), at(box(0.04, 0.47, 1.44), 0.2, H + 0.235, 0.06), at(box(0.04, 0.47, 1.44), -0.2, H + 0.235, 0.06),
          at(box(0.44, 0.47, 0.04), 0, H + 0.235, -0.64), at(box(0.44, 0.47, 0.04), 0, H + 0.235, 0.76), at(box(0.06, 0.03, 1.52), 0.25, H + 0.015, 0.06), at(box(0.06, 0.03, 1.52), -0.25, H + 0.015, 0.06)], 'paint'),
        M([at(box(0.2, 0.015, 0.3), 0, 1.137, 0.06), at(cyl(0.025, 0.06, 'y', 10), 0, 1.16, -0.4)], 'cast'),
      ], { cut: true, enclosure: true }),
      part('input_shaft', 'input_shaft', [-0.25, H, zi], [-1.15, 0, -0.3], [M([cyl(0.045, 1.1, 'x', 20), paint(at(box(0.18, 0.02, 0.024), -0.42, 0.05, 0), DARK)], 'steel', { motion: input })]),
      part('pinion', 'pinion', [0, H, zi], [0, 0, -0.3], [M(gear(z1, m, 0.2, 0.045), 'steel', { motion: input })]),
      part('output_shaft', 'output_shaft', [0.25, H, zo], [1.15, 0, 0.3], [M([cyl(0.06, 1.1, 'x', 22), paint(at(box(0.18, 0.022, 0.03), 0.42, 0.064, 0), DARK)], 'steel', { motion: output })]),
      part('wheel', 'wheel', [0, H, zo], [0, 0, 0.3], [M([gear(z2, m, 0.2, 0.06), tube(0.13, 0.06, 0.26)], 'brass', { motion: output })]),
      bearing('bearing_de', 'bearing_de', 'Output bearing (drive end)', [0.205, H, zo], [0.42, 0, 0.3], 'x', 0.12, 0.06, 0.05, 1),
      bearing('bearing_nde', 'bearing_nde', 'Output bearing (non-drive end)', [-0.205, H, zo], [-0.45, 0, 0.3], 'x', 0.12, 0.06, 0.05, 1),
      bearing('bearing_in_a', 'bearing_input', 'Input bearing (outer)', [-0.205, H, zi], [-0.42, 0, -0.3], 'x', 0.1, 0.045, 0.05, k),
      bearing('bearing_in_b', 'bearing_input', 'Input bearing (inner)', [0.205, H, zi], [0.45, 0, -0.3], 'x', 0.1, 0.045, 0.05, k),
    ],
  })
}

// ---------------------------------------------------------------------------------- conveyor
function conveyor(R = 0.1, reduction = 1): AssemblyDef {
  const H = 0.88, L = 5, t = 0.012, rd = R - t, k = 1 / reduction, ri = 0.04
  const drum: Motion = { kind: 'rotate', axis: 'z', ratio: -k }         // top surface moves towards +X
  const loop = beltLoop({ x: -L, y: H, r: R }, { x: L, y: H, r: R }, 1.0, t, 0.5)
  const drumBody = () => [cyl(rd, 1.04, 'z', 32), ...around('z', 10, () => paint(at(box(0.016, 0.004, 1.0), 0, rd, 0), DARK))]
  const roller = merge([cyl(ri, 1.0, 'z', 14), paint(at(box(0.012, 0.003, 0.96), 0, ri, 0), DARK), paint(at(box(0.012, 0.003, 0.96), 0, -ri, 0), DARK)])
  const legX = [-4.5, -2.25, 0, 2.25, 4.5]
  return finish({
    type: 'conveyor', maxRatio: (k * rd) / ri, master: 'the drive drum', cutPlanes: [],
    views: { assembled: [0.5, 0.4, 0.77], exploded: [0.45, 0.42, 0.79] },
    items: { part: 'belt', count: 6, spacing: 1.6, start: -4.6, span: 9.2, y: H + R, radius: R, ratio: k },
    parts: [
      part('legs', 'legs', [0, 0, 0], [0, -0.7, 0], [M(legX.flatMap((x) => [at(box(0.07, H - 0.1, 0.07), x, (H - 0.1) / 2, 0.6), at(box(0.07, H - 0.1, 0.07), x, (H - 0.1) / 2, -0.6),
        at(box(0.16, 0.02, 0.16), x, 0.01, 0.6), at(box(0.16, 0.02, 0.16), x, 0.01, -0.6), at(box(0.05, 0.05, 1.2), x, 0.3, 0)]), 'dark')]),
      part('frame', 'frame', [0, 0, 0], [0, 0, 0], [M([at(box(10.7, 0.16, 0.05), 0, H, 0.6), at(box(10.7, 0.16, 0.05), 0, H, -0.6),
        ...Array.from({ length: 9 }, (_, i) => at(box(0.05, 0.05, 1.2), -4.4 + i * 1.1, H - 0.17, 0))], 'paint')], { name: 'Side frames' }),
      part('drive_drum', 'drive_drum', [L, H, 0], [0.9, 0, 0], [
        M([...drumBody(), at(cyl(0.03, 1.72, 'z', 16), 0, 0, 0.11)], 'steel', { motion: drum }),
        M([at(cyl(0.075, 0.07, 'z', 22), 0, 0, 0.93), ...bolts('z', 6, 0.055, 0.012, 0.02, 0.975)], 'cast', { motion: drum }),
        M(at(box(0.03, 0.016, 0.05), 0, 0.078, 0.93), 'yellow', { motion: drum }),
      ]),
      part('tail_drum', 'tail_drum', [-L, H, 0], [-0.9, 0, 0], [M([...drumBody(), cyl(0.03, 1.36, 'z', 16)], 'steel', { motion: drum })]),
      part('carry_idlers', 'rollers', [0, 0, 0], [0, 0.35, 0], Array.from({ length: 15 }, (_, i) => ({ geo: roller, mat: 'steel' as MatKey, pivot: [-4.375 + i * 0.625, H + rd - ri, 0] as V3, motion: { kind: 'rotate', axis: 'z', ratio: (-k * rd) / ri } as Motion }))),
      part('return_idlers', 'return_idlers', [0, 0, 0], [0, -0.35, 0], [-4, -2, 0, 2, 4].map((x) => ({ geo: roller, mat: 'steel' as MatKey, pivot: [x, H - R - ri, 0] as V3, motion: { kind: 'rotate', axis: 'z', ratio: (k * R) / ri } as Motion }))),
      part('belt', 'belt', [0, 0, 0], [0, 0.8, 0], [M(loop.geo, 'belt', { motion: { kind: 'belt', radius: R, ratio: k, tile: loop.tile } })]),
      part('side_guides', 'side_guides', [0, 0, 0], [0, 1.35, 0], [M([at(box(10.0, 0.09, 0.02), 0, H + R + 0.06, 0.54), at(box(10.0, 0.09, 0.02), 0, H + R + 0.06, -0.54),
        ...[-4, -2, 0, 2, 4].flatMap((x) => [at(box(0.03, 0.2, 0.03), x, H + R - 0.03, 0.57), at(box(0.03, 0.2, 0.03), x, H + R - 0.03, -0.57)])], 'steel')]),
    ],
  })
}

const EXPLODE_GAIN = 1.1
/** Widen the separation and give every component its place in the staged sequence (outermost first). */
function stage(a: AssemblyDef) {
  const byReach = a.parts.map((p, i) => [Math.hypot(...p.explode), i]).sort((x, y) => y[0] - x[0])
  byReach.forEach(([, i], n) => { a.parts[i].rank = byReach.length > 1 ? n / (byReach.length - 1) : 0 })
  for (const p of a.parts) p.explode = p.explode.map((x) => x * EXPLODE_GAIN) as V3
  a.lift *= EXPLODE_GAIN
}

const cache: Record<string, AssemblyDef> = {}
/** Assemblies are built once per machine type and shared by every view. */
export function getAssembly(type: string, drive?: { drum_radius_m?: number; reduction?: number }): AssemblyDef {
  const key = type === 'conveyor' ? `conveyor:${drive?.drum_radius_m ?? 0.1}:${drive?.reduction ?? 1}` : type
  if (!cache[key]) {
    cache[key] = type === 'cooling_fan' ? coolingFan() : type === 'pump' ? pump() : type === 'compressor' ? compressor() : type === 'gearbox' ? gearbox()
      : type === 'conveyor' ? conveyor(drive?.drum_radius_m ?? 0.1, drive?.reduction ?? 1) : motor(type === 'dc_motor' ? 'dc_motor' : 'motor')
    stage(cache[key])
  }
  return cache[key]
}

/** Bounding box of an assembly (or one component) at explosion factor k, in the assembly's own frame. */
export function boundsAt(asm: AssemblyDef, k: number, lift = false, only?: string): THREE.Box3 {
  const b = new THREE.Box3()
  const dy = lift ? asm.lift * k : 0
  for (const p of asm.parts) {
    if (only && p.id !== only) continue
    const s = stagedK(k, p.rank ?? 0)
    b.union(p.box.clone().translate(new THREE.Vector3(p.pos[0] + p.explode[0] * s, p.pos[1] + p.explode[1] * s + dy, p.pos[2] + p.explode[2] * s)))
  }
  return b
}
export function partCentre(asm: AssemblyDef, p: PartDef, k: number, lift = false, target = new THREE.Vector3()): THREE.Vector3 {
  p.box.getCenter(target)
  const s = stagedK(k, p.rank ?? 0)
  return target.set(target.x + p.pos[0] + p.explode[0] * s, target.y + p.pos[1] + p.explode[1] * s + (lift ? asm.lift * k : 0), target.z + p.pos[2] + p.explode[2] * s)
}

export const MODEL_NOTE = 'Conceptual 3D model built for illustration. Not manufacturer geometry.'
export { out }
