// Shared interface pieces. Pages compose these instead of styling things themselves.
import { ReactNode, useEffect, useRef, useState } from 'react'
import { AssetState, fmtAge, fmtNum } from '../lib/store'

const paths: Record<string, string> = {
  overview: 'M3 12h7V3H3zM14 21h7v-9h-7zM14 3v6h7V3zM3 21h7v-6H3z',
  twin: 'M12 2 3 7v10l9 5 9-5V7zM3 7l9 5 9-5M12 12v10',
  assets: 'M4 6h16M4 12h16M4 18h16',
  monitor: 'M3 12h4l3-8 4 16 3-8h4',
  diag: 'M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3',
  sim: 'M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8',
  wrench: 'M14.7 6.3a4 4 0 0 0 5 5L11 20a2.1 2.1 0 0 1-3-3z M16 5l3 3',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  events: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  health: 'M20.8 5.6a5 5 0 0 0-7.1 0L12 7.3l-1.7-1.7a5 5 0 0 0-7.1 7.1L12 21.5l8.8-8.8a5 5 0 0 0 0-7.1z',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 2.9-1.2V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0 1.2 2.9h.1a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  bell: 'M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  play: 'M6 4l14 8-14 8z', pause: 'M7 4v16M17 4v16', stop: 'M6 6h12v12H6z', reset: 'M3 12a9 9 0 1 0 3-6.7M3 4v5h5',
  check: 'M5 12l5 5 9-11', x: 'M6 6l12 12M18 6 6 18', warn: 'M12 3 2 20h20zM12 10v5M12 18h.01',
  chip: 'M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3M6 6h12v12H6z',
  download: 'M12 3v12M7 10l5 5 5-5M4 21h16', link: 'M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1',
  layers: 'M12 3 2 8l10 5 10-5zM2 13l10 5 10-5M2 18l10 5 10-5', target: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  expand: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5', menu: 'M4 7h16M4 12h16M4 17h16', shield: 'M12 3 4 6v6c0 5 3.4 8 8 9 4.6-1 8-4 8-9V6z',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3', arrow: 'M5 12h14M13 6l6 6-6 6', inbox: 'M3 13h5l1 3h6l1-3h5M5 5h14l2 8v6H3v-6z',
}
export function Icon({ name, ...rest }: { name: string } & React.SVGProps<SVGSVGElement>) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...rest}><path d={paths[name] || paths.overview} /></svg>
}

/** Data-source label. Shape differs per mode (solid / dashed / dotted), not just colour. */
/** Data-source chips are no longer shown. */
export function ModeChip(_: { mode: string }) {
  return null
}

export function StateChip({ state }: { state: string }) {
  const map: Record<string, [string, string]> = {
    NORMAL: ['ok', 'check'], WARNING: ['warn', 'warn'], CRITICAL: ['crit', 'warn'], LIVE: ['real', 'check'], STALE: ['warn', 'warn'], OFFLINE: ['off', 'x'],
    'SYNC UNAVAILABLE': ['off', 'x'], SYNCHRONISED: ['real', 'check'], 'NO RECORDING': ['off', 'x'],
  }
  const [cls, icon] = map[state] || ['', 'check']
  return <span className={`chip ${cls}`}><Icon name={icon} />{state}</span>
}
/** One chip describing whether an asset's data can be trusted right now. */
export function Freshness({ a }: { a?: AssetState }) {
  if (!a) return <span className="chip off">–</span>
  if (a.data_mode === 'REAL' && a.asset_type !== 'conveyor') {
    if (a.freshness === 'LIVE') return <span className="chip real"><span className="dot live" />LIVE</span>
    return <span className={`chip ${a.freshness === 'STALE' ? 'warn' : 'off'}`}><Icon name={a.freshness === 'STALE' ? 'warn' : 'x'} />{a.freshness}{a.age_s !== null ? ` · ${fmtAge(a.age_s)}` : ''}</span>
  }
  // simulated and replayed assets have no connection state of their own; only show exceptions
  return a.freshness === a.data_mode ? null : <StateChip state={a.freshness} />
}
export const levelClass = (s?: string) => (s === 'CRITICAL' ? 'crit' : s === 'WARNING' ? 'warn' : 'ok')

export function Panel({ title, actions, children, tight, className = '' }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; tight?: boolean; className?: string }) {
  return (
    <section className={`panel ${className}`}>
      {title && <header className="panel-head"><h2>{title}</h2>{actions}</header>}
      <div className={`panel-body ${tight ? 'tight' : ''}`}>{children}</div>
    </section>
  )
}
export function Empty({ icon = 'inbox', title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return <div className="empty"><Icon name={icon} /><b>{title}</b>{children && <span className="small">{children}</span>}</div>
}
export function Note({ kind = '', children }: { kind?: string; children: ReactNode }) {
  return <div className={`note ${kind}`} role="note">{children}</div>
}
export function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return <div className="seg" role="group" aria-label={label}>{options.map(([v, l]) => <button key={v} aria-pressed={value === v} onClick={() => onChange(v)}>{l}</button>)}</div>
}

/** A telemetry value that briefly highlights when it changes. Stale values are dimmed and labelled. */
export function Tele({ label, value, unit, level, stale, status }: { label: string; value?: number | null; unit: string; level?: string; stale?: boolean; status?: string }) {
  const ref = useRef<HTMLSpanElement>(null)
  const last = useRef(value)
  useEffect(() => {
    if (value !== last.current && ref.current && !stale) { ref.current.classList.remove('flash'); void ref.current.offsetWidth; ref.current.classList.add('flash') }
    last.current = value
  }, [value, stale])
  const missing = value === null || value === undefined
  return (
    <div className={`tele ${stale ? 'stale' : ''} ${level === 'CRITICAL' ? 'crit' : level === 'WARNING' ? 'warn' : ''}`}>
      <div className="label" title={label}>{label}</div>
      <div className="v"><span ref={ref}>{missing ? '–' : fmtNum(value)}</span><small>{missing ? (status === 'INVALID' ? 'invalid reading' : 'no data') : unit}</small></div>
      {stale && !missing && <div className="small faint">last valid value</div>}
    </div>
  )
}

export function useFetch<T>(loader: () => Promise<T>, deps: any[], refreshMs = 0): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [n, setN] = useState(0)
  useEffect(() => {
    let alive = true
    loader().then((d) => { if (alive) { setData(d); setError(null); setLoading(false) } }).catch((e) => { if (alive) { setError(e.message); setLoading(false) } })
    const timer = refreshMs ? setInterval(() => setN((x) => x + 1), refreshMs) : undefined
    return () => { alive = false; if (timer) clearInterval(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n])
  return { data, error, loading, reload: () => setN((x) => x + 1) }
}
export function Loading({ rows = 3 }: { rows?: number }) {
  return <div className="stack" aria-busy="true">{Array.from({ length: rows }, (_, i) => <div key={i} className="skeleton" style={{ height: 22, width: `${90 - i * 12}%` }} />)}</div>
}
export function ErrorNote({ error }: { error: string }) {
  return <Note kind="crit"><b>Could not load this.</b> {error}</Note>
}
