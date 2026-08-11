import { useEffect, useMemo, useState } from 'react'
import {
  createColumnHelper, flexRender, getCoreRowModel, getSortedRowModel,
  useReactTable, SortingState,
} from '@tanstack/react-table'
import { ChevronUp, ChevronDown, Play } from 'lucide-react'
import { useRuns, useTriggerRun } from '../hooks'
import { api, EvalRun, RunInfo, RunPhase } from '../api'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { Button } from '../components/ui/Button'
import { StatusBadge } from '../components/ui/StatusBadge'
import { LABELS } from '../lib/labels'

const PER_PAGE = 20

/** Map a raw pipeline status to the shared StatusBadge palette. */
function statusPill(s: string) {
  const map: Record<string, Parameters<typeof StatusBadge>[0]['status']> = {
    completed: 'success',
    running: 'processing',
    failed: 'error',
  }
  return <StatusBadge status={map[s] ?? 'idle'} label={s} />
}

function fmtDate(iso: string | null) {
  return iso ? new Date(iso).toLocaleString() : '-'
}

function duration(start: string | null, end: string | null) {
  if (!start || !end) return '-'
  const ms = new Date(end).getTime() - new Date(start).getTime()
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

function metricSummary(metrics: Record<string, unknown> | null) {
  if (!metrics) return 'no metrics'
  return Object.entries(metrics)
    .filter(([, value]) => typeof value === 'number')
    .slice(0, 4)
    .map(([key, value]) => `${key.replace(/_/g, ' ')} ${Number(value).toFixed(Number(value) <= 1 ? 2 : 0)}`)
    .join(' · ') || 'no numeric metrics'
}

function gateStatus(run: EvalRun): Parameters<typeof StatusBadge>[0]['status'] {
  const gate = run.metrics?.gate_status
  if (gate === 'fail') return 'error'
  if (gate === 'warn') return 'warning'
  if (gate === 'pass') return 'success'
  return run.status === 'completed' ? 'success' : 'warning'
}

const col = createColumnHelper<RunInfo>()
const columns = [
  // Show the friendly run-type label if we have one (LABELS.tier acts as a
  // fallback dict — future run types can be plain-language mapped here).
  col.accessor('run_type', {
    header: 'Type',
    cell: (c) => LABELS.tier[c.getValue()] ?? c.getValue(),
  }),
  col.accessor('status', { header: 'Status', cell: (c) => statusPill(c.getValue()) }),
  col.accessor('started_at', { header: 'Started', cell: (c) => fmtDate(c.getValue()) }),
  col.display({
    id: 'duration',
    header: 'Duration',
    cell: (c) => duration(c.row.original.started_at, c.row.original.finished_at),
  }),
  col.accessor('entities_processed', { header: 'Entities' }),
  col.accessor('events_created', { header: 'Events' }),
  col.accessor('alerts_created', { header: 'Alerts' }),
  col.accessor('signals_created', { header: 'Signals' }),
]

export default function RunsPage() {
  const [page, setPage] = useState(1)
  const [sorting, setSorting] = useState<SortingState>([])
  const [phaseRun, setPhaseRun] = useState<string | null>(null)
  const [phases, setPhases] = useState<RunPhase[]>([])
  const [evalRuns, setEvalRuns] = useState<EvalRun[]>([])
  const { data, isLoading } = useRuns(page)
  const trigger = useTriggerRun()

  useEffect(() => {
    api.getEvalLatest().then((r) => setEvalRuns(r.data)).catch(() => setEvalRuns([]))
  }, [])

  const rows = useMemo(() => data?.data ?? [], [data])
  const total = data?.total ?? 0

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  const openPhases = (runId: string) => {
    const next = phaseRun === runId ? null : runId
    setPhaseRun(next)
    setPhases([])
    if (next) api.getRunPhases(next).then((data) => setPhases(data.phases)).catch(() => setPhases([]))
  }

  return (
    <div>
      <PageHeader
        title="Runs"
        description="The background pipeline that refreshes everything. Frequent incremental runs are quick; full runs re-check everyone from scratch."
        actions={
          <Button
            onClick={() => trigger.mutate()}
            disabled={trigger.isPending}
            loading={trigger.isPending}
            icon={<Play className="h-3.5 w-3.5" />}
          >
            {trigger.isPending ? 'Running…' : 'Trigger run'}
          </Button>
        }
      />

      <div className="mb-6 rounded-lg border border-border bg-surface p-4">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold">Evaluation harness</div>
            <div className="text-xs text-text-muted">Latest regression checks for search, sentiment, and alert rules.</div>
          </div>
          <StatusBadge status={evalRuns.some((run) => run.metrics?.gate_status === 'fail' || run.status !== 'completed') ? 'warning' : 'success'} label={`${evalRuns.length} tasks`} />
        </div>
        {evalRuns.length === 0 ? (
          <div className="text-sm text-text-muted">No eval runs have been recorded yet. Use the eval CLI after seeding sets.</div>
        ) : (
          <div className="grid gap-2 md:grid-cols-3">
            {evalRuns.map((run) => (
              <div key={run.id} className="rounded-md border border-border bg-background px-3 py-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{run.task_type}</span>
                  <StatusBadge status={gateStatus(run)} label={String(run.metrics?.gate_status || run.status)} />
                </div>
                <div className="text-xs text-text-muted">{metricSummary(run.metrics)}</div>
                {Array.isArray(run.metrics?.gate_failures) && run.metrics.gate_failures.length > 0 && (
                  <div className="mt-1 text-xs text-error">{run.metrics.gate_failures.slice(0, 2).join(' · ')}</div>
                )}
                <div className="mt-1 text-[0.7rem] text-text-muted">{fmtDate(run.finished_at || run.started_at)}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {isLoading ? (
        <LoadingSpinner />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<Play className="h-10 w-10" />}
          title="No runs yet"
          description="Click Trigger run to start the pipeline. It refreshes people, evidence, alerts and media analysis."
        />
      ) : (
        <>
          <table>
            <thead>
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((h) => {
                    const sorted = h.column.getIsSorted()
                    return (
                      <th
                        key={h.id}
                        onClick={h.column.getToggleSortingHandler()}
                        className="cursor-pointer select-none"
                      >
                        <span className="inline-flex items-center gap-1">
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {sorted === 'asc' && <ChevronUp size={12} />}
                          {sorted === 'desc' && <ChevronDown size={12} />}
                        </span>
                      </th>
                    )
                  })}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                  ))}
                  <td>
                    <button className="text-xs text-text-muted hover:text-text-primary" onClick={() => openPhases(row.original.id)}>
                      phases
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {phaseRun && (
            <div className="mt-3 rounded-lg border border-border bg-surface p-3">
              <div className="mb-2 text-sm font-semibold">Phase waterfall</div>
              {phases.length === 0 ? (
                <div className="text-sm text-text-muted">No phase rows recorded.</div>
              ) : (
                <div className="grid gap-1 md:grid-cols-2">
                  {phases.map((phase) => (
                    <div key={`${phase.phase}-${phase.created_at}`} className="flex items-center justify-between gap-3 rounded bg-hover px-2 py-1 text-xs">
                      <span className="truncate">{phase.phase}</span>
                      <span className="shrink-0 text-text-muted">
                        {phase.status} · {phase.duration_ms != null ? `${phase.duration_ms}ms` : '—'}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="mt-4 flex items-center justify-between">
            <span className="text-sm text-text-muted tabular-nums">{total} runs</span>
            <div className="flex gap-1">
              <Button variant="ghost" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                Prev
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={page * PER_PAGE >= total}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
