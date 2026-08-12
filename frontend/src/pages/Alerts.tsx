import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { BellOff, Radio, ShieldCheck, X } from 'lucide-react'
import { api, Alert, AlertFingerprint, AlertSuppression, AlertWindow, StreamAlertStatus } from '../api'
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
  const [windows, setWindows] = useState<AlertWindow[]>([])
  const [streamFilter, setStreamFilter] = useState('pending')
  const [streamTypeFilter, setStreamTypeFilter] = useState('')
  const [streamSourceFilter, setStreamSourceFilter] = useState('')
  const [selectedFingerprint, setSelectedFingerprint] = useState<AlertFingerprint | null>(null)
  const [silence, setSilence] = useState({ alert_type: '', source: '', reason: '', hours: '6' })
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
      api.getAlertFingerprints(streamFilter, streamTypeFilter, 100),
      api.getAlertSuppressions(true),
      api.getAlertWindows('', '', 50),
    ]).then(([status, fp, sup, win]) => {
      setStreamStatus(status)
      setFingerprints(fp.data)
      setSuppressions(sup.data)
      setWindows(win.data)
    }).catch(() => {})
  }

  useEffect(loadStream, [streamFilter, streamTypeFilter])

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
      ends_at: new Date(Date.now() + Math.max(1, Number(silence.hours) || 6) * 60 * 60 * 1000).toISOString(),
    })
    setSilence({ alert_type: '', source: '', reason: '', hours: '6' })
    setStreamMsg('Suppression added.')
    loadStream()
  }

  const expireSuppression = async (id: string) => {
    await api.expireAlertSuppression(id)
    setStreamMsg('Suppression expired.')
    loadStream()
  }

  const extendSuppression = async (s: AlertSuppression) => {
    await api.updateAlertSuppression(s.id, {
      status: 'active',
      ends_at: new Date(Date.now() + 6 * 60 * 60 * 1000).toISOString(),
    })
    setStreamMsg('Suppression extended 6 hours.')
    loadStream()
  }

  const editSuppressionReason = async (s: AlertSuppression) => {
    const reason = window.prompt('Suppression reason', s.reason)
    if (reason == null) return
    if (!reason.trim()) {
      setStreamMsg('Reason cannot be empty.')
      return
    }
    await api.updateAlertSuppression(s.id, { reason })
    setStreamMsg('Suppression reason updated.')
    loadStream()
  }

  const visibleFingerprints = fingerprints.filter((fp) => {
    if (streamSourceFilter && fp.source !== streamSourceFilter) return false
    return true
  })
  const streamSources = Array.from(new Set(fingerprints.map((fp) => fp.source).filter(Boolean) as string[])).sort()
  const groupedFingerprints = visibleFingerprints.reduce<Record<string, AlertFingerprint[]>>((acc, fp) => {
    const key = fp.alert_type || 'unknown'
    acc[key] = acc[key] || []
    acc[key].push(fp)
    return acc
  }, {})

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
            <input
              value={streamTypeFilter}
              onChange={(e) => setStreamTypeFilter(e.target.value)}
              placeholder="type filter"
              className="rounded-md border border-border bg-background px-2 py-1 text-xs"
            />
            <select
              value={streamSourceFilter}
              onChange={(e) => setStreamSourceFilter(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1 text-xs"
            >
              <option value="">all sources</option>
              {streamSources.map((source) => <option key={source} value={source}>{source}</option>)}
            </select>
          </div>
          {visibleFingerprints.length === 0 ? (
            <div className="text-sm text-text-muted">No stream fingerprints for this filter.</div>
          ) : (
            <div className="max-h-80 space-y-3 overflow-auto">
              {Object.entries(groupedFingerprints).map(([type, rows]) => (
                <div key={type} className="rounded-md border border-border bg-background p-2">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <div className="text-xs font-semibold">{type.replace(/_/g, ' ')}</div>
                    <span className="text-[0.7rem] text-text-muted">{rows.length} groups</span>
                  </div>
                  <div className="grid gap-2 md:grid-cols-2">
                    {rows.map((fp) => (
                      <button
                        key={fp.fingerprint}
                        type="button"
                        onClick={() => setSelectedFingerprint(fp)}
                        className="rounded-md border border-border bg-surface p-2 text-left hover:bg-hover"
                      >
                        <div className="mb-1 flex items-center justify-between gap-2">
                          <span className="truncate text-xs font-medium">{fp.source || 'all sources'} · {detailTerm(fp.detail) || fp.entity_id || 'source group'}</span>
                          <span className={`shrink-0 rounded-full px-2 py-0.5 text-[0.65rem] ${statusClass(fp.status)}`}>{fp.status}</span>
                        </div>
                        <div className="text-[0.7rem] text-text-muted">
                          {fp.count.toLocaleString()} events · {smallTime(fp.window_start)}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card className="mb-6">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <BellOff className="h-4 w-4" /> Stream suppressions
        </div>
        <div className="mb-3 grid gap-2 md:grid-cols-[1fr_1fr_2fr_0.7fr_auto]">
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
          <input
            value={silence.hours}
            onChange={(e) => setSilence({ ...silence, hours: e.target.value })}
            placeholder="hours"
            type="number"
            min={1}
            max={168}
            className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
          <button onClick={createSilence}>Silence</button>
        </div>
        {streamMsg && <div className="mb-2 text-xs text-text-muted">{streamMsg}</div>}
        <div className="space-y-2">
          {suppressions.length === 0 ? (
            <span className="text-sm text-text-muted">No active suppressions.</span>
          ) : suppressions.map((s) => (
            <div key={s.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-background px-3 py-2">
              <div className="min-w-0 text-xs text-text-secondary">
                <div className="font-medium text-text-primary">
                  {s.alert_type || 'all alerts'} · {s.source || 'all sources'} · {s.scope}
                </div>
                <div className="mt-0.5 truncate">{s.reason}</div>
                <div className="mt-0.5 text-text-muted">
                  entity {s.entity_id || 'all'} · starts {smallTime(s.starts_at)} · ends {smallTime(s.ends_at)}
                </div>
              </div>
              <div className="flex shrink-0 gap-1">
                <button type="button" onClick={() => editSuppressionReason(s)} className="text-xs">Edit</button>
                <button type="button" onClick={() => extendSuppression(s)} className="text-xs">Extend 6h</button>
                <button type="button" onClick={() => expireSuppression(s.id)} className="text-xs">Expire</button>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card className="mb-6">
        <div className="mb-3 text-sm font-semibold">Rolling windows</div>
        {windows.length === 0 ? (
          <div className="text-sm text-text-muted">No rolling alert windows recorded.</div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
            {windows.map((w) => (
              <div key={`${w.bucket_type}:${w.bucket_key}:${w.window_end}`} className="rounded-md border border-border bg-background p-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="truncate text-xs font-medium">{w.bucket_type} · {w.bucket_key}</span>
                  <span className="rounded-full bg-hover px-2 py-0.5 text-[0.65rem] text-text-secondary">{w.count.toLocaleString()}</span>
                </div>
                <div className="text-[0.7rem] text-text-muted">
                  {w.source || 'all sources'} · baseline {w.baseline ?? 'n/a'} · {smallTime(w.window_end)}
                </div>
              </div>
            ))}
          </div>
        )}
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
      {selectedFingerprint && (
        <StreamFingerprintDrawer
          fingerprint={selectedFingerprint}
          onClose={() => setSelectedFingerprint(null)}
          onSilence={(fp) => {
            setSilence({
              alert_type: fp.alert_type,
              source: fp.source || '',
              reason: `Investigating ${fp.alert_type}`,
              hours: '6',
            })
            setSelectedFingerprint(null)
          }}
        />
      )}
    </div>
  )
}

function StreamFingerprintDrawer({
  fingerprint,
  onClose,
  onSilence,
}: {
  fingerprint: AlertFingerprint
  onClose: () => void
  onSilence: (fingerprint: AlertFingerprint) => void
}) {
  const entries = Object.entries(fingerprint.detail || {}).filter(([, value]) => value != null)
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" role="dialog" aria-modal="true">
      <div className="h-full w-full max-w-xl overflow-y-auto border-l border-border bg-surface p-5 shadow-xl">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <div className="text-lg font-semibold">Stream fingerprint</div>
            <div className="text-sm text-text-muted">{fingerprint.alert_type.replace(/_/g, ' ')}</div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border p-1 text-text-muted hover:bg-hover hover:text-text-primary"
            aria-label="Close fingerprint details"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div className="rounded-md bg-background p-2"><div className="text-xs text-text-muted">Status</div><div>{fingerprint.status}</div></div>
          <div className="rounded-md bg-background p-2"><div className="text-xs text-text-muted">Count</div><div>{fingerprint.count.toLocaleString()}</div></div>
          <div className="rounded-md bg-background p-2"><div className="text-xs text-text-muted">Source</div><div>{fingerprint.source || 'all'}</div></div>
          <div className="rounded-md bg-background p-2"><div className="text-xs text-text-muted">Last sent</div><div>{smallTime(fingerprint.last_sent_at)}</div></div>
        </div>
        <div className="mt-4 rounded-md border border-border bg-background p-3">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">Window</div>
          <div className="text-sm text-text-secondary">{smallTime(fingerprint.window_start)} to {smallTime(fingerprint.window_end)}</div>
        </div>
        <div className="mt-4 rounded-md border border-border bg-background p-3">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">Safe detail</div>
          {entries.length === 0 ? (
            <div className="text-sm text-text-muted">No structured detail recorded.</div>
          ) : (
            <div className="space-y-2">
              {entries.map(([key, value]) => (
                <div key={key} className="text-sm">
                  <span className="text-text-muted">{key.replace(/_/g, ' ')}: </span>
                  <code className="break-all text-text-secondary">{Array.isArray(value) ? `${value.length} refs` : String(value)}</code>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={() => onSilence(fingerprint)}>Prepare silence</button>
        </div>
      </div>
    </div>
  )
}
