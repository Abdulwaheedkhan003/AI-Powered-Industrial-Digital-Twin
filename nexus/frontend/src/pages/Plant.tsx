// Asset inventory and Live Monitoring. (Overview, the digital twin and asset inspection now live on the Command Centre.)
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AssetPicker, SourceNote, isStale, useLivePoints } from '../components/domain'
import LineChart, { SERIES_COLORS, Series } from '../components/LineChart'
import { Empty, ErrorNote, Freshness, Icon, Loading, ModeChip, Note, Panel, Seg, StateChip, useFetch } from '../components/ui'
import { hasLiveData } from '../lib/status'
import { api, fmtAge, fmtDateTime, fmtNum, fmtTime, useAsset, useDef, useStore, BACKEND_URL } from '../lib/store'

// ------------------------------------------------------------------ Assets
export function Assets() {
  const site = useStore((s) => s.site), snap = useStore((s) => s.snapshot)
  const [params, setParams] = useSearchParams()
  const mode = params.get('mode') || 'all'
  const nav = useNavigate()
  if (!site || !snap) return <Loading rows={6} />
  const rows = site.assets.filter((d) => mode === 'all' || d.data_mode === mode)
  return (
    <div className="page">
      <div className="page-head"><div className="grow"><h1>Asset inventory</h1><p>Assets are defined in a configuration file, not in the interface code. Each one states where its data comes from.</p></div>
        <Seg label="Data mode" value={mode as any} onChange={(v) => setParams(v === 'all' ? {} : { mode: v })} options={[['all', 'All'], ['REAL', 'Real'], ['SIMULATED', 'Simulated'], ['REPLAY', 'Replay']]} /></div>
      <Panel tight title={`${rows.length} asset${rows.length === 1 ? '' : 's'}`}>
        <div className="scroll-x"><table className="table">
          <thead><tr><th>Asset ID</th><th>Name</th><th>Type</th><th>Data source</th><th>Status</th><th>Key readings</th><th>Last sample</th><th>3D model</th></tr></thead>
          <tbody>{rows.map((d) => {
            const a = snap.assets.find((x) => x.asset_id === d.asset_id)!
            const stale = isStale(a)
            return (
              <tr key={d.asset_id} className="clickable" onClick={() => nav(`/app/command?asset=${d.asset_id}`)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && nav(`/app/command?asset=${d.asset_id}`)}>
                <td className="mono">{d.asset_id}</td><td>{d.name}</td><td className="muted">{d.type.replace('_', ' ')}</td><td><ModeChip mode={d.data_mode} /></td>
                <td><div className="row"><Freshness a={a} />{hasLiveData(a) && <StateChip state={a.anomaly_state} />}</div></td>
                <td className="mono small">{d.type === 'conveyor' ? (a.conveyor.smoothed_rpm !== null ? `${fmtNum(a.conveyor.belt_speed_m_s, 'm/s')}` : 'sync unavailable') : a.last_seen === null ? 'no data' : d.channels.slice(0, 3).filter((c) => a.telemetry[c.key] !== undefined).map((c) => `${fmtNum(a.telemetry[c.key])} ${c.unit}`).join(' · ')}{stale && a.last_seen !== null ? ' (last valid)' : ''}</td>
                <td className="mono small faint">{a.last_seen ? `${fmtTime(a.last_seen)} · ${fmtAge(a.age_s)} ago` : '–'}</td>
                <td className="small faint">{d.model_fidelity}</td>
              </tr>
            )
          })}</tbody>
        </table></div>
      </Panel>
    </div>
  )
}

// ------------------------------------------------------------------ Live monitoring
const RANGES: [string, string, number][] = [['live', 'Live · 5 min', 5], ['15', '15 min', 15], ['60', '1 hour', 60], ['360', '6 hours', 360]]

