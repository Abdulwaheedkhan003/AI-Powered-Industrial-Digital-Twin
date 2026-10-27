// Analysis Hub: deep offline analysis from the predictive_maintenance engine.
// Sessions, anomaly detection, data quality, vibration FFT, model training.
import { FormEvent, useEffect, useState } from 'react'
import LineChart, { SERIES_COLORS, type Series } from '../components/LineChart'
import { Empty, ErrorNote, Icon, Loading, Note, Panel, Seg, useFetch } from '../components/ui'
import { act, api, fmtNum, fmtDateTime } from '../lib/store'

/* ---------- types -------------------------------------------------------- */
type Session = { session_id: string; file: string; data_source: string; start: string; end: string; samples: number; available_sensors: string; median_interval_s: number | null }
type AlertRow = { timestamp: string; signal: string; severity: string; alert_type: string; explanation: string; value: number }
type ScoreRow = { window_start: string; anomaly_score: number; anomaly_prediction: number }
type QualityRow = { sensor: string; unit: string; samples: number; valid: number; missing: number; out_of_range: number; sentinel: number; frozen: number; jumps: number; status: string }
type BurstRow = { burst_id: number; burst_time: string; rms_x_g: number; rms_y_g: number; rms_z_g: number; rms_total_g: number; peak_g: number; crest_factor: number }
type CompareRow = { sensor: string; unit: string; median_A: number; median_B: number; median_change: number; std_A: number; std_B: number }
type ModelMeta = { model_version: string; trained_at: string; data_source: string; n_windows: number; threshold: number; warnings: string[] }
type SpectrumPoint = { frequency_hz: number; amplitude_g: number }

/* ---------- sub-views ---------------------------------------------------- */
type Tab = 'sessions' | 'detection' | 'quality' | 'vibration' | 'models' | 'compare'

