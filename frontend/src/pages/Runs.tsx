import { useMemo, useState } from 'react'
import {
  createColumnHelper, flexRender, getCoreRowModel, getSortedRowModel,
  useReactTable, SortingState,
} from '@tanstack/react-table'
import { ChevronUp, ChevronDown } from 'lucide-react'
import { useRuns, useTriggerRun } from '../hooks'
import { RunInfo } from '../api'

const PER_PAGE = 20

function statusBadge(s: string) {
  const cls = s === 'completed' ? 'badge-green' : s === 'running' ? 'badge-yellow' : 'badge-red'
  return <span className={`badge ${cls}`}>{s}</span>
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
  col.accessor('run_type', { header: 'Type' }),
  col.accessor('status', { header: 'Status', cell: (c) => statusBadge(c.getValue()) }),
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
      <div className="flex-between mb-2">
        <div>
          <h2 className="text-xl font-semibold">Runs</h2>
          <p className="mt-1 text-sm text-text-secondary">
            The background pipeline that refreshes everything. Frequent incremental runs are quick; full runs re-check everyone from scratch.
          </p>
        </div>
        <button className="primary" onClick={() => trigger.mutate()} disabled={trigger.isPending}>
          {trigger.isPending ? 'Running…' : 'Trigger Run'}
        </button>
      </div>

      {isLoading ? (
        <div className="empty-state">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">No runs yet. Click "Trigger Run" to start.</div>
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
          <div className="flex-between" style={{ marginTop: '1rem' }}>
            <span className="text-sm text-muted">{total} runs</span>
            <div className="flex gap-1">
              <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
              <button disabled={page * PER_PAGE >= total} onClick={() => setPage((p) => p + 1)}>Next</button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
