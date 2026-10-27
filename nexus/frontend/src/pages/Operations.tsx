// Analytics, Event History, System Health and Settings.
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AssetPicker } from '../components/domain'
import LineChart, { SERIES_COLORS } from '../components/LineChart'
import { Empty, ErrorNote, Icon, Loading, ModeChip, Note, Panel, Seg, useFetch } from '../components/ui'
import { act, api, fmtAge, fmtDateTime, fmtNum, isAdmin, openLogin, useDef, useStore } from '../lib/store'

// ------------------------------------------------------------------ Analytics
export function Analytics() {
  const [hours, setHours] = useState('1')
  const [id, setId] = useState('FAN-201')
  const def = useDef(id)
  const [key, setKey] = useState('')
  const ch = def?.channels.find((c) => c.key === key) || def?.channels[0]
  const sum = useFetch<any>(() => api(`/analytics/summary?hours=${hours}`), [hours], 10000)
  const hist = useFetch<any>(() => api(`/assets/${id}/history?minutes=${+hours * 60}`), [id, hours], 15000)
  useEffect(() => setKey(''), [id])
  const rows = sum.data ? Object.entries<any>(sum.data.assets).filter(([, v]) => v.samples > 0 || v.anomaly_events > 0) : []
  const maxEvents = Math.max(1, ...rows.map(([, v]) => v.anomaly_events))
  const compared = (sum.data?.work_orders || []).filter((w: any) => w.payload.before && w.payload.after)
  const pts = ch ? (hist.data?.rows || []).filter((r: any) => typeof r.vals[ch.key] === 'number').map((r: any) => [r.ts, r.vals[ch.key]] as [number, number]) : []
  return (
    <div className="page">
      <div className="page-head"><div className="grow"><h1>Performance analytics</h1><p>Counts, durations and trends computed from stored telemetry and events. Nothing here estimates cost, energy or downtime savings: the data to support such figures does not exist yet.</p></div>
        <Seg label="Period" value={hours} onChange={setHours} options={[['1', '1 hour'], ['6', '6 hours'], ['24', '24 hours']]} /></div>
      {sum.error && <ErrorNote error={sum.error} />}
      <div className="grid cols-2">
        <Panel title="Anomaly events per asset">
          {sum.loading ? <Loading /> : !rows.some(([, v]) => v.anomaly_events) ? <Empty icon="chart" title="No anomaly events in this period" /> : <div className="stack">
            {rows.map(([aid, v]) => <div className="bar-row" key={aid} title={`${v.name}: ${v.anomaly_events}`}><span>{aid} <span className="faint small">{v.data_mode === 'SIMULATED' ? 'SIM' : v.data_mode}</span></span><div className="bar-track"><div className="bar-fill" style={{ width: `${(v.anomaly_events / maxEvents) * 100}%` }} /></div><span className="mono num">{v.anomaly_events}</span></div>)}
            <p className="small faint">Number of times an asset entered a warning or critical state. Simulated assets are marked SIM.</p>
          </div>}
        </Panel>
        <Panel title="Time in alert and data coverage" tight>
          {sum.loading ? <div className="panel-body"><Loading /></div> : !rows.length ? <Empty title="No data in this period" /> : <table className="table"><thead><tr><th>Asset</th><th>Source</th><th className="num">Time in alert</th><th className="num">Samples</th><th className="num">Data span</th></tr></thead><tbody>
            {rows.map(([aid, v]) => <tr key={aid}><td className="mono">{aid}</td><td><ModeChip mode={v.data_mode} /></td><td className="num">{fmtAge(v.alert_duration_s)}</td><td className="num">{v.samples}</td><td className="num">{v.first_ts ? fmtAge(v.last_ts - v.first_ts) : '–'}</td></tr>)}
          </tbody></table>}
        </Panel>
      </div>
      <Panel title={<>Trend {def && <ModeChip mode={def.data_mode} />}</>} actions={<div className="row">
        <AssetPicker value={id} onChange={setId} label="" />
        <select className="input" aria-label="Signal" value={ch?.key || ''} onChange={(e) => setKey(e.target.value)}>{def?.channels.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select></div>}>
        {hist.loading ? <Loading rows={5} /> : hist.error ? <ErrorNote error={hist.error} /> : ch && <LineChart series={[{ id: ch.key, label: ch.label, color: SERIES_COLORS[0], points: pts }]} unit={ch.unit} warn={ch.warn} crit={ch.crit} height={260} empty="No stored samples for this asset in the period." />}
      </Panel>
      <Panel title="Before and after maintenance" tight>
        {!compared.length ? <Empty icon="wrench" title="No verified work orders yet">A comparison appears once a work order has been moved to VERIFIED.</Empty> : compared.map((w: any) => (
          <div key={w.id} style={{ borderBottom: '1px solid var(--line)' }}>
            <div className="panel-body row"><b>WO-{w.id}</b><span>{w.title}</span><ModeChip mode={w.data_mode} /><span className={`chip ${w.data_mode === 'REAL' ? 'real' : 'sim'}`}>{w.payload.before.kind}</span><Link to={`/app/maintenance?wo=${w.id}`} className="small">Open</Link></div>
            <table className="table"><thead><tr><th>Signal</th><th className="num">Baseline period</th><th className="num">After</th><th className="num">Observed change</th><th className="num">Samples</th></tr></thead><tbody>
              {Object.entries<any>(w.payload.before.values).map(([k, b]) => { const x = w.payload.after.values[k]; return <tr key={k}><td>{b.label}</td><td className="num">{fmtNum(b.mean)} {b.unit}</td><td className="num">{x ? `${fmtNum(x.mean)} ${x.unit}` : '–'}</td><td className="num">{x && b.mean ? `${(((x.mean - b.mean) / Math.abs(b.mean)) * 100).toFixed(1)}%` : '–'}</td><td className="num">{b.samples} / {x?.samples ?? 0}</td></tr> })}
            </tbody></table>
            <p className="small faint" style={{ padding: '8px 16px' }}>Baseline: 60 s mean ending {fmtDateTime(w.payload.before.captured_ts)}. After: 60 s mean ending {fmtDateTime(w.payload.after.captured_ts)}. Operating conditions were not recorded. This is an observed difference, not an estimate of savings.</p>
          </div>
        ))}
      </Panel>
    </div>
  )
}

// ------------------------------------------------------------------ Events
const CATEGORIES = ['SENSOR_ANOMALY', 'SIMULATED_FAULT', 'RETURN_TO_NORMAL', 'DEVICE_CONNECTION', 'SIMULATION', 'DIAGNOSIS', 'COMMAND', 'MAINTENANCE', 'SETTINGS', 'REPLAY']
const ORIGIN_LABEL: Record<string, string> = { SYSTEM: 'System observation', SIMULATION: 'Simulation engine', OPERATOR: 'Operator action', DEVICE: 'Device response', DIAGNOSTIC_RULES: 'Diagnostic rules' }

export function Events() {
  const [params, setParams] = useSearchParams()
  const site = useStore((s) => s.site), auth = useStore((s) => s.auth)
  const f = Object.fromEntries(params.entries())
  const alertsOnly = f.category === 'alerts'
  const query = new URLSearchParams(Object.entries(f).filter(([k, v]) => v && !(k === 'category' && alertsOnly))).toString()
  const list = useFetch<any[]>(() => api(`/events?limit=300&${query}`), [query], 5000)
  const set = (k: string, v: string) => { const next = { ...f, [k]: v }; if (!v) delete next[k]; setParams(next) }
  const rows = (list.data || []).filter((e) => !alertsOnly || ['SENSOR_ANOMALY', 'SIMULATED_FAULT'].includes(e.category))
  const sel = (k: string, label: string, options: string[]) => <label className="field">{label}<select className="input" value={f[k] || ''} onChange={(e) => set(k, e.target.value)}><option value="">All</option>{options.map((o) => <option key={o} value={o}>{o}</option>)}</select></label>
  return (
    <div className="page">
      <div className="page-head"><div className="grow"><h1>Event history</h1><p>System observations, simulation events, diagnostic results, operator actions and device responses, each labelled by origin and data source. Stored as ordinary database rows: an activity log, not a tamper-proof audit trail.</p></div></div>
      <div className="row" style={{ alignItems: 'flex-end' }}>
        {sel('asset_id', 'Asset', (site?.assets || []).map((a) => a.asset_id))}
        <label className="field">Category<select className="input" value={f.category || ''} onChange={(e) => set('category', e.target.value)}><option value="">All</option><option value="alerts">Alerts only</option>{CATEGORIES.map((o) => <option key={o} value={o}>{o}</option>)}</select></label>
        {sel('severity', 'Severity', ['INFO', 'WARNING', 'CRITICAL'])}
        {sel('data_mode', 'Data source', ['REAL', 'SIMULATED', 'REPLAY'])}
        {sel('origin', 'Origin', Object.keys(ORIGIN_LABEL))}
        <label className="field">Acknowledged<select className="input" value={f.acknowledged || ''} onChange={(e) => set('acknowledged', e.target.value)}><option value="">All</option><option value="false">Not acknowledged</option><option value="true">Acknowledged</option></select></label>
        <label className="field">Period<select className="input" value={f.hours || ''} onChange={(e) => set('hours', e.target.value)}><option value="">All time</option><option value="1">Last hour</option><option value="24">Last 24 hours</option></select></label>
        <label className="field">Search<input className="input" value={f.search || ''} onChange={(e) => set('search', e.target.value)} placeholder="Title, detail or scenario" /></label>
        {Object.keys(f).length > 0 && <button className="btn ghost" onClick={() => setParams({})}>Clear filters</button>}
      </div>
      <Panel tight title={`${rows.length} event${rows.length === 1 ? '' : 's'}`}>
        {list.loading ? <div className="panel-body"><Loading rows={6} /></div> : list.error ? <div className="panel-body"><ErrorNote error={list.error} /></div> : !rows.length ? <Empty title="No events match these filters" /> : (
          <div className="scroll-x"><table className="table"><thead><tr><th>Time</th><th>ID</th><th>Asset</th><th>Source</th><th>Event</th><th>Origin</th><th>Acknowledgement</th></tr></thead><tbody>
            {rows.map((e) => (
              <tr key={e.id}>
                <td className="mono small" style={{ whiteSpace: 'nowrap' }}>{fmtDateTime(e.ts)}</td><td className="mono small faint">EVT-{e.id}</td>
                <td className="mono small"><Link to={`/app/assets/${e.asset_id}`}>{e.asset_id}</Link></td><td><ModeChip mode={e.data_mode} /></td>
                <td><div className="row" style={{ gap: 8 }}><span className={`chip ${e.severity === 'CRITICAL' ? 'crit' : e.severity === 'WARNING' ? 'warn' : ''}`}>{e.severity}</span><b>{e.title}</b></div><div className="small muted">{e.detail}</div><div className="small faint mono">{e.category}{e.scenario_id ? ` · ${e.scenario_id}` : ''}{e.ref ? ` · ${e.ref}` : ''}</div></td>
                <td className="small muted">{ORIGIN_LABEL[e.origin] || e.origin}</td>
                <td className="small">{e.ack_ts ? <span className="muted">{e.ack_by} · {fmtDateTime(e.ack_ts)}</span> : e.severity !== 'INFO' ? <button className="btn sm" disabled={!auth} title={auth ? '' : 'Sign in to acknowledge'} onClick={() => act(() => api(`/events/${e.id}/ack`, { method: 'POST' }), 'Acknowledged').then(list.reload)}><Icon name="check" />Acknowledge</button> : <span className="faint">–</span>}</td>
              </tr>
            ))}
          </tbody></table></div>
        )}
      </Panel>
    </div>
  )
}

// ------------------------------------------------------------------ System health
export function Health() {
  const h = useFetch<any>(() => api('/health'), [], 3000)
  const cmds = useFetch<any[]>(() => api('/device/commands'), [], 4000)
  const conn = useStore((s) => s.connection), ts = useStore((s) => s.snapshot?.ts)
  const chip = (v: string | undefined) => <span className={`chip ${v === 'OK' || v === 'LIVE' || v === 'CONNECTED' || v === 'PRESENT' ? 'ok' : !v || v === 'UNKNOWN' ? 'off' : 'warn'}`}>{v || 'UNKNOWN'}</span>
  const link = h.data?.device_link
  return (
    <div className="page">
      <div className="page-head"><div className="grow"><h1>System health</h1><p>Each indicator is read from the running system. Anything that cannot be checked is shown as UNKNOWN, not as healthy.</p></div></div>
      {h.error && <ErrorNote error={h.error} />}
      <div className="grid cols-4">
        <div className="stat"><div className="caps">Backend API</div><div className="value" style={{ fontSize: 18 }}>{chip(h.error ? 'UNREACHABLE' : h.data?.backend)}</div><div className="hint">up {fmtAge(h.data?.uptime_s)}</div></div>
        <div className="stat"><div className="caps">Database</div><div className="value" style={{ fontSize: 18 }}>{chip(h.data?.database)}</div><div className="hint">local SQLite file</div></div>
        <div className="stat"><div className="caps">Engine loop</div><div className="value" style={{ fontSize: 18 }}>{chip(h.data?.engine_loop)}</div><div className="hint">last tick {fmtAge(h.data?.engine_last_tick_age_s)} ago</div></div>
        <div className="stat"><div className="caps">This browser's live stream</div><div className="value" style={{ fontSize: 18 }}>{chip(conn === 'open' ? 'OK' : conn.toUpperCase())}</div><div className="hint">last update {ts ? fmtAge(Date.now() / 1000 - ts) : '–'} ago · {h.data?.websocket_clients ?? '–'} client(s)</div></div>
      </div>
      <div className="grid cols-2">
        <Panel title="ESP32 device link">
          {!link ? <Loading /> : <div className="stack">
            <dl className="kv">
              <dt>Serial link</dt><dd>{chip(link.status)} <span className="small muted">{link.message}</span></dd>
              <dt>Telemetry</dt><dd>{chip(link.telemetry)}</dd>
              <dt>Port</dt><dd className="mono">{link.port || '–'}{link.baud ? ` @ ${link.baud}` : ''}</dd>
              <dt>Last line</dt><dd className="mono">{link.last_line_age_s === null ? 'never' : `${fmtAge(link.last_line_age_s)} ago`}</dd>
              <dt>Lines</dt><dd className="mono">{link.lines_ok} accepted · {link.lines_bad} rejected · {link.missed_sequences} missed sequence numbers</dd>
              <dt>Firmware version</dt><dd>{link.firmware_version ? <span className="mono">{link.firmware_version}</span> : <span className="muted">UNKNOWN (the device has not reported one; the original sketch never does)</span>}</dd>
              <dt>Device identity</dt><dd>{link.reported_device_id ? <span className="mono">{link.reported_device_id}</span> : <span className="muted">Not reported by the device</span>}</dd>
              <dt>Command support</dt><dd>{link.commands_supported ? 'Reported (firmware v3)' : <span className="muted">UNKNOWN until the device answers a status request</span>}</dd>
            </dl>
            {link.recent_problems.length > 0 && <div><div className="caps">Recent line problems</div>{link.recent_problems.map((p: string, i: number) => <div key={i} className="small mono muted">{p}</div>)}</div>}
          </div>}
        </Panel>
        <Panel title="Troubleshooting">
          <ul className="small muted" style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 8 }}>
            <li><b>Not connected:</b> plug in the ESP32 and connect in <Link to="/app/settings">Settings</Link>. Close the Arduino Serial Monitor first; only one program can hold the port.</li>
            <li><b>Connected but no telemetry:</b> check the baud rate (115200) and that the sketch is running. Rejected lines above show what arrived.</li>
            <li><b>A sensor shows NO DATA:</b> the sketch on the board does not send that field. RPM, LiDAR, analog light and raw vibration need firmware v3 with their pins set.</li>
            <li><b>A sensor shows INVALID:</b> the reading was outside what the sensor can physically report (for example a disconnected DS18B20), so it is not displayed.</li>
            <li><b>Commands time out:</b> the board is running a sketch without command support. Flash <span className="mono">firmware/nexus_motor_node</span>.</li>
            <li><b>Keep the motor off while connecting:</b> opening the port resets the ESP32 and the sketch zeroes the current sensor at start-up.</li>
          </ul>
        </Panel>
      </div>
      <Panel title="Recent device commands" tight>
        {cmds.loading ? <div className="panel-body"><Loading /></div> : !cmds.data?.length ? <Empty icon="chip" title="No commands have been issued" /> : <div className="scroll-x"><table className="table"><thead><tr><th>Time</th><th>ID</th><th>Command</th><th>Outcome</th><th>Issued by</th><th>Detail</th></tr></thead><tbody>
          {cmds.data.map((c) => <tr key={c.id}><td className="mono small">{fmtDateTime(c.ts)}</td><td className="mono small faint">CMD-{c.id}</td><td className="mono">{c.name} {c.params?.argument || ''}</td>
            <td><span className={`chip ${c.state === 'ACKNOWLEDGED' ? 'ok' : c.state === 'PENDING' ? 'real' : 'warn'}`}>{c.state === 'PENDING' ? 'PENDING · waiting for device' : c.state}</span></td><td className="small">{c.issued_by}</td><td className="small muted">{c.detail}{c.params?.note ? ` ${c.params.note}` : ''}{c.event_ref ? ` (${c.event_ref})` : ''}</td></tr>)}
        </tbody></table></div>}
      </Panel>
      <div className="grid cols-2">
        <Panel title="Backend error log" tight>{!h.data?.errors?.length ? <Empty icon="check" title="No errors recorded since start" /> : <table className="table"><tbody>{h.data.errors.map((e: any, i: number) => <tr key={i}><td className="mono small faint">{fmtDateTime(e.ts)}</td><td className="mono small">{e.where}: {e.error}</td></tr>)}</tbody></table>}</Panel>
        <Panel title="Other components"><dl className="kv">
          <dt>Web app build</dt><dd>{chip(h.data?.frontend_build)}</dd>
          <dt>Simulation engine</dt><dd>{h.data ? `${h.data.simulated_assets} simulated assets` : '–'}</dd>
          <dt>Replay</dt><dd>{h.data?.replay?.file ? <><span className="mono">{h.data.replay.file}</span> · {h.data.replay.rows} samples · origin {h.data.replay.origin}</> : 'NO RECORDING LOADED'}</dd>
          <dt>Message broker</dt><dd className="muted">Not used. The ESP32 is on a direct USB serial link.</dd>
          <dt>Allowed device commands</dt><dd className="mono small">{h.data ? Object.entries<any>(h.data.allowed_commands).map(([k, v]) => (v ? `${k}(${v.min_ms}–${v.max_ms} ms)` : k)).join(', ') : '–'}</dd>
        </dl></Panel>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ Settings
export function Settings() {
  const auth = useStore((s) => s.auth), link = useStore((s) => s.snapshot?.link)
  const admin = isAdmin(auth)
  const s = useFetch<any>(() => api('/settings'), [], 6000)
  const ports = useFetch<any[]>(() => (admin ? api('/device/ports') : Promise.resolve([])), [admin])
  const def = useDef('MOTOR-001')
  const [port, setPort] = useState('')
  const [limits, setLimits] = useState<Record<string, any>>({})
  const [last, setLast] = useState<any>(null)
  useEffect(() => { if (s.data) setLimits(s.data.limits) }, [s.data?.limits && JSON.stringify(s.data.limits)])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (ports.data?.length && !port) setPort(ports.data[0].device) }, [ports.data, port])
  const cmd = async (name: string, duration_ms?: number) => { const r = await act(() => api('/device/command', { method: 'POST', body: { name, duration_ms } })); if (r) setLast(r) }
  const setLimit = (k: string, field: string, v: string) => setLimits({ ...limits, [k]: { direction: 'high', ...limits[k], [field]: field === 'direction' ? v : v === '' ? null : Number(v) } })
  return (
    <div className="page">
      <div className="page-head"><div className="grow"><h1>Settings and device configuration</h1><p>Changes are checked again by the backend before they are applied.</p></div></div>
      <div className="grid cols-2">
        <Panel title="ESP32 connection">
          <div className="stack">
            <div className="row"><span className={`chip ${link?.status === 'CONNECTED' ? 'ok' : link?.status === 'DISCONNECTED' ? 'off' : 'warn'}`}>{link?.status || '–'}</span><span className="small muted">{link?.message}</span></div>
            <div className="row" style={{ alignItems: 'flex-end' }}>
              <label className="field grow">Serial port<select className="input" value={port} disabled={!admin} onChange={(e) => setPort(e.target.value)}>{!ports.data?.length && <option value="">No serial ports found</option>}{ports.data?.map((p) => <option key={p.device} value={p.device}>{p.device} — {p.description}</option>)}</select></label>
              <button className="btn" disabled={!admin} onClick={ports.reload}>Rescan</button>
            </div>
            <div className="row">
              <button className="btn primary" disabled={!admin || !port} onClick={() => act(() => api('/device/connect', { method: 'POST', body: { port, baud: 115200 } }), `Connecting to ${port}`)}><Icon name="link" />Connect</button>
              <button className="btn" disabled={!admin || link?.status === 'DISCONNECTED'} onClick={() => act(() => api('/device/disconnect', { method: 'POST' }), 'Disconnected').then(s.reload)}>Disconnect</button>
            </div>
            <p className="small faint">Keep the motor off while connecting. Baud rate 115200. Real samples are also appended to the analysis project's <span className="mono">data/raw</span> folder.</p>
          </div>
        </Panel>
        <Panel title="Device commands">
          <div className="stack">
            <p className="small muted">Only three commands exist: PING, REQUEST_STATUS and BUZZER_ALERT (at most 2000 ms). There is no motor control and no general pin access. A command is shown as acknowledged only when the device confirms it.</p>
            <div className="row">
              <button className="btn" disabled={!admin} onClick={() => cmd('PING')}>Ping</button>
              <button className="btn" disabled={!admin} onClick={() => cmd('REQUEST_STATUS')}>Request status</button>
              <button className="btn danger" disabled={!admin} onClick={() => cmd('BUZZER_ALERT', 2000)}><Icon name="bell" />Test buzzer · 2 s <span className="small">(physical)</span></button>
            </div>
            {last && <LiveCommand id={last.id} />}
            <label className="switch"><input type="checkbox" checked={!!s.data?.demo_notifications?.enabled} disabled={!admin} onChange={(e) => act(() => api('/settings/notifications', { method: 'PUT', body: { enabled: e.target.checked } }), e.target.checked ? 'Enabled' : 'Disabled').then(s.reload)} />Sound the buzzer when a simulated fault raises an alert</label>
            <p className="small faint">Off by default and switched off again on disconnect. The buzzer then signals a simulated event, not a physical failure.</p>
          </div>
        </Panel>
      </div>
      <Panel title="Operating limits for the physical motor (MOTOR-001)" tight actions={<button className="btn sm primary" disabled={!admin} onClick={() => act(() => api('/settings/limits', { method: 'PUT', body: { limits } }), 'Limits saved').then(s.reload)}>Save limits</button>}>
        <div className="scroll-x"><table className="table"><thead><tr><th>Signal</th><th>Alert when</th><th>Warning</th><th>Critical</th><th>Source</th></tr></thead><tbody>
          {def?.channels.filter((c) => c.kind !== 'environment').map((c) => (
            <tr key={c.key}><td>{c.label} <span className="faint">[{c.unit}]</span></td>
              <td><select className="input" aria-label={`${c.label} direction`} disabled={!admin} value={limits[c.key]?.direction || 'high'} onChange={(e) => setLimit(c.key, 'direction', e.target.value)}><option value="high">above</option><option value="low">below</option></select></td>
              <td><input className="input mono" aria-label={`${c.label} warning`} style={{ width: 110 }} type="number" step="any" disabled={!admin} value={limits[c.key]?.warn ?? ''} onChange={(e) => setLimit(c.key, 'warn', e.target.value)} /></td>
              <td><input className="input mono" aria-label={`${c.label} critical`} style={{ width: 110 }} type="number" step="any" disabled={!admin} value={limits[c.key]?.crit ?? ''} onChange={(e) => setLimit(c.key, 'crit', e.target.value)} /></td>
              <td className="small faint">{s.data?.limits?.[c.key]?.source || 'none configured'}</td></tr>
          ))}
        </tbody></table></div>
        <div className="panel-body small faint" style={{ borderTop: '1px solid var(--line)' }}>No limits are built in: safe values depend on this motor and where each sensor is mounted. Without a limit, a signal is judged against the first 60 valid readings of the connection.</div>
      </Panel>
      <div className="grid cols-2">
        <Panel title="Access and security"><dl className="kv small">
          <dt>Access</dt><dd>Full access from this computer</dd>
          <dt>Accounts</dt><dd>admin, operator. Passwords and the signing secret were generated on first start and live in <span className="mono">{s.data?.secrets_file}</span>.</dd>
          <dt>Enforced by</dt><dd>The backend, on every action. Hidden or disabled buttons are a convenience only.</dd>
          <dt>Not implemented</dt><dd>TLS, per-user accounts, password hashing, single sign-on, and signed commands on the wire. Required before any network deployment.</dd>
        </dl></Panel>
        <Panel title="Integrations"><dl className="kv small">
          <dt>ESP32 over USB serial</dt><dd><span className="chip ok">IMPLEMENTED</span> proof-of-concept device link</dd>
          <dt>Replay of recorded sessions</dt><dd><span className="chip ok">IMPLEMENTED</span> {s.data?.replay?.file || 'no recording loaded'}</dd>
          <dt>OPC UA, Modbus, PLC/SCADA</dt><dd><span className="chip off">NOT IMPLEMENTED</span> roadmap</dd>
          <dt>MQTT / Wi-Fi nodes</dt><dd><span className="chip off">NOT IMPLEMENTED</span> roadmap</dd>
          <dt>Maintenance systems (CMMS)</dt><dd><span className="chip off">NOT IMPLEMENTED</span> roadmap</dd>
          <dt>Multiple sites</dt><dd><span className="chip off">NOT IMPLEMENTED</span> roadmap</dd>
        </dl></Panel>
      </div>
    </div>
  )
}

function LiveCommand({ id }: { id: number }) {
  const cmds = useFetch<any[]>(() => api('/device/commands'), [id], 1000)
  const c = cmds.data?.find((x) => x.id === id)
  if (!c) return null
  const kind = c.state === 'ACKNOWLEDGED' ? 'real' : c.state === 'PENDING' ? '' : 'warn'
  return <div className={`note ${kind}`} aria-live="polite"><b className="mono">CMD-{c.id} {c.name}</b>: {c.state === 'PENDING' ? 'Command pending. Waiting for the device to acknowledge…' : c.state === 'ACKNOWLEDGED' ? `Acknowledged by the device. ${c.detail}` : `${c.state}. ${c.detail}`}</div>
}
