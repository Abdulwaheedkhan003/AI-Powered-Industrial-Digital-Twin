// The 3D views: the factory hall and the single-machine inspection view.
// Machines read their motion from the shared trains inside the render loop, so telemetry
// updates never re-render a scene through React.
import { OrbitControls } from '@react-three/drei'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Component, MutableRefObject, ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { assetStatus } from '../lib/status'
import { AssetDef, live, useAsset, useStore } from '../lib/store'
import Assembly, { AssemblyApi } from './Assembly'
import { AssemblyDef, boundsAt, getAssembly, partCentre } from './assemblies'
import { at, box, capsule, cyl, merge, paint, tube } from './geom'
import { stagedK } from './kinematics'
import { allTrains, control, trainFor, useControl } from './motion'

export function webglAvailable() {
  try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')) } catch { return false }
}
/** If a 3D view throws, the rest of the page keeps working. */
export class SceneBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() { return this.state.failed ? this.props.fallback : this.props.children }
}

// Read-only hooks for automated checks of the scene (positions, angles, framing).
const registry: any = typeof window !== 'undefined' ? ((window as any).__NEXUS_TWIN__ ||= { views: {}, trains: allTrains, control }) : { views: {} }

function Environment({ intensity = 0.6 }: { intensity?: number }) {
  const { gl, scene } = useThree()
  useEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl)
    const env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
    scene.environment = env
    scene.environmentIntensity = intensity
    return () => { env.dispose(); pmrem.dispose() }
  }, [gl, scene, intensity])
  return null
}

