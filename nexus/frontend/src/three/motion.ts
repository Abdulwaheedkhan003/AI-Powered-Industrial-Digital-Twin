// Motion state shared by every 3D view.
//
// Each machine has one "train": the angle of its master shaft, advanced in real time from
// the speed reading in the backend's state. Every view (factory, machine, exploded, conveyor)
// reads the same train, so they always agree. Nothing here sends anything to a device:
// pause, reset and playback speed are purely visual.
import { useEffect, useState, useSyncExternalStore } from 'react'
import { AssetDef, getState, live } from '../lib/store'
import { getAssembly } from './assemblies'
import { approach, rpmToRad } from './kinematics'

export type Source = 'LIVE' | 'SIMULATED' | 'REPLAY' | 'PREVIEW' | 'OFFLINE' | 'NO SPEED SIGNAL'
export type Train = {
  id: string
  theta: number        // displayed master-shaft angle, rad
  omega: number        // displayed angular speed, rad/s
  trueRpm: number      // smoothed true speed used for the animation
  hasSignal: boolean
  strobe: number       // display slow-down factor (1 = true speed)
  source: Source
  fault: number        // 0..1 vibration cue: how hard the machine shakes
  alarm: number        // 0..1 smoothed fault level, drives the red glow
  maxRatio: number
}

/** Speed used for the clearly labelled visual preview of the physical drive when no live RPM exists. */
export const PREVIEW_RPM = 90
/** The fastest element of a machine is drawn at about this speed, so motion is brisk but readable. */
const DISPLAY_RPM = 170
const systemReduced = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
const urlReduced = () => typeof location !== 'undefined' && new URLSearchParams(location.search).get('motion') === 'reduced'

export const control = {
  playback: 1,                               // visual playback speed
  paused: systemReduced() || urlReduced(),   // continuous motion starts paused when reduced motion is requested
  preview: false,                            // labelled preview motion for the physical drive
  reduced: systemReduced() || urlReduced(),  // transitions become instant
}
let version = 0
const listeners = new Set<() => void>()
export function setControl(patch: Partial<typeof control>) {
  Object.assign(control, patch)
  version++
  listeners.forEach((l) => l())
}
export function useControl() {
  useSyncExternalStore((cb) => { listeners.add(cb); return () => listeners.delete(cb) }, () => version)
  return control
}

const trains: Record<string, Train> = {}
function newTrain(def: AssetDef, all: AssetDef[]): Train {
  const sharing = [def, ...all.filter((a) => a.driven_by === def.asset_id)]
  const maxRatio = Math.max(...sharing.map((a) => getAssembly(a.type, (a as any).drive).maxRatio), 1)
  const nominal = def.channels.find((c) => c.key === 'rpm')?.nominal || 0
  return { id: def.asset_id, theta: 0, omega: 0, trueRpm: 0, hasSignal: false, strobe: Math.min(1, DISPLAY_RPM / Math.max(1, (nominal || 120) * maxRatio)), source: 'OFFLINE', fault: 0, alarm: 0, maxRatio }
}

let last = 0
/** Advance every train to `now`. Safe to call from several render loops: time is only ever counted once. */
export function advance(now = performance.now()) {
  const dt = last ? Math.min(0.1, (now - last) / 1000) : 0
  if (last && dt <= 0) return
  last = now
  const site = getState().site
  if (!site) return
  for (const def of site.assets) {
    if (def.driven_by || def.channels.length === 0) continue
    const t = (trains[def.asset_id] ||= newTrain(def, site.assets))
    const a = live.assets[def.asset_id]
    let rpm: number | null = null
    let source: Source = 'OFFLINE'
    if (a) {
      const v = a.telemetry?.rpm
      if (a.data_mode === 'SIMULATED') { rpm = typeof v === 'number' ? v : 0; source = 'SIMULATED' }
      else if (a.data_mode === 'REPLAY') { if (typeof v === 'number') { rpm = v; source = 'REPLAY' } else source = 'NO SPEED SIGNAL' }
      else if (a.freshness === 'LIVE' && typeof v === 'number' && a.sensor_status?.rpm === 'OK') { rpm = v; source = 'LIVE' }
      else source = a.freshness === 'LIVE' ? 'NO SPEED SIGNAL' : 'OFFLINE'      // never animate from stale or missing data
    }
    if (rpm === null && control.preview && def.data_mode === 'REAL') { rpm = PREVIEW_RPM; source = 'PREVIEW' }
    t.source = source
    t.hasSignal = rpm !== null
    t.trueRpm = approach(t.trueRpm, rpm ?? 0, dt, 0.7)            // smooth start-up, shut-down and 1 Hz telemetry steps
    if (!rpm && Math.abs(t.trueRpm) < 0.02) t.trueRpm = 0
    t.omega = rpmToRad(t.trueRpm) * t.strobe * control.playback
    if (!control.paused) t.theta += t.omega * dt

    // Simulated mechanical faults get a deliberate, scenario-driven cue: rotor run-out that grows
    // with the simulated vibration. It is an illustration of the scenario, not a measurement.
    let cue = 0
    const ch = def.channels.find((c) => c.key === 'vibration_mm_s')
    const vib = a?.telemetry?.vibration_mm_s
    if (ch?.nominal && typeof vib === 'number') cue = Math.min(1, Math.max(0, (vib / ch.nominal - 1.3) / 3))
    t.fault = approach(t.fault, cue, dt, 0.8)
    t.alarm = approach(t.alarm, a?.anomaly_state === 'CRITICAL' ? 1 : a?.anomaly_state === 'WARNING' ? 0.65 : 0, dt, 0.5)
  }
}

const IDLE: Train = { id: '', theta: 0, omega: 0, trueRpm: 0, hasSignal: false, strobe: 1, source: 'OFFLINE', fault: 0, alarm: 0, maxRatio: 1 }
/** The train that moves this asset. A driven asset (the conveyor) uses its driver's train. */
export const trainFor = (def: { asset_id: string; driven_by?: string }): Train => trains[def.driven_by || def.asset_id] || IDLE
export function resetPhase(id?: string) {
  for (const t of Object.values(trains)) if (!id || t.id === id) t.theta = 0
}
export const allTrains = () => trains

/** Train readout for the interface, refreshed a few times per second. */
export function useTrain(def?: { asset_id: string; driven_by?: string }) {
  const [snap, setSnap] = useState<Train>(IDLE)
  useEffect(() => {
    if (!def) return
    const read = () => setSnap({ ...trainFor(def) })
    read()
    const timer = setInterval(read, 300)
    return () => clearInterval(timer)
  }, [def?.asset_id, def?.driven_by])   // eslint-disable-line react-hooks/exhaustive-deps
  return snap
}
