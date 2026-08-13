import { useEffect, useMemo, useState } from 'react'
import { Activity, Database, Gauge, Globe2, RefreshCw, Send, ShieldCheck } from 'lucide-react'
import { api, CollectorCoverageResponse, CollectorCoverageRow, CollectorInfo, CollectorProductionStatus } from '../api'
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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function asList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length > 0) : []
}

function asNumber(value: unknown): number {
  const parsed = Number(value || 0)
  return Number.isFinite(parsed) ? parsed : 0
}

function asString(value: unknown, fallback = '-'): string {
  return typeof value === 'string' && value ? value : fallback
}

function labelize(value: string): string {
  return value.replace(/_/g, ' ')
}

function surfacePayload(production: CollectorProductionStatus | null, key: string): Record<string, unknown> {
  return asRecord(production?.surfaces?.[key]?.payload)
}

type CompactStatus = 'success' | 'warning' | 'error' | 'idle'

function stageStatus(stage: string): CompactStatus {
  if (stage === 'ok' || stage === 'dry_run' || stage === 'advance_stage') return 'success'
  if (stage === 'cooldown' || stage === 'telegram_upload' || stage === 'stop_or_rollback') return 'warning'
  if (stage === 'login_or_browser' || stage === 'vault' || stage === 'realtime_feed') return 'error'
  return 'idle'
}

