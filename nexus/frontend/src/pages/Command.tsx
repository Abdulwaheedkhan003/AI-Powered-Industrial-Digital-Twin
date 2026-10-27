// Command centre: the whole plant on one page, top to bottom.
//   1. the plant floor in 3D      2. one card per machine
//   3. the selected machine in 3D with its live readings
//   4. trend and recent events
// A machine that develops a fault glows red; opening it starts the fault autopsy.
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useLivePoints } from '../components/domain'
import LineChart, { SERIES_COLORS } from '../components/LineChart'
import { Icon, Seg, useFetch } from '../components/ui'
import { Tone, assetStatus, hasLiveData, readingStatus } from '../lib/status'
import { AssetDef, AssetState, act, api, buffers, fmtNum, fmtTime, useAsset, useStore } from '../lib/store'
import { getAssembly } from '../three/assemblies'
import { FactoryView, MachineView, SceneBoundary, webglAvailable } from '../three/scenes'
import { useThumbs } from '../three/thumbs'

const isFault = (a?: AssetState) => !!a && a.anomaly_state !== 'NORMAL'
const tidy = (s: string) => { const t = s.replace(/simulated\s+/gi, '').trim(); return t.charAt(0).toUpperCase() + t.slice(1) }
const Thumb = ({ src }: { src?: string }) => (src ? <img src={src} alt="" /> : <span className="thumb-ph" />)
const No3D = () => <div className="empty small" style={{ height: '100%' }}><Icon name="layers" /><b>3D view unavailable</b><span>This browser cannot run WebGL.</span></div>

function Spark({ id, channel, tone }: { id: string; channel?: string; tone: Tone }) {
  useStore((s) => s.tick)
  const pts = channel ? (buffers[id] || []).slice(-60).map((p) => p.v[channel]).filter((v) => typeof v === 'number') : []
  if (pts.length < 3) return <svg className="spark" aria-hidden="true" />
  const lo = Math.min(...pts), hi = Math.max(...pts), span = hi - lo || 1
  const d = pts.map((v, i) => `${((i / (pts.length - 1)) * 60 + 1).toFixed(1)},${(20 - ((v - lo) / span) * 18).toFixed(1)}`).join(' ')
  return <svg className={`spark tone-${tone}`} viewBox="0 0 62 22" aria-hidden="true"><polyline points={d} stroke="currentColor" /></svg>
}

/** The headline number of a machine: its speed where it has one. */
function headline(d: AssetDef, a?: AssetState) {
  if (d.type === 'conveyor') return a?.conveyor?.belt_speed_m_s != null ? `${fmtNum(a.conveyor.belt_speed_m_s)} m/s` : '–'
  const ch = d.channels.find((c) => c.kind === 'rpm' && a?.telemetry[c.key] !== undefined) || d.channels.find((c) => a?.telemetry[c.key] !== undefined)
  return ch && a && hasLiveData(a) ? `${fmtNum(a.telemetry[ch.key])} ${ch.unit}` : '–'
}

