// Application frame: navigation, connection state, alerts, sign-in.
import { AnimatePresence, motion } from 'framer-motion'
import { FormEvent, useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { act, api, fmtTime, openLogin, setAuth, useStore } from '../lib/store'
import { Icon } from './ui'

export const NAV: [string, string, string][] = [
  ['command', 'Command Centre', 'twin'], ['assets', 'Assets', 'assets'],
  ['monitoring', 'Live Monitoring', 'monitor'], ['diagnostics', 'AI Diagnostics', 'diag'],
  ['maintenance', 'Maintenance', 'wrench'], ['analytics', 'Analytics', 'chart'], ['events', 'Event History', 'events'],
  ['health', 'System Health', 'health'], ['settings', 'Settings', 'settings'],
]

export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <path d="M16 2.5 28 9.3v13.4L16 29.5 4 22.7V9.3z" stroke="var(--accent)" strokeWidth="1.6" />
      <path d="M10.5 21V11l11 10V11" stroke="var(--text)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function Alerts() {
  const assets = useStore((s) => s.snapshot?.assets) || []
  const [open, setOpen] = useState(false)
  const active = assets.filter((a) => a.anomaly_state !== 'NORMAL' && a.data_mode !== 'REPLAY')
  const crit = active.some((a) => a.anomaly_state === 'CRITICAL')
  return (
    <div style={{ position: 'relative' }}>
      <button className="btn ghost icon" aria-label={`Alerts: ${active.length} active`} aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="bell" />{active.length > 0 && <span className={`badge ${crit ? '' : 'warn'}`} style={{ position: 'absolute', top: 2, right: 0 }}>{active.length}</span>}
      </button>
      <AnimatePresence>
        {open && (
          <motion.div className="popover" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.16 }}>
            <div className="panel-head"><h2>Active faults</h2><Link className="btn sm ghost" to="/app/events" onClick={() => setOpen(false)}>History</Link></div>
            {active.length === 0 && <div className="empty small">No active faults.</div>}
            {active.map((a) => (
              <Link key={a.asset_id} to={`/app/command?asset=${a.asset_id}&autopsy=1`} onClick={() => setOpen(false)} style={{ display: 'block', padding: '10px 16px', borderBottom: '1px solid var(--line)', color: 'inherit' }}>
                <div className="row"><span className={`dot ${a.anomaly_state === 'CRITICAL' ? 'crit' : 'warn'}`} /><b>{a.asset_name}</b></div>
                <div className="small muted">Fault: {a.active_faults.map((f) => f.label).join(', ')}</div>
              </Link>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function LoginModal() {
  const open = useStore((s) => s.loginOpen)
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (open) { setError(''); setPassword('') } }, [open])
  if (!open) return null
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError('')
    try { setAuth(await api('/auth/login', { method: 'POST', body: { username, password } })); openLogin(false) } catch (err: any) { setError(err.message) }
    setBusy(false)
  }
  return (
    <div className="scrim" onClick={() => openLogin(false)} role="dialog" aria-modal="true" aria-label="Sign in">
      <motion.form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit} initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 0.18 }}>
        <div className="panel-head"><h2>Sign in</h2><button type="button" className="btn ghost icon sm" aria-label="Close" onClick={() => openLogin(false)}><Icon name="x" /></button></div>
        <div className="panel-body stack">
          <p className="small muted">Viewing needs no account. Running simulations, acknowledging alerts and maintenance need <b>operator</b>; device commands and settings need <b>admin</b>.</p>
          <label className="field">Account<select className="input" value={username} onChange={(e) => setUsername(e.target.value)}><option value="admin">admin</option><option value="operator">operator</option></select></label>
          <label className="field">Password<input className="input" type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
          <p className="small faint">Passwords were generated on first start and are stored in the local file <span className="mono">nexus/.env</span>.</p>
          {error && <div className="note crit">{error}</div>}
          <button className="btn primary" disabled={busy || !password}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </div>
      </motion.form>
    </div>
  )
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts)
  return (
    <div className="toasts" aria-live="polite">
      <AnimatePresence>{toasts.map((t) => <motion.div key={t.id} className={`toast ${t.kind}`} initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 24 }} transition={{ duration: 0.2 }}>{t.text}</motion.div>)}</AnimatePresence>
    </div>
  )
}

