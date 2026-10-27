// Renders one machine assembly: separate components, mechanical motion, exploded view,
// selection, cutaway and X-ray.
//
// Exploded view: every frame each component is placed at  original + vector x k,  computed
// from its stored original position. Nothing is accumulated, so explode / assemble can be
// repeated or interrupted any number of times and k = 0 is exactly the original assembly.
import { ThreeEvent, useFrame } from '@react-three/fiber'
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import { AssetDef } from '../lib/store'
import { AssemblyDef, MeshDef, PartDef, getAssembly } from './assemblies'
import { box, cyl, merge, paint, at } from './geom'
import { itemOnBelt, sliderCrank, stagedK } from './kinematics'
import { MatKey, disposeMaterial, makeMaterial } from './materials'
import { advance, control, trainFor } from './motion'

export type AssemblyApi = {
  asm: AssemblyDef
  k: () => number                                   // current explosion factor
  theta: () => number
  parts: () => { id: string; lib: string; position: number[]; visible: boolean; angles: number[]; offsets: number[][]; emissive: number }[]
}
type Props = {
  def: AssetDef
  explode?: number                 // target explosion factor, 0..1
  lift?: boolean                   // raise the assembly as it explodes so nothing sinks into the floor
  interactive?: boolean
  selected?: string | null         // component id
  suspects?: string[]              // component-library keys suspected by the diagnosis
  isolate?: boolean
  xray?: boolean
  planes?: THREE.Plane[] | null    // world-space cutaway planes, or null for no cutaway
  runWhileExploded?: boolean
  showFlow?: boolean
  glow?: boolean                   // draw a red glow around suspected components
  onSelect?: (id: string | null) => void
  onHover?: (id: string | null) => void
  api?: (api: AssemblyApi | null) => void
}

const SELECT = new THREE.Color('#2f81f7'), HOVER = new THREE.Color('#8fb0d9'), SUSPECT = new THREE.Color('#d60f1f'), NONE = new THREE.Color('#000000')
const smooth = (x: number, a: number, b: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t) }
const crateGeo = (() => { let g: THREE.BufferGeometry | null = null; return () => (g ||= merge([box(0.56, 0.4, 0.46), paint(at(box(0.565, 0.405, 0.07), 0, 0, 0), 0xd8c9a6)])) })()
const arrowGeo = (() => { let g: THREE.BufferGeometry | null = null; return () => (g ||= merge([cyl(0.07, 0.2, 'x', 16, 0.001)])) })()

function applyMotion(mesh: THREE.Mesh, md: MeshDef, theta: number, wobble: number) {
  const m = md.motion!, p = md.pivot
  switch (m.kind) {
    case 'rotate': {
      const a = theta * m.ratio + (m.phase || 0)
      mesh.rotation[m.axis] = a
      if (m.wobble) {
        // run-out: the rotating element turns about a slightly offset centre
        const e = wobble * 0.016, c = Math.cos(a) * e, s = Math.sin(a) * e
        if (m.axis === 'x') mesh.position.set(p ? p[0] : 0, (p ? p[1] : 0) + c, (p ? p[2] : 0) + s)
        else if (m.axis === 'z') mesh.position.set((p ? p[0] : 0) + c, (p ? p[1] : 0) + s, p ? p[2] : 0)
        else mesh.position.set((p ? p[0] : 0) + c, p ? p[1] : 0, (p ? p[2] : 0) + s)
      }
      break
    }
    case 'piston': {
      const k = sliderCrank(theta * m.ratio, m.r, m.l)
      mesh.position.y = (p ? p[1] : 0) + k.pin - (m.r + m.l)
      break
    }
    case 'rod': {
      const k = sliderCrank(theta * m.ratio, m.r, m.l)
      mesh.position.set((p ? p[0] : 0) + k.crankX, (p ? p[1] : 0) + k.crankY, p ? p[2] : 0)
      mesh.rotation.z = k.rodAngle
      break
    }
    case 'belt': {
      const map = (mesh.material as THREE.MeshStandardMaterial).map
      const u = (theta * m.ratio * m.radius) / m.tile
      if (map) map.offset.x = -(u - Math.floor(u))
      break
    }
  }
}