/** Stop rendering a view while it is scrolled out of sight. */
function useInView<T extends Element>() {
  const ref = useRef<T>(null)
  const [inView, setInView] = useState(true)
  useEffect(() => {
    if (!ref.current || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(([e]) => setInView(e.isIntersecting), { rootMargin: '120px' })
    io.observe(ref.current)
    return () => io.disconnect()
  }, [])
  return [ref, inView] as const
}

const place = (el: HTMLElement, v: THREE.Vector3, w: number, h: number) => {
  const show = v.z < 1
  el.style.display = show ? '' : 'none'
  if (show) el.style.transform = `translate(-50%, -100%) translate(${((v.x * 0.5 + 0.5) * w).toFixed(1)}px, ${((-v.y * 0.5 + 0.5) * h).toFixed(1)}px)`
}

// =====================================================================================
// Factory hall
// =====================================================================================
const hallGeo = (() => {
  let cached: { structure: THREE.BufferGeometry; lights: THREE.BufferGeometry } | null = null
  return () => {
    if (cached) return cached
    const wall = 0x151c28, steel = 0x283344, yellow = 0xb8922a, blue = 0x23467a, crate = 0x8a6f4c, grey = 0x39465a
    const g: THREE.BufferGeometry[] = []
    const add = (geo: THREE.BufferGeometry, color: number) => g.push(paint(geo, color))
    // shell
    add(at(box(46, 10, 0.4), 0, 5, -12.2), wall); add(at(box(0.4, 10, 26), -23, 5, 0.8), wall); add(at(box(0.4, 10, 26), 23, 5, 0.8), wall)
    add(at(box(46, 1.1, 0.46), 0, 0.55, -12.15), grey)
    for (const x of [-18, -9, 0, 9, 18]) { add(at(box(0.5, 10, 0.5), x, 5, -11.7), steel); add(at(box(0.32, 0.55, 25), x, 9.7, 0.8), steel) }
    add(at(box(46, 0.35, 0.3), 0, 9.3, -4), steel); add(at(box(46, 0.35, 0.3), 0, 9.3, 5), steel)
    // services along the back wall: cable tray, process pipes with drops to the pump and compressor
    add(at(box(42, 0.1, 0.7), 0, 6.4, -11.5), steel)
    add(at(cyl(0.17, 40, 'x', 16), 0, 3.4, -11.4), 0x35506f); add(at(cyl(0.11, 40, 'x', 12), 0, 3.9, -11.4), 0x6b5a3a)
    for (const x of [-3.5, 3.5]) { add(at(cyl(0.09, 2.3, 'y', 12), x, 2.3, -11.4), 0x35506f); add(at(cyl(0.09, 4.2, 'z', 12), x, 1.2, -9.3), 0x35506f) }
    // storage tank and control cabinets
    add(at(capsule(1.5, 3.6, 'y'), -19.3, 3.3, -8.6), 0x9aa5b1); add(at(tube(1.56, 1.45, 0.3, 'y', 32), -19.3, 0.15, -8.6), steel)
    for (let i = 0; i < 4; i++) { add(at(box(0.7, 2.1, 1.1), -22.3, 1.05, -3 + i * 1.3), 0x2b3a50); add(at(box(0.04, 0.25, 0.5), -21.93, 1.6, -3 + i * 1.3), 0x7fd0ff) }
    // pallet racking with boxes
    for (let bay = 0; bay < 3; bay++) {
      const x = 15.4 + bay * 2.5
      for (const dx of [-1.2, 1.2]) for (const dz of [-0.55, 0.55]) add(at(box(0.09, 4.4, 0.09), x + dx, 2.2, -10.3 + dz), blue)
      for (const y of [0.25, 1.7, 3.15]) {
        add(at(box(2.45, 0.08, 1.15), x, y, -10.3), 0xb5651d)
        for (const dx of [-0.6, 0.6]) add(at(box(0.9, 0.75 + ((bay + dx > 1 ? 1 : 0) * 0.2), 0.85), x + dx, y + 0.45, -10.3), crate)
      }
    }
    // floor markings: walkway and one bay outline per machine row
    for (const z of [0.2, 1.9]) add(at(box(40, 0.006, 0.09), 0, 0.004, z), yellow)
    for (let x = -19; x <= 19; x += 2) add(at(box(0.9, 0.006, 0.09), x, 0.004, 1.05), 0x6f7d8f)
    for (const [x0, x1, z0, z1] of [[-13, 13.2, -8, -2.2], [-7, 7.6, 3.2, 9.9]]) {
      add(at(box(x1 - x0, 0.006, 0.07), (x0 + x1) / 2, 0.004, z0), yellow); add(at(box(x1 - x0, 0.006, 0.07), (x0 + x1) / 2, 0.004, z1), yellow)
      add(at(box(0.07, 0.006, z1 - z0), x0, 0.004, (z0 + z1) / 2), yellow); add(at(box(0.07, 0.006, z1 - z0), x1, 0.004, (z0 + z1) / 2), yellow)
    }
    // safety bollards at the walkway
    for (let x = -12; x <= 12; x += 4) { add(at(cyl(0.07, 0.9, 'y', 10), x, 0.45, -1.6), yellow); add(at(cyl(0.075, 0.12, 'y', 10), x, 0.7, -1.6), 0x20262e) }
    const l: THREE.BufferGeometry[] = []
    for (const x of [-13.5, -4.5, 4.5, 13.5]) for (const z of [-5, 5]) l.push(at(box(2.6, 0.06, 0.5), x, 9.0, z))
    cached = { structure: merge(g), lights: merge(l) }
    return cached
  }
})()

function Hall() {
  const geo = useMemo(hallGeo, [])
  return (
    <group dispose={null}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow><planeGeometry args={[70, 48]} /><meshStandardMaterial color="#121823" roughness={0.78} metalness={0.2} /></mesh>
      <gridHelper args={[70, 35, '#1c2636', '#172030']} position={[0, 0.002, 0]} />
      <mesh geometry={geo.structure} receiveShadow castShadow><meshStandardMaterial vertexColors roughness={0.75} metalness={0.25} /></mesh>
      <mesh geometry={geo.lights}><meshBasicMaterial color="#e6f0fb" /></mesh>
      {[[-9, -5], [9, -5], [-9, 5], [9, 5]].map(([x, z]) => <pointLight key={`${x}${z}`} position={[x, 8.6, z]} intensity={70} distance={26} decay={2} color="#dbe7f5" />)}
    </group>
  )
}

const STATE_COLOR: Record<string, string> = { ok: '#3fb97a', warn: '#c8101c', crit: '#c8101c', off: '#4b5a70' }
const noRay = () => null
/** Soft radial falloff used for the red pool of light under a faulted machine. */
const glowTexture = (() => {
  let tex: THREE.CanvasTexture | null = null
  return () => {
    if (tex) return tex
    const c = document.createElement('canvas'); c.width = c.height = 128
    const g = c.getContext('2d')!, grad = g.createRadialGradient(64, 64, 4, 64, 64, 64)
    grad.addColorStop(0, 'rgba(255,255,255,1)'); grad.addColorStop(0.45, 'rgba(255,255,255,0.45)'); grad.addColorStop(1, 'rgba(255,255,255,0)')
    g.fillStyle = grad; g.fillRect(0, 0, 128, 128)
    tex = new THREE.CanvasTexture(c)
    return tex
  }
})()

function AssetNode({ def, selected, onSelect, hovered, setHovered }: { def: AssetDef; selected: boolean; onSelect?: (id: string) => void; hovered: boolean; setHovered: (id: string | null) => void }) {
  const beacon = useRef<THREE.MeshBasicMaterial>(null)
  const halo = useRef<THREE.Mesh>(null)
  const haloMat = useRef<THREE.MeshBasicMaterial>(null)
  const alarm = useRef<THREE.Group>(null), alarmLight = useRef<THREE.PointLight>(null)
  const dome = useRef<THREE.MeshBasicMaterial>(null), pool = useRef<THREE.MeshBasicMaterial>(null), beam = useRef<THREE.MeshBasicMaterial>(null)
  const asm = useMemo(() => getAssembly(def.type, (def as any).drive), [def])
  const radius = useMemo(() => { const s = boundsAt(asm, 0).getSize(new THREE.Vector3()); return Math.max(s.x, s.z) / 2 + 0.55 }, [asm])
  useFrame((state) => {
    const tone = assetStatus(live.assets[def.asset_id], control.preview).tone
    beacon.current?.color.set(STATE_COLOR[tone])
    if (halo.current && haloMat.current) {
      const alarm = tone === 'warn' || tone === 'crit'
      halo.current.visible = alarm || selected || hovered
      haloMat.current.color.set(alarm ? '#ff1626' : selected ? '#2f81f7' : '#9fb4cf')
      // a slow breathing ring, about one cycle every two seconds; never a flash
      haloMat.current.opacity = alarm && !control.reduced ? 0.55 + 0.35 * Math.sin(state.clock.elapsedTime * 3) : selected ? 0.75 : 0.35
    }
    // a faulted machine sits in a pulsing pool of deep red light
    const level = trainFor(def as any).alarm
    if (alarm.current) {
      alarm.current.visible = level > 0.02
      const beat = control.reduced ? 1 : 0.62 + 0.38 * Math.sin(state.clock.elapsedTime * 3)
      if (alarmLight.current) alarmLight.current.intensity = 170 * level * beat
      if (dome.current) dome.current.opacity = 0.34 * level * beat
      if (pool.current) pool.current.opacity = 0.95 * level * beat
      if (beam.current) beam.current.opacity = 0.11 * level * beat
    }
  })
  const events = onSelect ? {
    onClick: (e: any) => { e.stopPropagation(); onSelect(def.asset_id) },
    onPointerOver: (e: any) => { e.stopPropagation(); setHovered(def.asset_id); document.body.style.cursor = 'pointer' },
    onPointerOut: () => { setHovered(null); document.body.style.cursor = '' },
  } : {}
  const conveyor = def.type === 'conveyor'
  return (
    <group position={def.position as any} rotation={[0, def.rotation, 0]} {...events}>
      {!conveyor && <mesh position={[0, 0.05, 0]} receiveShadow castShadow><boxGeometry args={[radius * 1.75, 0.1, Math.min(radius * 1.5, 2.9)]} /><meshStandardMaterial color="#1a2332" roughness={0.9} /></mesh>}
      <group position={[0, conveyor ? 0 : 0.1, 0]}>
        <Assembly def={def} api={(api) => { registry.views.factory ||= { assets: {} }; if (api) registry.views.factory.assets[def.asset_id] = api }} />
      </group>
      <group position={[-(radius * 0.86), 0, Math.min(radius * 0.7, 1.35)]}>
        <mesh position={[0, 1.0, 0]}><cylinderGeometry args={[0.03, 0.03, 2.0, 8]} /><meshStandardMaterial color="#39465a" metalness={0.7} roughness={0.4} /></mesh>
        <mesh position={[0, 2.1, 0]}><cylinderGeometry args={[0.1, 0.1, 0.24, 16]} /><meshBasicMaterial ref={beacon} color="#4b5a70" /></mesh>
      </group>
      <group ref={alarm} visible={false}>
        <pointLight ref={alarmLight} position={[0, 2.0, 0]} color="#ff1525" intensity={0} distance={11} decay={1.5} />
        <mesh position={[0, 4.2, 0]} raycast={noRay}><cylinderGeometry args={[radius * 0.5, radius * 1.05, 8.4, 32, 1, true]} /><meshBasicMaterial ref={beam} color="#ff1020" transparent opacity={0} blending={THREE.AdditiveBlending} depthWrite={false} side={THREE.DoubleSide} /></mesh>
        <mesh position={[0, 1.2, 0]} raycast={noRay}><sphereGeometry args={[radius * 1.7, 32, 20]} /><meshBasicMaterial ref={dome} color="#b00d1a" transparent opacity={0} blending={THREE.AdditiveBlending} depthWrite={false} side={THREE.BackSide} /></mesh>
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.045, 0]} raycast={noRay}><circleGeometry args={[radius * 3.4, 48]} /><meshBasicMaterial ref={pool} map={glowTexture()} color="#ff0f20" transparent opacity={0} blending={THREE.AdditiveBlending} depthWrite={false} /></mesh>
      </group>
      <mesh ref={halo} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]} visible={false}>
        {conveyor ? <planeGeometry args={[11.6, 2.3]} /> : <ringGeometry args={[radius, radius + 0.1, 72]} />}
        <meshBasicMaterial ref={haloMat} transparent opacity={0.5} depthWrite={false} wireframe={conveyor} />
      </mesh>
    </group>
  )
}

