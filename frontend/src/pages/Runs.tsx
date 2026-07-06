import { useMemo, useState } from 'react'
import {
  createColumnHelper, flexRender, getCoreRowModel, getSortedRowModel,
  useReactTable, SortingState,
} from '@tanstack/react-table'
import { ChevronUp, ChevronDown, Play } from 'lucide-react'
import { useRuns, useTriggerRun } from '../hooks'
import { RunInfo } from '../api'
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
  const { data, isLoading } = useRuns(page)
  const trigger = useTriggerRun()

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
                </tr>
              ))}
            </tbody>
          </table>
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
