import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { BellOff, Radio, ShieldCheck } from 'lucide-react'
import { api, Alert, AlertFingerprint, AlertSuppression, StreamAlertStatus } from '../api'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { InfoTip } from '../components/ui/InfoTip'
import { Card } from '../components/ui/Card'
import { LABELS, alertMeaning } from '../lib/labels'

function severityBadge(sev: string) {
  const cls = sev === 'warning'
    ? 'bg-warning/20 text-warning'
    : sev === 'critical'
      ? 'bg-error/20 text-error'
      : 'bg-info/15 text-info'
  return <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>{sev}</span>
}

function typeBadge(t: string) {
  const meaning = alertMeaning(t)
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-hover px-2 py-0.5 text-xs font-medium text-text-secondary">
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

function detailTerm(detail: Record<string, unknown>) {
  const value = detail.term
  return typeof value === 'string' ? value : null
}

function statusClass(status: string) {
  if (status === 'sent') return 'bg-success/15 text-success'
  if (status === 'suppressed') return 'bg-warning/15 text-warning'
  if (status === 'notify_failed') return 'bg-error/15 text-error'
  return 'bg-info/15 text-info'
}

function smallTime(iso: string | null) {
  return iso ? new Date(iso).toLocaleString() : '—'
}

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [loading, setLoading] = useState(true)
  const [streamStatus, setStreamStatus] = useState<StreamAlertStatus | null>(null)
  const [fingerprints, setFingerprints] = useState<AlertFingerprint[]>([])
  const [suppressions, setSuppressions] = useState<AlertSuppression[]>([])
  const [streamFilter, setStreamFilter] = useState('pending')
  const [silence, setSilence] = useState({ alert_type: '', source: '', reason: '' })
  const [streamMsg, setStreamMsg] = useState('')

  const load = () => {
    setLoading(true)
    api.getAlerts(page, unreadOnly)
      .then(r => { setAlerts(r.data); setTotal(r.total) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(load, [page, unreadOnly])
  const loadStream = () => {
    Promise.all([
      api.getStreamAlertStatus(),
      api.getAlertFingerprints(streamFilter, '', 50),
      api.getAlertSuppressions(true),
    ]).then(([status, fp, sup]) => {
      setStreamStatus(status)
      setFingerprints(fp.data)
      setSuppressions(sup.data)
    }).catch(() => {})
  }

  useEffect(loadStream, [streamFilter])

  const markRead = async (id: string) => {
    await api.markRead(id)
    load()
  }

  const markAllRead = async () => {
    await api.markAllRead()
    load()
  }

  const createSilence = async () => {
    if (!silence.reason.trim()) {
      setStreamMsg('Reason is required.')
      return
    }
    await api.createAlertSuppression({
      scope: 'manual',
      alert_type: silence.alert_type || null,
      source: silence.source || null,
      reason: silence.reason,
    })
    setSilence({ alert_type: '', source: '', reason: '' })
    setStreamMsg('Suppression added.')
    loadStream()
  }

  return (
    <div>
      <PageHeader
        title="Alerts"
        description="Classic entity alerts plus near-real-time stream fingerprints. Stream rows are grouped summaries only, not raw private messages."
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

      <div className="mb-6 grid gap-3 lg:grid-cols-3">
        <Card>
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <Radio className="h-4 w-4" /> Stream worker
          </div>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="rounded-md bg-hover p-2">
              <div className="text-lg font-semibold">{streamStatus?.sent_fingerprints ?? 0}</div>
              <div className="text-[0.7rem] text-text-muted">sent</div>
            </div>
            <div className="rounded-md bg-hover p-2">
              <div className="text-lg font-semibold">{streamStatus?.suppressed_fingerprints ?? 0}</div>
              <div className="text-[0.7rem] text-text-muted">suppressed</div>
            </div>
            <div className="rounded-md bg-hover p-2">
              <div className="text-lg font-semibold">{streamStatus?.active_suppressions ?? 0}</div>
              <div className="text-[0.7rem] text-text-muted">active silences</div>
            </div>
          </div>
          <div className="mt-2 text-xs text-text-muted">
            Last offset: {smallTime(streamStatus?.offsets?.[0]?.updated_at ?? null)}
          </div>
        </Card>

        <Card className="lg:col-span-2">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <ShieldCheck className="h-4 w-4" /> Stream fingerprints
              <InfoTip text="Fingerprints dedupe grouped alerts by type/source/window. Pending rows are what Telegram delivery will pick up." />
            </div>
            <select
              value={streamFilter}
              onChange={(e) => setStreamFilter(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1 text-xs"
            >
              <option value="">all</option>
              <option value="pending">pending</option>
              <option value="sent">sent</option>
              <option value="notify_failed">notify failed</option>
              <option value="suppressed">suppressed</option>
            </select>
          </div>
          {fingerprints.length === 0 ? (
            <div className="text-sm text-text-muted">No stream fingerprints for this filter.</div>
          ) : (
            <div className="max-h-72 overflow-auto">
              <table className="text-xs">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Source</th>
                    <th>Term</th>
                    <th>Count</th>
                    <th>Status</th>
                    <th>Window</th>
                  </tr>
                </thead>
                <tbody>
                  {fingerprints.map((fp) => (
                    <tr key={fp.fingerprint}>
                      <td>{fp.alert_type.replace(/_/g, ' ')}</td>
                      <td>{fp.source || '—'}</td>
                      <td><code>{detailTerm(fp.detail) || '—'}</code></td>
                      <td>{fp.count.toLocaleString()}</td>
                      <td><span className={`rounded-full px-2 py-0.5 ${statusClass(fp.status)}`}>{fp.status}</span></td>
                      <td>{smallTime(fp.window_start)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <Card className="mb-6">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <BellOff className="h-4 w-4" /> Stream suppressions
        </div>
        <div className="mb-3 grid gap-2 md:grid-cols-[1fr_1fr_2fr_auto]">
          <input
            value={silence.alert_type}
            onChange={(e) => setSilence({ ...silence, alert_type: e.target.value })}
            placeholder="alert type, blank = all"
            className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
          <input
            value={silence.source}
            onChange={(e) => setSilence({ ...silence, source: e.target.value })}
            placeholder="source, blank = all"
            className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
          <input
            value={silence.reason}
            onChange={(e) => setSilence({ ...silence, reason: e.target.value })}
            placeholder="reason"
            className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
          <button onClick={createSilence}>Silence</button>
        </div>
        {streamMsg && <div className="mb-2 text-xs text-text-muted">{streamMsg}</div>}
        <div className="flex flex-wrap gap-1">
          {suppressions.length === 0 ? (
            <span className="text-sm text-text-muted">No active suppressions.</span>
          ) : suppressions.map((s) => (
            <span key={s.id} className="rounded-full border border-border bg-hover px-2 py-0.5 text-xs text-text-secondary">
              {s.alert_type || 'all'} · {s.source || 'all'} · {s.reason}
            </span>
          ))}
        </div>
      </Card>

      {loading ? (
        <LoadingSpinner label="Loading alerts…" />
      ) : alerts.length === 0 ? (
        <EmptyState title="No alerts" description="Alerts about posting rhythm and coordinated behavior will show up here." />
      ) : (
        <>
          {alerts.map(a => (
            <Card key={a.id} className={`mb-2 ${a.is_read ? 'opacity-60' : ''}`}>
              <div className="mb-1 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {severityBadge(a.severity)}
                  {typeBadge(a.alert_type)}
                  <span className="text-xs text-text-muted">{timeAgo(a.detected_at)}</span>
                </div>
                {!a.is_read && (
                  <button onClick={() => markRead(a.id)} style={{ fontSize: '0.75rem', padding: '0.25rem 0.5rem' }}>
                    Mark read
                  </button>
                )}
              </div>
              <div style={{ fontWeight: 500 }}>{a.title}</div>
              {a.entity_id && (
                <div className="mt-1 text-sm text-text-muted">
                  <Link to={`/entities/${a.entity_id}`}>{a.entity_name || a.entity_id}</Link>
                </div>
              )}
            </Card>
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
