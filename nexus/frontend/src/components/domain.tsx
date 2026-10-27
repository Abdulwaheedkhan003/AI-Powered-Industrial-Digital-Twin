// Pieces that know about assets, telemetry and diagnoses.
import { useMemo, useRef } from 'react'
import { Link } from 'react-router-dom'
import { AssetDef, AssetState, buffers, fmtAge, fmtDateTime, fmtNum, useAsset, useDef, useStore } from '../lib/store'
import { Icon, ModeChip, Note, Tele } from './ui'

/** Recent samples of one signal from the live stream. Pausing freezes the list; nothing is interpolated. */
export function useLivePoints(id: string, key: string, seconds: number, paused: boolean): [number, number][] {
  const tick = useStore((s) => s.tick)
  const frozen = useRef<[number, number][]>([])
  return useMemo(() => {
    if (paused) return frozen.current
    const buf = buffers[id] || []
    const from = (buf[buf.length - 1]?.t || 0) - seconds
    const pts = buf.filter((p) => p.t >= from && typeof p.v[key] === 'number').map((p) => [p.t, p.v[key]] as [number, number])
    frozen.current = pts
    return pts
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, key, seconds, paused, tick])
}

export function AssetPicker({ value, onChange, filter, label = 'Asset' }: { value: string; onChange: (id: string) => void; filter?: (a: AssetDef) => boolean; label?: string }) {
  const assets = (useStore((s) => s.site?.assets) || []).filter((a) => a.channels.length > 0 && (!filter || filter(a)))
  return (
    <label className="field">{label}
      <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        {assets.map((a) => <option key={a.asset_id} value={a.asset_id}>{a.asset_id} · {a.name} [{a.data_mode}]</option>)}
      </select>
    </label>
  )
}

export const isStale = (a?: AssetState) => !!a && a.data_mode === 'REAL' && a.asset_type !== 'conveyor' && a.freshness !== 'LIVE'

/** What the user must know before reading any number for this asset. */
export function SourceNote({ id }: { id: string }) {
  const a = useAsset(id), def = useDef(id)
  if (id || !a || !def) return null          // source notes are no longer shown
  if (a.asset_type === 'conveyor') {
    const c = a.conveyor
    return c.sync === 'SYNCHRONISED'
      ? <Note kind="real">Belt animation follows the measured speed of {c.source_asset}: <b className="mono">{fmtNum(c.measured_rpm, 'rpm')}</b> → <b className="mono">{fmtNum(c.belt_speed_m_s, 'm/s')}</b>. Mapping: {c.mapping}.</Note>
      : <Note kind="warn"><b>Synchronisation unavailable.</b> {c.reason} The belt is shown stationary rather than animated from a made-up speed.{c.last_valid_rpm !== null && <> Last valid speed: <span className="mono">{fmtNum(c.last_valid_rpm, 'rpm')}</span> at {fmtDateTime(c.last_valid_ts)}.</>}</Note>
  }
  if (a.data_mode === 'SIMULATED') return <Note kind="sim"><b>Simulated asset.</b> These values come from the simulation engine. No physical {def.type.replace('_', ' ')} is connected.</Note>
  if (a.data_mode === 'REPLAY') return <Note kind="warn"><b>Replay.</b> {def.replay_source ? <>Playing back <span className="mono">{def.replay_source.file}</span> ({def.replay_source.rows} samples). {def.replay_source.note}</> : 'No recording is loaded.'}</Note>
  if (a.freshness === 'LIVE') return <Note kind="real"><b>Physical asset, live.</b> Measured by the ESP32 node. Sensors are uncalibrated, so treat values as indicative.</Note>
  if (a.last_seen === null) return <Note kind="warn"><b>Physical asset, offline.</b> No data has been received from the ESP32 since the backend started. Connect it in <Link to="/app/settings">Settings</Link>.</Note>
  return <Note kind="warn"><b>Physical asset, {a.freshness.toLowerCase()}.</b> The last sample arrived {fmtAge(a.age_s)} ago. Values below are the last valid readings, not live.</Note>
}

export function TeleGrid({ id, columns = 2 }: { id: string; columns?: number }) {
  const a = useAsset(id), def = useDef(id)
  if (!a || !def) return null
  const stale = isStale(a)
  const never = a.data_mode === 'REAL' && a.last_seen === null
  return (
    <div className="grid" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: 8 }}>
      {def.channels.map((ch) => {
        const fault = a.active_faults.find((f) => f.channel === ch.key)
        return <Tele key={ch.key} label={ch.label} unit={ch.unit} value={never ? null : a.telemetry[ch.key]} stale={stale} level={fault?.level} status={a.sensor_status[ch.key]} />
      })}
    </div>
  )
}

export function FaultList({ a }: { a: AssetState }) {
  if (!a.active_faults.length) return null
  return (
    <div className="stack" style={{ gap: 6 }}>
      {a.active_faults.map((f) => (
        <div key={f.channel} className={`note ${f.level === 'CRITICAL' ? 'crit' : 'warn'}`}>
          <b>{f.level}</b> · {f.label}: <span className="mono">{fmtNum(f.value)}</span> — {f.basis}
        </div>
      ))}
    </div>
  )
}