function Callout({ def, selected, onSelect }: { def: AssetDef; selected: boolean; onSelect: (id: string) => void }) {
  const a = useAsset(def.asset_id)
  const { preview } = useControl()
  const st = assetStatus(a, preview)
  return (
    <button className={`callout ${st.tone} ${selected ? 'sel' : ''}`} onClick={() => onSelect(def.asset_id)} aria-label={`${def.name}: ${st.label}`}>
      <span className="callout-ring" aria-hidden="true">{st.tone === 'crit' || st.tone === 'warn' ? '!' : st.tone === 'ok' ? '✓' : '–'}</span>
      <span className="callout-text"><b>{def.name}</b><span><i className={`dot ${st.tone}`} />{st.label}</span></span>
    </button>
  )
}

type Els = MutableRefObject<Record<string, HTMLElement | null>>
function TagProjector({ assets, els }: { assets: AssetDef[]; els: Els }) {
  const v = useMemo(() => new THREE.Vector3(), [])
  useFrame((state) => {
    for (const def of assets) {
      const el = els.current[def.asset_id]
      if (!el) continue
      v.set(def.position[0], def.type === 'conveyor' ? 1.9 : def.type === 'compressor' ? 3.0 : 2.5, def.position[2]).project(state.camera)
      place(el, v, state.size.width, state.size.height)
    }
  })
  return null
}

