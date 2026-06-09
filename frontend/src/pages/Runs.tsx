import { useEffect, useState } from 'react'
import { api, RunInfo } from '../api'

function statusBadge(s: string) {
  const cls = s === 'completed' ? 'badge-green' : s === 'running' ? 'badge-yellow' : 'badge-red'
  return <span className={`badge ${cls}`}>{s}</span>
}

function formatDate(iso: string | null) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString()
}

function duration(start: string | null, end: string | null) {
  if (!start || !end) return '-'
  const ms = new Date(end).getTime() - new Date(start).getTime()
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export default function RunsPage() {
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [triggering, setTriggering] = useState(false)

  const load = () => {
    setLoading(true)
    api.getRuns(page)
      .then(r => { setRuns(r.data); setTotal(r.total) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(load, [page])

  const trigger = async () => {
    setTriggering(true)
    try {
      await api.triggerRun()
      load()
    } catch {
      // handled
    } finally {
      setTriggering(false)
    }
  }

  return (
    <div>
      <div className="flex-between mb-2">
        <h2>Analysis Runs</h2>
        <button className="primary" onClick={trigger} disabled={triggering}>
          {triggering ? 'Running...' : 'Trigger Run'}
        </button>
      </div>

      {loading ? (
        <div className="empty-state">Loading...</div>
      ) : runs.length === 0 ? (
        <div className="empty-state">No runs yet. Click "Trigger Run" to start.</div>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>Status</th>
                <th>Started</th>
                <th>Duration</th>
                <th>Entities</th>
                <th>Events</th>
                <th>Alerts</th>
                <th>Signals</th>
              </tr>
            </thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.id}>
                  <td>{r.run_type}</td>
                  <td>{statusBadge(r.status)}</td>
                  <td className="text-sm">{formatDate(r.started_at)}</td>
                  <td className="text-sm">{duration(r.started_at, r.finished_at)}</td>
                  <td>{r.entities_processed}</td>
                  <td>{r.events_created}</td>
                  <td>{r.alerts_created}</td>
                  <td>{r.signals_created}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex-between" style={{ marginTop: '1rem' }}>
            <span className="text-sm text-muted">{total} runs</span>
            <div className="flex gap-1">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Prev</button>
              <button disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)}>Next</button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