function SessionList({ sessions, active, onSelect }: { sessions: Session[]; active: string; onSelect: (f: string) => void }) {
  if (!sessions.length) return <Empty icon="inbox" title="No saved sessions">Record a session from the ESP32 or generate a synthetic demo.</Empty>
  return (
    <div className="table-scroll">
      <table>
        <thead><tr><th>Session</th><th>Source</th><th>Samples</th><th>Start</th><th>Sensors</th></tr></thead>
        <tbody>
          {sessions.map((s) => (
            <tr key={s.file} className={s.file === active ? 'active-row' : ''} onClick={() => onSelect(s.file)} style={{ cursor: 'pointer' }}>
              <td className="mono">{s.session_id}</td>
              <td><span className={`chip sm ${s.data_source === 'real_hardware' ? 'real' : s.data_source === 'synthetic_demo' ? 'sim' : ''}`}>{s.data_source}</span></td>
              <td>{s.samples}</td>
              <td className="small">{s.start?.slice(0, 19)}</td>
              <td className="small muted">{s.available_sensors}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DetectionView({ file }: { file: string }) {
  const [model, setModel] = useState<string>('')
  const { data, error, loading, reload } = useFetch(() => api(`/analysis/detect/${encodeURIComponent(file)}${model ? `?model_version=${model}` : ''}`, { method: 'POST' }), [file, model])
  const models = useFetch(() => api('/analysis/models'), [])

  if (loading) return <Loading />
  if (error) return <ErrorNote error={error} />
  if (!data) return null
  const d = data as any

  // Build anomaly score chart
  const scoresSeries: Series[] = []
  if (d.scores?.length) {
    scoresSeries.push({
      id: 'score', label: 'Anomaly Score', color: SERIES_COLORS[0],
      points: d.scores.map((s: ScoreRow) => [new Date(s.window_start).getTime() / 1000, s.anomaly_score] as [number, number]).filter((p: [number, number]) => p[1] !== null)
    })
  }

  return (
    <div className="stack">
      <div className="row gap" style={{ flexWrap: 'wrap' }}>
        <div className="stat-card"><div className="stat-value">{d.alert_count}</div><div className="stat-label">Alerts raised</div></div>
        <div className="stat-card"><div className="stat-value">{d.alert_summary?.threshold ?? 0}</div><div className="stat-label">Threshold</div></div>
        <div className="stat-card"><div className="stat-value">{d.alert_summary?.statistical ?? 0}</div><div className="stat-label">Statistical</div></div>
        <div className="stat-card"><div className="stat-value">{d.alert_summary?.ai_model ?? 0}</div><div className="stat-label">AI Model</div></div>
      </div>

      {d.notes?.length > 0 && <Note>{d.notes.map((n: string, i: number) => <div key={i}>• {n}</div>)}</Note>}

      <div className="row gap" style={{ alignItems: 'center' }}>
        <label className="small">AI Model:</label>
        <select className="input" style={{ maxWidth: 300 }} value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="">None</option>
          {(models.data as ModelMeta[] || []).map((m) => <option key={m.model_version} value={m.model_version}>{m.model_version}</option>)}
        </select>
        <button className="btn sm ghost" onClick={reload}><Icon name="reset" /> Re-run</button>
      </div>

      <p className="small muted">{d.ai_message}</p>

      {scoresSeries.length > 0 && (
        <Panel title="Anomaly Score Timeline">
          <LineChart series={scoresSeries} unit="score" height={200} empty="No AI scores available." />
        </Panel>
      )}

      {d.alerts?.length > 0 && (
        <Panel title="Alert Details">
          <div className="table-scroll">
            <table>
              <thead><tr><th>Time</th><th>Type</th><th>Signal</th><th>Severity</th><th>Explanation</th></tr></thead>
              <tbody>
                {d.alerts.slice(0, 100).map((a: AlertRow, i: number) => (
                  <tr key={i} className={a.severity === 'critical' ? 'row-crit' : a.severity === 'warning' ? 'row-warn' : ''}>
                    <td className="mono small">{a.timestamp?.slice(11, 19)}</td>
                    <td><span className="chip sm">{a.alert_type}</span></td>
                    <td>{a.signal}</td>
                    <td><span className={`chip sm ${a.severity === 'critical' ? 'crit' : 'warn'}`}>{a.severity}</span></td>
                    <td className="small">{a.explanation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  )
}

function QualityView({ file }: { file: string }) {
  const { data, error, loading } = useFetch(() => api(`/analysis/quality/${encodeURIComponent(file)}`), [file])
  if (loading) return <Loading />
  if (error) return <ErrorNote error={error} />
  if (!data) return null
  const d = data as any

  return (
    <div className="stack">
      <Note>These checks describe the <b>data</b>, not the motor. A problem here points to a sensor, wiring, or communication issue.</Note>

      <Panel title="Sensor Status">
        <div className="table-scroll">
          <table>
            <thead><tr><th>Sensor</th><th>Status</th><th>Valid</th><th>Missing</th><th>Out of range</th><th>Sentinel</th><th>Frozen</th><th>Jumps</th></tr></thead>
            <tbody>
              {(d.quality as QualityRow[]).map((q) => (
                <tr key={q.sensor}>
                  <td className="mono">{q.sensor}</td>
                  <td><span className={`chip sm ${q.status?.includes('unavailable') ? 'off' : q.status?.includes('invalid') || q.status?.includes('frozen') ? 'warn' : 'ok'}`}>{q.status}</span></td>
                  <td>{q.valid}</td><td>{q.missing}</td><td>{q.out_of_range}</td><td>{q.sentinel}</td><td>{q.frozen}</td><td>{q.jumps}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Sampling Quality">
        <div className="row gap" style={{ flexWrap: 'wrap' }}>
          <div className="stat-card"><div className="stat-value">{d.timing?.samples}</div><div className="stat-label">Samples</div></div>
          <div className="stat-card"><div className="stat-value">{fmtNum(d.timing?.median_interval_s, 's')}</div><div className="stat-label">Median interval</div></div>
          <div className="stat-card"><div className="stat-value">{fmtNum(d.timing?.approx_sample_rate_hz, 'Hz')}</div><div className="stat-label">Sample rate</div></div>
          <div className="stat-card"><div className="stat-value">{d.timing?.gaps ?? '–'}</div><div className="stat-label">Gaps</div></div>
        </div>
      </Panel>

      <Panel title="Statistics (valid readings only)">
        <div className="table-scroll">
          <table>
            <thead><tr><th>Sensor</th><th>Min</th><th>Max</th><th>Mean</th><th>Median</th><th>Std</th></tr></thead>
            <tbody>
              {(d.statistics as any[]).map((s) => (
                <tr key={s.sensor}><td className="mono">{s.sensor}</td><td>{fmtNum(s.min)}</td><td>{fmtNum(s.max)}</td><td>{fmtNum(s.mean)}</td><td>{fmtNum(s.median)}</td><td>{fmtNum(s.std)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

function VibrationView({ file }: { file: string }) {
  const { data, error, loading } = useFetch(() => api(`/analysis/vibration/${encodeURIComponent(file)}`), [file])
  if (loading) return <Loading />
  if (error) return <ErrorNote error={error} />
  if (!data) return null
  const d = data as any
  if (!d.has_bursts) return <Note>This dataset has no raw acceleration bursts. The original sketch sends one pre-processed vibration number per line; raw bursts need firmware v2.</Note>

  // RMS timeline
  const rmsSeries: Series[] = [
    { id: 'rms_total', label: 'RMS Total (g)', color: SERIES_COLORS[0], points: d.bursts.map((b: BurstRow) => [new Date(b.burst_time).getTime() / 1000, b.rms_total_g] as [number, number]) },
    { id: 'rms_x', label: 'RMS X (g)', color: SERIES_COLORS[1], points: d.bursts.map((b: BurstRow) => [new Date(b.burst_time).getTime() / 1000, b.rms_x_g] as [number, number]) },
    { id: 'rms_z', label: 'RMS Z (g)', color: SERIES_COLORS[2], points: d.bursts.map((b: BurstRow) => [new Date(b.burst_time).getTime() / 1000, b.rms_z_g] as [number, number]) },
  ]

  // FFT spectrum
  const fftSeries: Series[] = d.fft ? [{
    id: 'fft', label: 'FFT Amplitude', color: 'var(--accent)',
    points: d.fft.spectrum.map((p: SpectrumPoint) => [p.frequency_hz, p.amplitude_g] as [number, number])
  }] : []

  return (
    <div className="stack">
      <Note kind="">Gravity and constant offset are removed by subtracting each axis's mean within a burst. Values are in g. Sensor calibration and mounting are not verified.</Note>

      <Panel title={`Vibration RMS per burst — ${d.burst_count} bursts`}>
        <LineChart series={rmsSeries} unit="g" height={220} xLabel="time" />
      </Panel>

      <Panel title="Burst Summary">
        <div className="table-scroll">
          <table>
            <thead><tr><th>Burst</th><th>Time</th><th>RMS Total</th><th>Peak</th><th>Crest Factor</th></tr></thead>
            <tbody>
              {d.bursts.slice(-20).reverse().map((b: BurstRow) => (
                <tr key={b.burst_id}><td>{b.burst_id}</td><td className="mono small">{b.burst_time?.slice(11, 19)}</td><td>{fmtNum(b.rms_total_g, 'g')}</td><td>{fmtNum(b.peak_g, 'g')}</td><td>{fmtNum(b.crest_factor)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      {d.fft && (
        <Panel title={`Frequency Spectrum — Burst ${d.fft.burst_id}`}>
          <div className="row gap" style={{ flexWrap: 'wrap', marginBottom: 12 }}>
            <div className="stat-card sm"><div className="stat-value">{fmtNum(d.fft.sample_rate_hz, 'Hz')}</div><div className="stat-label">Sample rate</div></div>
            <div className="stat-card sm"><div className="stat-value">{fmtNum(d.fft.resolution_hz, 'Hz')}</div><div className="stat-label">Resolution</div></div>
            <div className="stat-card sm"><div className="stat-value">{fmtNum(d.fft.nyquist_hz, 'Hz')}</div><div className="stat-label">Nyquist</div></div>
            <div className="stat-card sm"><div className="stat-value">{d.fft.dominant_hz !== null ? fmtNum(d.fft.dominant_hz, 'Hz') : 'none clear'}</div><div className="stat-label">Dominant</div></div>
          </div>
          <LineChart series={fftSeries} unit="g" height={220} xLabel="seconds" />
          <p className="small muted">Hann window, mean removed. A peak does not by itself indicate a bearing or gear fault.</p>
        </Panel>
      )}
    </div>
  )
}

function ModelsView() {
  const { data, error, loading, reload } = useFetch(() => api('/analysis/models'), [])
  if (loading) return <Loading />
  if (error) return <ErrorNote error={error} />
  const models = (data as ModelMeta[]) || []

  return (
    <div className="stack">
      <Note>Train only on data you believe shows healthy operation, covering start-up and the speeds and loads you will monitor.</Note>
      {models.length === 0 && <Empty icon="target" title="No trained models">Use the "Train" button on a session to create one.</Empty>}
      {models.map((m) => (
        <div key={m.model_version} className="card">
          <div className="row gap"><b className="mono">{m.model_version}</b><span className={`chip sm ${m.data_source === 'synthetic_demo' ? 'sim' : 'real'}`}>{m.data_source}</span></div>
          <div className="small muted">Trained {m.trained_at?.slice(0, 19)} · {m.n_windows} windows · threshold {fmtNum(m.threshold)}</div>
          {m.warnings?.map((w, i) => <div key={i} className="small warn-text">⚠ {w}</div>)}
        </div>
      ))}
    </div>
  )
}

function CompareView({ sessions }: { sessions: Session[] }) {
  const [fileA, setFileA] = useState('')
  const [fileB, setFileB] = useState('')
  const { data, error, loading } = useFetch(() => (fileA && fileB ? api(`/analysis/compare?file_a=${encodeURIComponent(fileA)}&file_b=${encodeURIComponent(fileB)}`) : Promise.resolve(null)), [fileA, fileB])

  if (sessions.length < 2) return <Empty icon="layers" title="Need at least two sessions">Save at least two sessions to compare them.</Empty>

  return (
    <div className="stack">
      <div className="row gap">
        <label className="field" style={{ flex: 1 }}>Session A
          <select className="input" value={fileA} onChange={(e) => setFileA(e.target.value)}>
            <option value="">Select…</option>
            {sessions.map((s) => <option key={s.file} value={s.file}>{s.session_id} [{s.data_source}]</option>)}
          </select>
        </label>
        <label className="field" style={{ flex: 1 }}>Session B
          <select className="input" value={fileB} onChange={(e) => setFileB(e.target.value)}>
            <option value="">Select…</option>
            {sessions.map((s) => <option key={s.file} value={s.file}>{s.session_id} [{s.data_source}]</option>)}
          </select>
        </label>
      </div>
      {loading && <Loading />}
      {error && <ErrorNote error={error} />}
      {data && (
        <>
          {(data as any).warnings?.map((w: string, i: number) => <Note key={i} kind="warn">⚠ {w}</Note>)}
          <div className="table-scroll">
            <table>
              <thead><tr><th>Sensor</th><th>Unit</th><th>Median A</th><th>Median B</th><th>Change</th><th>Std A</th><th>Std B</th></tr></thead>
              <tbody>
                {((data as any).comparison as CompareRow[]).map((r) => (
                  <tr key={r.sensor}>
                    <td className="mono">{r.sensor}</td><td className="small">{r.unit}</td>
                    <td>{fmtNum(r.median_A)}</td><td>{fmtNum(r.median_B)}</td>
                    <td className={r.median_change > 0 ? 'warn-text' : ''}>{r.median_change !== null ? (r.median_change > 0 ? '+' : '') + fmtNum(r.median_change) : '–'}</td>
                    <td>{fmtNum(r.std_A)}</td><td>{fmtNum(r.std_B)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

/* ---------- main page ---------------------------------------------------- */
export default function Analysis() {
  const [tab, setTab] = useState<Tab>('sessions')
  const [selectedFile, setSelectedFile] = useState('')
  const { data: sessionsRaw, reload: reloadSessions } = useFetch(() => api('/analysis/sessions'), [])
  const sessions = (sessionsRaw as Session[]) || []
  const [generating, setGenerating] = useState(false)
  const [training, setTraining] = useState(false)

  const selected = sessions.find((s) => s.file === selectedFile)

  const generateDemo = async () => {
    setGenerating(true)
    const result = await act(() => api('/analysis/demo/generate', { method: 'POST', body: { scenario: 'increasing_vibration', duration_s: 900, seed: 42 } }), 'Demo session generated')
    setGenerating(false)
    if (result) reloadSessions()
  }

  const trainModel = async () => {
    if (!selectedFile) return
    setTraining(true)
    await act(() => api('/analysis/train', { method: 'POST', body: { session_file: selectedFile, conditions: 'Dashboard training' } }), 'Model trained successfully')
    setTraining(false)
  }

  return (
    <div className="analysis-page stack">
      <div className="page-header">
        <h1><Icon name="search" /> Analysis Hub</h1>
        <p className="muted">Deep offline analysis from recorded or synthetic sessions — anomaly detection, FFT, model training, data quality.</p>
      </div>

      <div className="analysis-tabs">
        <Seg value={tab} onChange={setTab as any} label="Analysis view" options={[
          ['sessions', 'Sessions'], ['detection', 'Anomaly Detection'], ['quality', 'Data Quality'],
          ['vibration', 'Vibration FFT'], ['models', 'AI Models'], ['compare', 'Compare'],
        ]} />
      </div>

      {/* Active session bar */}
      {selected && tab !== 'sessions' && tab !== 'models' && tab !== 'compare' && (
        <div className="active-session-bar">
          <Icon name="target" />
          <span className="mono">{selected.session_id}</span>
          <span className={`chip sm ${selected.data_source === 'real_hardware' ? 'real' : 'sim'}`}>{selected.data_source}</span>
          <span className="small muted">{selected.samples} samples</span>
          <span className="spacer" />
          <button className="btn sm ghost" onClick={() => setTab('sessions')}>Change session</button>
          {tab === 'detection' && <button className="btn sm primary" disabled={training} onClick={trainModel}>{training ? 'Training…' : 'Train AI model'}</button>}
          <a className="btn sm ghost" href={`/api/analysis/report/${encodeURIComponent(selectedFile)}`} download><Icon name="download" /> Report</a>
        </div>
      )}

      {tab === 'sessions' && (
        <div className="stack">
          <div className="row gap">
            <button className="btn primary" disabled={generating} onClick={generateDemo}>{generating ? 'Generating…' : 'Generate demo session'}</button>
            <button className="btn ghost" onClick={reloadSessions}><Icon name="reset" /> Refresh</button>
          </div>
          <SessionList sessions={sessions} active={selectedFile} onSelect={(f) => { setSelectedFile(f); setTab('detection') }} />
        </div>
      )}
      {tab === 'detection' && selectedFile && <DetectionView file={selectedFile} />}
      {tab === 'detection' && !selectedFile && <Empty icon="search" title="Select a session">Pick a session from the Sessions tab to run anomaly detection.</Empty>}
      {tab === 'quality' && selectedFile && <QualityView file={selectedFile} />}
      {tab === 'quality' && !selectedFile && <Empty icon="search" title="Select a session">Pick a session from the Sessions tab.</Empty>}
      {tab === 'vibration' && selectedFile && <VibrationView file={selectedFile} />}
      {tab === 'vibration' && !selectedFile && <Empty icon="search" title="Select a session">Pick a session from the Sessions tab.</Empty>}
      {tab === 'models' && <ModelsView />}
      {tab === 'compare' && <CompareView sessions={sessions} />}
    </div>
  )
}