const VIEWS: Record<string, { pos: [number, number, number]; target: [number, number, number] }> = {
  iso: { pos: [-17, 17, 27], target: [0.5, 0.6, 0.8] },
  top: { pos: [0.5, 47, 1.2], target: [0.5, 0, 1.0] },
  showcase: { pos: [-10, 9, 20], target: [0, 1, 1] },
  walk: { pos: [0, 1.7, 13.5], target: [0, 1.5, 0] },
}

function FactoryRig({ focus, view, resetKey, showcase, walk }: { focus: AssetDef | null; view: 'iso' | 'top'; resetKey: number; showcase: boolean; walk: boolean }) {
  const controls = useRef<any>(null)
  const { camera, gl } = useThree()
  const goal = useRef({ pos: new THREE.Vector3(), target: new THREE.Vector3(), active: false })
  const walking = useRef(false)
  const look = useRef({ yaw: 0, pitch: -0.05 })
  const keys = useRef<Record<string, boolean>>({})
  const first = useRef(true)
  const assets = useStore((s) => s.site?.assets) || []

  useEffect(() => {
    const g = goal.current
    walking.current = false
    if (walk) { g.pos.set(...VIEWS.walk.pos); g.target.set(...VIEWS.walk.target) }
    else if (focus && !showcase) {
      // frame the machine with some of its surroundings still in view
      const size = boundsAt(getAssembly(focus.type, (focus as any).drive), 0).getSize(new THREE.Vector3())
      const r = Math.max(size.x, size.z, 2.4)
      const dir = new THREE.Vector3().subVectors(camera.position, controls.current?.target || new THREE.Vector3()).setY(0).normalize()
      if (dir.lengthSq() < 0.1) dir.set(-0.5, 0, 0.86)
      g.target.set(focus.position[0], 1.0, focus.position[2])
      g.pos.copy(g.target).addScaledVector(dir, r * 2.5).setY(1.0 + r * 1.5)
    } else { const v = VIEWS[showcase ? 'showcase' : view]; g.pos.set(...v.pos); g.target.set(...v.target) }
    g.active = true
    if ((first.current || control.reduced) && controls.current) { camera.position.copy(g.pos); controls.current.target.copy(g.target); controls.current.update(); g.active = walk }
    first.current = false
  }, [focus?.asset_id, view, resetKey, showcase, walk])   // eslint-disable-line react-hooks/exhaustive-deps

  // walk mode: arrow keys / WASD to move, drag to look around
  useEffect(() => {
    if (!walk) return
    const typing = (e: KeyboardEvent) => ['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement)?.tagName)
    const down = (e: KeyboardEvent) => { if (typing(e)) return; keys.current[e.key.toLowerCase()] = true; if (e.key.startsWith('Arrow')) e.preventDefault() }
    const up = (e: KeyboardEvent) => { keys.current[e.key.toLowerCase()] = false }
    let drag: { x: number; y: number } | null = null
    const pd = (e: PointerEvent) => { drag = { x: e.clientX, y: e.clientY } }
    const pm = (e: PointerEvent) => { if (!drag || !walking.current) return; look.current.yaw -= (e.clientX - drag.x) * 0.004; look.current.pitch = THREE.MathUtils.clamp(look.current.pitch - (e.clientY - drag.y) * 0.003, -1.1, 1.1); drag = { x: e.clientX, y: e.clientY } }
    const pu = () => { drag = null }
    window.addEventListener('keydown', down); window.addEventListener('keyup', up)
    gl.domElement.addEventListener('pointerdown', pd); window.addEventListener('pointermove', pm); window.addEventListener('pointerup', pu)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); gl.domElement.removeEventListener('pointerdown', pd); window.removeEventListener('pointermove', pm); window.removeEventListener('pointerup', pu); keys.current = {} }
  }, [walk, gl])

  useFrame((_, dt) => {
    const g = goal.current, c = controls.current
    if (walking.current) {
      const k = keys.current
      const f = (k.w || k.arrowup ? 1 : 0) - (k.s || k.arrowdown ? 1 : 0), s = (k.d || k.arrowright ? 1 : 0) - (k.a || k.arrowleft ? 1 : 0)
      const speed = (k.shift ? 6.5 : 3.2) * Math.min(dt, 0.05), yaw = look.current.yaw
      let x = camera.position.x + (-Math.sin(yaw) * f + Math.cos(yaw) * s) * speed
      let z = camera.position.z + (-Math.cos(yaw) * f - Math.sin(yaw) * s) * speed
      x = THREE.MathUtils.clamp(x, -21.5, 21.5); z = THREE.MathUtils.clamp(z, -10.8, 16)
      // keep out of the machines: refuse a step that would enter a machine's footprint
      const blocked = assets.some((a) => a.type === 'conveyor' ? Math.abs(x - a.position[0]) < 6 && Math.abs(z - a.position[2]) < 1.2 : Math.hypot(x - a.position[0], z - a.position[2]) < 2.1)
      if (!blocked) { camera.position.x = x; camera.position.z = z }
      camera.position.y = 1.7
      camera.rotation.set(look.current.pitch, yaw, 0, 'YXZ')
      return
    }
    if (!g.active || !c) return
    const rate = 3.4
    camera.position.set(THREE.MathUtils.damp(camera.position.x, g.pos.x, rate, dt), THREE.MathUtils.damp(camera.position.y, g.pos.y, rate, dt), THREE.MathUtils.damp(camera.position.z, g.pos.z, rate, dt))
    c.target.set(THREE.MathUtils.damp(c.target.x, g.target.x, rate, dt), THREE.MathUtils.damp(c.target.y, g.target.y, rate, dt), THREE.MathUtils.damp(c.target.z, g.target.z, rate, dt))
    c.update()
    if (camera.position.distanceTo(g.pos) < 0.06) {
      g.active = false                               // hand control back to the user
      if (walk) { walking.current = true; look.current = { yaw: 0, pitch: -0.05 } }
    }
  })
  return <OrbitControls ref={controls} makeDefault enableDamping dampingFactor={0.08}
    enabled={!showcase && !walk} autoRotate={showcase && !control.reduced} autoRotateSpeed={0.3}
    minDistance={4} maxDistance={62} maxPolarAngle={Math.PI / 2 - 0.05} minPolarAngle={0.02}
    onStart={() => { goal.current.active = false }}
    onChange={() => { const t = controls.current?.target; if (t) { t.x = THREE.MathUtils.clamp(t.x, -20, 20); t.z = THREE.MathUtils.clamp(t.z, -11, 12); t.y = THREE.MathUtils.clamp(t.y, 0, 6) } }} />
}

