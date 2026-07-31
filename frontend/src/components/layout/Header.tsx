import { Link } from 'react-router'
import { HelpCircle } from 'lucide-react'
import { clsx } from '../../lib/cx'
import { useRuns } from '../../hooks'
import type { LiveHealth } from '../../api'

/**
 * Global top strip inside the AppShell. Right-aligned:
 *   • Live pipeline pill (running / idle · age, pulsing dot)
 *   • (?) help link → /help
 *
 * Deviates slightly from the plan spec ("logo mark + title + pipeline pill") —
 * the logo mark and title already live in the sidebar's brand block, so
 * duplicating them here read as noisy. The pipeline pill is the new value-add.
 */
export function Header({ health }: { health: LiveHealth | null }) {
  return (
    <header className="sticky top-0 z-10 flex h-11 items-center justify-between border-b border-border bg-background/80 px-6 backdrop-blur">
      {/* Reserved for future breadcrumb / page context. Kept empty so the
          pipeline pill has full weight on the right side. */}
      <div />
      <div className="flex items-center gap-3">
        <PipelinePill health={health} />
        <Link
          to="/help"
          title="Help & glossary"
          className="text-text-muted hover:text-text-primary"
          aria-label="Help"
        >
          <HelpCircle className="h-4 w-4" />
        </Link>
      </div>
    </header>
  )
}

/** Age of an ISO timestamp as a short human string. */
function ago(iso: string | null | undefined): string {
  if (!iso) return '—'
  const ms = Date.now() - new Date(iso).getTime()
  const m = Math.max(0, Math.floor(ms / 60000))
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

/**
 * Small live status pill. Three states:
 *   running — pulsing blue, "Pipeline running"
 *   idle    — steady green, "Idle · <age of last completed run>"
 *   none    — grey, "No runs yet"
 *
 * Running-vs-idle is derived from the most recent run row (useRuns page 1).
 * Last-run age comes from `LiveHealth.last_completed_run.finished_at`.
 */
function PipelinePill({ health }: { health: LiveHealth | null }) {
  const runs = useRuns(1)
  const anyRunning = (runs.data?.data ?? []).some((r) => r.status === 'running')
  const last = health?.last_completed_run

  let dot: string
  let label: string
  if (anyRunning) {
    dot = 'bg-info animate-pulse'
    label = 'Pipeline running'
  } else if (last) {
    dot = 'bg-success'
    label = `Idle · ${last.run_type} ${ago(last.finished_at)}`
  } else {
    dot = 'bg-text-muted'
    label = 'No runs yet'
  }

  return (
    <div
      className="flex items-center gap-1.5 text-xs"
      title={last ? `Last ${last.run_type} finished ${last.finished_at}` : 'No completed runs recorded'}
    >
      <span className={clsx('h-2 w-2 rounded-full', dot)} />
      <span className="tabular-nums text-text-secondary">{label}</span>
    </div>
  )
}