// ------------------------------------------------------------------ 1. plant floor
function Floor({ defs, assetId, onSelect, onAutopsy }: { defs: AssetDef[]; assetId: string; onSelect: (id: string) => void; onAutopsy: (id: string) => void }) {
  const assets = useStore((s) => s.snapshot?.assets) || []
  const gl = useMemo(webglAvailable, [])
  const [view, setView] = useState<'iso' | 'top'>('iso')
  const [resetKey, setResetKey] = useState(0)
  const faulted = defs.filter((d) => isFault(assets.find((a) => a.asset_id === d.asset_id)))
  const running = defs.filter((d) => assetStatus(assets.find((a) => a.asset_id === d.asset_id)).tone === 'ok').length
  return (
    <section className={`floor ${faulted.length ? 'alarm' : ''}`}>
      {gl ? <SceneBoundary fallback={<No3D />}><FactoryView selected={assetId} onSelect={onSelect} view={view} resetKey={resetKey} focusOnSelect={false} /></SceneBoundary> : <No3D />}
      <div className="floor-top">
        <div className="glass floor-title"><b>Plant floor</b><span><i className="dot ok" />{running} running</span>{faulted.length > 0 && <span className="tone-crit"><i className="dot crit" />{faulted.length} fault</span>}</div>
        <div className="glass" style={{ padding: 2, display: 'flex', gap: 2 }}>
          <Seg label="Camera view" value={view} onChange={(v) => { setView(v); setResetKey((k) => k + 1) }} options={[['iso', '3D'], ['top', 'Top']]} />
          <button className="toggle" style={{ border: 0 }} onClick={() => setResetKey((k) => k + 1)} aria-label="Reset camera"><Icon name="reset" style={{ width: 13, height: 13 }} /></button>
        </div>
      </div>
      {faulted.map((d) => (
        <button key={d.asset_id} className="fault-banner" onClick={() => onAutopsy(d.asset_id)}>
          <Icon name="warn" /><span><b>Fault detected on {d.name}</b><small>Click to open the machine and find out where and why</small></span><span className="go">Run autopsy<Icon name="arrow" /></span>
        </button>
      )).slice(0, 1)}
    </section>
  )
}

// ------------------------------------------------------------------ 2. machine cards
function Cards({ defs, assetId, onSelect, thumbs }: { defs: AssetDef[]; assetId: string; onSelect: (id: string) => void; thumbs: ReturnType<typeof useThumbs> }) {
  const assets = useStore((s) => s.snapshot?.assets) || []
  return (
    <div className="cards">
      {defs.map((d) => {
        const a = assets.find((x) => x.asset_id === d.asset_id)
        const st = assetStatus(a)
        const ch = d.channels.find((c) => c.kind === 'rpm') || d.channels[0]
        return (
          <button key={d.asset_id} className={`mcard ${isFault(a) ? 'fault' : ''}`} aria-pressed={assetId === d.asset_id} onClick={() => onSelect(d.asset_id)}>
            <Thumb src={thumbs.asset(d.type)} />
            <div style={{ minWidth: 0 }}>
              <div className="mcard-name">{d.name}</div>
              <div className="mcard-st"><i className={`dot ${st.tone}`} />{st.label}</div>
              <div className="mcard-row"><span className="mcard-val">{headline(d, a)}</span><Spark id={d.asset_id} channel={ch?.key} tone={st.tone} /></div>
            </div>
          </button>
        )
      })}
    </div>
  )
}

// ------------------------------------------------------------------ 3. selected machine
function Readings({ def, a }: { def: AssetDef; a?: AssetState }) {
  if (def.type === 'conveyor') {
    const c = a?.conveyor
    return (
      <div className="readings">
        <div className="reading"><span className="n">Belt speed</span><b>{c?.belt_speed_m_s != null ? `${fmtNum(c.belt_speed_m_s)} m/s` : '–'}</b></div>
        <div className="reading"><span className="n">Drive speed</span><b>{c?.smoothed_rpm != null ? `${fmtNum(c.smoothed_rpm)} rpm` : '–'}</b></div>
        <div className="reading"><span className="n">Driven by</span><b>{c?.source_asset || '–'}</b></div>
      </div>
    )
  }
  const order = ['rpm', 'vibration', 'temperature', 'current', 'load', 'process', 'environment']
  const seen = new Set<string>()
  const channels = [...def.channels].sort((x, y) => order.indexOf(x.kind) - order.indexOf(y.kind))
    .filter((c) => a?.telemetry[c.key] !== undefined && (c.kind === 'environment' ? false : !seen.has(c.kind) && !!seen.add(c.kind)))
  return (
    <div className="readings">
      {channels.map((c) => {
        const st = readingStatus(a, c.key)
        return (
          <div key={c.key} className={`reading ${st.tone}`}>
            <span className="n">{c.label.replace(/\s*\(.*\)/, '')}</span>
            <Spark id={def.asset_id} channel={c.key} tone={st.tone === 'ok' ? 'off' : st.tone} />
            <b>{fmtNum(a!.telemetry[c.key])}<small> {c.unit}</small></b>
          </div>
        )
      })}
      {channels.length === 0 && <div className="small muted">Waiting for readings…</div>}
    </div>
  )
}