type FactoryProps = { selected?: string | null; onSelect?: (id: string) => void; view?: 'iso' | 'top'; resetKey?: number; showcase?: boolean; walk?: boolean; focusOnSelect?: boolean }

export function FactoryView({ selected = null, onSelect, view = 'iso', resetKey = 0, showcase = false, walk = false, focusOnSelect = true }: FactoryProps) {
  const all = useStore((s) => s.site?.assets)
  const assets = useMemo(() => (all || []).filter((a) => a.data_mode !== 'REPLAY'), [all])
  const [hovered, setHovered] = useState<string | null>(null)
  const focus = (focusOnSelect && assets.find((a) => a.asset_id === selected)) || null
  const tagEls = useRef<Record<string, HTMLElement | null>>({})
  const [wrap, inView] = useInView<HTMLDivElement>()
  return (
    <div ref={wrap} className="scene">
      <Canvas shadows dpr={[1, 1.5]} frameloop={inView ? 'always' : 'never'} camera={{ position: VIEWS.iso.pos, fov: 30, near: 0.2, far: 160 }} gl={{ antialias: true, powerPreference: 'high-performance' }}>
        <color attach="background" args={['#070b13']} />
        <fog attach="fog" args={['#070b13', 48, 110]} />
        <Environment intensity={0.5} />
        <hemisphereLight args={['#aebfd8', '#0a0e16', 0.4]} />
        <directionalLight position={[14, 24, 14]} intensity={1.7} castShadow shadow-mapSize={[2048, 2048]} shadow-bias={-0.0004}>
          <orthographicCamera attach="shadow-camera" args={[-26, 26, 18, -18, 1, 70]} />
        </directionalLight>
        <Hall />
        {assets.map((def) => <AssetNode key={def.asset_id} def={def} selected={selected === def.asset_id} hovered={hovered === def.asset_id} setHovered={setHovered} onSelect={showcase || walk ? undefined : onSelect} />)}
        {!showcase && <TagProjector assets={assets} els={tagEls} />}
        <FactoryRig focus={focus} view={view} resetKey={resetKey} showcase={showcase} walk={walk} />
      </Canvas>
      {!showcase && onSelect && (
        <div className="label-layer">
          {assets.map((def) => <div key={def.asset_id} className="label" style={{ display: 'none' }} ref={(el) => { tagEls.current[def.asset_id] = el }}><Callout def={def} selected={selected === def.asset_id} onSelect={onSelect} /></div>)}
        </div>
      )}
    </div>
  )
}