export function Boot() {
  const site = useStore((s) => s.site), err = useStore((s) => s.siteError), conn = useStore((s) => s.connection), snap = useStore((s) => s.snapshot)
  const [gone, setGone] = useState(false)
  const steps = [conn === 'open', !!site, !!snap]
  const done = steps.every(Boolean)
  useEffect(() => { if (done) { const t = setTimeout(() => setGone(true), 450); return () => clearTimeout(t) } }, [done])
  if (gone) return null
  const label = !steps[0] ? 'Connecting to the backend' : !steps[1] ? 'Loading the asset model' : !steps[2] ? 'Waiting for the first state update' : 'Ready'
  return (
    <motion.div className="boot" animate={{ opacity: done ? 0 : 1 }} transition={{ duration: 0.4 }}>
      <div className="boot-inner">
        <svg className="boot-mark" width="44" height="44" viewBox="0 0 32 32" fill="none"><path d="M16 2.5 28 9.3v13.4L16 29.5 4 22.7V9.3z" stroke="var(--accent)" strokeWidth="1.4" /><path d="M10.5 21V11l11 10V11" stroke="var(--text)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        <div><div className="brand-name">NEXUS</div><div className="brand-sub">Industrial Intelligence</div></div>
        <div className="boot-bar"><i style={{ width: `${(steps.filter(Boolean).length / 3) * 100}%` }} /></div>
        <div className="small muted">{label}…</div>
        {err && conn !== 'open' && <div className="note crit small">The backend is not answering. Start it with <span className="mono">run_nexus.bat</span>; this page will connect by itself.</div>}
      </div>
    </motion.div>
  )
}

export default function Shell() {
  const [collapsed, setCollapsed] = useState(false)
  const site = useStore((s) => s.site?.site)
  const ts = useStore((s) => s.snapshot?.ts)
  const alerts = useStore((s) => s.snapshot?.assets.filter((a) => a.anomaly_state !== 'NORMAL' && a.data_mode !== 'REPLAY').length) || 0
  const loc = useLocation()
  const section = NAV.find((n) => loc.pathname.startsWith(`/app/${n[0]}`))
  const tight = loc.pathname.startsWith('/app/command')
  return (
    <div className={`shell ${collapsed ? 'collapsed' : ''}`}>
      <aside className="sidebar">
        <Link to="/" className="brand" aria-label="NEXUS home"><Logo /><span className="brand-text"><div className="brand-name">NEXUS</div><div className="brand-sub">Industrial Intelligence</div></span></Link>
        <nav className="nav" aria-label="Main">
          {NAV.map(([path, label, icon]) => (
            <NavLink key={path} to={`/app/${path}`} title={label}><Icon name={icon} /><span className="nav-label">{label}</span>{path === 'events' && alerts > 0 && <span className="badge warn">{alerts}</span>}</NavLink>
          ))}
        </nav>
        <div className="side-foot">
          <button className="btn ghost icon sm" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}><Icon name="menu" /></button>
          <span className="side-foot-text small faint">Proof-of-concept build</span>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="crumbs"><span className="hide-sm">{site?.name || 'Plant'}</span><span className="hide-sm">/</span><span className="hide-sm">{site?.area}</span><span className="hide-sm">/</span><b>{section?.[1] || 'Asset'}</b></div>
          <span className="spacer" />
          <span className="small faint mono hide-sm" title="Time of the last state update from the backend">{fmtTime(ts)}</span>
          <Alerts />
        </header>
        <main className={`content ${tight ? 'tight' : ''}`}>
          <AnimatePresence mode="wait">
            <motion.div key={loc.pathname.split('/').slice(0, 3).join('/')} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.2, ease: [0.22, 0.8, 0.24, 1] }}>
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  )
}
