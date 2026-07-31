import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { GitCompare, Bell, UserPlus } from 'lucide-react'
import { api, TriageData } from '../api'
import { FaceAvatar } from '../components/FaceAvatar'
import { PageHeader } from '../components/ui/PageHeader'
import { MetricCard } from '../components/ui/MetricCard'
import { EmptyState } from '../components/ui/EmptyState'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { LABELS } from '../lib/labels'

/**
 * Triage — the at-a-glance home / overview. Shows coverage, what needs
 * reviewing (as a jump-off to the Review tab — merge decisions happen there, not
 * here), what changed recently, and who's new. No duplicated same/not-same
 * controls: Review is the single place to make those calls.
 */
export default function TriagePage() {
  const [data, setData] = useState<TriageData | null>(null)
  useEffect(() => {
    api.getTriage().then(setData).catch(() => setData(null))
  }, [])

  if (!data) return <LoadingSpinner label="Loading triage…" />
  const cov = data.coverage

  return (
    <div>
      <PageHeader
        title="Triage"
        description="Your at-a-glance status: how much is covered, what needs reviewing, what changed, and who's new."
      />

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <MetricCard label="People tracked" value={cov.entities} help="Distinct people the system is following, each built from one or more accounts." />
        <MetricCard label="With a face" value={`${cov.with_faces_pct}%`} help="Share of people we have at least one photo/face for." />
        <MetricCard label="On 2+ platforms" value={`${cov.multi_platform_pct}%`} help="Share of people linked across more than one platform." />
        <MetricCard
          label="Pairs to review"
          value={cov.merge_backlog}
          status={cov.merge_backlog > 0 ? 'warning' : 'success'}
          help="Account pairs that might be the same person. Decide them in the Review tab."
        />
        <MetricCard label="Unread alerts" value={cov.unread_alerts} />
      </div>

      {cov.merge_backlog > 0 && (
        <Link
          to="/review"
          className="mb-6 flex items-center justify-between rounded-lg border border-border bg-surface p-4 transition-colors hover:bg-hover"
        >
          <div className="flex items-center gap-3">
            <GitCompare className="h-5 w-5 text-text-muted" />
            <div>
              <div className="font-medium">{cov.merge_backlog} pairs waiting to review</div>
              <div className="text-sm text-text-secondary">
                Confirm or reject accounts that might be the same person — each choice trains the system.
              </div>
            </div>
          </div>
          <span className="shrink-0 text-sm">Open Review →</span>
        </Link>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <div className="mb-2 flex items-center justify-between text-sm font-semibold">
            <span className="flex items-center gap-1.5"><Bell className="h-4 w-4" /> What changed</span>
            <Link to="/alerts" className="text-xs text-text-muted">all &rarr;</Link>
          </div>
          {data.alerts.length === 0 ? (
            <EmptyState title="Nothing new" description="Recent alerts about people you track will appear here." />
          ) : (
            <div className="flex flex-col gap-1.5">
              {data.alerts.map((a) => (
                <Link
                  key={a.id}
                  to={a.entity_id ? `/entities/${a.entity_id}` : '/alerts'}
                  className="flex items-center gap-2 rounded-lg border border-border bg-surface p-2 hover:bg-hover"
                >
                  {a.entity_id && <FaceAvatar url={a.face} name={a.entity_name} size={28} />}
                  <div className="min-w-0">
                    <div className="truncate text-xs font-medium">{a.title || LABELS.alertType[a.alert_type] || a.alert_type}</div>
                    <div className="truncate text-xs text-text-muted">
                      {a.entity_name || ''}{a.detail ? ' · ' + a.detail : ''}
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>

        <div>
          <div className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
            <UserPlus className="h-4 w-4" /> New people
          </div>
          {data.new_entities.length === 0 ? (
            <EmptyState title="No one new" description="Newly-discovered people show up here as collection continues." />
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {data.new_entities.map((n) => (
                <Link
                  key={n.id}
                  to={`/entities/${n.id}`}
                  className="flex items-center gap-1.5 rounded-full border border-border bg-surface py-1 pl-1 pr-2.5 text-xs hover:bg-hover"
                >
                  <FaceAvatar url={n.face} name={n.canonical_name} size={22} />
                  <span className="max-w-[140px] truncate">{n.canonical_name || n.id.slice(0, 8)}</span>
                  <span className="text-text-muted">{n.platforms}p</span>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