export function Monitoring() {
  const [params, setParams] = useSearchParams()
  const site = useStore((s) => s.site)
  const id = params.get('asset') || 'MOTOR-001'
  const def = useDef(id), a = useAsset(id)
  const [key, setKey] = useState('')
  const [range, setRange] = useState('live')
  const [paused, setPaused] = useState(false)
  const [compare, setCompare] = useState(false)
  const [table, setTable] = useState(false)
  const ch = def?.channels.find((c) => c.key === key) || def?.channels[0]
  useEffect(() => { setKey('') }, [id])
  const minutes = RANGES.find((r) => r[0] === range)![2]
  const livePts = useLivePoints(id, ch?.key || '', 300, paused)
  const hist = useFetch<any>(() => (range === 'live' ? Promise.resolve(null) : api(`/assets/${id}/history?minutes=${minutes}`)), [id, range], range === 'live' || paused ? 0 : 10000)
  const prev = useFetch<any>(() => (compare && range !== 'live' ? api(`/assets/${id}/history?minutes=${minutes}&until=${Date.now() / 1000 - minutes * 60}`) : Promise.resolve(null)), [id, range, compare])
  if (!site || !def || !a || !ch) return <Loading rows={6} />
  const toPts = (rows: any[]) => rows.filter((r) => typeof r.vals[ch.key] === 'number').map((r) => [r.ts, r.vals[ch.key]] as [number, number])
  const pts = range === 'live' ? livePts : toPts(hist.data?.rows || [])
  const values = pts.map((p) => p[1])
  const gaps = pts.slice(1).map((p, i) => p[0] - pts[i][0])
  const typical = [...gaps].sort((x, y) => x - y)[Math.floor(gaps.length / 2)] || 0
  const missing = gaps.filter((g) => typical > 0 && g > typical * 3).length
  let series: Series[] = [{ id: 'now', label: compare ? 'Selected period' : ch.label, color: SERIES_COLORS[0], points: pts }]
  let xLabel: 'time' | 'seconds' = 'time'
  if (compare && range !== 'live' && prev.data) {
    const before = toPts(prev.data.rows)
    const t0 = pts[0]?.[0] || 0, p0 = before[0]?.[0] || 0
    series = [{ id: 'now', label: 'Selected period', color: SERIES_COLORS[0], points: pts.map((p) => [p[0] - t0, p[1]]) }, { id: 'prev', label: 'Previous period', color: SERIES_COLORS[1], points: before.map((p) => [p[0] - p0, p[1]]) }]
    xLabel = 'seconds'
  }
  const stale = isStale(a)
  return (
    <div className="page">
      <div className="page-head"><div className="grow"><h1>Live monitoring</h1><p>Charts show stored samples only. Missing data appears as a gap in the line; it is never bridged or estimated.</p></div></div>
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <AssetPicker value={id} onChange={(v) => setParams({ asset: v })} />
        <label className="field">Signal<select className="input" value={ch.key} onChange={(e) => setKey(e.target.value)}>{def.channels.map((c) => <option key={c.key} value={c.key}>{c.label} [{c.unit}]</option>)}</select></label>
        <Seg label="Time range" value={range} onChange={(v) => { setRange(v); setPaused(false) }} options={RANGES.map((r) => [r[0], r[1]] as [string, string])} />
        <button className="btn" onClick={() => setPaused(!paused)} aria-pressed={paused}><Icon name={paused ? 'play' : 'pause'} />{paused ? 'Resume' : 'Pause'}</button>
        <label className="switch small"><input type="checkbox" checked={compare} disabled={range === 'live'} onChange={(e) => setCompare(e.target.checked)} />Compare with previous period</label>
        <a className="btn" href={`${BACKEND_URL}/api/assets/${id}/export.csv?minutes=${minutes}`}><Icon name="download" />Export CSV</a>
      </div>
      <SourceNote id={id} />
      <div className="grid cols-side">
        <Panel title={<>{ch.label} <ModeChip mode={def.data_mode} />{paused && <span className="chip warn">PAUSED</span>}{stale && <span className="chip off">NOT LIVE</span>}</>} actions={<button className="btn sm ghost" onClick={() => setTable(!table)}>{table ? 'Chart' : 'Samples'}</button>}>
          {hist.error ? <ErrorNote error={hist.error} /> : range !== 'live' && hist.loading ? <Loading rows={5} /> : table ? (
            <div style={{ maxHeight: 320, overflow: 'auto' }}><table className="table"><thead><tr><th>Time</th><th className="num">{ch.label} [{ch.unit}]</th></tr></thead><tbody>{pts.slice(-200).reverse().map((p) => <tr key={p[0]}><td className="mono">{fmtDateTime(p[0])}</td><td className="num">{fmtNum(p[1])}</td></tr>)}</tbody></table>{!pts.length && <Empty title="No samples in this range" />}</div>
          ) : <LineChart series={series} unit={ch.unit} warn={ch.warn} crit={ch.crit} height={320} xLabel={xLabel} empty={a.last_seen === null ? 'No data has been received for this asset.' : 'No samples for this signal in the selected range.'} />}
          <p className="small faint" style={{ marginTop: 8 }}>{ch.key === 'vibration_ms2' ? 'Definition: |‖a‖ − 1 g| of a single MPU6050 sample, in m/s². This is an acceleration deviation, not vibration velocity or displacement, and not an RMS. ' : ch.key === 'vib_rms_g' ? 'Definition: RMS of the 3-axis acceleration in one 256-sample burst at 500 Hz, gravity removed by subtracting each axis mean. Acceleration, not velocity. ' : ch.key === 'vibration_mm_s' ? 'Simulated vibration velocity (RMS). ' : ''}{def.data_mode === 'REAL' ? 'Timestamps are the PC clock at arrival; the ESP32 has no clock. Nominal rate about one sample per second (two seconds with firmware v3).' : 'One sample per second.'}</p>
        </Panel>
        <div className="stack">
          <Panel title="Selected range"><dl className="kv">
            <dt>Samples</dt><dd className="mono">{values.length}</dd>
            <dt>Minimum</dt><dd className="mono">{values.length ? fmtNum(Math.min(...values), ch.unit) : '–'}</dd>
            <dt>Maximum</dt><dd className="mono">{values.length ? fmtNum(Math.max(...values), ch.unit) : '–'}</dd>
            <dt>Average</dt><dd className="mono">{values.length ? fmtNum(values.reduce((s, v) => s + v, 0) / values.length, ch.unit) : '–'}</dd>
            <dt>Gaps</dt><dd className="mono">{missing}{missing ? ' (missing data)' : ''}</dd>
            <dt>Span</dt><dd className="mono">{pts.length ? `${fmtTime(pts[0][0])} – ${fmtTime(pts[pts.length - 1][0])}` : '–'}</dd>
          </dl></Panel>
          <Panel title="Sensor availability" tight><table className="table"><tbody>{def.channels.map((c) => {
            const has = a.telemetry[c.key] !== undefined && a.last_seen !== null
            const status = a.data_mode !== 'REAL' ? (has ? a.data_mode : 'NO DATA') : a.sensor_status[c.key] === 'INVALID' ? 'INVALID' : !has ? 'NO DATA' : stale ? 'STALE' : 'OK'
            return <tr key={c.key}><td>{c.label}{c.calibration && <div className="small faint">calibration: {c.calibration.toLowerCase()}</div>}</td><td><span className={`chip ${status === 'OK' ? 'ok' : status === 'INVALID' || status === 'STALE' ? 'warn' : status === 'NO DATA' ? 'off' : ''}`}>{status}</span></td></tr>
          })}</tbody></table></Panel>
          {a.data_quality.length > 0 && <Note kind="warn"><b>Data-quality flags on the last sample:</b> {a.data_quality.join(', ')}</Note>}
        </div>
      </div>
    </div>
  )
}
