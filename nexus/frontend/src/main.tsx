import { useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes, useParams, useSearchParams } from 'react-router-dom'
import Shell, { Boot, Toasts } from './components/Shell'
import { startStream } from './lib/store'
import Command from './pages/Command'
import Landing from './pages/Landing'
import { Analytics, Events, Health, Settings } from './pages/Operations'
import { Assets, Monitoring } from './pages/Plant'
import { Diagnostics, Maintenance, Simulator } from './pages/Reliability'
import Analysis from './pages/Analysis'
import './styles.css'
import './command.css'

/** Old addresses (asset page, twin page) open the same thing on the Command Centre. */
function ToCommand() {
  const { id } = useParams()
  const [p] = useSearchParams()
  const q = new URLSearchParams()
  const asset = id || p.get('asset')
  if (asset) q.set('asset', asset)
  if (p.get('suspect')) q.set('suspect', p.get('suspect')!)
  return <Navigate to={`/app/command${q.toString() ? `?${q}` : ''}`} replace />
}

function App() {
  useEffect(() => { startStream() }, [])
  return (
    <BrowserRouter>
      <Boot />
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/app" element={<Shell />}>
          <Route index element={<Navigate to="command" replace />} />
          <Route path="command" element={<Command />} />
          <Route path="overview" element={<ToCommand />} />
          <Route path="twin" element={<ToCommand />} />
          <Route path="assets" element={<Assets />} />
          <Route path="assets/:id" element={<ToCommand />} />
          <Route path="monitoring" element={<Monitoring />} />
          <Route path="diagnostics" element={<Diagnostics />} />
          <Route path="simulator" element={<Simulator />} />
          <Route path="maintenance" element={<Maintenance />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="events" element={<Events />} />
          <Route path="health" element={<Health />} />
          <Route path="settings" element={<Settings />} />
          <Route path="analysis" element={<Analysis />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toasts />
    </BrowserRouter>
  )
}

// StrictMode is off on purpose: its double-mount in development doubles every WebGL context.
createRoot(document.getElementById('root')!).render(<App />)
