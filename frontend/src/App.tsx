import { Routes, Route, NavLink } from 'react-router-dom'
import { useEffect, useState } from 'react'
import {
  Bell, Users, Network, Play, Images, ScanFace, ListChecks, FolderOpen, GitCompare,
} from 'lucide-react'
import TriagePage from './pages/Triage'
import CasesPage from './pages/Cases'
import AlertsPage from './pages/Alerts'
import ReviewPage from './pages/Review'
import EntitiesPage from './pages/Entities'
import EntityDetailPage from './pages/EntityDetail'
import RunsPage from './pages/Runs'
import CommunitiesPage from './pages/Communities'
import MediaPage from './pages/Media'
import FacesPage from './pages/Faces'
import { openHealthSocket, LiveHealth } from './api'
import { CommandPalette } from './components/CommandPalette'

const NAV = [
  { to: '/', label: 'Triage', icon: ListChecks, end: true },
  { to: '/review', label: 'Review', icon: GitCompare },
  { to: '/alerts', label: 'Alerts', icon: Bell },
  { to: '/entities', label: 'Entities', icon: Users },
  { to: '/communities', label: 'Communities', icon: Network },
  { to: '/media', label: 'Media', icon: Images },
  { to: '/faces', label: 'Faces', icon: ScanFace },
  { to: '/cases', label: 'Cases', icon: FolderOpen },
  { to: '/runs', label: 'Runs', icon: Play },
]

/** Subscribe to /ws/health, auto-reconnecting on drop. */
function useLiveHealth(): LiveHealth | null {
  const [health, setHealth] = useState<LiveHealth | null>(null)
  useEffect(() => {
    let ws: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout>
    let closed = false
    const connect = () => {
      ws = openHealthSocket(setHealth)
      ws.onclose = () => {
        if (!closed) retry = setTimeout(connect, 3000)
      }
    }
    connect()
    return () => {
      closed = true
      clearTimeout(retry)
      ws?.close()
    }
  }, [])
  return health
}

function App() {
  const health = useLiveHealth()

  return (
    <div className="flex min-h-screen">
      <CommandPalette />
      <nav className="flex w-56 flex-col gap-1 border-r border-border bg-card p-4">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-lg font-bold text-accent">Analyzer</h1>
          <kbd className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted">⌘K</kbd>
        </div>
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors ${
                isActive ? 'bg-hover text-fg' : 'text-muted hover:bg-hover hover:text-fg'
              }`
            }
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}

        <div className="mt-auto border-t border-border pt-3 text-xs">
          {health ? (
            <>
              <div className="mb-1.5 flex items-center font-medium">
                <span className={`health-dot ${health.status}`} />
                {health.status === 'ok' ? 'Healthy' : 'Degraded'}
              </div>
              <div className="text-muted">
                {health.entity_count.toLocaleString()} entities
                {health.alert_count_unread > 0 && <> · {health.alert_count_unread} unread</>}
              </div>
              <div className="text-muted">{health.media_items_analyzed.toLocaleString()} media analyzed</div>
            </>
          ) : (
            <span className="text-muted">Connecting…</span>
          )}
        </div>
      </nav>

      <main className="max-w-[1200px] flex-1 p-8">
        <Routes>
          <Route path="/" element={<TriagePage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/entities" element={<EntitiesPage />} />
          <Route path="/entities/:id" element={<EntityDetailPage />} />
          <Route path="/communities" element={<CommunitiesPage />} />
          <Route path="/media" element={<MediaPage />} />
          <Route path="/faces" element={<FacesPage />} />
          <Route path="/cases" element={<CasesPage />} />
          <Route path="/runs" element={<RunsPage />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