export default function Assembly({ def, explode = 0, lift = false, interactive = false, selected = null, suspects, isolate = false, xray = false, planes = null, runWhileExploded = false, showFlow = false, glow = false, onSelect, onHover, api }: Props) {
  const drive = (def as any).drive
  const asm = useMemo(() => getAssembly(def.type, drive), [def.type, drive?.drum_radius_m, drive?.reduction])
  // one private material set per component, so one component can be highlighted or sectioned alone
  const mats = useMemo(() => asm.parts.map((p) => {
    const set: Partial<Record<MatKey, THREE.MeshStandardMaterial>> = {}
    for (const m of p.meshes) set[m.mat] ||= makeMaterial(m.mat, def.type)
    return set
  }), [asm, def.type])
  const crateMat = useMemo(() => makeMaterial('crate', def.type), [def.type])
  const arrowMat = useMemo(() => new THREE.MeshBasicMaterial({ color: '#4aa3ff', transparent: true, opacity: 0.85 }), [])
  const glows = useRef<(THREE.Mesh | null)[]>([])
  const glowMat = useMemo(() => new THREE.MeshBasicMaterial({ color: '#ff1626', transparent: true, opacity: 0.22, blending: THREE.AdditiveBlending, depthWrite: false, depthTest: false }), [])
  const centres = useMemo(() => asm.parts.map((p) => ({ c: p.box.getCenter(new THREE.Vector3()), r: Math.max(0.12, p.box.getSize(new THREE.Vector3()).length() * 0.42) })), [asm])
  useEffect(() => () => { mats.forEach((set) => Object.values(set).forEach((m) => m && disposeMaterial(m))); crateMat.dispose(); arrowMat.dispose(); glowMat.dispose() }, [mats, crateMat, arrowMat, glowMat])

  const groups = useRef<(THREE.Group | null)[]>([])
  const meshes = useRef<(THREE.Mesh | null)[][]>([])
  const items = useRef<(THREE.Mesh | null)[]>([])
  const arrows = useRef<(THREE.Mesh | null)[]>([])
  const k = useRef(explode)
  const lag = useRef(0), lastTheta = useRef<number | null>(null), theta = useRef(0)
  const hover = useRef<string | null>(null)
  const applied = useRef<string[]>([])
  const hidden = (p: PartDef) => isolate && !!selected && selected !== p.id
  const inCut = (point: THREE.Vector3) => !!planes && planes.length > 0 && planes.every((pl) => pl.distanceToPoint(point) < 0)

  useEffect(() => {
    if (!api) return
    api({
      asm, k: () => k.current, theta: () => theta.current,
      parts: () => asm.parts.map((p, i) => {
        const g = groups.current[i]
        const w = new THREE.Vector3()
        g?.getWorldPosition(w)
        const mat = Object.values(mats[i])[0]
        return { id: p.id, lib: p.lib, position: w.toArray(), visible: !!g?.visible, emissive: mat ? mat.emissiveIntensity : 0,
          angles: p.meshes.map((md, j) => { const m = meshes.current[i]?.[j]; return m && md.motion?.kind === 'rotate' ? m.rotation[md.motion.axis] : 0 }),
          offsets: p.meshes.map((_, j) => meshes.current[i]?.[j]?.position.toArray() || [0, 0, 0]) }
      }),
    })
    return () => api(null)
  }, [api, asm, mats])

  useFrame((state, dt) => {
    advance()
    const train = trainFor(def as any)
    // explosion: smooth, interruptible, and exact at both ends
    // global progress moves at a steady rate; each component follows its own staged, overshooting curve
    const stepK = Math.min(dt, 0.05) / 1.25
    k.current = control.reduced || Math.abs(explode - k.current) <= stepK ? explode : k.current + Math.sign(explode - k.current) * stepK
    const kk = k.current
    // an exploded view eases its mechanism to rest; assembled views stay in step with the shared train
    const gate = runWhileExploded ? 1 : 1 - smooth(kk, 0.02, 0.3)
    if (lastTheta.current === null) lastTheta.current = train.theta
    lag.current += (train.theta - lastTheta.current) * (1 - gate)
    lastTheta.current = train.theta
    const th = (theta.current = train.theta - lag.current)
    const rise = lift ? asm.lift * kk : 0
    const time = state.clock.elapsedTime
    const pulse = 0.55 + 0.4 * Math.sin(time * 3.4)
    // a vibrating machine visibly shakes; the shake fades out as the machine is opened up
    const shake = control.reduced ? 0 : train.fault * 0.014 * (1 - smooth(kk, 0, 0.25))
    const sx = Math.sin(time * 53) * shake, sy = Math.sin(time * 71 + 1.3) * shake * 0.7, sz = Math.sin(time * 61 + 0.7) * shake

    for (let i = 0; i < asm.parts.length; i++) {
      const p = asm.parts[i], g = groups.current[i]
      if (!g) continue
      const ki = stagedK(kk, p.rank ?? 0)
      g.position.set(p.pos[0] + p.explode[0] * ki + sx, p.pos[1] + p.explode[1] * ki + rise + sy, p.pos[2] + p.explode[2] * ki + sz)
      const row = meshes.current[i]
      if (row) for (let j = 0; j < p.meshes.length; j++) { const m = row[j]; if (m && p.meshes[j].motion) applyMotion(m, p.meshes[j], th, train.fault) }

      // visual state: only touch the materials when something actually changed
      const isSel = selected === p.id, isSus = !!suspects?.includes(p.lib), isHov = hover.current === p.id
      const cut = !!planes && planes.length > 0 && !!p.cut
      const ghost = xray && !!p.enclosure && !isSel
      const hide = hidden(p)
      g.visible = !hide
      const sig = `${isSel}${isSus}${isHov}${cut}${ghost}`
      const set = mats[i]
      if (applied.current[i] !== sig) {
        applied.current[i] = sig
        for (const m of Object.values(set)) {
          if (!m) continue
          m.emissive.copy(isSus ? SUSPECT : isSel ? SELECT : isHov ? HOVER : NONE)
          m.emissiveIntensity = isSel ? 0.3 : isHov ? 0.14 : 0
          const wantClip = cut ? planes : null
          const wasTransparent = m.transparent, hadClip = !!m.clippingPlanes?.length
          m.clippingPlanes = wantClip
          m.clipIntersection = true
          m.clipShadows = true
          m.transparent = ghost
          m.opacity = ghost ? 0.2 : 1
          m.depthWrite = !ghost
          if (m.map === null) m.side = cut || ghost ? THREE.DoubleSide : THREE.FrontSide
          if (wasTransparent !== ghost || hadClip !== !!wantClip) m.needsUpdate = true
        }
      }
      if (isSus) for (const m of Object.values(set)) if (m) m.emissiveIntensity = pulse
      const halo = glows.current[i]
      if (halo) { halo.visible = glow && isSus && !hide; if (halo.visible) halo.scale.setScalar(centres[i].r * (1.25 + 0.22 * Math.sin(time * 3.4))) }
    }

    // items riding on a conveyor belt: they move exactly as far as the belt surface does
    if (asm.items) {
      const it = asm.items, travel = th * it.ratio * it.radius
      for (let i = 0; i < it.count; i++) {
        const m = items.current[i]
        if (!m) continue
        const { s, scale } = itemOnBelt(travel, i, it.spacing, it.span)
        m.visible = scale > 0.02
        m.position.set(it.start + s, it.y + 0.2 * scale, i % 2 ? 0.1 : -0.08)
        m.scale.setScalar(Math.max(0.02, scale))
      }
    }
    // flow-direction arrows: an illustration of direction, drawn only while the machine turns
    if (asm.flow) {
      const on = showFlow && kk < 0.05 && Math.abs(train.omega) > 0.05
      const phase = (th * 0.12) % 1
      asm.flow.forEach((f, fi) => {
        for (let n = 0; n < 3; n++) {
          const m = arrows.current[fi * 3 + n]
          if (!m) continue
          m.visible = on
          if (!on) continue
          const u = (((phase + n / 3) % 1) + 1) % 1
          m.position.set(f.from[0] + (f.to[0] - f.from[0]) * u, f.from[1] + (f.to[1] - f.from[1]) * u, f.from[2] + (f.to[2] - f.from[2]) * u)
          m.scale.setScalar(0.35 + 0.65 * Math.sin(Math.PI * u))
        }
      })
    }
  })

  const handlers = (p: PartDef) => !interactive ? {} : {
    onClick: (e: ThreeEvent<MouseEvent>) => { if (hidden(p) || (p.cut && inCut(e.point))) return; e.stopPropagation(); onSelect?.(p.id) },
    onPointerOver: (e: ThreeEvent<PointerEvent>) => { if (hidden(p) || (p.cut && inCut(e.point))) return; e.stopPropagation(); hover.current = p.id; onHover?.(p.id); document.body.style.cursor = 'pointer' },
    onPointerOut: () => { if (hover.current === p.id) { hover.current = null; onHover?.(null); document.body.style.cursor = '' } },
  }

  return (
    <group dispose={null}>
      {asm.parts.map((p, i) => (
        <group key={p.id} name={p.id} ref={(el) => { groups.current[i] = el }} position={p.pos} {...handlers(p)}>
          {p.meshes.map((md, j) => (
            <mesh key={j} ref={(el) => { (meshes.current[i] ||= [])[j] = el }} geometry={md.geo} material={mats[i][md.mat]} position={md.pivot} castShadow receiveShadow />
          ))}
          <mesh ref={(el) => { glows.current[i] = el }} position={centres[i].c} material={glowMat} visible={false} raycast={() => null} renderOrder={5}><sphereGeometry args={[1, 24, 16]} /></mesh>
          {asm.items?.part === p.id && Array.from({ length: asm.items.count }, (_, n) => (
            <mesh key={`item${n}`} ref={(el) => { items.current[n] = el }} geometry={crateGeo()} material={crateMat} castShadow />
          ))}
        </group>
      ))}
      {asm.flow?.flatMap((f, fi) => [0, 1, 2].map((n) => {
        const dir = new THREE.Vector3(f.to[0] - f.from[0], f.to[1] - f.from[1], f.to[2] - f.from[2]).normalize()
        const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(1, 0, 0), dir)
        return <mesh key={`flow${fi}${n}`} ref={(el) => { arrows.current[fi * 3 + n] = el }} geometry={arrowGeo()} material={arrowMat} quaternion={q} visible={false} />
      }))}
    </group>
  )
}