function Stage({ def, a, onAutopsy }: { def: AssetDef; a?: AssetState; onAutopsy: (id: string) => void }) {
  const site = useStore((s) => s.site)
  const gl = useMemo(webglAvailable, [])
  const asm = useMemo(() => getAssembly(def.type, (def as any).drive), [def])
  const [open, setOpen] = useState(0)                 // explosion, 0..100
  const [xray, setXray] = useState(false), [cutaway, setCutaway] = useState(false), [labels, setLabels] = useState(false), [spin, setSpin] = useState(true)
  const [part, setPart] = useState<string | null>(null)
  const [fitKey, setFitKey] = useState(0)
  useEffect(() => { setOpen(0); setPart(null); setXray(false); setCutaway(false); setLabels(false) }, [def.asset_id])
  const fault = isFault(a)
  const dx = useFetch<any>(() => (fault ? api(`/autopsy/${def.asset_id}`) : Promise.resolve(null)), [def.asset_id, fault], fault ? 3000 : 0)
  const top = fault ? dx.data?.hypotheses?.[0] : null
  const suspects = top?.component_id ? [top.component_id] : []
  const names = useMemo(() => Object.fromEntries(Object.entries(site?.components || {}).map(([k, v]: any) => [k, v.name])), [site])
  const p = asm.parts.find((q) => q.id === part)
  const comp = p ? site?.components[p.lib] : null
  const st = assetStatus(a)
  return (
    <section className="stage">
      <div className={`pnl stage-main ${fault ? 'fault' : ''}`}>
        <header className="pnl-h">
          <h2>{def.name}<span className={`pill ${st.tone}`}><i className={`dot ${st.tone}`} />{st.label}</span></h2>
          <span className="small faint">drag to rotate · scroll to zoom · click a part</span>
        </header>
        <div className="stage-view">
          {gl ? <SceneBoundary fallback={<No3D />}>
            <MachineView key={def.asset_id} assetId={def.asset_id} viewId="machine" mode={open > 0 ? 'exploded' : 'assembled'} explode={open / 100} cutaway={cutaway && open === 0} xray={xray} labels={labels}
              selected={part} suspects={suspects} onSelect={setPart} fitKey={fitKey} runWhileExploded showFlow spin={spin ? 0.9 : 0} partNames={names} />
          </SceneBoundary> : <No3D />}
          {fault && <button className="fault-chip" onClick={() => onAutopsy(def.asset_id)}><Icon name="warn" />Fault detected · run autopsy</button>}
        </div>
        <div className="stage-bar">
          <button className={`btn ${open > 0 ? '' : 'primary'}`} onClick={() => { setOpen(open > 0 ? 0 : 100); setCutaway(false) }}><Icon name="layers" />{open > 0 ? 'Assemble' : 'Explode'}</button>
          <label className="slider"><input type="range" min={0} max={100} value={open} onChange={(e) => setOpen(+e.target.value)} aria-label="Explosion distance" /><b>{open}%</b></label>
          <button className="toggle" aria-pressed={labels} onClick={() => setLabels(!labels)}>Labels</button>
          <button className="toggle" aria-pressed={xray} onClick={() => setXray(!xray)}>X-ray</button>
          <button className="toggle" aria-pressed={cutaway && open === 0} disabled={open > 0 || !asm.cutPlanes.length} onClick={() => setCutaway(!cutaway)}>Cutaway</button>
          <button className="toggle" aria-pressed={spin} onClick={() => setSpin(!spin)}>Spin</button>
          <button className="toggle" onClick={() => setFitKey((k) => k + 1)}><Icon name="reset" style={{ width: 13, height: 13 }} />View</button>
        </div>
      </div>
      <aside className="stage-side">
        {fault && (
          <button className="fault-card" onClick={() => onAutopsy(def.asset_id)}>
            <b><Icon name="warn" />{top ? top.title : 'Fault developing'}</b>
            <span>{(a?.active_faults || []).map((f) => f.label.replace(/\s*\(.*\)/, '')).join(' · ') || 'Readings are leaving their normal range'}</span>
            <span className="go">Run fault autopsy<Icon name="arrow" /></span>
          </button>
        )}
        <div className="pnl"><header className="pnl-h"><h2>Live readings</h2><span className="small faint mono">{fmtTime(a?.timestamp)}</span></header><div className="pnl-b"><Readings def={def} a={a} /></div></div>
        <div className="pnl">
          <header className="pnl-h"><h2>{p ? p.name || comp?.name || p.id : 'Component'}</h2>{p && <button className="btn ghost icon sm" aria-label="Clear selection" onClick={() => setPart(null)}><Icon name="x" /></button>}</header>
          <div className="pnl-b cdetail">
            {p ? <>
              {suspects.includes(p.lib) && <p className="tone-crit" style={{ marginBottom: 6 }}><b>The fault is most likely here.</b></p>}
              <p>{comp?.description || 'Part of this machine.'}</p>
              <dl><dt>Motion</dt><dd>{p.state}</dd>{comp?.failure_modes && <><dt>Wears by</dt><dd>{comp.failure_modes.join(', ')}</dd></>}</dl>
            </> : <p className="muted">Click any part of the machine to see what it does. {asm.parts.length} separate parts.</p>}
          </div>
        </div>
      </aside>
    </section>
  )
}

