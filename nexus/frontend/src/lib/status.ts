// One place that turns an asset's state into the short status shown on tags, cards and lists.
import type { AssetDef, AssetState, Channel } from './store'

export type Tone = 'ok' | 'warn' | 'crit' | 'off'
export type Status = { label: string; tone: Tone }

export function assetStatus(a?: AssetState, preview = false): Status {
  if (!a) return { label: '–', tone: 'off' }
  if (a.asset_type === 'conveyor') {
    if (a.conveyor?.sync === 'SYNCHRONISED') return { label: 'Running', tone: 'ok' }
    return { label: 'Stopped', tone: 'off' }
  }
  if (a.data_mode === 'REAL' && a.freshness !== 'LIVE') return a.freshness === 'STALE' ? { label: 'Stale', tone: 'warn' } : { label: 'Offline', tone: 'off' }
  if (a.data_mode === 'REPLAY' && a.freshness !== 'REPLAY') return { label: 'No recording', tone: 'off' }
  if (a.anomaly_state === 'CRITICAL') return { label: 'Fault', tone: 'crit' }
  if (a.anomaly_state === 'WARNING') return { label: 'Fault developing', tone: 'crit' }
  if (a.data_mode === 'SIMULATED' && a.operating_state !== 'RUNNING') return { label: a.operating_state.charAt(0) + a.operating_state.slice(1).toLowerCase(), tone: 'off' }
  return { label: 'Running', tone: 'ok' }
}

/** True when the asset's readings can be shown as current values. */
export const hasLiveData = (a?: AssetState) => !!a && a.asset_type !== 'conveyor' && !(a.data_mode === 'REAL' && a.freshness !== 'LIVE') && a.last_seen !== null

/**
 * Margin to limit: how much of the distance between the normal value and the warning limit is
 * still unused by the signal closest to its limit (100% = at normal, 0% = at the limit).
 * A plain, documented calculation from configured limits. It is not a validated health score,
 * and it is undefined when no limits are configured.
 */
export function marginToLimit(def: AssetDef | undefined, a: AssetState | undefined): { pct: number; channel: Channel } | null {
  if (!def || !a || !hasLiveData(a) || (a.data_mode === 'SIMULATED' && a.operating_state !== 'RUNNING')) return null
  let worst: { pct: number; channel: Channel } | null = null
  for (const ch of def.channels) {
    const v = a.telemetry[ch.key]
    if (ch.warn === null || ch.nominal === null || typeof v !== 'number' || ch.warn === ch.nominal) continue
    const used = (v - ch.nominal) / (ch.warn - ch.nominal)
    const pct = Math.max(0, Math.min(100, (1 - used) * 100))
    if (!worst || pct < worst.pct) worst = { pct, channel: ch }
  }
  return worst
}

/** Chip text for one reading, from the asset's active faults. */
export function readingStatus(a: AssetState | undefined, key: string): Status {
  if (!a || !hasLiveData(a)) return { label: 'No data', tone: 'off' }
  if (a.telemetry[key] === undefined) return { label: a.sensor_status[key] === 'INVALID' ? 'Invalid' : 'No data', tone: 'off' }
  const fault = a.active_faults.find((f) => f.channel === key)
  if (!fault) return { label: 'Normal', tone: 'ok' }
  const low = /below|less/.test(fault.basis) || fault.basis.includes('<')
  return fault.level === 'CRITICAL' ? { label: 'Exceeded', tone: 'crit' } : { label: low ? 'Low' : 'High', tone: 'warn' }
}
