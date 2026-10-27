// Time-series line chart: one y-axis, 2px lines, crosshair tooltip, gaps left as gaps.
import { useEffect, useMemo, useRef, useState } from 'react'
import { fmtNum, fmtTime } from '../lib/store'

export type Series = { id: string; label: string; color: string; points: [number, number][] }
type Props = { series: Series[]; unit: string; height?: number; warn?: number | null; crit?: number | null; xLabel?: 'time' | 'seconds'; empty?: string }

export const SERIES_COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)']

export default function LineChart({ series, unit, height = 220, warn, crit, xLabel = 'time', empty = 'No data in this range.' }: Props) {
  const wrap = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(600)
  const [hover, setHover] = useState<number | null>(null)
  useEffect(() => {
    if (!wrap.current) return
    const ro = new ResizeObserver((e) => setWidth(Math.max(240, e[0].contentRect.width)))
    ro.observe(wrap.current)
    return () => ro.disconnect()
  }, [])

  const m = { l: 52, r: 12, t: 10, b: 24 }
  const all = series.flatMap((s) => s.points)
  const model = useMemo(() => {
    if (!all.length) return null
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity
    for (const [x, y] of all) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y }
    for (const l of [warn, crit]) if (l !== null && l !== undefined && l < y1 * 1.6 && l > y0 * 0.4) { y0 = Math.min(y0, l); y1 = Math.max(y1, l) }
    const pad = (y1 - y0 || Math.abs(y1) || 1) * 0.12
    y0 -= pad; y1 += pad
    if (x1 === x0) x1 = x0 + 1
    const sx = (x: number) => m.l + ((x - x0) / (x1 - x0)) * (width - m.l - m.r)
    const sy = (y: number) => m.t + (1 - (y - y0) / (y1 - y0)) * (height - m.t - m.b)
    const paths = series.map((s) => {
      // A gap longer than 3x the usual spacing is missing data: break the line, never bridge it.
      const gaps = s.points.slice(1).map((p, i) => p[0] - s.points[i][0]).sort((a, b) => a - b)
      const typical = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 0
      let d = ''
      s.points.forEach((p, i) => {
        const broken = i > 0 && typical > 0 && p[0] - s.points[i - 1][0] > typical * 3
        d += `${i === 0 || broken ? 'M' : 'L'}${sx(p[0]).toFixed(1)} ${sy(p[1]).toFixed(1)}`
      })
      return d
    })
    return { x0, x1, y0, y1, sx, sy, paths }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [all.length, all[all.length - 1]?.[0], all[0]?.[0], width, height, warn, crit, series.length])

  if (!model) return <div ref={wrap} className="chart"><div className="empty small" style={{ height }}>{empty}</div></div>
  const { x0, x1, y0, y1, sx, sy, paths } = model
  const yTicks = [0, 1, 2, 3].map((i) => y0 + ((y1 - y0) * i) / 3)
  const xTicks = [0, 1, 2, 3].map((i) => x0 + ((x1 - x0) * i) / 3)
  const xText = (x: number) => (xLabel === 'time' ? fmtTime(x) : `${Math.round(x)} s`)

  let tip: { x: number; rows: { s: Series; p: [number, number] }[] } | null = null
  if (hover !== null) {
    const rows = series.map((s) => {
      let best = s.points[0]
      for (const p of s.points) if (best === undefined || Math.abs(sx(p[0]) - hover) < Math.abs(sx(best[0]) - hover)) best = p
      return { s, p: best }
    }).filter((r) => r.p)
    if (rows.length) tip = { x: sx(rows[0].p[0]), rows }
  }

  return (
    <div ref={wrap} className="chart">
      {series.length > 1 && <div className="legend" style={{ marginBottom: 6 }}>{series.map((s) => <span key={s.id}><i style={{ background: s.color }} />{s.label}</span>)}</div>}
      <svg height={height} role="img" aria-label={`${series.map((s) => s.label).join(', ')} over time, in ${unit}`}
        onMouseMove={(e) => setHover(e.clientX - e.currentTarget.getBoundingClientRect().left)} onMouseLeave={() => setHover(null)}>
        {yTicks.map((t, i) => <g key={i}><line className="grid-line" x1={m.l} x2={width - m.r} y1={sy(t)} y2={sy(t)} /><text className="axis-text" x={m.l - 8} y={sy(t) + 3} textAnchor="end">{fmtNum(t)}</text></g>)}
        {xTicks.map((t, i) => <text key={i} className="axis-text" x={sx(t)} y={height - 6} textAnchor={i === 0 ? 'start' : i === 3 ? 'end' : 'middle'}>{xText(t)}</text>)}
        <text className="axis-text" x={m.l} y={m.t} textAnchor="start">{unit}</text>
        {warn !== null && warn !== undefined && warn >= y0 && warn <= y1 && <g><line className="limit" stroke="var(--warn)" x1={m.l} x2={width - m.r} y1={sy(warn)} y2={sy(warn)} /><text className="axis-text" x={width - m.r} y={sy(warn) - 4} textAnchor="end">warning {fmtNum(warn)}</text></g>}
        {crit !== null && crit !== undefined && crit >= y0 && crit <= y1 && <g><line className="limit" stroke="var(--crit)" x1={m.l} x2={width - m.r} y1={sy(crit)} y2={sy(crit)} /><text className="axis-text" x={width - m.r} y={sy(crit) - 4} textAnchor="end">critical {fmtNum(crit)}</text></g>}
        {paths.map((d, i) => <path key={series[i].id} className="series draw" pathLength={1} d={d} stroke={series[i].color} />)}
        {tip && <g><line className="cross" x1={tip.x} x2={tip.x} y1={m.t} y2={height - m.b} />{tip.rows.map((r) => <circle key={r.s.id} cx={sx(r.p[0])} cy={sy(r.p[1])} r={4} fill={r.s.color} stroke="var(--surface-1)" strokeWidth={2} />)}</g>}
      </svg>
      {tip && (
        <div className="chart-tip" style={{ left: Math.min(Math.max(tip.x + 10, 0), width - 170), top: 8 }}>
          <div className="faint mono">{xText(tip.rows[0].p[0])}</div>
          {tip.rows.map((r) => <div key={r.s.id}><i style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 4, background: r.s.color, marginRight: 6 }} />{series.length > 1 ? `${r.s.label}: ` : ''}<span className="mono">{fmtNum(r.p[1], unit)}</span></div>)}
        </div>
      )}
    </div>
  )
}
