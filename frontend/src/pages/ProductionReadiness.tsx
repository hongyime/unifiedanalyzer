import { useEffect, useMemo, useState } from 'react'
import { Activity, Database, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react'
import { api, ProductionReadinessCheck, ProductionReadinessReport } from '../api'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorState } from '../components/ui/ErrorState'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { MetricCard } from '../components/ui/MetricCard'
import { PageHeader } from '../components/ui/PageHeader'
import { StatusBadge } from '../components/ui/StatusBadge'

function badgeStatus(check: ProductionReadinessCheck): Parameters<typeof StatusBadge>[0]['status'] {
  if (check.ok) return 'success'
  return check.severity === 'critical' ? 'error' : 'warning'
}

function fmtBool(value: unknown) {
  if (value === true) return 'yes'
  if (value === false) return 'no'
  return '-'
}

function num(value: unknown) {
  const parsed = Number(value ?? 0)
  return Number.isFinite(parsed) ? parsed : 0
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function compactEvidence(check: ProductionReadinessCheck): string {
  const evidence = asRecord(check.evidence)
  if (check.id === 'supabase_populated') {
    const remote = asRecord(evidence.remote_readback)
    return `ready ${num(evidence.ready_to_export)} · local ${num(evidence.exported_count).toLocaleString()} · remote ${num(remote.row_count).toLocaleString()} · raw mirror ${fmtBool(evidence.raw_mirror)}`
  }
  if (check.id === 'collector_production_surfaces') {
    const summary = asRecord(evidence.summary)
    return `sources ${num(summary.source_issues)} · browser ${num(summary.browser_extension_issues)} · ingest ${String(summary.browser_ingest_effective_state || summary.browser_ingest_state || '-')}`
  }
  if (check.id === 'collector_hourly_yield_floor') {
    const failing = Array.isArray(evidence.failing) ? evidence.failing.length : 0
    const exempt = Array.isArray(evidence.exempt) ? evidence.exempt.length : 0
    return `threshold ${num(evidence.threshold)} · failing ${failing} · exempt ${exempt}`
  }
  if (check.id === 'collector_action_queue_visible') {
    const actions = Array.isArray(evidence.actions) ? evidence.actions : []
    const sources = actions.slice(0, 3).map((item) => {
      const row = asRecord(item)
      return `${String(row.source || '-')}/${String(row.action_type || '-')}`
    })
    return `open ${num(evidence.count)}${sources.length ? ` · ${sources.join(', ')}` : ''}`
  }
  if (check.id === 'analyst_workflows_available') {
    const missing = Array.isArray(evidence.missing) ? evidence.missing.length : 0
    const mounted = Array.isArray(evidence.mounted) ? evidence.mounted.length : 0
    return `mounted ${mounted} · missing ${missing}`
  }
  if (check.id === 'data_quality_ledger') {
    const summary = asRecord(evidence.summary)
    return `sources ${num(summary.total_sources)} · gaps ${num(summary.gap_sources)}`
  }
  if (check.id === 'scheduler_self_healing') {
    const incremental = asRecord(evidence.incremental)
    const full = asRecord(evidence.full_resolution)
    return `incremental ${String(incremental.state || '-')} · full ${String(full.state || '-')}`
  }
  if (check.id === 'backup_restorable') {
    return `status ${String(evidence.status || '-')} · restore ${String(evidence.restore_validation || '-')}`
  }
  return check.detail
}

function groupChecks(checks: ProductionReadinessCheck[]) {
  return {
    critical: checks.filter((check) => check.severity === 'critical'),
    warning: checks.filter((check) => check.severity !== 'critical'),
  }
}

export default function ProductionReadinessPage() {
  const [report, setReport] = useState<ProductionReadinessReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    api.getProductionReadiness()
      .then(setReport)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const grouped = useMemo(() => groupChecks(report?.checks ?? []), [report])
  const failing = (report?.checks ?? []).filter((check) => !check.ok)

  if (loading) return <LoadingSpinner label="Loading production readiness..." />
  if (error) return <ErrorState message={`Failed to load production readiness: ${error}`} onRetry={load} />
  if (!report) {
    return <EmptyState title="No readiness report" description="The production readiness endpoint did not return a report." />
  }

  return (
    <div>
      <PageHeader
        title="Production Readiness"
        description="Code-backed operator and analyst stories, with the live evidence that proves each production gate."
        actions={
          <Button variant="ghost" onClick={load} icon={<RefreshCw className="h-3.5 w-3.5" />}>
            Refresh
          </Button>
        }
      />

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <MetricCard
          label="Overall"
          value={report.status}
          status={report.ok ? 'success' : 'error'}
          icon={<ShieldCheck className="h-4 w-4" />}
        />
        <MetricCard label="Checks" value={report.summary.total} icon={<Activity className="h-4 w-4" />} />
        <MetricCard label="Passing" value={report.summary.ok} status="success" />
        <MetricCard
          label="Degraded"
          value={report.summary.degraded}
          status={report.summary.degraded ? 'warning' : 'success'}
          icon={<TriangleAlert className="h-4 w-4" />}
        />
        <MetricCard
          label="Critical failed"
          value={report.summary.critical_failed}
          status={report.summary.critical_failed ? 'error' : 'success'}
          icon={<Database className="h-4 w-4" />}
        />
      </div>

      {failing.length > 0 && (
        <Card className="mb-6 border-warning/40 bg-warning/10" title="Open readiness work">
          <div className="grid gap-2 md:grid-cols-2">
            {failing.map((check) => (
              <div key={check.id} className="rounded-md border border-border bg-background px-3 py-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{check.title}</span>
                  <StatusBadge status={badgeStatus(check)} label={check.severity} />
                </div>
                <div className="text-xs text-text-secondary">{compactEvidence(check)}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <div className="space-y-4">
          <Card title="Critical stories">
            <div className="space-y-2">
              {grouped.critical.map((check) => (
                <ReadinessRow key={check.id} check={check} />
              ))}
            </div>
          </Card>

          <Card title="Warning stories">
            <div className="space-y-2">
              {grouped.warning.map((check) => (
                <ReadinessRow key={check.id} check={check} />
              ))}
            </div>
          </Card>
        </div>

        <Card title="Story coverage">
          <div className="space-y-3">
            {report.checks.map((check) => (
              <div key={check.id} className="rounded-md border border-border bg-background p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold uppercase text-text-muted">{check.user_story.actor}</span>
                  <StatusBadge status={badgeStatus(check)} label={check.ok ? 'covered' : check.severity} />
                </div>
                <div className="text-sm text-text-primary">{check.user_story.story}</div>
                <div className="mt-1 text-xs text-text-secondary">{check.user_story.value}</div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  )
}

function ReadinessRow({ check }: { check: ProductionReadinessCheck }) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-text-primary">{check.title}</h2>
            <span className="text-xs text-text-muted">{check.id}</span>
          </div>
          <p className="mt-1 text-sm text-text-secondary">{check.user_story.story}</p>
        </div>
        <StatusBadge status={badgeStatus(check)} label={check.ok ? 'ok' : check.severity} />
      </div>
      <div className="mt-3 grid gap-2 text-xs text-text-secondary md:grid-cols-[1fr_1fr]">
        <div>
          <div className="text-text-muted">Proof required</div>
          <div className="mt-0.5 text-text-primary">{check.user_story.proves}</div>
        </div>
        <div>
          <div className="text-text-muted">Live evidence</div>
          <div className="mt-0.5 text-text-primary">{compactEvidence(check)}</div>
        </div>
      </div>
    </div>
  )
}