export default function CollectorCoveragePage() {
  const [coverage, setCoverage] = useState<CollectorCoverageResponse | null>(null)
  const [collectors, setCollectors] = useState<CollectorInfo[]>([])
  const [production, setProduction] = useState<CollectorProductionStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    Promise.all([
      api.getCollectorCoverage(),
      api.getCollectorHealth(),
      api.getCollectorProductionStatus().catch(() => null),
    ])
      .then(([coverageRes, healthRes, productionRes]) => {
        setCoverage(coverageRes)
        setCollectors(healthRes.collectors)
        setProduction(productionRes)
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
      seenTargets: acc.seenTargets + Number(row.seen_targets_total || 0),
      backfilledTargets: acc.backfilledTargets + Number(row.seen_targets_backfilled || 0),
      pendingTargets: acc.pendingTargets + Number(row.seen_targets_pending || 0),
      freshTargets: acc.freshTargets + Number(row.seen_targets_fresh || 0),
      staleSeenTargets: acc.staleSeenTargets + Number(row.seen_targets_stale || 0),
      newTargets: acc.newTargets + Number(row.seen_targets_newly_discovered || 0),
    }),
    {
      rows24h: 0,
      media24h: 0,
      errors24h: 0,
      rateLimits24h: 0,
      privateFailures: 0,
      staleTargets: 0,
      seenTargets: 0,
      backfilledTargets: 0,
      pendingTargets: 0,
      freshTargets: 0,
      staleSeenTargets: 0,
      newTargets: 0,
    },
  )
  const instagramHealth = surfacePayload(production, 'instagram_health')
  const instagramTargets = asRecord(instagramHealth.targets)
  const instagramCooldown = asRecord(instagramHealth.cooldown)
  const instagramStage = asString(instagramHealth.stuck_stage, 'unknown')
  const realtimeFeed = surfacePayload(production, 'realtime_feed')
  const sourceCounters = Object.entries(asRecord(realtimeFeed.source_counters)).map(([source, value]) => {
    const counters = asRecord(value)
    return {
      source,
      sent: asNumber(counters.sent),
      deferred: asNumber(counters.deferred),
      deduped: asNumber(counters.deduped),
      tooLarge: asNumber(counters.too_large),
      localFallback: asNumber(counters.local_fallback),
      failed: asNumber(counters.failed),
    }
  })
  const domainPacing = surfacePayload(production, 'domain_pacing')
  const domainSources = asList(domainPacing.sources)
  const apiQuotas = surfacePayload(production, 'api_quotas')
  const quotaSnapshots = asList(apiQuotas.snapshots).slice(0, 6)
  const optionalRollout = surfacePayload(production, 'optional_rollout')
  const rolloutAction = asString(optionalRollout.recommended_action, 'unknown')
  const quotaProgress = asRecord(apiQuotas.progress)

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

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-6">
        <MetricCard label="Seen targets" value={totals.seenTargets} />
        <MetricCard label="Backfilled" value={totals.backfilledTargets} status="success" />
        <MetricCard label="Pending" value={totals.pendingTargets} status={totals.pendingTargets ? 'warning' : 'success'} />
        <MetricCard label="Fresh targets" value={totals.freshTargets} status="success" />
        <MetricCard label="Stale targets" value={totals.staleSeenTargets} status={totals.staleSeenTargets ? 'error' : 'success'} />
        <MetricCard label="New targets" value={totals.newTargets} />
      </div>

      {production && (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
            <MetricCard
              label="Instagram stage"
              value={labelize(instagramStage)}
              status={stageStatus(instagramStage)}
              icon={<Activity className="h-4 w-4" />}
            />
            <MetricCard
              label="Realtime queue"
              value={production.summary.realtime_queue_depth ?? 0}
              status={production.summary.realtime_queue_depth ? 'warning' : 'success'}
              icon={<Send className="h-4 w-4" />}
            />
            <MetricCard
              label="Paced domains"
              value={production.summary.domain_pacing_sources ?? 0}
              sublabel={`${production.summary.domain_429 ?? 0} rate limited`}
              icon={<Globe2 className="h-4 w-4" />}
            />
            <MetricCard
              label="Quota paused"
              value={production.summary.quota_paused ?? 0}
              sublabel={`${production.summary.quota_snapshots ?? 0} snapshots`}
              status={production.summary.quota_paused ? 'warning' : 'success'}
              icon={<Gauge className="h-4 w-4" />}
            />
            <MetricCard
              label="Optional rollout"
              value={labelize(rolloutAction)}
              status={stageStatus(rolloutAction)}
              icon={<ShieldCheck className="h-4 w-4" />}
            />
          </div>

          <div className="mb-6 grid gap-3 xl:grid-cols-2">
            <Card title="Instagram walkthrough">
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <StatusBadge status={stageStatus(instagramStage)} label={labelize(instagramStage)} />
                <span className="text-text-muted">targets {asNumber(instagramTargets.total).toLocaleString()}</span>
                <span className="text-text-muted">pending {asNumber(instagramTargets.pending).toLocaleString()}</span>
                <span className="text-text-muted">cooldown {instagramCooldown.active ? 'active' : 'clear'}</span>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-3 text-xs text-text-secondary sm:grid-cols-4">
                <div>
                  <div className="text-text-muted">Latest profile</div>
                  <div className="font-medium text-text-primary">{fmt(asString(asRecord(instagramHealth.latest_profile).updated_at, ''))}</div>
                </div>
                <div>
                  <div className="text-text-muted">Latest post</div>
                  <div className="font-medium text-text-primary">{fmt(asString(asRecord(instagramHealth.latest_post).collected_at, ''))}</div>
                </div>
                <div>
                  <div className="text-text-muted">Latest media</div>
                  <div className="font-medium text-text-primary">{fmt(asString(asRecord(instagramHealth.latest_media).collected_at, ''))}</div>
                </div>
                <div>
                  <div className="text-text-muted">Realtime</div>
                  <div className="font-medium text-text-primary">{asRecord(instagramHealth.realtime_delivery).available === false ? 'unavailable' : 'available'}</div>
                </div>
              </div>
            </Card>

            <Card title="Realtime Telegram media">
              {sourceCounters.length === 0 ? (
                <div className="text-sm text-text-muted">No per-source delivery counters yet.</div>
              ) : (
                <div className="overflow-x-auto">
                  <table>
                    <thead>
                      <tr>
                        <th>Source</th>
                        <th>Sent</th>
                        <th>Deferred</th>
                        <th>Deduped</th>
                        <th>Fallback</th>
                        <th>Failed</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sourceCounters.slice(0, 8).map((row) => (
                        <tr key={row.source}>
                          <td>{row.source}</td>
                          <td>{row.sent.toLocaleString()}</td>
                          <td>{row.deferred.toLocaleString()}</td>
                          <td>{row.deduped.toLocaleString()}</td>
                          <td>{(row.localFallback + row.tooLarge).toLocaleString()}</td>
                          <td>{row.failed.toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <Card title="Website and search pacing">
              {domainSources.length === 0 ? (
                <div className="text-sm text-text-muted">No domain pacing events yet.</div>
              ) : (
                <div className="overflow-x-auto">
                  <table>
                    <thead>
                      <tr>
                        <th>Source</th>
                        <th>Domains</th>
                        <th>Active</th>
                        <th>Robots</th>
                        <th>403/429</th>
                        <th>PDF/docs</th>
                      </tr>
                    </thead>
                    <tbody>
                      {domainSources.map((row) => (
                        <tr key={asString(row.source)}>
                          <td>{asString(row.source)}</td>
                          <td>{asNumber(row.domains_seen).toLocaleString()}</td>
                          <td>{asNumber(row.recently_active_domains).toLocaleString()}</td>
                          <td>{asNumber(row.robots_blocked).toLocaleString()}</td>
                          <td>{asNumber(row.http_403).toLocaleString()} / {asNumber(row.http_429).toLocaleString()}</td>
                          <td>{asNumber(row.pdfs_found).toLocaleString()} / {asNumber(row.docs_found).toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <Card title="GitHub and YouTube quotas">
              {quotaSnapshots.length === 0 ? (
                <div className="text-sm text-text-muted">
                  {Object.keys(quotaProgress).length ? 'Progress counts are available; quota snapshots will appear after the next API call.' : 'No quota snapshots yet.'}
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table>
                    <thead>
                      <tr>
                        <th>Service</th>
                        <th>Bucket</th>
                        <th>Used</th>
                        <th>Target</th>
                        <th>Remaining</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {quotaSnapshots.map((row, idx) => (
                        <tr key={`${asString(row.service)}-${asString(row.bucket)}-${idx}`}>
                          <td>{asString(row.service)}</td>
                          <td>{asString(row.bucket)}</td>
                          <td>{asNumber(row.used_units).toLocaleString()}</td>
                          <td>{asNumber(row.target_units).toLocaleString()}</td>
                          <td>{asNumber(row.remaining_units).toLocaleString()}</td>
                          <td><StatusBadge status={row.paused ? 'warning' : 'success'} label={row.paused ? 'paused' : 'active'} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <Card title="Optional rollout">
              <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                <div>
                  <div className="text-xs text-text-muted">Feature</div>
                  <div className="font-medium">{asString(optionalRollout.feature, 'spiderfoot')}</div>
                </div>
                <div>
                  <div className="text-xs text-text-muted">Stage</div>
                  <div className="font-medium">{asString(optionalRollout.stage, 'dry-run')}</div>
                </div>
                <div>
                  <div className="text-xs text-text-muted">Candidates</div>
                  <div className="font-medium">{asNumber(optionalRollout.candidate_count).toLocaleString()}</div>
                </div>
                <div>
                  <div className="text-xs text-text-muted">Stop reasons</div>
                  <div className="font-medium">{asList(optionalRollout.stop_reasons).length.toLocaleString()}</div>
                </div>
              </div>
              <div className="mt-3">
                <StatusBadge
                  status={optionalRollout.can_proceed === false ? 'warning' : 'success'}
                  label={labelize(rolloutAction)}
                />
              </div>
            </Card>
          </div>
        </>
      )}

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
                  <th>Seen</th>
                  <th>Pending</th>
                  <th>Fresh/Stale</th>
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
                      <td>{Number(row.seen_targets_total || 0).toLocaleString()}</td>
                      <td>{Number(row.seen_targets_pending || 0).toLocaleString()}</td>
                      <td>
                        {Number(row.seen_targets_fresh || 0).toLocaleString()} / {Number(row.seen_targets_stale || 0).toLocaleString()}
                      </td>
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
