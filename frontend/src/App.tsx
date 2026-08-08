import { lazy, Suspense, useEffect, useState } from 'react'
import { Routes, Route } from 'react-router'
import { openHealthSocket, LiveHealth } from './api'
import { CommandPalette } from './components/CommandPalette'
import { AppShell } from './components/AppShell'
import { LoadingSpinner } from './components/ui/LoadingSpinner'

const TriagePage = lazy(() => import('./pages/Triage'))
const CasesPage = lazy(() => import('./pages/Cases'))
const AlertsPage = lazy(() => import('./pages/Alerts'))
const ReviewPage = lazy(() => import('./pages/Review'))
const EntitiesPage = lazy(() => import('./pages/Entities'))
const EntityDetailPage = lazy(() => import('./pages/EntityDetail'))
const RunsPage = lazy(() => import('./pages/Runs'))
const CommunitiesPage = lazy(() => import('./pages/Communities'))
const MediaPage = lazy(() => import('./pages/Media'))
const FacesPage = lazy(() => import('./pages/Faces'))
const SearchPage = lazy(() => import('./pages/Search'))
const HelpPage = lazy(() => import('./pages/Help'))

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
    <AppShell health={health}>
      <CommandPalette />
      <Suspense fallback={<LoadingSpinner className="min-h-[50vh]" />}>
        <Routes>
          <Route path="/" element={<TriagePage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/entities" element={<EntitiesPage />} />
          <Route path="/entities/:id" element={<EntityDetailPage />} />
          <Route path="/communities" element={<CommunitiesPage />} />
          <Route path="/media" element={<MediaPage />} />
          <Route path="/faces" element={<FacesPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/cases" element={<CasesPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/help" element={<HelpPage />} />
        </Routes>
      </Suspense>
    </AppShell>
  )
}

export default App
