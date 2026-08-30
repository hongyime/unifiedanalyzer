import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { GitCompare, Bell, UserPlus, MapPin, MessageCircle, ScanFace, Database, Gauge, Languages } from 'lucide-react'
import { api, CollectorCoverageRow, EvalRun, MultilingualStatus, TriageData } from '../api'
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
/** Strip a raw metadata dict the backend sometimes appends ("name · {json}") so
 *  the feed never leaks JSON; the alert title already carries the human message. */
function cleanDetail(detail: string | null): string {
  if (!detail) return ''
  const brace = detail.indexOf('{')
  return (brace >= 0 ? detail.slice(0, brace) : detail).replace(/[·\s]+$/, '').trim()
}

function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

export default function TriagePage() {
  const [data, setData] = useState<TriageData | null>(null)
  const [coverage, setCoverage] = useState<CollectorCoverageRow[]>([])
  const [evalRuns, setEvalRuns] = useState<EvalRun[]>([])
  const [multilingual, setMultilingual] = useState<MultilingualStatus | null>(null)
  useEffect(() => {
    api.getTriage().then(setData).catch(() => setData(null))
    api.getCollectorCoverage().then((r) => setCoverage(r.sources)).catch(() => setCoverage([]))
    api.getEvalLatest().then((r) => setEvalRuns(r.data)).catch(() => setEvalRuns([]))
    api.getMultilingualStatus().then(setMultilingual).catch(() => setMultilingual(null))
  }, [])

  if (!data) return <LoadingSpinner label="Loading triage…" />
  const cov = data.coverage
  const freshSources = coverage.filter((row) => row.status === 'fresh').length
  const degradedSources = coverage.filter((row) => row.status === 'degraded').length
  const staleSources = coverage.filter((row) => row.status === 'stale').length
  const evalFailures = evalRuns.filter((run) => run.status !== 'completed' || run.metrics?.gate_status === 'fail').length
  const evalWarnings = evalRuns.filter((run) => run.metrics?.gate_status === 'warn').length

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
        <MetricCard label="Unread alerts" value={cov.unread_alerts} status={cov.unread_alerts > 0 ? 'warning' : 'idle'} />
      </div>

      {cov.merge_backlog > 0 && (
        <Link
          to="/review"
          className="mb-6 flex items-center justify-between rounded-xl border border-warning/30 bg-warning/5 p-4 transition-colors hover:bg-warning/10"
        >
          <div className="flex items-center gap-3">
            <GitCompare className="h-5 w-5 text-warning" />
            <div>
              <div className="font-medium">{cov.merge_backlog} pairs waiting to review</div>
              <div className="text-sm text-text-secondary">
                Confirm or reject accounts that might be the same person — each choice trains the system.
              </div>
            </div>
          </div>
          <span className="shrink-0 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white">Open Review →</span>
        </Link>
      )}

      <h2 className="mb-2 text-xs font-semibold uppercase tracking-widest text-text-muted">Explore</h2>
      <div className="mb-6 grid gap-3 md:grid-cols-3">
        <Link to="/search" className="rounded-lg border border-border bg-surface p-3 hover:bg-hover">
          <div className="mb-1 flex items-center gap-2 text-sm font-semibold"><MessageCircle className="h-4 w-4" /> Chat intelligence</div>
          <div className="text-xs text-text-muted">Thread analytics, replies, reactions, and context-only tone summaries.</div>
        </Link>
        <Link to="/faces" className="rounded-lg border border-border bg-surface p-3 hover:bg-hover">
          <div className="mb-1 flex items-center gap-2 text-sm font-semibold"><ScanFace className="h-4 w-4" /> Face audit</div>
          <div className="text-xs text-text-muted">
            {cov.face_bridge_audit?.available
              ? `${cov.face_bridge_audit.face_entity_collisions ?? 0} face collisions · ${cov.face_bridge_audit.cluster_entity_collisions ?? 0} drift clusters`
              : 'Audit unavailable'}
          </div>
        </Link>
        <Link to="/entities" className="rounded-lg border border-border bg-surface p-3 hover:bg-hover">
          <div className="mb-1 flex items-center gap-2 text-sm font-semibold"><MapPin className="h-4 w-4" /> Location quality</div>
          <div className="text-xs text-text-muted">Evidence chips expose source, review state, confidence, and weak samples per person.</div>
        </Link>
      </div>

      <h2 className="mb-2 text-xs font-semibold uppercase tracking-widest text-text-muted">Pipeline health</h2>
      <div className="mb-6 grid gap-3 lg:grid-cols-3">
        <Link to="/collector" className="rounded-lg border border-border bg-surface p-4 hover:bg-hover">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold"><Database className="h-4 w-4" /> Collector coverage</div>
            <span className={staleSources || degradedSources ? 'text-xs text-warning' : 'text-xs text-success'}>
              {freshSources}/{coverage.length || 0} fresh
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="rounded-md bg-hover p-2"><div className="font-semibold">{freshSources}</div><div className="text-[0.7rem] text-text-muted">fresh</div></div>
            <div className="rounded-md bg-hover p-2"><div className="font-semibold">{degradedSources}</div><div className="text-[0.7rem] text-text-muted">degraded</div></div>
            <div className="rounded-md bg-hover p-2"><div className="font-semibold">{staleSources}</div><div className="text-[0.7rem] text-text-muted">stale</div></div>
          </div>
          <div className="mt-2 text-xs text-text-muted">Alert confidence should be discounted when a source is degraded or stale.</div>
        </Link>

        <Link to="/eval" className="rounded-lg border border-border bg-surface p-4 hover:bg-hover">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold"><Gauge className="h-4 w-4" /> Evaluation checks</div>
            <span className={evalFailures ? 'text-xs text-error' : evalWarnings ? 'text-xs text-warning' : 'text-xs text-success'}>
              {evalFailures ? `${evalFailures} fail` : evalWarnings ? `${evalWarnings} warn` : `${evalRuns.length} latest`}
            </span>
          </div>
          <div className="flex flex-wrap gap-1">
            {evalRuns.length === 0 ? (
              <span className="text-xs text-text-muted">No eval runs recorded yet.</span>
            ) : evalRuns.map((run) => (
              <span key={run.id} className="rounded-full bg-hover px-2 py-0.5 text-xs text-text-secondary">
                {run.task_type}: {String(run.metrics?.gate_status || run.status)}
              </span>
            ))}
          </div>
          <div className="mt-2 text-xs text-text-muted">Search, sentiment, and alert rules now have machine-readable regression evidence.</div>
        </Link>

        <Link to="/multilingual" className="rounded-lg border border-border bg-surface p-4 hover:bg-hover">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold"><Languages className="h-4 w-4" /> Multilingual NLP</div>
            <span className={multilingual?.failed_translation_rows ? 'text-xs text-warning' : 'text-xs text-success'}>
              {multilingual ? `${multilingual.profile_coverage_pct}% profiled` : 'unavailable'}
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="rounded-md bg-hover p-2"><div className="font-semibold">{multilingual?.profile_rows.toLocaleString() ?? 0}</div><div className="text-[0.7rem] text-text-muted">profiles</div></div>
            <div className="rounded-md bg-hover p-2"><div className="font-semibold">{multilingual?.translated_rows.toLocaleString() ?? 0}</div><div className="text-[0.7rem] text-text-muted">translated</div></div>
            <div className="rounded-md bg-hover p-2"><div className="font-semibold">{multilingual?.code_mixed_rows.toLocaleString() ?? 0}</div><div className="text-[0.7rem] text-text-muted">code-mix</div></div>
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            {(multilingual?.languages ?? []).slice(0, 5).map((row) => (
              <span key={row.language} className="rounded-full bg-hover px-2 py-0.5 text-xs text-text-secondary">
                {row.language}: {row.count.toLocaleString()}
              </span>
            ))}
          </div>
          {multilingual?.failed_translation_rows ? (
            <div className="mt-2 text-xs text-warning">{multilingual.failed_translation_rows.toLocaleString()} translation failures need review.</div>
          ) : (
            <div className="mt-2 text-xs text-text-muted">Translated matches are labeled in timeline search.</div>
          )}
        </Link>
      </div>

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
                      {[a.entity_name || cleanDetail(a.detail), timeAgo(a.detected_at)].filter(Boolean).join(' · ')}
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
