// Pure mechanical relationships. No rendering code here, so every rule can be unit-tested.
// Angles are in radians. A machine's motion is derived from ONE master shaft angle, so
// meshing and belted parts can never drift apart.

export const TAU = Math.PI * 2
export const rpmToRad = (rpm: number) => (rpm * TAU) / 60

/** First-order approach to a target. Frame-rate independent: two half steps equal one full step. */
export function approach(current: number, target: number, dt: number, tau: number) {
  if (tau <= 0) return target
  return target + (current - target) * Math.exp(-dt / tau)
}

/**
 * Display slow-down for fast shafts. At 60 frames per second a shaft above a few
 * revolutions per second turns into a blur or appears to run backwards, so fast machines
 * are drawn at a fixed fraction of their true speed (1/2, 1/5, 1/10 ...). The fraction is
 * the same for every part of one machine, so ratios between its parts stay exact, and the
 * interface always states the fraction next to the true speed.
 */
const STROBE_STEPS = [1, 1 / 2, 1 / 5, 1 / 10, 1 / 20, 1 / 50, 1 / 100, 1 / 200, 1 / 500]
export function strobeFactor(maxRpm: number, limitRpm = 110) {
  for (const f of STROBE_STEPS) if (Math.abs(maxRpm) * f <= limitRpm) return f
  return STROBE_STEPS[STROBE_STEPS.length - 1]
}
export const strobeLabel = (f: number) => (f >= 1 ? 'true speed' : `1/${Math.round(1 / f)} speed`)

/** External gear pair: the driven gear turns the opposite way, slower by the tooth ratio. */
export const gearRatio = (drivingTeeth: number, drivenTeeth: number) => -drivingTeeth / drivenTeeth

/**
 * Rotation offsets that make two external gears mesh (tooth into gap) when gear B lies in
 * the direction `dir` from gear A. Tooth 0 of each gear points along the reference direction
 * at zero rotation; `dir` is measured from that reference in the gears' plane.
 */
export function meshOffsets(teethA: number, teethB: number, dir: number) {
  const pa = TAU / teethA, pb = TAU / teethB
  const mod = (x: number, m: number) => ((x % m) + m) % m
  return { a: mod(dir, pa), b: mod(dir + Math.PI + pb / 2, pb) }
}

/**
 * Slider-crank (piston engine / reciprocating compressor).
 * theta = crank angle from top dead centre, r = crank radius, l = connecting-rod length.
 * Returns the piston-pin distance from the crank centre along the cylinder axis and the
 * rod's swing angle. The pin and the crank pin are always exactly l apart.
 */
export function sliderCrank(theta: number, r: number, l: number) {
  const s = r * Math.sin(theta)
  return { pin: r * Math.cos(theta) + Math.sqrt(l * l - s * s), rodAngle: Math.asin(s / l), crankX: s, crankY: r * Math.cos(theta) }
}

/** Belt or conveyor: distance travelled by the surface of a drum of radius r turned through theta. */
export const surfaceTravel = (theta: number, r: number) => theta * r

/** Open belt drive around two pulleys: tangent geometry and total belt length. */
export function openBelt(a: { x: number; y: number; r: number }, b: { x: number; y: number; r: number }) {
  const dx = b.x - a.x, dy = b.y - a.y
  const d = Math.hypot(dx, dy)
  const phi = Math.atan2(dy, dx)
  const gamma = Math.acos((a.r - b.r) / d)        // angle between the centre line and the tangent normals
  const straight = Math.sqrt(d * d - (a.r - b.r) ** 2)
  return { d, phi, gamma, straight, length: 2 * straight + b.r * 2 * gamma + a.r * (TAU - 2 * gamma) }
}

/** Position of a part in the exploded view. Always computed from the original position, never accumulated. */
export function explodeAt(base: readonly number[], vector: readonly number[], k: number): [number, number, number] {
  return [base[0] + vector[0] * k, base[1] + vector[1] * k, base[2] + vector[2] * k]
}

/**
 * Staged explosion. For global progress K (0..1) returns the progress of one component.
 * Components with rank 0 (the outermost) leave first, rank 1 last; each one overshoots and
 * settles. Exactly 0 at K = 0 and exactly 1 at K = 1, so assembling restores the original.
 */
export function stagedK(K: number, rank: number, stagger = 0.6) {
  const u = Math.min(1, Math.max(0, K * (1 + stagger) - rank * stagger))
  if (u <= 0 || u >= 1) return u
  const c1 = 2.2, x = u - 1
  return 1 + (c1 + 1) * x * x * x + c1 * x * x
}

/** Position of item i on a conveyor's carrying run, recycled smoothly from the end back to the start. */
export function itemOnBelt(travel: number, i: number, spacing: number, span: number, fade = 0.4) {
  const s = (((travel + i * spacing) % span) + span) % span
  const scale = Math.min(1, s / fade, (span - s) / fade)
  return { s, scale: Math.max(0, scale) }
}

/** Ball-bearing cage speed as a fraction of shaft speed (inner race turning, outer race fixed). */
export const cageRatio = (ballDia: number, pitchDia: number) => 0.5 * (1 - ballDia / pitchDia)
