// Public product page. Claims are limited to what the application actually does.
import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Logo } from '../components/Shell'
import { Icon, ModeChip } from '../components/ui'
import { fmtNum, useAsset, useStore } from '../lib/store'
import { FactoryView, SceneBoundary, webglAvailable } from '../three/scenes'

function Reveal({ children, delay = 0, className = '' }: { children: ReactNode; delay?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { el.classList.add('in'); io.disconnect() } }, { threshold: 0.15 })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return <div ref={ref} className={`reveal ${className}`} style={{ transitionDelay: `${delay}ms` }}>{children}</div>
}

function HeroTele({ id, channel, label, unit }: { id: string; channel: string; label: string; unit: string }) {
  const a = useAsset(id)
  if (!a) return null
  const live = a.data_mode !== 'REAL' || a.freshness === 'LIVE'
  const v = a.telemetry[channel]
  return (
    <div className="hud-card">
      <div className="row" style={{ gap: 6 }}><span className="mono small">{id}</span><ModeChip mode={a.data_mode} /></div>
      <div className="mono" style={{ fontSize: 22, marginTop: 4 }}>{live && v !== undefined ? fmtNum(v) : '–'} <span className="small faint">{live && v !== undefined ? unit : a.data_mode === 'REAL' ? a.freshness.toLowerCase() : ''}</span></div>
      <div className="small faint">{label}</div>
    </div>
  )
}

const CHALLENGES: [string, string, string][] = [
  ['warn', 'Failures arrive unannounced', 'Most machines are checked on a calendar. Between checks, a bearing can go from fine to failed with nobody watching the trend.'],
  ['monitor', 'Data without context', 'A temperature number means little without the machine it belongs to, its baseline, and whether the sensor can be trusted right now.'],
  ['diag', 'Alarms without reasons', 'A red light says something is wrong. It rarely says what changed, what else it could be, or what to check first.'],
  ['shield', 'Demonstrations that blur real and fake', 'Pilots often mix live readings with invented ones. Decisions made on that footing do not survive contact with the plant.'],
]
const FEATURES: [string, string, string][] = [
  ['twin', 'Navigable 3D factory', 'Orbit, zoom and focus on any machine. Status, data source and alerts are attached to the asset they belong to.'],
  ['link', 'Physical-to-virtual link', 'An ESP32 node streams real motor telemetry. The matching virtual asset shows it live, and says so when it stops being live.'],
  ['sim', 'Fault simulation laboratory', 'Inject repeatable faults into virtual machines. Their signals respond together through one physical model.'],
  ['diag', 'Explainable diagnosis', 'Transparent rules list the evidence, rank plausible causes, name alternatives and say how to verify.'],
  ['layers', 'Exploded component views', 'Take a machine apart on screen, isolate a part and see which component a diagnosis suspects.'],
  ['wrench', 'Maintenance workbench', 'Turn a finding into a work order with a reviewed, auditable path from new to verified.'],
]

