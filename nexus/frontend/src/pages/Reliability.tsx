// AI Diagnostics, Fault Simulator and Maintenance Workbench.
import { FormEvent, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { AssetPicker, DiagnosisView, FaultList, SourceNote } from '../components/domain'
import LineChart, { SERIES_COLORS } from '../components/LineChart'
import { Empty, ErrorNote, Icon, Loading, ModeChip, Note, Panel, StateChip, useFetch } from '../components/ui'
import { act, api, buffers, fmtDateTime, fmtNum, isAdmin, openLogin, useAsset, useDef, useStore } from '../lib/store'

function NeedSignIn({ role = 'operator' }: { role?: string }) {
  return role ? null : <Note>{role}</Note>
}

// ------------------------------------------------------------------ Diagnostics
export function Diagnostics() {
  const [params, setParams] = useSearchParams()
  const id = params.get('asset') || 'FAN-201'
  const auth = useStore((s) => s.auth), def = useDef(id), a = useAsset(id)
  const list = useFetch<any[]>(() => api(`/diagnostics?asset_id=${id}`), [id])
  const [picked, setPicked] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()
  useEffect(() => setPicked(null), [id])
  const row = list.data?.find((d) => d.id === picked) || list.data?.[0]
  const run = async () => { setBusy(true); const r = await act(() => api(`/diagnostics/${id}/run`, { method: 'POST' })); setBusy(false); if (r) { setPicked(r.id); list.reload() } }
  const workOrder = async () => {
    const top = row.payload.hypotheses[0]
    const wo = await act(() => api('/workorders', { method: 'POST', body: { asset_id: id, title: `Verify: ${top ? top.title.toLowerCase() : 'reported deviation'} on ${def?.name}`, priority: row.payload.evidence.some((e: any) => e.beyond_limit === 'critical') ? 'HIGH' : 'MEDIUM', diagnosis_id: row.id } }), 'Work order created')
    if (wo) nav(`/app/maintenance?wo=${wo.id}`)
  }
  return (
    <div className="page">
      <div className="page-head"><div className="grow"><h1>AI diagnostics</h1><p>An evidence-based assessment from transparent rules. It lists what changed, which causes fit that pattern, and how to check. It is not a trained model and it never states that a part has failed.</p></div></div>
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <AssetPicker value={id} onChange={(v) => setParams({ asset: v })} />
        <button className="btn primary" onClick={run} disabled={busy || !auth}><Icon name="diag" />{busy ? 'Analysing…' : 'Run diagnosis now'}</button>
        {a && (a.data_mode !== 'REAL' || a.freshness === 'LIVE') && <StateChip state={a.anomaly_state} />}
      </div>
      {!auth && <NeedSignIn />}
      <SourceNote id={id} />
      <div className="grid cols-side">
        <Panel title={row ? `Diagnosis DX-${row.id}` : 'Diagnosis'} actions={row && auth && <div className="row">
          {!row.reviewed_by && <button className="btn sm" onClick={() => act(() => api(`/diagnostics/${row.id}/review`, { method: 'POST' }), 'Marked as reviewed').then(list.reload)}><Icon name="check" />Mark reviewed</button>}
          {row.payload.status !== 'INSUFFICIENT DATA' && row.payload.status !== 'NO ANOMALY DETECTED' && <button className="btn sm primary" onClick={workOrder}><Icon name="wrench" />Create work order</button>}
        </div>}>
          {list.loading ? <Loading rows={6} /> : list.error ? <ErrorNote error={list.error} /> : row ? <DiagnosisView row={row} /> : <Empty icon="diag" title="No diagnosis yet for this asset">Press "Run diagnosis now". The result is built from the last 30 seconds of stored samples.</Empty>}
        </Panel>
        <div className="stack">
          <Panel title="How this works"><ol className="small muted" style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 6 }}>
            <li>Check the data is fresh and there are enough samples.</li>
            <li>Compare each signal's recent level with its baseline.</li>
            <li>Mark a deviation when the change exceeds a fixed tolerance for that kind of signal.</li>
            <li>Match the pattern of deviations against a table of cause patterns.</li>
            <li>Rank causes by how many signals agree, and list what disagrees or is not measured.</li>
          </ol></Panel>
          <Panel title="Previous diagnoses" tight>
            {!list.data?.length ? <Empty title="None" /> : <table className="table"><tbody>{list.data.map((d) => (
              <tr key={d.id} className="clickable" onClick={() => setPicked(d.id)} style={row?.id === d.id ? { background: 'var(--accent-soft)' } : undefined}>
                <td className="mono small">DX-{d.id}</td><td>{d.status}<div className="small faint">{fmtDateTime(d.ts)}{d.reviewed_by ? ` · reviewed by ${d.reviewed_by}` : ''}</div></td></tr>
            ))}</tbody></table>}
          </Panel>
        </div>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ Fault simulator
export function Simulator() {
  const [params, setParams] = useSearchParams()
  const site = useStore((s) => s.site), auth = useStore((s) => s.auth), tick = useStore((s) => s.tick)
  const link = useStore((s) => s.snapshot?.link)
  const sims = (site?.assets || []).filter((x) => x.data_mode === 'SIMULATED')
  const id = params.get('asset') && sims.some((s) => s.asset_id === params.get('asset')) ? params.get('asset')! : 'FAN-201'
  const def = useDef(id), a = useAsset(id)
  const [scenario, setScenario] = useState('bearing-degradation')
  const [severity, setSeverity] = useState(80)
  const settings = useFetch<any>(() => api('/settings'), [], 5000)
  useEffect(() => { if (def && !def.scenarios.some((s) => s.scenario_id === scenario)) setScenario(def.scenarios[0]?.scenario_id) }, [def, scenario])
  const sim = a?.simulation
  const spec = def?.scenarios.find((s) => s.scenario_id === (sim?.scenario_id || scenario))
  const call = (action: string, body?: any, ok?: string) => act(() => api(`/sim/${id}/${action}`, { method: 'POST', body }), ok)
  const affected = useMemo(() => (def?.channels || []).filter((c) => spec?.affected.includes(c.key)), [def, spec])
  const notify = settings.data?.demo_notifications
  if (!site || !def || !a) return <Loading rows={6} />
  const progress = sim ? Math.min(1, sim.elapsed_s / sim.duration_s) : 0
  const series = (key: string) => { void tick; return (buffers[id] || []).slice(-240).filter((p) => typeof p.v[key] === 'number').map((p) => [p.t, p.v[key]] as [number, number]) }
  return (
    <div className="page">
      <div className="page-head"><div className="grow"><h1>Fault simulation laboratory</h1><p>Inject a repeatable fault into a virtual machine and watch its signals respond together. Scenarios run only on simulated assets; the physical motor's readings cannot be changed from here.</p></div></div>
      <Note kind="sim"><b>Everything on this page is simulated.</b> A scenario creates SIMULATED events. It is never recorded as a physical fault.</Note>
      {!auth && <NeedSignIn />}
      <div className="grid cols-side">
        <div className="stack">
          <Panel title="1 · Virtual asset">
            <div className="row">{sims.map((s) => <button key={s.asset_id} className="btn" aria-pressed={s.asset_id === id} style={s.asset_id === id ? { borderColor: 'var(--accent)', background: 'var(--accent-soft)' } : undefined} onClick={() => setParams({ asset: s.asset_id })}>{s.asset_id}<span className="muted">{s.name}</span></button>)}</div>
          </Panel>
          <Panel title="2 · Fault scenario">
            <div className="grid cols-2">
              {def.scenarios.map((s) => (
                <button key={s.scenario_id} className="stat" disabled={!!sim} aria-pressed={scenario === s.scenario_id} style={scenario === s.scenario_id ? { borderColor: 'var(--accent)' } : undefined} onClick={() => setScenario(s.scenario_id)}>
                  <b>{s.name}</b><div className="hint">{s.expected}</div><div className="hint mono">{s.scenario_id} · ramps over {s.duration_s} s</div>
                </button>
              ))}
            </div>
            <label className="field" style={{ marginTop: 16 }}>Severity: <b className="mono">{severity}%</b><input type="range" min={10} max={100} step={5} value={severity} disabled={!!sim} onChange={(e) => setSeverity(+e.target.value)} /></label>
          </Panel>
          <Panel title={<>3 · Run {sim && <span className={`chip ${sim.paused ? 'warn' : 'sim'}`}>{sim.paused ? 'PAUSED' : 'SIMULATED FAULT RUNNING'}</span>}</>}>
            <div className="stack">
              <div className="row">
                {!sim && <button className="btn primary" disabled={!auth} onClick={() => call('start', { scenario_id: scenario, severity: severity / 100 }, 'Simulated fault started')}><Icon name="play" />Start scenario</button>}
                {sim && !sim.paused && <button className="btn" disabled={!auth} onClick={() => call('pause')}><Icon name="pause" />Pause</button>}
                {sim?.paused && <button className="btn" disabled={!auth} onClick={() => call('resume')}><Icon name="play" />Resume</button>}
                {sim && <button className="btn" disabled={!auth} onClick={() => call('stop', undefined, 'Scenario ended; readings will recover')}><Icon name="stop" />End scenario</button>}
                <button className="btn" disabled={!auth} onClick={() => call('reset', undefined, 'Asset reset to baseline')}><Icon name="reset" />Reset to baseline</button>
                <span className="grow" />
                <Link className="btn ghost" to={`/app/diagnostics?asset=${id}`}><Icon name="diag" />Diagnose</Link>
                <Link className="btn ghost" to={`/app/twin?asset=${id}`}><Icon name="twin" />See in factory</Link>
              </div>
              <div>
                <div className="row small muted"><span>Timeline</span><span className="grow" /><span className="mono">{sim ? `${Math.round(sim.elapsed_s)} s · fault at ${Math.round(progress * sim.severity * 100)}% intensity` : 'not running'}</span></div>
                <div className="bar-track" style={{ borderRadius: 4, marginTop: 6 }} role="progressbar" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100}><div className="bar-fill" style={{ width: `${progress * 100}%`, background: 'var(--sim)', borderRadius: 4 }} /></div>
                <div className="row small faint" style={{ marginTop: 4 }}><span>start</span><span className="grow" /><span>full severity at {spec?.duration_s} s, then holds</span></div>
              </div>
              <FaultList a={a} />
              <div className="grid cols-2">
                {affected.slice(0, 4).map((c) => <div key={c.key}><div className="small muted" style={{ marginBottom: 4 }}>{c.label}</div><LineChart series={[{ id: c.key, label: c.label, color: SERIES_COLORS[0], points: series(c.key) }]} unit={c.unit} warn={c.warn} crit={c.crit} height={150} /></div>)}
              </div>
            </div>
          </Panel>
        </div>
        <div className="stack">
          <Panel title="Normal vs. now" tight>
            <table className="table"><thead><tr><th>Signal</th><th className="num">Normal</th><th className="num">Now</th><th className="num">Change</th></tr></thead><tbody>
              {def.channels.map((c) => { const v = a.telemetry[c.key]; const pct = c.nominal ? ((v - c.nominal) / c.nominal) * 100 : null
                return <tr key={c.key}><td>{c.label}{spec?.affected.includes(c.key) && <span className="small faint"> · affected</span>}</td><td className="num">{fmtNum(c.nominal)}</td><td className="num">{fmtNum(v)}</td><td className="num">{pct === null ? '–' : `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`}</td></tr> })}
            </tbody></table>
            <div className="panel-body small faint" style={{ borderTop: '1px solid var(--line)' }}>"Normal" is the simulation model's nominal value. Units as shown on the charts.</div>
          </Panel>
          <Panel title="Scenario definition">{spec ? <dl className="kv small">
            <dt>Scenario ID</dt><dd className="mono">{spec.scenario_id}</dd><dt>Run ID</dt><dd className="mono">{sim?.run_id || '–'}</dd>
            <dt>Fault model</dt><dd>{spec.fault}</dd><dt>Affected signals</dt><dd>{spec.affected.join(', ')}</dd>
            <dt>Expected</dt><dd>{spec.expected}</dd><dt>Reset</dt><dd>{spec.reset}</dd>
            <dt>Repeatable</dt><dd>Yes. Starting a scenario resets the asset and re-seeds its noise, so the same scenario gives the same numbers.</dd>
          </dl> : <Empty title="No scenario for this asset type" />}</Panel>
          <Panel title="Physical demo notification">
            <div className="stack small">
              <div className="row"><span className={`chip ${notify?.enabled ? 'warn' : 'off'}`}>{notify?.enabled ? 'ENABLED' : 'DISABLED'}</span><span className="muted">ESP32: {link?.status || '–'}</span></div>
              <p className="muted">When enabled, the first alert of a simulated scenario asks the ESP32 to sound its buzzer for 2 seconds. The buzzer signals a <b>simulated</b> event: no physical {def.type.replace('_', ' ')} exists and nothing has failed.</p>
              <label className="switch"><input type="checkbox" checked={!!notify?.enabled} disabled={!isAdmin(auth)} onChange={(e) => act(() => api('/settings/notifications', { method: 'PUT', body: { enabled: e.target.checked } }), e.target.checked ? 'Physical notifications enabled' : 'Physical notifications disabled').then(settings.reload)} />Sound the physical buzzer on simulated faults</label>
              {!isAdmin(auth) && <p className="faint">Only an admin can change this, and only while the ESP32 is connected.</p>}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ Maintenance
const STATES = ['NEW', 'REVIEW REQUIRED', 'INSPECTION PLANNED', 'IN PROGRESS', 'COMPLETED', 'VERIFIED', 'CLOSED']

export function Maintenance() {
  const [params, setParams] = useSearchParams()
  const auth = useStore((s) => s.auth), site = useStore((s) => s.site)
  const list = useFetch<any[]>(() => api('/workorders'), [], 6000)
  const [note, setNote] = useState('')
  const [form, setForm] = useState({ asset_id: 'MOTOR-001', title: '', priority: 'MEDIUM' })
  const picked = Number(params.get('wo')) || list.data?.[0]?.id
  const wo = list.data?.find((w) => w.id === picked)
  const flow = site?.work_order_flow || {}
  const update = (body: any, ok: string) => act(() => api(`/workorders/${wo.id}`, { method: 'POST', body }), ok).then(() => { setNote(''); list.reload() })
  const create = async (e: FormEvent) => { e.preventDefault(); const r = await act(() => api('/workorders', { method: 'POST', body: form }), 'Work order created'); if (r) { setForm({ ...form, title: '' }); setParams({ wo: String(r.id) }); list.reload() } }
  const snapshotTable = (wo: any) => {
    const before = wo.payload.before?.values || {}, after = wo.payload.after?.values || {}
    const keys = Object.keys(before)
    if (!keys.length) return <p className="small muted">No telemetry was available when this work order was created, so there is nothing to compare.</p>
    return (
      <>
        <table className="table"><thead><tr><th>Signal</th><th className="num">Before</th><th className="num">After</th><th className="num">Change</th></tr></thead><tbody>
          {keys.map((k) => { const b = before[k], x = after[k]; return <tr key={k}><td>{b.label}<div className="small faint">{b.samples} samples{x ? ` / ${x.samples}` : ''}</div></td><td className="num">{fmtNum(b.mean)} {b.unit}</td><td className="num">{x ? `${fmtNum(x.mean)} ${x.unit}` : '–'}</td><td className="num">{x && b.mean ? `${(((x.mean - b.mean) / Math.abs(b.mean)) * 100).toFixed(1)}%` : '–'}</td></tr> })}
        </tbody></table>
        <p className="small faint" style={{ padding: '8px 16px' }}><b>{wo.payload.before.kind}</b>. Mean over {wo.payload.before.window_s} s, captured {fmtDateTime(wo.payload.before.captured_ts)}{wo.payload.after ? ` and ${fmtDateTime(wo.payload.after.captured_ts)}` : '; the "after" values are captured when the order is marked VERIFIED'}. Operating conditions are not recorded, so a change is an observation, not proof the work caused it.</p>
      </>
    )
  }
  return (
    <div className="page">
      <div className="page-head"><div className="grow"><h1>Maintenance workbench</h1><p>Recommendations become work orders that people review, plan, carry out and verify. Suggested checks are decision support and do not replace an approved maintenance procedure or qualified personnel.</p></div></div>
      {!auth && <NeedSignIn />}
      <div className="grid cols-side">
        <div className="stack">
          <Panel title="Work orders" tight>
            {list.loading ? <div className="panel-body"><Loading /></div> : list.error ? <div className="panel-body"><ErrorNote error={list.error} /></div> : !list.data?.length ? <Empty icon="wrench" title="No work orders yet">Create one below, or from a diagnosis.</Empty> : (
              <div className="scroll-x"><table className="table"><thead><tr><th>ID</th><th>Title</th><th>Asset</th><th>Priority</th><th>State</th><th>Updated</th></tr></thead><tbody>
                {list.data.map((w) => <tr key={w.id} className="clickable" onClick={() => setParams({ wo: String(w.id) })} style={w.id === picked ? { background: 'var(--accent-soft)' } : undefined}>
                  <td className="mono">WO-{w.id}</td><td>{w.title}</td><td>{w.asset_id} <ModeChip mode={w.data_mode} /></td><td><span className={`chip ${w.priority === 'HIGH' ? 'crit' : w.priority === 'MEDIUM' ? 'warn' : ''}`}>{w.priority}</span></td><td><span className="chip">{w.state}</span></td><td className="mono small faint">{fmtDateTime(w.updated)}</td></tr>)}
              </tbody></table></div>
            )}
          </Panel>
          {wo && <Panel title={`WO-${wo.id} · ${wo.title}`}>
            <div className="stack">
              <div className="row" role="list" aria-label="Workflow">{STATES.map((s, i) => { const at = STATES.indexOf(wo.state); return <span key={s} role="listitem" className={`chip ${i < at ? 'ok' : i === at ? 'real' : 'off'}`}>{i < at ? '✓ ' : i === at ? '● ' : ''}{s}</span> })}</div>
              {wo.data_mode !== 'REAL' && <Note kind="sim">This work order concerns a {wo.data_mode} asset. It is a demonstration of the workflow, not real maintenance.</Note>}
              <div className="row">{(flow[wo.state] || []).map((s) => <button key={s} className={`btn ${s === 'CLOSED' ? '' : 'primary'}`} disabled={!auth} onClick={() => update({ state: s }, `Moved to ${s}`)}>Move to {s}</button>)}{!(flow[wo.state] || []).length && <span className="muted small">This work order is closed.</span>}</div>
              {wo.payload.diagnostic_evidence && <div><div className="caps">Diagnostic evidence {wo.diagnosis_id && <Link to={`/app/diagnostics?asset=${wo.asset_id}`}>DX-{wo.diagnosis_id}</Link>}</div><p className="small">{wo.payload.diagnostic_evidence}</p></div>}
              {wo.payload.recommended_checks.length > 0 && <div><div className="caps">Recommended checks</div><ol className="small" style={{ margin: '6px 0 0', paddingLeft: 18 }}>{wo.payload.recommended_checks.map((c: string) => <li key={c}>{c}</li>)}</ol></div>}
              <div><div className="caps">Parts</div><p className="small muted">{wo.payload.parts}</p></div>
              <div><div className="caps">Technician notes</div>
                {wo.payload.notes.length === 0 && <p className="small faint">No notes yet.</p>}
                {wo.payload.notes.map((n: any) => <div key={n.ts} className="small" style={{ padding: '6px 0', borderBottom: '1px solid var(--line)' }}><span className="mono faint">{fmtDateTime(n.ts)} · {n.user}</span><div>{n.text}</div></div>)}
                <div className="row" style={{ marginTop: 8 }}><input className="input grow" placeholder="Add a note…" aria-label="Add a note" value={note} disabled={!auth} onChange={(e) => setNote(e.target.value)} /><button className="btn" disabled={!auth || !note.trim()} onClick={() => update({ note }, 'Note added')}>Add note</button></div>
              </div>
            </div>
          </Panel>}
          {wo && <Panel title="Before and after measurements" tight>{snapshotTable(wo)}</Panel>}
        </div>
        <div className="stack">
          <Panel title="New work order"><form className="stack" onSubmit={create}>
            <label className="field">Asset<select className="input" value={form.asset_id} onChange={(e) => setForm({ ...form, asset_id: e.target.value })}>{(site?.assets || []).map((x) => <option key={x.asset_id} value={x.asset_id}>{x.asset_id} · {x.name} [{x.data_mode}]</option>)}</select></label>
            <label className="field">Title<input className="input" value={form.title} maxLength={200} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="What needs to be checked?" /></label>
            <label className="field">Priority<select className="input" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}><option>LOW</option><option>MEDIUM</option><option>HIGH</option></select></label>
            <button className="btn primary" disabled={!auth || form.title.trim().length < 3}>Create work order</button>
          </form></Panel>
          {wo && <Panel title="Change history" tight><table className="table"><tbody>{wo.payload.history.map((h: any) => <tr key={h.ts}><td className="mono small faint">{fmtDateTime(h.ts)}</td><td>{h.from ? `${h.from} → ${h.to}` : 'Created'}<div className="small faint">{h.user}</div></td></tr>)}{wo.payload.completed_ts && <tr><td className="mono small faint">{fmtDateTime(wo.payload.completed_ts)}</td><td>Completion recorded</td></tr>}</tbody></table></Panel>}
        </div>
      </div>
    </div>
  )
}