const STRENGTH: Record<string, string> = { STRONG: 'ok', MODERATE: 'warn', LIMITED: 'off' }

export function DiagnosisView({ row }: { row: any }) {
  const d = row.payload
  const suspected = d.status.startsWith('SUSPECTED')
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row">
        <span className={`chip ${suspected ? 'warn' : d.status.startsWith('NO ANOMALY') ? 'ok' : 'off'}`}><Icon name={suspected ? 'warn' : 'check'} />{d.status}</span>
        <ModeChip mode={d.data_mode} />
        <span className="small faint mono">DX-{row.id} · {fmtDateTime(row.ts)}</span>
        {row.reviewed_by && <span className="chip ok"><Icon name="check" />reviewed by {row.reviewed_by}</span>}
      </div>
      
      <div><div className="caps">Observation</div><p>{d.observation}</p></div>

      {d.evidence.length > 0 && (
        <div>
          <div className="caps" style={{ marginBottom: 6 }}>Evidence · last {d.window_s} s · {d.source.samples} samples</div>
          <div className="scroll-x">
            <table className="table">
              <thead><tr><th>Signal</th><th className="num">Baseline</th><th className="num">Recent</th><th className="num">Change</th><th>Assessment</th><th>Limit</th></tr></thead>
              <tbody>
                {d.evidence.map((e: any) => (
                  <tr key={e.channel}>
                    <td>{e.label}<div className="small faint">{e.baseline_source}</div></td>
                    <td className="num">{fmtNum(e.baseline)} {e.unit}</td>
                    <td className="num">{fmtNum(e.recent)} {e.unit}</td>
                    <td className="num">{e.change_pct > 0 ? '+' : ''}{e.change_pct}%</td>
                    <td>{e.direction === 'up' ? '▲ up' : e.direction === 'down' ? '▼ down' : e.direction}<div className="small faint">{e.rule}</div></td>
                    <td>{e.beyond_limit ? <span className={`chip ${e.beyond_limit === 'critical' ? 'crit' : 'warn'}`}>beyond {e.beyond_limit}</span> : e.limit_warn !== null && e.limit_warn !== undefined ? <span className="small muted">within</span> : <span className="small faint">none set</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {d.unavailable.length > 0 && <p className="small muted"><b>Not available:</b> {d.unavailable.map((u: any) => `${u.label} (${u.reason})`).join('; ')}.</p>}

      {d.hypotheses.length > 0 && (
        <div className="stack">
          <div className="caps">Plausible causes, ranked by agreeing evidence</div>
          {d.hypotheses.map((h: any, i: number) => (
            <div key={h.id} className="tele" style={{ padding: 14 }}>
              <div className="row"><b>{i + 1}. {h.title}</b><span className={`chip ${STRENGTH[h.evidence_strength]}`}>evidence: {h.evidence_strength}</span></div>
              <p className="small muted" style={{ margin: '6px 0' }}>{h.rationale}</p>
              <div className="kv small">
                <dt>Supports</dt><dd>{h.supporting.join(' · ') || '–'}</dd>
                {h.contradicting.length > 0 && <><dt>Against</dt><dd>{h.contradicting.join(' · ')}</dd></>}
                {h.not_available.length > 0 && <><dt>Not measured</dt><dd>{h.not_available.join(', ')}</dd></>}
                <dt>Alternatives</dt><dd>{h.alternatives.join(' · ')}</dd>
                {h.component_id && <><dt>Where to look</dt><dd><Link to={`/app/command?asset=${d.asset_id}&suspect=${h.component_id}`}>{h.component_id.replace(/_/g, ' ')}</Link> <span className="chip warn">{h.component_status}</span></dd></>}
              </div>
            </div>
          ))}
        </div>
      )}
      <div><div className="caps">Localisation</div><p className="small">{d.localisation}</p></div>
      {d.recommended_next_steps?.length > 0 && <div><div className="caps">Recommended verification</div><ol className="small" style={{ margin: '6px 0 0', paddingLeft: 18 }}>{d.recommended_next_steps.map((s: string) => <li key={s}>{s}</li>)}</ol></div>}
      <div className="kv small">
        <dt>Method</dt><dd>{d.method}</dd>
        <dt>Confidence</dt><dd>{d.confidence_note}</dd>
        <dt>Data quality</dt><dd>{d.data_quality.freshness}; {d.data_quality.samples_in_window} samples{d.data_quality.issues.length ? `; issues: ${d.data_quality.issues.join(', ')}` : '; no quality flags'}</dd>
        <dt>Source data</dt><dd className="mono">telemetry[{d.asset_id}] {fmtDateTime(d.source.from_ts)} → {fmtDateTime(d.source.to_ts)}</dd>
      </div>
    </div>
  )
}
