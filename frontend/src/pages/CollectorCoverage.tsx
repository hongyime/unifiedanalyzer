import { useEffect, useMemo, useState } from 'react'
import { Database, RefreshCw } from 'lucide-react'
import { api, CollectorCoverageResponse, CollectorCoverageRow, CollectorInfo } from '../api'
import { PageHeader } from '../components/ui/PageHeader'
import { Card } from '../components/ui/Card'
import { MetricCard } from '../components/ui/MetricCard'
import { StatusBadge } from '../components/ui/StatusBadge'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { ErrorState } from '../components/ui/ErrorState'
import { EmptyState } from '../components/ui/EmptyState'

function fmt(iso: string | null | undefined) {
  return iso ? new Date(iso).toLocaleString() : '-'
}

function statusFor(row: CollectorCoverageRow): Parameters<typeof StatusBadge>[0]['status'] {
  if (row.status === 'fresh') return 'success'
  if (row.status === 'degraded') return 'warning'
  if (row.status === 'stale') return 'error'
  return 'idle'
}

function healthBySource(collectors: CollectorInfo[]) {
  return collectors.reduce<Record<string, CollectorInfo>>((acc, row) => {
    acc[row.source] = row
    return acc
  }, {})
}

export default function CollectorCoveragePage() {
  const [coverage, setCoverage] = useState<CollectorCoverageResponse | null>(null)
  const [collectors, setCollectors] = useState<CollectorInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    Promise.all([api.getCollectorCoverage(), api.getCollectorHealth()])
      .then(([coverageRes, healthRes]) => {
        setCoverage(coverageRes)
        setCollectors(healthRes.collectors)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const sourceHealth = useMemo(() => healthBySource(collectors), [collectors])
  const rows = coverage?.sources ?? []
  const summary = coverage?.summary ?? {
    fresh: rows.filter((row) => row.status === 'fresh').length,
    degraded: rows.filter((row) => row.status === 'degraded').length,
    stale: rows.filter((row) => row.status === 'stale').length,
    unknown: rows.filter((row) => !['fresh', 'degraded', 'stale'].includes(row.status)).length,
  }
  const totals = rows.reduce(
    (acc, row) => ({
      rows24h: acc.rows24h + Number(row.rows_24h || 0),
      media24h: acc.media24h + Number(row.media_24h || 0),
      errors24h: acc.errors24h + Number(row.errors_24h || 0),
      rateLimits24h: acc.rateLimits24h + Number(row.rate_limits_24h || 0),
      privateFailures: acc.privateFailures + Number(row.private_access_failures || 0),
      staleTargets: acc.staleTargets + Number(row.stale_targets || 0),
    }),
    { rows24h: 0, media24h: 0, errors24h: 0, rateLimits24h: 0, privateFailures: 0, staleTargets: 0 },
  )

  if (loading) return <LoadingSpinner label="Loading collector coverage..." />
  if (error) return <ErrorState message={`Failed to load collector coverage: ${error}`} onRetry={load} />

  return (
    <div>
      <PageHeader
        title="Collector Coverage"
        description="Per-source freshness, gaps, private-access failures, rate-limit pressure, and media/document coverage."
        actions={<button type="button" onClick={load}><RefreshCw className="mr-1 inline h-3.5 w-3.5" />Refresh</button>}
      />

      {coverage?.snapshot_stale && (
        <Card className="mb-4 border-warning/40 bg-warning/10">
          <div className="text-sm font-semibold text-warning">Coverage snapshot is stale</div>
          <div className="mt-1 text-xs text-text-secondary">
            Latest snapshot: {fmt(coverage.snapshot_created_at)}. Treat source freshness and alert confidence as degraded until Collector writes a new snapshot.
          </div>
        </Card>
      )}

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-6">
        <MetricCard label="Fresh" value={summary.fresh} status="success" />
        <MetricCard label="Degraded" value={summary.degraded} status={summary.degraded ? 'warning' : 'success'} />
        <MetricCard label="Stale" value={summary.stale} status={summary.stale ? 'error' : 'success'} />
        <MetricCard label="Rows 24h" value={totals.rows24h} icon={<Database className="h-4 w-4" />} />
        <MetricCard label="Media 24h" value={totals.media24h} />
        <MetricCard label="Private fails" value={totals.privateFailures} status={totals.privateFailures ? 'warning' : 'success'} />
      </div>

      <div className="mb-6 grid gap-3 md:grid-cols-3">
        <Card>
          <div className="text-xs text-text-muted">Rate limits 24h</div>
          <div className="mt-1 text-2xl font-semibold">{totals.rateLimits24h.toLocaleString()}</div>
        </Card>
        <Card>
          <div className="text-xs text-text-muted">Errors 24h</div>
          <div className="mt-1 text-2xl font-semibold">{totals.errors24h.toLocaleString()}</div>
        </Card>
        <Card>
          <div className="text-xs text-text-muted">Stale targets</div>
          <div className="mt-1 text-2xl font-semibold">{totals.staleTargets.toLocaleString()}</div>
        </Card>
      </div>

      {rows.length === 0 ? (
        <EmptyState title="No coverage rows" description="Collector coverage snapshots have not been written yet." />
      ) : (
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <div className="text-sm font-semibold">Source freshness grid</div>
            <div className="text-xs text-text-muted">
              Snapshot: {fmt(coverage?.snapshot_created_at)} {coverage?.snapshot_age_seconds != null ? `(${Math.round(coverage.snapshot_age_seconds / 60)}m old)` : ''}
            </div>
          </div>
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Latest data</th>
                  <th>Latest run</th>
                  <th>Rows 24h</th>
                  <th>Media 24h</th>
                  <th>Errors</th>
                  <th>Rate limits</th>
                  <th>Private fails</th>
                  <th>Targets</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const health = sourceHealth[row.source]
                  return (
                    <tr key={row.source}>
                      <td>
                        <div className="font-medium">{row.source}</div>
                        <div className="text-xs text-text-muted">{row.expected_cadence || 'cadence unknown'}</div>
                      </td>
                      <td>
                        <StatusBadge status={statusFor(row)} label={row.status} />
                        {health?.latest_status && (
                          <div className="mt-1 text-[0.7rem] text-text-muted">run {health.latest_status}</div>
                        )}
                      </td>
                      <td>{fmt(row.latest_data_at)}</td>
                      <td>{fmt(row.latest_run_at)}</td>
                      <td>{row.rows_24h.toLocaleString()}</td>
                      <td>{row.media_24h.toLocaleString()}</td>
                      <td>{row.errors_24h.toLocaleString()}</td>
                      <td>{row.rate_limits_24h.toLocaleString()}</td>
                      <td>{row.private_access_failures.toLocaleString()}</td>
                      <td>{row.stale_targets.toLocaleString()}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