export default function Landing() {
  const gl = useMemo(webglAvailable, [])
  const link = useStore((s) => s.snapshot?.link)
  const [sent, setSent] = useState(false)
  const submit = (e: FormEvent) => { e.preventDefault(); setSent(true) }
  return (
    <div className="landing">
      <header className="land-nav">
        <a href="#top" className="row" style={{ color: 'var(--text)', gap: 10 }}><Logo /><span><div className="brand-name">NEXUS</div><div className="brand-sub">Industrial Intelligence</div></span></a>
        <nav aria-label="Sections"><a href="#challenges">Challenges</a><a href="#platform">Platform</a><a href="#workflow">Workflow</a><a href="#architecture">Architecture</a><a href="#security">Security</a><a href="#roadmap">Roadmap</a><a href="#contact">Contact</a></nav>
        <Link className="btn primary sm" to="/app/command">Open the platform</Link>
      </header>

      <section className="hero" id="top">
        <div className="hero-scene" aria-hidden="true">{gl && <SceneBoundary fallback={null}><FactoryView showcase /></SceneBoundary>}</div>
        <div className="hero-shade" />
        <div className="hero-copy">
          <span className="chip real" style={{ alignSelf: 'flex-start' }}>AI-powered industrial digital twin · predictive maintenance</span>
          <h1>See every asset. Understand every anomaly.</h1>
          <p className="lede">NEXUS joins live machine telemetry to an interactive 3D model of the plant, explains what changed and why it might matter, and carries the finding through to a verified maintenance action.</p>
          <div className="row">
            <Link className="btn primary" to="/app/command" style={{ height: 42, padding: '0 20px' }}>Explore the Digital Twin<Icon name="arrow" /></Link>
            <a className="btn" href="#platform" style={{ height: 42, padding: '0 20px' }}>View System Capabilities</a>
          </div>
          <p className="small faint">Proof-of-concept. The scene behind this text is the running application: one physical ESP32 node{link?.telemetry === 'LIVE' ? ' (live now)' : ' (not connected right now)'}, with every other machine simulated and labelled.</p>
        </div>
        <div className="hero-tele">
          <HeroTele id="MOTOR-001" channel="temperature_c" label="Motor temperature" unit="°C" />
          <HeroTele id="FAN-201" channel="vibration_mm_s" label="Fan vibration" unit="mm/s" />
          <HeroTele id="PUMP-301" channel="discharge_pressure_bar" label="Pump discharge pressure" unit="bar" />
        </div>
      </section>

      <section className="section" id="challenges">
        <Reveal><h2>The problems it is built for</h2><p className="sub">Maintenance teams rarely lack data. They lack a trustworthy line from a reading to a decision.</p></Reveal>
        <div className="grid cols-4">{CHALLENGES.map(([icon, title, text], i) => <Reveal key={title} delay={i * 70} className="feature"><Icon name={icon} /><h3>{title}</h3><p>{text}</p></Reveal>)}</div>
      </section>

      <section className="section" id="platform">
        <Reveal><h2>One platform, from sensor to work order</h2><p className="sub">Every capability below is implemented in this build and can be tried now.</p></Reveal>
        <div className="grid cols-3">{FEATURES.map(([icon, title, text], i) => <Reveal key={title} delay={i * 60} className="feature"><Icon name={icon} /><h3>{title}</h3><p>{text}</p></Reveal>)}</div>
      </section>

      <section className="section" id="sync">
        <div className="grid cols-2" style={{ alignItems: 'center', gap: 48 }}>
          <Reveal><h2>Physical and virtual, kept honest</h2><p className="sub" style={{ marginBottom: 16 }}>Each asset carries one of three labels everywhere it appears, in the 3D scene, on charts, in events and in exported files.</p>
            <div className="stack">
              <div className="row"><ModeChip mode="REAL" /><span className="muted">Measured by a connected device. Shown as STALE or OFFLINE when data stops; old values are never presented as live.</span></div>
              <div className="row"><ModeChip mode="SIMULATED" /><span className="muted">Generated by the simulation engine. Cannot overwrite a real measurement or be logged as a physical fault.</span></div>
              <div className="row"><ModeChip mode="REPLAY" /><span className="muted">Playback of a recorded session, with the recording's origin stated.</span></div>
            </div></Reveal>
          <Reveal delay={120} className="feature"><h3>Conveyor synchronisation</h3><p>The virtual conveyor's belt follows the measured speed of the physical motor through a documented mapping. With no valid speed reading the belt stops and the page says synchronisation is unavailable. It does not invent a speed.</p><Link to="/app/command?asset=CONV-001" className="small">See the conveyor →</Link></Reveal>
        </div>
      </section>

      <section className="section" id="workflow">
        <Reveal><h2>Predictive maintenance workflow</h2><p className="sub">Five steps, each recorded with its data source and who did it.</p></Reveal>
        <Reveal className="flow">
          <div data-n="01"><h3>Monitor</h3><p className="small muted">Telemetry is validated on arrival. Invalid readings are flagged, not displayed.</p></div>
          <div data-n="02"><h3>Detect</h3><p className="small muted">Limits or a baseline must be exceeded on several readings in a row before an alert is raised.</p></div>
          <div data-n="03"><h3>Explain</h3><p className="small muted">The diagnosis shows which signals moved, ranked causes, alternatives and evidence strength.</p></div>
          <div data-n="04"><h3>Inspect</h3><p className="small muted">The suspected component is highlighted in the exploded view, marked as suspected, not confirmed.</p></div>
          <div data-n="05"><h3>Act and verify</h3><p className="small muted">A work order moves through review, planning, work and verification, with before and after readings.</p></div>
        </Reveal>
      </section>

      <section className="section" id="architecture">
        <Reveal><h2>Integration architecture</h2><p className="sub">Four layers with clear boundaries, so a device, a model or a data source can be replaced without rebuilding the rest.</p></Reveal>
        <div className="grid cols-4">
          <Reveal className="feature"><Icon name="chip" /><h3>Equipment</h3><p>ESP32 node on a DC motor: temperature, acceleration, current. Speed, distance and light when fitted.</p></Reveal>
          <Reveal delay={70} className="feature"><Icon name="events" /><h3>Backend</h3><p>Validates telemetry, owns asset state and data modes, runs detection, authorises commands, keeps history.</p></Reveal>
          <Reveal delay={140} className="feature"><Icon name="twin" /><h3>Digital twin</h3><p>Browser-based 3D factory rendered from the asset configuration, driven by the live state stream.</p></Reveal>
          <Reveal delay={210} className="feature"><Icon name="overview" /><h3>Application</h3><p>Overview, monitoring, diagnostics, simulation, maintenance, analytics, events and system health.</p></Reveal>
        </div>
        <Reveal><p className="small faint" style={{ marginTop: 16 }}>Implemented today: USB serial to one ESP32. Not implemented: OPC UA, Modbus, PLC/SCADA, MQTT and maintenance-system connectors. They are on the roadmap below.</p></Reveal>
      </section>

      <section className="section" id="security">
        <Reveal><h2>Security and deployment approach</h2><p className="sub">Built for a local demonstration, with the boundaries a real deployment needs already in place.</p></Reveal>
        <div className="grid cols-3">
          <Reveal className="feature"><Icon name="shield" /><h3>Server-side authorisation</h3><p>Viewer, operator and admin roles are enforced by the backend on every action. Sign-in attempts are rate-limited.</p></Reveal>
          <Reveal delay={70} className="feature"><Icon name="chip" /><h3>Narrow device commands</h3><p>Three allowlisted commands, a buzzer limited to two seconds, increasing sequence numbers, and success shown only on device acknowledgement. No motor control.</p></Reveal>
          <Reveal delay={140} className="feature"><Icon name="link" /><h3>Local-first</h3><p>Runs on one machine. No telemetry leaves it and no external AI service is called. Secrets live in a local file, not in the code.</p></Reveal>
        </div>
        <Reveal><p className="small faint" style={{ marginTop: 16 }}>Still required before a networked deployment: TLS, per-user accounts with hashed passwords, signed device commands, backups and a security review. The ESP32 prototype is not a certified safety controller.</p></Reveal>
      </section>

      <section className="section" id="roadmap">
        <Reveal><h2>Deployment roadmap</h2><p className="sub">Where this goes next, in the order that reduces risk fastest.</p></Reveal>
        <Reveal className="flow">
          <div data-n="NOW"><h3>Proof of concept</h3><p className="small muted">One physical node, simulated fleet, rules-based diagnosis. Tested in software.</p></div>
          <div data-n="NEXT"><h3>Bench validation</h3><p className="small muted">Calibrate sensors, flash the command firmware, record healthy and deliberately altered runs.</p></div>
          <div data-n="THEN"><h3>Pilot on one line</h3><p className="small muted">Industrial sensors and a gateway, limits agreed with the maintenance team, measured false-alarm rate.</p></div>
          <div data-n="LATER"><h3>Plant integration</h3><p className="small muted">OPC UA or Modbus sources, maintenance-system connector, hardened access.</p></div>
          <div data-n="LATER"><h3>Validated models</h3><p className="small muted">Replace rules with models only where labelled plant data shows they do better.</p></div>
        </Reveal>
      </section>

      <section className="section" id="contact">
        <div className="grid cols-2" style={{ gap: 48 }}>
          <Reveal><h2>Request a demonstration</h2><p className="sub">See the platform run against the physical node and the simulated plant.</p><Link className="btn primary" to="/app/command">Or open the platform now<Icon name="arrow" /></Link></Reveal>
          <Reveal delay={100} className="feature">
            <span className="chip warn" style={{ alignSelf: 'flex-start' }}>DEMONSTRATION FORM</span>
            <p>This form is not connected to email or any service. Submitting it sends nothing and stores nothing.</p>
            {sent ? <div className="note" role="status"><b>Nothing was sent.</b> This is where a confirmation would appear once a contact service is connected.</div> : (
              <form className="stack" onSubmit={submit}>
                <label className="field">Name<input className="input" required autoComplete="name" /></label>
                <label className="field">Work email<input className="input" type="email" required autoComplete="email" /></label>
                <label className="field">What would you like to see?<textarea className="input" rows={3} /></label>
                <button className="btn">Submit (demonstration only)</button>
              </form>
            )}
          </Reveal>
        </div>
      </section>
      <footer className="footer"><span>NEXUS Industrial Intelligence · proof-of-concept build</span><span>3D models are conceptual. Simulated data is labelled wherever it appears.</span></footer>
    </div>
  )
}
