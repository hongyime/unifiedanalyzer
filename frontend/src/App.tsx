import { Routes, Route } from 'react-router-dom'
import { useEffect, useState } from 'react'
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
import HelpPage from './pages/Help'
import { openHealthSocket, LiveHealth } from './api'
import { CommandPalette } from './components/CommandPalette'
import { Sidebar } from './components/layout/Sidebar'

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
    <div className="min-h-screen bg-background">
      <CommandPalette />
      <Sidebar health={health} />
      <main className="ml-52 p-6">
        <div className="mx-auto max-w-7xl">
          <Routes>
            <Route path="/" element={<TriagePage />} />
            <Route path="/review" element={<ReviewPage />} />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/entities" element={<EntitiesPage />} />
            <Route path="/entities/:id" element={<EntityDetailPage />} />
            <Route path="/communities" element={<CommunitiesPage />} />
            <Route path="/media" element={<MediaPage />} />
            <Route path="/faces" element={<FacesPage />} />
            <Route path="/cases" element={<CasesPage />} />
            <Route path="/runs" element={<RunsPage />} />
            <Route path="/help" element={<HelpPage />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}

export default App