// ------------------------------------------------------------------ 4. trend and events
function Trend({ def, a }: { def: AssetDef; a?: AssetState }) {
  const kinds = ['vibration', 'temperature', 'rpm', 'current']
  const options = kinds.map((k) => def.channels.find((c) => c.kind === k && a?.telemetry[c.key] !== undefined)).filter(Boolean) as AssetDef['channels']
  const [key, setKey] = useState('')
  const ch = options.find((c) => c.key === key) || options[0]
  const points = useLivePoints(def.asset_id, ch?.key || '', 180, false)
  return (
    <div className="pnl">
      <header className="pnl-h"><h2>Trend · {def.name}</h2>{options.length > 1 && <Seg label="Signal" value={ch.key} onChange={setKey} options={options.map((c) => [c.key, c.kind.charAt(0).toUpperCase() + c.kind.slice(1)] as [string, string])} />}</header>
      <div className="pnl-b">{ch ? <LineChart series={[{ id: ch.key, label: ch.label, color: SERIES_COLORS[0], points }]} unit={ch.unit} height={210} warn={ch.warn} crit={ch.crit} empty="Collecting readings…" /> : <div className="empty small">This machine has no sensors of its own; it follows its drive motor.</div>}</div>
    </div>
  )
}

function Events({ defs, onSelect }: { defs: AssetDef[]; onSelect: (id: string) => void }) {
  const events = useFetch<any[]>(() => api('/events?limit=80'), [], 4000)
  const rows = (events.data || []).filter((e) => ['SENSOR_ANOMALY', 'SIMULATED_FAULT', 'RETURN_TO_NORMAL', 'MAINTENANCE'].includes(e.category) && defs.some((d) => d.asset_id === e.asset_id)).slice(0, 7)
  return (
    <div className="pnl">
      <header className="pnl-h"><h2>Recent events</h2></header>
      <div className="pnl-b flush">
        {rows.length === 0 && <div className="empty small">Nothing has happened yet. All machines are running normally.</div>}
        {rows.map((e) => {
          const bad = e.severity === 'CRITICAL' || e.severity === 'WARNING'
          return (
            <button key={e.id} className="ev" onClick={() => e.asset_id && onSelect(e.asset_id)}>
              <Icon name={bad ? 'warn' : 'check'} style={{ width: 15, height: 15, color: bad ? 'var(--crit)' : 'var(--ok)' }} />
              <span>{tidy(e.title)}</span><span className="t">{fmtTime(e.ts)}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ fault autopsy
const STEPS = ['Scan', 'Open', 'Locate', 'Cause', 'Action'] as const
const HOLD = [5200, 3800, 5200, 11000, 0]

function MiniTrend({ points, onset, limit, unit }: { points: [number, number][]; onset?: number; limit?: number | null; unit: string }) {
  if (points.length < 3) return null
  const W = 320, H = 92, x0 = points[0][0], x1 = points[points.length - 1][0]
  const ys = points.map((p) => p[1]).concat(limit != null ? [limit] : [])
  const lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1
  const X = (t: number) => ((t - x0) / (x1 - x0 || 1)) * (W - 8) + 4, Y = (v: number) => H - 8 - ((v - lo) / span) * (H - 20)
  return (
    <svg className="mini" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Signal since the fault began">
      {limit != null && <><line x1="4" x2={W - 4} y1={Y(limit)} y2={Y(limit)} className="lim" /><text x={W - 6} y={Y(limit) - 4} textAnchor="end">limit {fmtNum(limit)} {unit}</text></>}
      {onset && onset > x0 && <><line x1={X(onset)} x2={X(onset)} y1="6" y2={H - 6} className="onset" /><text x={X(onset) + 4} y="14">fault begins</text></>}
      <polyline points={points.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' ')} />
    </svg>
  )
}

function Autopsy({ def, onClose }: { def: AssetDef; onClose: () => void }) {
  const site = useStore((s) => s.site)
  const a = useAsset(def.asset_id)
  const asm = useMemo(() => getAssembly(def.type, (def as any).drive), [def])
  const [step, setStep] = useState(0)
  const [auto, setAuto] = useState(true)
  const dx = useFetch<any>(() => api(`/autopsy/${def.asset_id}`), [def.asset_id], 2500)
  const d = dx.data
  const top = d?.hypotheses?.[0]
  const susPart = asm.parts.find((p) => p.lib === top?.component_id)
  useEffect(() => {
    if (!auto || !HOLD[step] || (step === 1 && !top)) return     // wait in "Open" until the evidence is in
    const t = setTimeout(() => setStep((s) => Math.min(s + 1, STEPS.length - 1)), HOLD[step])
    return () => clearTimeout(t)
  }, [step, auto, !!top])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])
  const go = (n: number) => { setAuto(false); setStep(n) }
  const names = useMemo(() => Object.fromEntries(Object.entries(site?.components || {}).map(([k, v]: any) => [k, v.name])), [site])
  const evidence: any[] = (d?.evidence || []).filter((e: any) => e.kind !== 'environment')
  const moved = evidence.filter((e) => e.direction === 'up' || e.direction === 'down')
  const main = [...moved].sort((x, y) => Math.abs(y.change_pct) - Math.abs(x.change_pct))[0]
  const repair = async () => { if (await act(() => api(`/faults/${def.asset_id}/repair`, { method: 'POST' }), `${def.name} repaired and back in service`)) onClose() }
  const partName = susPart ? susPart.name || names[susPart.lib] || susPart.id : d?.component?.name
  const waiting = <p className="muted">Collecting evidence… the fault is still developing and the picture sharpens every second.</p>
  return (
    <div className="autopsy" role="dialog" aria-modal="true" aria-label={`Fault autopsy of ${def.name}`}>
      <div className="autopsy-view">
        <MachineView assetId={def.asset_id} viewId="autopsy" mode={step >= 1 ? 'exploded' : 'assembled'} explode={step >= 1 ? 1 : 0} xray={step >= 2} scan={step === 0}
          labels={step >= 2} labelIds={susPart ? [susPart.id] : []} selected={null} suspects={step >= 2 && top?.component_id ? [top.component_id] : []}
          focusPart={step >= 2 && susPart ? susPart.id : null} spin={step === 0 ? 5 : step === 1 ? 1.6 : 0.7} runWhileExploded interactive={false} partNames={names} />
        <div className="autopsy-tag"><i className="dot crit" />FAULT AUTOPSY · {def.name}</div>
        <div className="autopsy-caption">{['Scanning every signal against its healthy baseline', 'Opening the machine part by part', partName ? `Fault located: ${partName}` : 'Locating the fault', 'Tracing cause and effect', 'What to do next'][step]}</div>
      </div>
      <div className="autopsy-panel">
        <header>
          <div className="steps">{STEPS.map((s, i) => <button key={s} className={i === step ? 'on' : i < step ? 'done' : ''} onClick={() => go(i)}><i>{i + 1}</i>{s}</button>)}</div>
          <button className="btn ghost icon" aria-label="Close autopsy" onClick={onClose}><Icon name="x" /></button>
        </header>
        <div className="autopsy-body" key={step}>
          {step === 0 && <>
            <h3>What the sensors show</h3>
            <p className="muted">Each reading over the last {d?.window_s || 30} seconds, compared with this machine's healthy baseline.</p>
            <div className="ev-table">
              {evidence.map((e, i) => {
                const dev = e.direction === 'up' || e.direction === 'down'
                return (
                  <div key={e.channel} className={`ev-row ${dev ? 'dev' : ''}`} style={{ animationDelay: `${i * 140}ms` }}>
                    <span>{e.label.replace(/\s*\(.*\)/, '')}</span>
                    <span className="mono">{fmtNum(e.baseline)} → <b>{fmtNum(e.recent)}</b> {e.unit}</span>
                    <span className={`mono chg ${dev ? 'tone-crit' : 'tone-ok'}`}>{e.change_pct > 0 ? '+' : ''}{e.change_pct}%</span>
                    <i className="bar"><i style={{ width: `${Math.min(100, Math.abs(e.change_pct))}%` }} /></i>
                  </div>
                )
              })}
              {evidence.length === 0 && waiting}
            </div>
            {moved.length > 0 && <p><b>{moved.length} of {evidence.length}</b> signals have left their normal range.</p>}
          </>}
          {step === 1 && <>
            <h3>Opening the machine</h3>
            <p className="muted">The casing comes away so each of the {asm.parts.length} parts can be examined on its own.</p>
            <div className="chips">{asm.parts.map((p) => <span key={p.id} className={p.lib === top?.component_id ? 'hot' : ''}>{p.name || names[p.lib] || p.id}</span>)}</div>
            {!top && waiting}
          </>}
          {step === 2 && (top ? <>
            <h3>Where the fault is</h3>
            <div className="located"><Icon name="target" /><div><b>{partName || 'No single part identified'}</b><span>{d?.component?.description}</span></div></div>
            <p>{top.rationale}</p>
            <dl className="facts">
              <dt>Evidence</dt><dd><b>{top.evidence_strength}</b> · {top.supporting.length} signals agree{top.contradicting.length ? `, ${top.contradicting.length} disagree` : ''}</dd>
              <dt>Agreeing signals</dt><dd>{top.supporting.join(' · ') || '–'}</dd>
              {top.contradicting.length > 0 && <><dt>Against</dt><dd>{top.contradicting.join(' · ')}</dd></>}
              <dt>Certainty</dt><dd>Most likely location from the signal pattern. Inspection confirms it.</dd>
            </dl>
          </> : <><h3>Where the fault is</h3>{waiting}</>)}
          {step === 3 && (top && d?.mechanism ? <>
            <h3>Why it happened</h3>
            <p className="lead">{d.mechanism.headline}.</p>
            <ol className="chain">{d.mechanism.chain.map(([t, text]: [string, string], i: number) => <li key={t} style={{ animationDelay: `${i * 900}ms` }}><b>{t}</b><span>{text}</span></li>)}</ol>
            {main && <><div className="caps">{main.label.replace(/\s*\(.*\)/, '')} since the fault began</div><MiniTrend points={d.series?.[main.channel] || []} onset={d.onset_ts} limit={main.limit_warn} unit={main.unit} /></>}
            <div className="caps">What usually starts it</div>
            <ul className="causes">{d.mechanism.root_causes.map((c: string) => <li key={c}>{c}</li>)}</ul>
            <p className="muted"><b>If nothing is done:</b> {d.mechanism.if_ignored}</p>
          </> : <><h3>Why it happened</h3>{waiting}</>)}
          {step === 4 && <>
            <h3>What to do</h3>
            {top ? <>
              <ol className="todo">{(d.recommended_next_steps || []).slice(0, 4).map((s: string) => <li key={s}>{s}</li>)}</ol>
              <div className="caps">Rule these out too</div>
              <ul className="causes">{top.alternatives.map((s: string) => <li key={s}>{s}</li>)}</ul>
            </> : waiting}
            <dl className="facts">
              <dt>Fault began</dt><dd className="mono">{fmtTime(d?.onset_ts)}</dd>
              {d?.detected_ts && <><dt>Detected</dt><dd className="mono">{fmtTime(d.detected_ts)} · {Math.max(0, Math.round(d.detected_ts - d.onset_ts))} s after it began</dd></>}
              <dt>State now</dt><dd>{assetStatus(a).label}</dd>
            </dl>
          </>}
        </div>
        <footer>
          <button className="btn" disabled={step === 0} onClick={() => go(step - 1)}>Back</button>
          {step < STEPS.length - 1 ? <button className="btn primary" onClick={() => go(step + 1)}>Next<Icon name="arrow" /></button>
            : def.data_mode === 'SIMULATED' ? <button className="btn primary" onClick={repair}><Icon name="wrench" />Mark as repaired</button> : <button className="btn primary" onClick={onClose}>Done</button>}
          <button className="btn ghost" onClick={() => { setAuto(true); setStep(0) }}><Icon name="reset" />Replay</button>
        </footer>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ page
export default function Command() {
  const site = useStore((s) => s.site)
  const assets = useStore((s) => s.snapshot?.assets) || []
  const [params, setParams] = useSearchParams()
  const defs = useMemo(() => (site?.assets || []).filter((d) => d.data_mode !== 'REPLAY'), [site])
  const thumbs = useThumbs(useMemo(() => defs.map((d) => ({ type: d.type, drive: (d as any).drive })), [defs]))
  const [autopsy, setAutopsy] = useState<string | null>(null)
  const assetId = defs.find((d) => d.asset_id === params.get('asset'))?.asset_id || defs[0]?.asset_id || ''
  const def = defs.find((d) => d.asset_id === assetId)
  const a = assets.find((x) => x.asset_id === assetId)
  useEffect(() => { if (params.get('autopsy') && def) { setAutopsy(def.asset_id); const p = new URLSearchParams(params); p.delete('autopsy'); setParams(p, { replace: true }) } }, [params, def])   // eslint-disable-line react-hooks/exhaustive-deps
  const select = (id: string) => {
    setParams({ asset: id }, { replace: true })
    if (isFault(assets.find((x) => x.asset_id === id))) setAutopsy(id)        // a faulted machine opens straight into its autopsy
  }
  const openAutopsy = (id: string) => { setParams({ asset: id }, { replace: true }); setAutopsy(id) }
  const autopsyDef = defs.find((d) => d.asset_id === autopsy)
  if (!site || !def) return null
  return (
    <div className="cc">
      <Floor defs={defs} assetId={assetId} onSelect={select} onAutopsy={openAutopsy} />
      <Cards defs={defs} assetId={assetId} onSelect={select} thumbs={thumbs} />
      <Stage def={def} a={a} onAutopsy={openAutopsy} />
      <div className="row2"><Trend def={def} a={a} /><Events defs={defs} onSelect={select} /></div>
      {autopsyDef && <Autopsy key={autopsyDef.asset_id} def={autopsyDef} onClose={() => setAutopsy(null)} />}
    </div>
  )
}
