import { useEffect, useMemo, useState } from 'react'
import { Gauge, RefreshCw } from 'lucide-react'
import { api, EvalRun } from '../api'
import { PageHeader } from '../components/ui/PageHeader'
import { Card } from '../components/ui/Card'
import { MetricCard } from '../components/ui/MetricCard'
import { StatusBadge } from '../components/ui/StatusBadge'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { ErrorState } from '../components/ui/ErrorState'
import { EmptyState } from '../components/ui/EmptyState'

const TASKS = ['', 'search', 'identity', 'sentiment', 'face', 'location', 'alerts']

function fmt(iso: string | null) {
  return iso ? new Date(iso).toLocaleString() : '-'
}

function gate(run: EvalRun) {
  return String(run.metrics?.gate_status || run.status || 'unknown')
}

function gateStatus(run: EvalRun): Parameters<typeof StatusBadge>[0]['status'] {
  const value = gate(run)
  if (value === 'pass' || value === 'completed') return 'success'
  if (value === 'warn') return 'warning'
  if (value === 'fail' || run.status === 'failed') return 'error'
  return 'idle'
}

function metricRows(metrics: Record<string, unknown> | null) {
  if (!metrics) return []
  return Object.entries(metrics).filter(([, value]) => typeof value === 'number')
}

function summary(metrics: Record<string, unknown> | null) {
  const rows = metricRows(metrics).slice(0, 4)
  return rows.map(([key, value]) => `${key.replace(/_/g, ' ')} ${Number(value).toFixed(Number(value) <= 1 ? 3 : 0)}`).join(' · ') || 'no numeric metrics'
}

export default function EvalPage() {
  const [task, setTask] = useState('')
  const [runs, setRuns] = useState<EvalRun[]>([])
  const [latest, setLatest] = useState<EvalRun[]>([])
  const [regression, setRegression] = useState<{ task: string; delta: Record<string, number>; runs: EvalRun[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    Promise.all([
      api.getEvalRuns(task, 100),
      api.getEvalLatest(),
      task ? api.getEvalRegressions(task) : Promise.resolve(null),
    ])
      .then(([runRes, latestRes, regressionRes]) => {
        setRuns(runRes.data)
        setLatest(latestRes.data)
        setRegression(regressionRes)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(load, [task])

  const counts = useMemo(() => ({
    pass: latest.filter((run) => gate(run) === 'pass').length,
    warn: latest.filter((run) => gate(run) === 'warn').length,
    fail: latest.filter((run) => gate(run) === 'fail' || run.status === 'failed').length,
    tasks: latest.length,
  }), [latest])

  if (loading) return <LoadingSpinner label="Loading evaluation runs..." />
  if (error) return <ErrorState message={`Failed to load evaluation runs: ${error}`} onRetry={load} />

  return (
    <div>
      <PageHeader
        title="Evaluation"
        description="Machine-readable benchmark runs and regression gates for search, identity, sentiment, face, location, and alert rules."
        actions={
          <div className="flex items-center gap-2">
            <select value={task} onChange={(e) => setTask(e.target.value)} aria-label="Task filter">
              {TASKS.map((value) => <option key={value || 'all'} value={value}>{value || 'all tasks'}</option>)}
            </select>
            <button type="button" onClick={load}><RefreshCw className="mr-1 inline h-3.5 w-3.5" />Refresh</button>
          </div>
        }
      />

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard label="Latest tasks" value={counts.tasks} icon={<Gauge className="h-4 w-4" />} />
        <MetricCard label="Passing" value={counts.pass} status="success" />
        <MetricCard label="Warnings" value={counts.warn} status={counts.warn ? 'warning' : 'success'} />
        <MetricCard label="Failures" value={counts.fail} status={counts.fail ? 'error' : 'success'} />
      </div>

      {latest.length > 0 && (
        <Card className="mb-6">
          <div className="mb-3 text-sm font-semibold">Latest by task</div>
          <div className="grid gap-2 md:grid-cols-3">
            {latest.map((run) => (
              <div key={run.id} className="rounded-md border border-border bg-background p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold">{run.task_type}</span>
                  <StatusBadge status={gateStatus(run)} label={gate(run)} />
                </div>
                <div className="text-xs text-text-muted">{summary(run.metrics)}</div>
                {Array.isArray(run.metrics?.gate_failures) && run.metrics.gate_failures.length > 0 && (
                  <div className="mt-2 text-xs text-error">{run.metrics.gate_failures.slice(0, 2).join(' · ')}</div>
                )}
                {Array.isArray(run.metrics?.gate_warnings) && run.metrics.gate_warnings.length > 0 && (
                  <div className="mt-2 text-xs text-warning">{run.metrics.gate_warnings.slice(0, 2).join(' · ')}</div>
                )}
                <div className="mt-2 text-[0.7rem] text-text-muted">{fmt(run.finished_at || run.started_at)}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {regression && (
        <Card className="mb-6">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="text-sm font-semibold">Regression delta: {regression.task}</div>
            <div className="text-xs text-text-muted">latest minus previous</div>
          </div>
          {Object.keys(regression.delta).length === 0 ? (
            <div className="text-sm text-text-muted">Need two completed runs for numeric deltas.</div>
          ) : (
            <div className="grid gap-2 md:grid-cols-4">
              {Object.entries(regression.delta).map(([key, value]) => (
                <div key={key} className="rounded-md bg-background p-2">
                  <div className="text-xs text-text-muted">{key.replace(/_/g, ' ')}</div>
                  <div className={value < 0 ? 'font-mono text-error' : 'font-mono text-success'}>
                    {value > 0 ? '+' : ''}{value.toFixed(4)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {runs.length === 0 ? (
        <EmptyState title="No eval runs" description="Seed eval sets and run the eval CLI to populate this page." />
      ) : (
        <Card>
          <div className="mb-3 text-sm font-semibold">Run history</div>
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Gate</th>
                  <th>Version</th>
                  <th>Metrics</th>
                  <th>Finished</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td className="font-medium">{run.task_type}</td>
                    <td><StatusBadge status={gateStatus(run)} label={gate(run)} /></td>
                    <td>{run.model_or_rule_version}</td>
                    <td className="max-w-md text-xs text-text-muted">{summary(run.metrics)}</td>
                    <td>{fmt(run.finished_at || run.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
