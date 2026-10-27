// Live state shared by the whole app.
// The backend is authoritative: this file only mirrors what it sends.
import { useSyncExternalStore } from 'react'

export type Fault = { channel: string; label: string; level: 'WARNING' | 'CRITICAL'; since: number; value: number; basis: string }
export type AssetState = {
  asset_id: string; asset_type: string; asset_name: string; data_mode: 'REAL' | 'SIMULATED' | 'REPLAY'
  timestamp: number | null; last_seen: number | null; age_s: number | null; freshness: string
  telemetry: Record<string, number>; sensor_status: Record<string, string>; data_quality: string[]
  anomaly_state: 'NORMAL' | 'WARNING' | 'CRITICAL'; active_faults: Fault[]; operating_state: string
  simulation: any | null; simulation_scenario_id: string | null; conveyor?: any
}
export type Channel = { key: string; label: string; unit: string; kind: string; nominal: number | null; warn: number | null; crit: number | null; direction: string; limit_source: string | null; calibration?: string }
export type AssetDef = { asset_id: string; name: string; type: string; data_mode: string; position: number[]; rotation: number; description: string; channels: Channel[]; scenarios: any[]; model_fidelity: string; manufacturer: string | null; model_number: string | null; device_id?: string; driven_by?: string; replay_source?: any }
type Point = { t: number; v: Record<string, number> }
type Toast = { id: number; kind: 'info' | 'ok' | 'error'; text: string }
type Auth = { token: string; user: string; role: 'operator' | 'admin'; expires: number } | null

type State = {
  connection: 'connecting' | 'open' | 'reconnecting'
  snapshot: { ts: number; assets: AssetState[]; link: any; pending_commands: number } | null
  site: { site: any; assets: AssetDef[]; components: Record<string, any>; component_note: string; scenarios: any[]; work_order_flow: Record<string, string[]> } | null
  siteError: string | null
  auth: Auth
  toasts: Toast[]
  loginOpen: boolean
  tick: number
}

const BUFFER = 900
/** No sign-in: the backend gives requests from this computer full access. */
const LOCAL: Auth = { token: '', user: 'local user', role: 'admin', expires: Number.MAX_SAFE_INTEGER }
let state: State = {
  connection: 'connecting', snapshot: null, site: null, siteError: null, toasts: [], loginOpen: false, tick: 0,
  auth: LOCAL,
}
const listeners = new Set<() => void>()
// Mutable mirrors read inside the 3D render loop, so the scene never re-renders through React on each tick.
export const live: { assets: Record<string, AssetState> } = { assets: {} }
export const buffers: Record<string, Point[]> = {}

function set(patch: Partial<State>) { state = { ...state, ...patch }; listeners.forEach((l) => l()) }
export function useStore<T>(selector: (s: State) => T): T {
  return useSyncExternalStore((cb) => { listeners.add(cb); return () => listeners.delete(cb) }, () => selector(state))
}
export const getState = () => state

export function toast(text: string, kind: Toast['kind'] = 'info') {
  const id = Date.now() + Math.random()
  set({ toasts: [...state.toasts, { id, kind, text }] })
  setTimeout(() => set({ toasts: state.toasts.filter((t) => t.id !== id) }), kind === 'error' ? 7000 : 4200)
}
export function setAuth(auth: Auth) {
  if (auth) sessionStorage.setItem('nexus-auth', JSON.stringify(auth)); else sessionStorage.removeItem('nexus-auth')
  set({ auth })
}
export const openLogin = (_open = true) => {}       // sign-in is not used

export async function api(path: string, options: { method?: string; body?: any } = {}) {
  const headers: Record<string, string> = {}
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (state.auth?.token) headers.Authorization = `Bearer ${state.auth.token}`
  let res: Response
  try {
    res = await fetch(`/api${path}`, { method: options.method || 'GET', headers, body: options.body !== undefined ? JSON.stringify(options.body) : undefined })
  } catch { throw new Error('The backend is not reachable.') }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try { const j = await res.json(); detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail) } catch { /* not JSON */ }
    throw new Error(detail)
  }
  return res.json()
}
/** Run an action and report failures to the user instead of swallowing them. */
export async function act<T>(fn: () => Promise<T>, okText?: string): Promise<T | undefined> {
  try { const r = await fn(); if (okText) toast(okText, 'ok'); return r } catch (e: any) { toast(e.message || String(e), 'error'); return undefined }
}

export async function loadSite() {
  try { set({ site: await api('/site'), siteError: null }) } catch (e: any) { set({ siteError: e.message }) }
}

let socket: WebSocket | null = null
let retry = 0
let started = false
export function startStream() {
  if (started) return
  started = true
  const open = () => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    socket = new WebSocket(`${proto}://${location.host}/ws`)
    socket.onopen = () => { retry = 0; set({ connection: 'open' }); if (!state.site) loadSite() }
    socket.onmessage = (ev) => {
      const snap = JSON.parse(ev.data)
      if (snap.type !== 'snapshot') return
      for (const a of snap.assets as AssetState[]) {
        const prev = live.assets[a.asset_id]
        live.assets[a.asset_id] = a
        // only a genuinely new sample extends the chart; stale data adds nothing
        if (a.timestamp && (!prev || prev.timestamp !== a.timestamp)) {
          const buf = (buffers[a.asset_id] ||= [])
          buf.push({ t: a.timestamp, v: a.telemetry })
          if (buf.length > BUFFER) buf.splice(0, buf.length - BUFFER)
        }
      }
      set({ snapshot: snap, tick: state.tick + 1 })
    }
    socket.onclose = () => {
      set({ connection: 'reconnecting' })
      retry = Math.min(retry + 1, 6)
      setTimeout(open, 500 * 2 ** retry)
    }
    socket.onerror = () => socket?.close()
  }
  loadSite()
  open()
}

export const useAsset = (id?: string) => useStore((s) => s.snapshot?.assets.find((a) => a.asset_id === id))
export const useDef = (id?: string) => useStore((s) => s.site?.assets.find((a) => a.asset_id === id))
export const canOperate = (a: Auth) => !!a
export const isAdmin = (a: Auth) => a?.role === 'admin'

export const fmtTime = (ts?: number | null) => (ts ? new Date(ts * 1000).toLocaleTimeString([], { hour12: false }) : '–')
export const fmtDateTime = (ts?: number | null) => (ts ? new Date(ts * 1000).toLocaleString([], { hour12: false, year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '–')
export function fmtNum(v: number | null | undefined, unit = '') {
  if (v === null || v === undefined || Number.isNaN(v)) return '–'
  const a = Math.abs(v)
  const d = a >= 1000 ? 0 : a >= 100 ? 1 : a >= 10 ? 2 : a >= 1 ? 2 : 3
  return v.toFixed(d) + (unit ? ` ${unit}` : '')
}
export const fmtAge = (s?: number | null) => (s === null || s === undefined ? '–' : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`)