// =====================================================================================
// Single-machine inspection view
// =====================================================================================
function fitCamera(boxIn: THREE.Box3, dir: THREE.Vector3, fovDeg: number, aspect: number, useY: number, useX = 0.92) {
  const centre = boxIn.getCenter(new THREE.Vector3())
  const forward = dir.clone().negate()
  const right = new THREE.Vector3().crossVectors(forward, new THREE.Vector3(0, 1, 0))
  if (right.lengthSq() < 1e-6) right.set(1, 0, 0)
  right.normalize()
  const up = new THREE.Vector3().crossVectors(right, forward).normalize()
  const tanV = Math.tan((fovDeg * Math.PI) / 360) * useY, tanH = Math.tan((fovDeg * Math.PI) / 360) * aspect * useX
  let dist = 0.6
  const c = new THREE.Vector3()
  for (let i = 0; i < 8; i++) {
    c.set(i & 1 ? boxIn.max.x : boxIn.min.x, i & 2 ? boxIn.max.y : boxIn.min.y, i & 4 ? boxIn.max.z : boxIn.min.z).sub(centre)
    dist = Math.max(dist, c.dot(dir) + Math.abs(c.dot(right)) / tanH, c.dot(dir) + Math.abs(c.dot(up)) / tanV)
  }
  return { centre, dist }
}

type RigProps = { asm: AssemblyDef; k: number; lift: boolean; mode: string; labels: boolean; focusPart: string | null; fitKey: number; spin: number; preset?: { position: [number, number, number]; target: [number, number, number] } }
function MachineRig({ asm, k, lift, mode, labels, focusPart, fitKey, spin, preset }: RigProps) {
  const controls = useRef<any>(null)
  const { camera, size } = useThree()
  const goal = useRef({ pos: new THREE.Vector3(), target: new THREE.Vector3(), active: false })
  const prev = useRef<{ asm: AssemblyDef | null; mode: string; fitKey: number }>({ asm: null, mode: '', fitKey: -1 })
  useEffect(() => {
    const g = goal.current, c = controls.current
    const fresh = prev.current.asm !== asm || prev.current.mode !== mode || prev.current.fitKey !== fitKey
    const firstTime = prev.current.asm === null
    prev.current = { asm, mode, fitKey }
    if (preset && !focusPart) { g.pos.set(...preset.position); g.target.set(...preset.target) }
    else {
      // keep the user's viewing direction unless the machine, the mode or "reset view" changed
      const dir = new THREE.Vector3()
      if (fresh || !c) dir.set(...(mode === 'exploded' ? asm.views.exploded : asm.views.assembled))
      else dir.subVectors(camera.position, c.target)
      dir.normalize()
      const fit = fitCamera(boundsAt(asm, k, lift, focusPart || undefined), dir, (camera as THREE.PerspectiveCamera).fov, size.width / Math.max(size.height, 1), labels && !focusPart ? 0.76 : 1.0)
      g.target.copy(fit.centre)
      g.pos.copy(fit.centre).addScaledVector(dir, fit.dist * (focusPart ? 2.8 : 1.04))
    }
    g.active = true
    if ((firstTime || control.reduced) && c) { camera.position.copy(g.pos); c.target.copy(g.target); c.update(); g.active = false }
  }, [asm, k, lift, mode, labels, focusPart, fitKey, size.width, size.height, preset])   // eslint-disable-line react-hooks/exhaustive-deps
  useFrame((_, dt) => {
    const g = goal.current, c = controls.current
    if (c) { c.autoRotate = spin > 0 && !g.active && !control.reduced; c.autoRotateSpeed = spin }
    if (!g.active || !c) return
    const rate = 5
    camera.position.set(THREE.MathUtils.damp(camera.position.x, g.pos.x, rate, dt), THREE.MathUtils.damp(camera.position.y, g.pos.y, rate, dt), THREE.MathUtils.damp(camera.position.z, g.pos.z, rate, dt))
    c.target.set(THREE.MathUtils.damp(c.target.x, g.target.x, rate, dt), THREE.MathUtils.damp(c.target.y, g.target.y, rate, dt), THREE.MathUtils.damp(c.target.z, g.target.z, rate, dt))
    c.update()
    if (camera.position.distanceTo(g.pos) < 0.03) g.active = false
  })
  return <OrbitControls ref={controls} makeDefault enableDamping dampingFactor={0.1} minDistance={0.35} maxDistance={60} maxPolarAngle={Math.PI * 0.62} onStart={() => { goal.current.active = false }} />
}

/** A plane of light sweeping through the machine while its signals are scanned. */
function ScanPlane({ asm }: { asm: AssemblyDef }) {
  const ref = useRef<THREE.Group>(null)
  const { min, size, centre } = useMemo(() => { const b = boundsAt(asm, 0); return { min: b.min, size: b.getSize(new THREE.Vector3()), centre: b.getCenter(new THREE.Vector3()) } }, [asm])
  useFrame(({ clock }) => { if (ref.current) ref.current.position.x = min.x - 0.08 + (size.x + 0.16) * (0.5 + 0.5 * Math.sin(clock.elapsedTime * 2.2)) })
  const w = size.z * 1.5 + 0.3, h = size.y * 1.4 + 0.3
  return (
    <group ref={ref} position={[0, centre.y, centre.z]} rotation={[0, Math.PI / 2, 0]}>
      <mesh raycast={noRay}><planeGeometry args={[w, h]} /><meshBasicMaterial color="#39c5ff" transparent opacity={0.16} side={THREE.DoubleSide} depthWrite={false} blending={THREE.AdditiveBlending} /></mesh>
      <lineSegments raycast={noRay}><edgesGeometry args={[new THREE.PlaneGeometry(w, h)]} /><lineBasicMaterial color="#7fdcff" transparent opacity={0.9} /></lineSegments>
    </group>
  )
}

