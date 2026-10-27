// Run with:  npm test      (uses Node's built-in test runner; no extra packages)
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { TAU, approach, cageRatio, explodeAt, gearRatio, itemOnBelt, meshOffsets, openBelt, sliderCrank, strobeFactor, surfaceTravel } from './kinematics.ts'

const near = (a: number, b: number, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`)

test('slider-crank: piston at top and bottom dead centre, rod length never changes', () => {
  const r = 0.09, l = 0.3
  near(sliderCrank(0, r, l).pin, r + l)
  near(sliderCrank(Math.PI, r, l).pin, l - r)
  near(sliderCrank(0, r, l).rodAngle, 0)
  near(sliderCrank(Math.PI / 2, r, l).rodAngle, Math.asin(r / l))
  for (let i = 0; i < 360; i += 7) {
    const k = sliderCrank((i * Math.PI) / 180, r, l)
    near(Math.hypot(k.crankX, k.pin - k.crankY), l)          // crank pin to piston pin
    assert.ok(k.pin >= l - r - 1e-12 && k.pin <= l + r + 1e-12)
  }
})

test('gear pair: opposite direction, tooth-count ratio, equal pitch-line speed', () => {
  const z1 = 14, z2 = 34, m = 0.025
  const ratio = gearRatio(z1, z2)
  assert.ok(ratio < 0)
  near(Math.abs(ratio), z1 / z2)
  const r1 = (m * z1) / 2, r2 = (m * z2) / 2, w1 = 10
  near(w1 * r1, Math.abs(w1 * ratio) * r2)
})

test('gear pair stays in mesh: a tooth of A meets a gap of B at every angle', () => {
  const zA = 14, zB = 34, dir = Math.PI / 2
  const off = meshOffsets(zA, zB, dir)
  const pa = TAU / zA, pb = TAU / zB
  const mod = (x: number, m: number) => ((x % m) + m) % m
  for (let step = 0; step < 50; step++) {
    const phi = step * 0.137
    const a = phi + off.a, b = phi * gearRatio(zA, zB) + off.b
    // angular distance of the nearest A tooth from the line of centres, as a fraction of A's pitch
    const ta = mod(dir - a, pa) / pa
    // B is seen from the other side: the line of centres is at dir + 180 degrees
    const tb = mod(dir + Math.PI - b, pb) / pb
    // a tooth of A at fraction ta must coincide with a gap of B (half a pitch out of phase, mirrored)
    near(mod(ta + tb, 1), 0.5, 1e-6)
  }
})

test('belt drive: belt length and surface speed agree on both pulleys', () => {
  const equal = openBelt({ x: -5, y: 0, r: 0.1 }, { x: 5, y: 0, r: 0.1 })
  near(equal.length, 20 + TAU * 0.1)
  const fly = { x: -0.45, y: 1.27, r: 0.36 }, pul = { x: 0.5, y: 1.25, r: 0.09 }
  const belt = openBelt(fly, pul)
  assert.ok(belt.length > 2 * belt.d && belt.length < 2 * belt.d + TAU * fly.r)
  const theta = 3.3                                           // flywheel angle
  near(surfaceTravel(theta, fly.r), surfaceTravel(theta * (fly.r / pul.r), pul.r))
})

test('exploded view: exact restore, no drift after repeated explode / assemble', () => {
  const base = [0.56, 0.62, 0], vec = [1.44, 0, 0]
  assert.deepEqual(explodeAt(base, vec, 0), base)
  let k = 0
  for (let i = 0; i < 500; i++) k = i % 2 ? 0 : 1
  assert.deepEqual(explodeAt(base, vec, k), base)
  near(explodeAt(base, vec, 0.5)[0], 0.56 + 0.72)
  assert.ok(explodeAt(base, vec, 1)[0] > explodeAt(base, vec, 0.5)[0])
})

test('motion smoothing is frame-rate independent', () => {
  const one = approach(0, 100, 0.2, 0.6)
  const two = approach(approach(0, 100, 0.1, 0.6), 100, 0.1, 0.6)
  near(one, two, 1e-9)
  assert.equal(approach(5, 9, 0.1, 0), 9)
})

test('display slow-down keeps the fastest shaft readable and is a round fraction', () => {
  assert.equal(strobeFactor(90), 1)
  assert.equal(strobeFactor(1450), 1 / 20)
  assert.equal(strobeFactor(2900), 1 / 50)
  assert.ok(2900 * strobeFactor(2900) <= 110)
})

test('conveyor items advance with the belt and recycle without a jump', () => {
  const span = 9.2, spacing = 1.6
  const a = itemOnBelt(1.0, 0, spacing, span), b = itemOnBelt(1.25, 0, spacing, span)
  near(b.s - a.s, 0.25)
  assert.equal(itemOnBelt(0, 0, spacing, span).scale, 0)       // invisible exactly where it reappears
  assert.ok(itemOnBelt(span - 0.001, 0, spacing, span).scale < 0.01)
  assert.equal(itemOnBelt(span / 2, 0, spacing, span).scale, 1)
})

test('bearing cage turns slower than the shaft', () => {
  const c = cageRatio(0.02, 0.12)
  assert.ok(c > 0.3 && c < 0.5)
})
