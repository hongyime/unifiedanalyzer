import { Routes, Route, NavLink, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import AlertsPage from './pages/Alerts'
import EntitiesPage from './pages/Entities'
import EntityDetailPage from './pages/EntityDetail'
import RunsPage from './pages/Runs'
import { api, HealthInfo } from './api'

function App() {
  const location = useLocation()
  const [health, setHealth] = useState<HealthInfo | null>(null)

  useEffect(() => {
    api.getHealth().then(setHealth).catch(() => {})
    const iv = setInterval(() => {
      api.getHealth().then(setHealth).catch(() => {})
    }, 30000)
    return () => clearInterval(iv)
  }, [])

  const navClass = (path: string) =>
    location.pathname === path || location.pathname.startsWith(path + '/') ? 'active' : ''

  return (
    <div className="layout">
      <nav className="sidebar">
        <h1>Analyzer</h1>
        <NavLink to="/" className={navClass('/')} end>Alerts</NavLink>
        <NavLink to="/entities" className={navClass('/entities')}>Entities</NavLink>
        <NavLink to="/runs" className={navClass('/runs')}>Runs</NavLink>
        <div style={{ marginTop: 'auto', fontSize: '0.75rem' }}>
          {health && (
            <>
              <div className="mb-1">
                <span className={`health-dot ${health.status}`} />
                {health.status}
              </div>
              <div className="text-muted">
                {health.entity_count} entities
                {health.alert_count_unread > 0 && (
                  <> &middot; {health.alert_count_unread} unread</>
                )}
              </div>
            </>
          )}
        </div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<AlertsPage />} />
          <Route path="/entities" element={<EntitiesPage />} />
          <Route path="/entities/:id" element={<EntityDetailPage />} />
          <Route path="/runs" element={<RunsPage />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
