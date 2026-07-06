import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, Alert } from '../api'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { InfoTip } from '../components/ui/InfoTip'
import { LABELS, alertMeaning } from '../lib/labels'

function severityBadge(sev: string) {
  const cls = sev === 'warning' ? 'badge-yellow' : sev === 'critical' ? 'badge-red' : 'badge-blue'
  return <span className={`badge ${cls}`}>{sev}</span>
}

function typeBadge(t: string) {
  const meaning = alertMeaning(t)
  return (
    <span className="badge badge-gray inline-flex items-center gap-1">
      {LABELS.alertType[t] ?? t.replace(/_/g, ' ')}
      {meaning && <InfoTip text={meaning} />}
    </span>
  )
}

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    api.getAlerts(page, unreadOnly)
      .then(r => { setAlerts(r.data); setTotal(r.total) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(load, [page, unreadOnly])

  const markRead = async (id: string) => {
    await api.markRead(id)
    load()
  }

  const markAllRead = async () => {
    await api.markAllRead()
    load()
  }

  return (
    <div>
      <PageHeader
        title="Alerts"
        description="Things worth a look — someone going quiet, becoming active again, or two accounts behaving as one. Hover the (?) on any type for what it means."
        actions={
          <>
            <label style={{ fontSize: '0.875rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <input type="checkbox" checked={unreadOnly} onChange={e => { setUnreadOnly(e.target.checked); setPage(1) }} />
              Unread only
            </label>
            <button onClick={markAllRead}>Mark all read</button>
          </>
        }
      />

      {loading ? (
        <LoadingSpinner label="Loading alerts…" />
      ) : alerts.length === 0 ? (
        <EmptyState title="No alerts" description="Alerts about posting rhythm and coordinated behavior will show up here." />
      ) : (
        <>
          {alerts.map(a => (
            <div key={a.id} className="card" style={{ opacity: a.is_read ? 0.6 : 1 }}>
              <div className="flex-between mb-1">
                <div className="flex gap-1" style={{ alignItems: 'center' }}>
                  {severityBadge(a.severity)}
                  {typeBadge(a.alert_type)}
                  <span className="text-muted text-sm">{timeAgo(a.detected_at)}</span>
                </div>
                {!a.is_read && (
                  <button onClick={() => markRead(a.id)} style={{ fontSize: '0.75rem', padding: '0.25rem 0.5rem' }}>
                    Mark read
                  </button>
                )}
              </div>
              <div style={{ fontWeight: 500 }}>{a.title}</div>
              {a.entity_id && (
                <div className="text-sm text-muted" style={{ marginTop: '0.25rem' }}>
                  <Link to={`/entities/${a.entity_id}`}>{a.entity_name || a.entity_id}</Link>
                </div>
              )}
            </div>
          ))}
          <div className="flex-between" style={{ marginTop: '1rem' }}>
            <span className="text-sm text-muted">{total} total</span>
            <div className="flex gap-1">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Prev</button>
              <button disabled={page * 50 >= total} onClick={() => setPage(p => p + 1)}>Next</button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
