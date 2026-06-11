import { useEffect, useState } from 'react'
import { api, CollectorInfo } from '../api'

function timeAgo(iso: string | null): string {
  if (!iso) return 'never'
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function statusColor(status: string | null, lastCompleted: string | null): string {
  if (!lastCompleted) return 'badge-gray'
  const hours = (Date.now() - new Date(lastCompleted).getTime()) / 3600000
  if (status === 'failed') return 'badge-red'
  if (hours > 24) return 'badge-yellow'
  return 'badge-green'
}

export default function CollectorHealthPage() {
  const [collectors, setCollectors] = useState<CollectorInfo[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getCollectorHealth()
      .then(r => setCollectors(r.collectors))
      .finally(() => setLoading(false))
    const iv = setInterval(() => {
      api.getCollectorHealth().then(r => setCollectors(r.collectors)).catch(() => {})
    }, 30000)
    return () => clearInterval(iv)
  }, [])

  if (loading) return <div className="empty-state">Loading...</div>

  return (
    <div>
      <h2>Collector Health</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1rem' }}>
        {collectors.map(c => (
          <div key={c.source} className="card">
            <div className="flex-between mb-1">
              <span className={`platform-icon p-${c.source}`}>{c.source}</span>
              <span className={`badge ${statusColor(c.latest_status, c.last_completed)}`}>
                {c.latest_status || 'unknown'}
              </span>
            </div>
            <div className="text-sm" style={{ marginBottom: '0.75rem' }}>
              <div className="flex-between">
                <span className="text-muted">Last run</span>
                <span>{timeAgo(c.last_completed)}</span>
              </div>
              <div className="flex-between">
                <span className="text-muted">Items (24h)</span>
                <span>{c.items_24h.toLocaleString()}</span>
              </div>
              <div className="flex-between">
                <span className="text-muted">Failed (24h)</span>
                <span style={{ color: c.failed_24h > 0 ? 'var(--red)' : 'inherit' }}>{c.failed_24h}</span>
              </div>
              <div className="flex-between">
                <span className="text-muted">Runs (24h)</span>
                <span>{c.runs_24h}</span>
              </div>
            </div>
            {c.targets.length > 0 && (
              <div className="text-sm">
                <div className="text-muted" style={{ marginBottom: '0.25rem' }}>Targets:</div>
                {c.targets.map((t, i) => (
                  <div key={i} className="flex-between">
                    <span className={`badge ${t.status === 'active' ? 'badge-green' : t.status === 'error' ? 'badge-red' : 'badge-gray'}`}>
                      {t.status}
                    </span>
                    <span>{t.count} targets</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      {collectors.length === 0 && <div className="empty-state">No collector data found</div>}
    </div>
  )
}