type LabelRefs = MutableRefObject<Record<string, { el: HTMLElement | null; line: SVGLineElement | null; dot: SVGCircleElement | null }>>
/** Keeps component labels in two tidy rows (top and bottom) with a leader line to each part. */
function CalloutProjector({ asm, api, lift, ids, refs }: { asm: AssemblyDef; api: MutableRefObject<AssemblyApi | null>; lift: boolean; ids: string[]; refs: LabelRefs }) {
  const v = useMemo(() => new THREE.Vector3(), [])
  const widths = useRef<Record<string, number>>({})
  const frame = useRef(0)
  useFrame(({ camera, size }) => {
    const k = api.current ? api.current.k() : 0
    const W = size.width, H = size.height
    frame.current++
    const items: { id: string; x: number; y: number; w: number; top: boolean; lx: number }[] = []
    for (const id of ids) {
      const p = asm.parts.find((q) => q.id === id), r = refs.current[id]
      if (!p || !r?.el) continue
      partCentre(asm, p, k, lift, v).project(camera)
      if (frame.current % 30 === 1 || !widths.current[id]) widths.current[id] = r.el.offsetWidth || 90
      items.push({ id, x: (v.x * 0.5 + 0.5) * W, y: (-v.y * 0.5 + 0.5) * H, w: widths.current[id], top: true, lx: 0 })
    }
    items.sort((a, b) => a.x - b.x)
    items.forEach((it, i) => { it.top = it.y < H * 0.36 ? true : it.y > H * 0.64 ? false : i % 2 === 0 })
    for (const top of [true, false]) {
      const row = items.filter((it) => it.top === top)
      let edge = 6
      for (const it of row) { it.lx = Math.max(it.x - it.w / 2, edge); edge = it.lx + it.w + 6 }
      let limit = W - 6
      for (let i = row.length - 1; i >= 0; i--) { row[i].lx = Math.max(6, Math.min(row[i].lx, limit - row[i].w)); limit = row[i].lx - 6 }
    }
    for (const it of items) {
      const r = refs.current[it.id]
      const ly = it.top ? 6 : H - 42
      r.el!.style.transform = `translate(${it.lx.toFixed(1)}px, ${ly}px)`
      r.el!.style.visibility = 'visible'
      if (r.line) { r.line.setAttribute('x1', (it.lx + it.w / 2).toFixed(1)); r.line.setAttribute('y1', String(it.top ? ly + 36 : ly)); r.line.setAttribute('x2', it.x.toFixed(1)); r.line.setAttribute('y2', it.y.toFixed(1)) }
      if (r.dot) { r.dot.setAttribute('cx', it.x.toFixed(1)); r.dot.setAttribute('cy', it.y.toFixed(1)) }
    }
  })
  return null
}

function FrameCheck({ viewId, asm, api, lift }: { viewId: string; asm: AssemblyDef; api: MutableRefObject<AssemblyApi | null>; lift: boolean }) {
  const { camera, size } = useThree()
  useEffect(() => {
    const v = new THREE.Vector3()
    registry.views[viewId] = {
      api: () => api.current,
      camera: () => ({ position: camera.position.toArray(), size: [size.width, size.height] }),
      /** Largest normalised screen coordinate of any visible component corner. Below 1 means everything is in frame. */
      fit: () => {
        const k = api.current ? api.current.k() : 0
        let max = 0
        for (const p of asm.parts) {
          const s = stagedK(k, p.rank ?? 0)
          const b = p.box.clone().translate(new THREE.Vector3(p.pos[0] + p.explode[0] * s, p.pos[1] + p.explode[1] * s + (lift ? asm.lift * k : 0), p.pos[2] + p.explode[2] * s))
          for (let i = 0; i < 8; i++) { v.set(i & 1 ? b.max.x : b.min.x, i & 2 ? b.max.y : b.min.y, i & 4 ? b.max.z : b.min.z).project(camera); max = Math.max(max, Math.abs(v.x), Math.abs(v.y)) }
        }
        return max
      },
      screen: (partId: string) => { const p = asm.parts.find((q) => q.id === partId); if (!p) return null; partCentre(asm, p, api.current ? api.current.k() : 0, lift, v).project(camera); return [(v.x * 0.5 + 0.5) * size.width, (-v.y * 0.5 + 0.5) * size.height] },
    }
    return () => { delete registry.views[viewId] }
  }, [viewId, asm, api, lift, camera, size])
  return null
}

export type MachineViewProps = {
  assetId: string; viewId: string
  mode?: 'assembled' | 'exploded'
  explode?: number
  cutaway?: boolean; xray?: boolean; labels?: boolean; isolate?: boolean
  selected?: string | null; suspects?: string[]
  onSelect?: (id: string | null) => void
  fitKey?: number; focusPart?: string | null
  companions?: string[]
  preset?: { position: [number, number, number]; target: [number, number, number] }
  interactive?: boolean; runWhileExploded?: boolean; showFlow?: boolean
  spin?: number; scan?: boolean; labelIds?: string[]
  partNames?: Record<string, string>
}

