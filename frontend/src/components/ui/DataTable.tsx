import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import { useState } from 'react'
import { ChevronUp, ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from './Button'
import { clsx } from '../../lib/cx'

interface DataTableProps<T> {
  data: T[]
  columns: ColumnDef<T, unknown>[]
  /** Message when `data` is empty (defaults to "No data"). */
  emptyMessage?: string
  /** Optional wrapper class — e.g. constrain max-height. */
  className?: string
  /** When true, cells render with the mono face (nice for IDs / hashes). */
  monoCells?: boolean
}

/**
 * Sortable, paginated table built on TanStack Table. Adapted from the collector's
 * `DataTable` — same props/columns shape so column defs can be shared verbatim.
 */
export function DataTable<T>({
  data,
  columns,
  emptyMessage = 'No data',
  className,
  monoCells = false,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([])

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  })

  return (
    <div className={clsx('space-y-3', className)}>
      <div className="max-h-[600px] overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 border-b border-border bg-surface">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th
                    key={h.id}
                    className="cursor-pointer select-none px-4 py-2 text-left text-xs font-medium uppercase tracking-wider
                               text-text-muted hover:bg-white/5"
                    onClick={h.column.getToggleSortingHandler()}
                  >
                    <span className="inline-flex items-center gap-1">
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      {h.column.getIsSorted() === 'asc' && <ChevronUp className="h-3 w-3" />}
                      {h.column.getIsSorted() === 'desc' && <ChevronDown className="h-3 w-3" />}
                    </span>
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-border">
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} className="hover:bg-white/5">
                {row.getVisibleCells().map((cell) => (
                  <td
                    key={cell.id}
                    className={clsx(
                      'px-4 py-2 text-sm text-text-secondary',
                      monoCells && 'font-mono',
                    )}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
            {table.getRowModel().rows.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="px-4 py-8 text-center text-sm text-text-muted">
                  {emptyMessage}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {table.getPageCount() > 1 && (
        <div className="flex items-center justify-between text-xs text-text-muted">
          <span className="tabular-nums">{data.length} results</span>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              icon={<ChevronLeft className="h-3 w-3" />}
            >
              Prev
            </Button>
            <span className="tabular-nums">
              {table.getState().pagination.pageIndex + 1} / {table.getPageCount()}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              icon={<ChevronRight className="h-3 w-3" />}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