export function MachineView({ assetId, viewId, mode = 'assembled', explode = 0, cutaway = false, xray = false, labels = false, isolate = false, selected = null, suspects, onSelect, fitKey = 0, focusPart = null, companions, preset, interactive = true, runWhileExploded = false, showFlow = false, spin = 0, scan = false, labelIds, partNames = {} }: MachineViewProps) {
  const assets = useStore((s) => s.site?.assets) || []
  const def = assets.find((a) => a.asset_id === assetId)
  const [wrap, inView] = useInView<HTMLDivElement>()
  const api = useRef<AssemblyApi | null>(null)
  const refs = useRef<Record<string, { el: HTMLElement | null; line: SVGLineElement | null; dot: SVGCircleElement | null }>>({})
  const asm = useMemo(() => (def ? getAssembly(def.type, (def as any).drive) : null), [def])
  const planes = useMemo(() => (cutaway && asm && asm.cutPlanes.length ? asm.cutPlanes.map((p) => new THREE.Plane(new THREE.Vector3(...p.n), p.c)) : null), [cutaway, asm])
  if (!def || !asm) return <div className="scene" ref={wrap} />
  const lift = mode === 'exploded'
  const others = (companions || []).map((id) => assets.find((a) => a.asset_id === id)).filter(Boolean) as AssetDef[]
  const shown = !labels ? [] : asm.parts.filter((p) => !(isolate && selected && selected !== p.id) && (!labelIds || labelIds.includes(p.id))).map((p) => p.id)
  const span = boundsAt(asm, 0).getSize(new THREE.Vector3())
  const floor = Math.max(span.x, span.z) * 0.9 + 2
  return (
    <div ref={wrap} className="scene studio">
      <Canvas shadows dpr={[1, 1.6]} frameloop={inView ? 'always' : 'never'} camera={{ position: [4, 3, 6], fov: 32, near: 0.05, far: 200 }} gl={{ antialias: true, alpha: true }}
        onCreated={({ gl }) => { gl.localClippingEnabled = true }} onPointerMissed={() => onSelect?.(null)}>
        <Environment intensity={0.75} />
        <hemisphereLight args={['#c9d8ee', '#0a0e16', 0.55]} />
        <directionalLight position={[5, 9, 6]} intensity={2.0} castShadow shadow-mapSize={[1024, 1024]} shadow-bias={-0.0005}>
          <orthographicCamera attach="shadow-camera" args={[-floor, floor, floor, -floor, 0.5, 40]} />
        </directionalLight>
        <directionalLight position={[-6, 4, -5]} intensity={0.7} color="#5f9bff" />
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.002, 0]} receiveShadow><circleGeometry args={[floor * 1.6, 64]} /><meshStandardMaterial color="#0d131e" roughness={0.9} metalness={0.1} transparent opacity={0.92} /></mesh>
        <gridHelper args={[Math.ceil(floor * 3), Math.ceil(floor * 3), '#1d2a3f', '#152033']} position={[0, 0, 0]} />
        <Assembly def={def} explode={explode} lift={lift} interactive={interactive} selected={selected} suspects={suspects} isolate={isolate} xray={xray} planes={planes}
          runWhileExploded={runWhileExploded} showFlow={showFlow} glow onSelect={onSelect} api={(a) => { api.current = a }} />
        {scan && <ScanPlane asm={asm} />}
        {others.map((o) => (
          <group key={o.asset_id} position={[o.position[0] - def.position[0], 0, o.position[2] - def.position[2]]} rotation={[0, o.rotation - def.rotation, 0]}>
            <Assembly def={o} />
          </group>
        ))}
        <MachineRig asm={asm} k={explode} lift={lift} mode={mode} labels={labels} focusPart={focusPart} fitKey={fitKey} spin={spin} preset={preset} />
        {shown.length > 0 && <CalloutProjector asm={asm} api={api} lift={lift} ids={shown} refs={refs} />}
        <FrameCheck viewId={viewId} asm={asm} api={api} lift={lift} />
      </Canvas>
      {shown.length > 0 && (
        <div className="label-layer">
          <svg className="leaders" aria-hidden="true">
            {shown.map((id) => <g key={id} className={selected === id ? 'sel' : ''}><line ref={(el) => { (refs.current[id] ||= { el: null, line: null, dot: null }).line = el }} /><circle r="2.5" ref={(el) => { (refs.current[id] ||= { el: null, line: null, dot: null }).dot = el }} /></g>)}
          </svg>
          {shown.map((id) => {
            const p = asm.parts.find((q) => q.id === id)!
            const suspect = !!suspects?.includes(p.lib)
            return (
              <button key={id} className={`part-label ${selected === id ? 'sel' : ''} ${suspect ? 'suspect' : ''}`} style={{ visibility: 'hidden' }}
                ref={(el) => { (refs.current[id] ||= { el: null, line: null, dot: null }).el = el }} onClick={() => onSelect?.(id)}>
                <b>{suspect ? 'Fault here · ' : ''}{p.name || partNames[p.lib] || p.lib}</b>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

export { trainFor }
