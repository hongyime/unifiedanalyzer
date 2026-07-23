import { useEffect, useMemo, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { ArrowLeft, Trash2, Bell, MapPin, ChevronLeft, ChevronRight, History } from 'lucide-react'
import type { ColumnDef } from '@tanstack/react-table'
import {
  api,
  EntityDetail,
  TimelineEvent,
  BehaviorProfile,
  InteractionPeer,
  Relationship,
  IntelligenceReport,
  PlatformLink,
  Signal,
  SocialCircleEntry,
  DecisionHistoryEntry,
} from '../api'
import { FaceAvatar } from '../components/FaceAvatar'
import { TimelineLanes } from '../components/TimelineLanes'
import { ConnectionsPanel } from '../components/ConnectionsPanel'
import { IdentitySummary } from '../components/IdentitySummary'
import { IntersectPanel } from '../components/IntersectPanel'
import { GeoMap } from '../components/GeoMap'
import { PageHeader } from '../components/ui/PageHeader'
import { PlatformBadge } from '../components/ui/PlatformBadge'
import { Card } from '../components/ui/Card'
import { Button } from '../components/ui/Button'
import { ConfidencePill } from '../components/ui/Confidence'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { EmptyState } from '../components/ui/EmptyState'
import { StatusBadge } from '../components/ui/StatusBadge'
import { DataTable } from '../components/ui/DataTable'
import { InfoTip } from '../components/ui/InfoTip'
import { LABELS } from '../lib/labels'

const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function formatDate(iso: string) {
  return new Date(iso).toLocaleString()
}

function isoFromEpoch(ts: number | null): string | null {
  return ts == null ? null : new Date(ts * 1000).toISOString()
}

function engagementMetrics(metadata: Record<string, unknown> | null | undefined) {
  const out: { label: string; value: string }[] = []
  if (!metadata || typeof metadata !== 'object') return out
  for (const [key, label] of [
    ['likes_count', 'likes'],
    ['comments_count', 'comments'],
    ['views_count', 'views'],
  ] as const) {
    const raw = metadata[key]
    if (raw == null || raw === '') continue
    const num = typeof raw === 'number' ? raw : Number(raw)
    out.push({ label, value: Number.isFinite(num) ? num.toLocaleString() : String(raw) })
  }
  return out
}

function compactValue(value: unknown): string {
  if (value == null || value === '') return ''
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function decisionKeyValues(payload: Record<string, unknown>) {
  return ['notes', 'reason', 'confidence', 'relationship_type', 'watch_status', 'source', 'platform_id']
    .map((key) => ({ key, value: compactValue(payload[key]) }))
    .filter((item) => item.value)
}

function decisionDurability(decision: DecisionHistoryEntry): { status: 'success' | 'warning' | 'error'; label: string } {
  if (decision.decision_jsonl_error) return { status: 'error', label: 'JSONL error' }
  if (decision.durable) return { status: 'success', label: 'JSONL written' }
  return { status: 'warning', label: 'JSONL pending' }
}

/** Reused hour+day activity bars. Uses the info accent for both charts so the
 *  read is "the taller the bar, the more posts". */
function HeatmapGrid({ hourDist, dowDist }: { hourDist: Record<string, number>; dowDist: Record<string, number> }) {
  const maxH = Math.max(1, ...Object.values(hourDist))
  const maxD = Math.max(1, ...Object.values(dowDist))
  return (
    <div>
      <div className="mb-4">
        <div className="mb-1 text-xs text-text-muted">Activity by hour (UTC)</div>
        <div className="flex h-20 items-end gap-[2px]">
          {Array.from({ length: 24 }, (_, h) => {
            const val = hourDist[String(h)] || 0
            const pct = val / maxH
            return (
              <div key={h} className="flex flex-1 flex-col items-center">
                <div
                  className="w-full rounded-sm"
                  style={{
                    height: `${Math.max(2, pct * 70)}px`,
                    background: `rgba(59, 130, 246, ${0.2 + pct * 0.8})`,
                  }}
                  title={`${h}:00 — ${val} events`}
                />
                {h % 3 === 0 && <span className="mt-0.5 text-[0.6rem] text-text-muted">{h}</span>}
              </div>
            )
          })}
        </div>
      </div>
      <div>
        <div className="mb-1 text-xs text-text-muted">Activity by day</div>
        <div className="flex h-14 items-end gap-1">
          {Array.from({ length: 7 }, (_, d) => {
            const val = dowDist[String(d)] || 0
            const pct = val / maxD
            return (
              <div key={d} className="flex flex-1 flex-col items-center">
                <div
                  className="w-full rounded-sm"
                  style={{
                    height: `${Math.max(2, pct * 50)}px`,
                    background: `rgba(59, 130, 246, ${0.2 + pct * 0.8})`,
                  }}
                  title={`${DOW_LABELS[d]} — ${val} events`}
                />
                <span className="mt-0.5 text-[0.65rem] text-text-muted">{DOW_LABELS[d]}</span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

/** Small helper for stat rows inside Cards. */
function StatRow({ label, value, help }: { label: string; value: React.ReactNode; help?: string }) {
  return (
    <div className="mb-1 flex items-center justify-between">
      <span className="inline-flex items-center gap-1 text-sm text-text-muted">
        {label}
        {help && <InfoTip text={help} />}
      </span>
      <span className="text-sm font-semibold text-text-primary">{value}</span>
    </div>
  )
}

type TabKey =
  | 'identity'
  | 'changes'
  | 'timeline'
  | 'map'
  | 'intersect'
  | 'behavior'
  | 'connections'
  | 'intelligence'
  | 'decisions'
  | 'settings'

// Tabs that share the timeline brush (lanes + time-window filtering). The
// consolidated Connections tab keeps interactions windowable, so it is included.
const TIMELINE_TABS = new Set<TabKey>(['timeline', 'map', 'connections'])

export default function EntityDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [entity, setEntity] = useState<EntityDetail | null>(null)
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [eventsTotal, setEventsTotal] = useState(0)
  const [eventsPage, setEventsPage] = useState(1)
  const [tab, setTab] = useState<TabKey>('identity')
  const [loading, setLoading] = useState(true)
  const [behavior, setBehavior] = useState<BehaviorProfile | null>(null)
  const [intelligence, setIntelligence] = useState<IntelligenceReport | null>(null)
  const [selectedLinks, setSelectedLinks] = useState<Set<string>>(new Set())
  const [silenceThreshold, setSilenceThreshold] = useState('')
  const [notes, setNotes] = useState('')
  const [mergeTarget, setMergeTarget] = useState('')
  const [relationships, setRelationships] = useState<Relationship[]>([])
  const [interactions, setInteractions] = useState<InteractionPeer[]>([])
  const [actionMsg, setActionMsg] = useState('')
  const [cases, setCases] = useState<{ id: string; name: string }[]>([])
  const [pinCase, setPinCase] = useState('')
  const [brushRange, setBrushRange] = useState<[number, number] | null>(null)
  const [lanes, setLanes] = useState<Awaited<ReturnType<typeof api.getTimelineLanes>> | null>(null)
  const [selectedGeoEvent, setSelectedGeoEvent] = useState<{ label: string | null; source: string; occurred_at: string | null } | null>(null)
  useEffect(() => { api.getCases().then(d => setCases(d.cases)).catch(() => {}) }, [])
  useEffect(() => {
    setBrushRange(null)
    setLanes(null)
  }, [id])
  useEffect(() => { setSelectedGeoEvent(null) }, [id, tab])

  useEffect(() => {
    if (!id) return
    setLoading(true)
    api.getEntity(id).then(e => {
      setEntity(e)
      setSilenceThreshold('')
      setNotes('')
    }).finally(() => setLoading(false))
  }, [id])

  useEffect(() => {
    if (!id || tab !== 'timeline') return
    api.getTimeline(
      id,
      eventsPage,
      '',
      '',
      isoFromEpoch(brushRange?.[0] ?? null),
      isoFromEpoch(brushRange?.[1] ?? null),
    ).then(r => {
      setEvents(r.data)
      setEventsTotal(r.total)
    })
  }, [id, tab, eventsPage, brushRange])
  useEffect(() => {
    if (!id || !TIMELINE_TABS.has(tab)) return
    api.getTimelineLanes(id, 2000).then((data) => {
      setLanes(data)
      setBrushRange((current) => current ?? (data.min_t != null && data.max_t != null ? [data.min_t, data.max_t] : null))
    }).catch(() => setLanes(null))
  }, [id, tab])

  useEffect(() => {
    if (!id || tab !== 'behavior') return
    api.getBehavior(id).then(setBehavior).catch(() => setBehavior(null))
  }, [id, tab])

  // Interactions are windowable — reload when the time brush moves. The rest of
  // the Connections data (relationships, network, associates, social circle) is
  // window-independent and loaded once per entity when the tab opens.
  useEffect(() => {
    if (!id || tab !== 'connections') return
    api.getInteractions(
      id,
      isoFromEpoch(brushRange?.[0] ?? null),
      isoFromEpoch(brushRange?.[1] ?? null),
    ).then(r => setInteractions(r.data)).catch(() => setInteractions([]))
  }, [id, tab, brushRange])

  const [network, setNetwork] = useState<Awaited<ReturnType<typeof api.getEntityNetwork>> | null>(null)
  const [associates, setAssociates] = useState<Awaited<ReturnType<typeof api.getEntityAssociates>> | null>(null)
  useEffect(() => {
    if (!id || tab !== 'connections') return
    api.getRelationships(id).then(r => setRelationships(r.data)).catch(() => setRelationships([]))
    api.getEntityNetwork(id).then(setNetwork).catch(() => setNetwork(null))
    api.getEntityAssociates(id).then(setAssociates).catch(() => setAssociates(null))
    api.getEntitySocialCircle(id).then(d => setSocialCircle(d.associations)).catch(() => setSocialCircle([]))
  }, [id, tab])

  const [geo, setGeo] = useState<Awaited<ReturnType<typeof api.getEntityGeo>> | null>(null)
  useEffect(() => {
    if (!id || tab !== 'map') return
    api.getEntityGeo(
      id,
      isoFromEpoch(brushRange?.[0] ?? null),
      isoFromEpoch(brushRange?.[1] ?? null),
    ).then(setGeo).catch(() => setGeo(null))
  }, [id, tab, brushRange])

  const [socialCircle, setSocialCircle] = useState<SocialCircleEntry[] | null>(null)

  const [changelog, setChangelog] = useState<Awaited<ReturnType<typeof api.getChangelog>> | null>(null)
  const loadChangelog = () => { if (id) api.getChangelog(id).then(setChangelog).catch(() => setChangelog(null)) }
  useEffect(() => { if (id && tab === 'changes') loadChangelog() }, [id, tab])

  const [decisions, setDecisions] = useState<DecisionHistoryEntry[] | null>(null)
  const [decisionsTotal, setDecisionsTotal] = useState(0)
  const loadDecisions = () => {
    if (!id) return
    api.getEntityDecisions(id).then((data) => {
      setDecisions(data.decisions)
      setDecisionsTotal(data.total)
    }).catch(() => {
      setDecisions([])
      setDecisionsTotal(0)
    })
  }
  useEffect(() => {
    setDecisions(null)
    setDecisionsTotal(0)
  }, [id])
  useEffect(() => { if (id && tab === 'decisions') loadDecisions() }, [id, tab])

  useEffect(() => {
    if (!id || tab !== 'intelligence') return
    api.getIntelligence(id).then(setIntelligence).catch(() => setIntelligence(null))
  }, [id, tab])

  const handleSplit = async () => {
    if (!id || selectedLinks.size === 0) return
    try {
      const result = await api.splitEntity(id, Array.from(selectedLinks), 'Manual split from UI')
      setActionMsg(`Split done — new entity created`)
      setSelectedLinks(new Set())
      navigate(`/entities/${result.new_entity_id}`)
    } catch (e: any) {
      setActionMsg(`Split failed: ${e.message}`)
    }
  }

  const handleMerge = async () => {
    if (!id || !mergeTarget.trim()) return
    try {
      const result = await api.mergeEntities([id, mergeTarget.trim()], 'Manual merge from UI')
      setActionMsg(`Merged into entity`)
      navigate(`/entities/${result.target_entity_id}`)
    } catch (e: any) {
      setActionMsg(`Merge failed: ${e.message}`)
    }
  }

  // Same-person candidate decisions. These double as ground-truth labels for the
  // calibrated scorer (merge = same person, dismiss = different) — captured
  // server-side into identity_labels, no CSV.
  const handleConfirmSame = async (otherId: string) => {
    if (!id) return
    try {
      const result = await api.mergeEntities([id, otherId], 'Confirmed same person from candidates')
      setActionMsg('Merged — labeled as same person')
      navigate(`/entities/${result.target_entity_id}`)
    } catch (e: any) {
      setActionMsg(`Merge failed: ${e.message}`)
    }
  }

  const handleDismissMatch = async (otherId: string) => {
    if (!id) return
    try {
      await api.dismissMatch(id, otherId)
      setActionMsg('Dismissed — labeled as different people')
      const fresh = await api.getIntelligence(id)
      setIntelligence(fresh)
    } catch (e: any) {
      setActionMsg(`Dismiss failed: ${e.message}`)
    }
  }

  const handleRelationshipDecision = async (otherEntityId: string, relationshipType: string, isReal: boolean) => {
    if (!id) return
    try {
      await api.decideRelationship({
        entity_a: id,
        entity_b: otherEntityId,
        relationship_type: relationshipType,
        is_real: isReal,
        evidence_refs: {
          source: 'connections_panel',
          entity_page: id,
          other_entity_id: otherEntityId,
        },
      })
      setActionMsg(isReal ? 'Relationship confirmed' : 'Relationship rejected')
      api.getEntityDecisions(id, 1).then((data) => setDecisionsTotal(data.total)).catch(() => {})
      if (tab === 'decisions') loadDecisions()
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setActionMsg(`Relationship decision failed: ${message}`)
      throw e
    }
  }

  const handleSaveSettings = async () => {
    if (!id) return
    try {
      const threshold = silenceThreshold ? parseFloat(silenceThreshold) : null
      await api.updateEntitySettings(id, {
        silence_threshold_days: threshold,
        notes: notes || undefined,
      })
      setActionMsg('Settings saved')
    } catch (e: any) {
      setActionMsg(`Save failed: ${e.message}`)
    }
  }

  // ── Column definitions ──────────────────────────────────────────────────
  const platformLinkCols = useMemo<ColumnDef<PlatformLink, unknown>[]>(() => [
    {
      id: 'select',
      header: '',
      cell: ({ row }) => (
        <input
          type="checkbox"
          checked={selectedLinks.has(row.original.id)}
          onChange={(e) => {
            const next = new Set(selectedLinks)
            if (e.target.checked) next.add(row.original.id)
            else next.delete(row.original.id)
            setSelectedLinks(next)
          }}
          onClick={(e) => e.stopPropagation()}
        />
      ),
    },
    { id: 'platform', header: 'Platform', cell: ({ row }) => <PlatformBadge source={row.original.source} /> },
    {
      id: 'username',
      header: 'Username',
      accessorFn: (r) => r.platform_username ?? r.platform_id,
      cell: ({ row }) => <span className="font-mono text-xs">{row.original.platform_username || row.original.platform_id}</span>,
    },
    { id: 'name', header: 'Name', accessorFn: (r) => r.platform_name ?? '', cell: ({ row }) => row.original.platform_name || '—' },
    {
      id: 'confirmed',
      header: 'State',
      cell: ({ row }) => (
        row.original.is_confirmed
          ? <StatusBadge status="success" label="confirmed" />
          : <StatusBadge status="warning" label="candidate" />
      ),
    },
    { id: 'method', header: 'Method', cell: ({ row }) => <span className="text-xs text-text-muted">{row.original.link_method}</span> },
  ], [selectedLinks])

  const signalCols = useMemo<ColumnDef<Signal, unknown>[]>(() => [
    {
      id: 'type',
      header: 'Evidence',
      accessorFn: (r) => LABELS.signalType[r.signal_type] ?? r.signal_type,
      cell: ({ row }) => (
        <span
          className="rounded-full bg-info/15 px-2 py-0.5 text-xs font-medium text-info"
          title={row.original.signal_type}
        >
          {LABELS.signalType[row.original.signal_type] ?? row.original.signal_type.replace(/_/g, ' ')}
        </span>
      ),
    },
    { id: 'from', header: 'From', cell: ({ row }) => <PlatformBadge source={row.original.source_platform} /> },
    { id: 'to', header: 'To', cell: ({ row }) => <PlatformBadge source={row.original.target_platform} /> },
    {
      id: 'value',
      header: 'Value',
      accessorFn: (r) => r.value,
      cell: ({ row }) => (
        <span className="block max-w-[300px] truncate font-mono text-xs" title={row.original.value}>
          {row.original.value}
        </span>
      ),
    },
    {
      id: 'confidence',
      header: 'Confidence',
      accessorFn: (r) => r.confidence,
      cell: ({ row }) => <ConfidencePill score={row.original.confidence} />,
    },
  ], [])

  if (loading) return <LoadingSpinner label="Loading person…" />
  if (!entity) return <EmptyState title="Person not found" description="This entity may have been merged or removed." />

  const watchOptions: { key: 'priority' | 'watching' | 'archive'; label: string; help: string }[] = [
    { key: 'priority', label: 'Priority', help: 'Flag this person for high-attention monitoring.' },
    { key: 'watching', label: 'Watching', help: 'Keep an eye on this person — standard monitoring.' },
    { key: 'archive', label: 'Archive', help: 'Hide from default lists but keep the data.' },
  ]

  const tabs: { key: TabKey; label: string; badge?: string }[] = [
    { key: 'identity', label: 'Identity' },
    { key: 'changes', label: 'Changes', badge: changelog && changelog.total_changes > 0 ? String(changelog.total_changes) : undefined },
    { key: 'timeline', label: 'Timeline' },
    { key: 'map', label: 'Map' },
    { key: 'intersect', label: 'Intersect' },
    { key: 'behavior', label: 'Behavior' },
    { key: 'connections', label: 'Connections' },
    { key: 'intelligence', label: 'Intelligence' },
    { key: 'decisions', label: 'Decisions', badge: decisionsTotal > 0 ? String(decisionsTotal) : undefined },
    { key: 'settings', label: 'Settings' },
  ]

  return (
    <div>
      <Link to="/entities" className="mb-3 inline-flex items-center gap-1 text-sm text-text-muted hover:text-text-primary">
        <ArrowLeft className="h-3.5 w-3.5" /> Back to people
      </Link>

      <PageHeader
        title={entity.canonical_name || '(unnamed)'}
        description="Everything we know about this person — platform accounts, activity, evidence, alerts."
        actions={
          <div className="flex items-center gap-2">
            <a
              href={api.exportEntity(entity.id)}
              className="text-xs text-text-muted hover:text-text-primary"
            >
              Export JSON
            </a>
            <select
              value={pinCase}
              onChange={(e) => setPinCase(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1 text-xs text-text-primary"
            >
              <option value="">Pin to case…</option>
              {cases.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <Button
              size="sm"
              variant="ghost"
              disabled={!pinCase}
              onClick={async () => {
                if (!pinCase || !id) return
                await api.addCaseItem(pinCase, { item_type: 'entity', ref_id: id })
                setActionMsg('Pinned to case')
              }}
            >
              Pin
            </Button>
          </div>
        }
      />

      <Card className="mb-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <FaceAvatar url={entity.face_crop_url} name={entity.canonical_name} size={56} />
            <div>
              <div className="flex items-center gap-2 text-sm">
                <span className="inline-flex items-center gap-1 rounded-full bg-hover px-2 py-0.5 text-xs font-medium">
                  {LABELS.tier[entity.tier] ?? entity.tier}
                  <InfoTip text="Confirmed people have strong, multiple pieces of evidence. Unconfirmed ones are kept but flagged as lower-certainty." />
                </span>
                <span className="text-text-muted">
                  {entity.platform_links.length} platform{entity.platform_links.length === 1 ? '' : 's'}
                </span>
                <InfoTip text="Distinct social/messaging accounts we've linked to this person." />
                {entity.last_seen_at && (
                  <span className="text-text-muted">· last seen {new Date(entity.last_seen_at).toLocaleDateString()}</span>
                )}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1">
                <span className="mr-1 text-xs text-text-muted">Watch:</span>
                {watchOptions.map((opt) => (
                  <Button
                    key={opt.key}
                    size="sm"
                    variant={entity.watch_status === opt.key ? 'primary' : 'ghost'}
                    onClick={async () => {
                      const next = entity.watch_status === opt.key ? null : opt.key
                      await api.setWatch(entity.id, next)
                      setEntity({ ...entity, watch_status: next })
                    }}
                    title={opt.help}
                  >
                    {opt.label}
                  </Button>
                ))}
              </div>
            </div>
          </div>
          <div className="text-right">
            <div className="flex items-center justify-end gap-2">
              <ConfidencePill score={entity.confidence_score} />
            </div>
            <div className="mt-1 text-xs text-text-muted">
              {entity.signal_count} piece{entity.signal_count === 1 ? '' : 's'} of evidence
            </div>
          </div>
        </div>
      </Card>

      {actionMsg && (
        <Card className="mb-4 bg-hover">
          <div className="text-sm">{actionMsg}</div>
        </Card>
      )}

      <div className="mb-4 flex flex-wrap gap-1">
        {tabs.map((t) => (
          <Button
            key={t.key}
            size="sm"
            variant={tab === t.key ? 'primary' : 'ghost'}
            onClick={() => setTab(t.key)}
          >
            {t.label}{t.badge ? ` (${t.badge})` : ''}
          </Button>
        ))}
      </div>

      {/* ── Identity ─────────────────────────────────────────────────── */}
      {tab === 'identity' && (
        <div className="space-y-4">
          <IdentitySummary entity={entity} />

          <Card
            title="Platform links"
            actions={
              <InfoTip text="Each row is one social/messaging account linked to this person. Tick rows and click Split to peel them off into a new person." />
            }
          >
            <DataTable
              data={entity.platform_links}
              columns={platformLinkCols}
              pageSize={100}
              emptyMessage="No platform links yet."
            />
            {selectedLinks.size > 0 && (
              <div className="mt-3">
                <Button variant="danger" size="sm" icon={<Trash2 className="h-3 w-3" />} onClick={handleSplit}>
                  Split {selectedLinks.size} selected into new person
                </Button>
              </div>
            )}
            <div className="mt-3 flex items-center gap-2">
              <input
                type="text"
                placeholder="Paste entity ID to merge with…"
                value={mergeTarget}
                onChange={(e) => setMergeTarget(e.target.value)}
                className="max-w-[340px] flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-sm text-text-primary"
              />
              <Button size="sm" onClick={handleMerge} disabled={!mergeTarget.trim()}>Merge</Button>
              <InfoTip text="Merging combines all accounts, evidence, and history into a single person. Use this when two entities are clearly the same human." />
            </div>
          </Card>

          <Card
            title="Identity evidence"
            actions={<InfoTip text="Individual clues linking this person to other accounts — same phone, similar name, same face in photos, etc. More independent clues = higher confidence." />}
          >
            {entity.identity_signals.length === 0 ? (
              <div className="text-sm text-text-muted">No evidence recorded.</div>
            ) : (
              <DataTable
                data={entity.identity_signals}
                columns={signalCols}
                pageSize={25}
              />
            )}
          </Card>
        </div>
      )}

      {/* ── Connections (merged: relationships + interactions + social circle) ── */}
      {tab === 'connections' && (
        <div className="space-y-3">
          {lanes && lanes.total > 0 && (
            <Card>
              <div className="mb-2 flex items-center justify-between">
                <div className="text-sm font-semibold">Time window</div>
                <div className="text-xs text-text-muted">Filters directed interactions</div>
              </div>
              <TimelineLanes data={lanes} selectedRange={brushRange} onRangeChange={setBrushRange} />
            </Card>
          )}
          <ConnectionsPanel
            entityId={entity.id}
            centerName={entity.canonical_name}
            centerFace={entity.face_crop_url ?? null}
            relationships={relationships}
            interactions={interactions}
            network={network}
            associates={associates?.associates ?? []}
            socialCircle={socialCircle ?? []}
            onRelationshipDecision={handleRelationshipDecision}
          />
        </div>
      )}

      {/* ── Changes ──────────────────────────────────────────────────── */}
      {tab === 'changes' && (
        !changelog ? (
          <LoadingSpinner label="Loading changelog…" />
        ) : (
          <div className="space-y-3">
            <div className="mb-2 flex items-center justify-between">
              <div className="text-sm text-text-muted">
                Since {changelog.since ? changelog.since.slice(0, 10) : '—'} · {changelog.total_changes} changes
              </div>
              <Button size="sm" onClick={async () => { if (id) { await api.markReviewed(id); loadChangelog() } }}>
                Mark reviewed
              </Button>
            </div>
            {changelog.total_changes === 0 ? (
              <EmptyState title="Nothing new" description="Nothing has changed since your last visit." />
            ) : (
              <>
                {changelog.deletions.length > 0 && (
                  <Card className="border-error/60">
                    <div className="mb-1 text-sm font-semibold text-error">
                      🗑 Deleted messages ({changelog.deletions.length})
                    </div>
                    {changelog.deletions.map((d, i) => (
                      <div key={i} className="mb-1 text-sm">
                        <span className="rounded-full bg-hover px-2 py-0.5 text-xs text-text-secondary">{d.platform}</span>{' '}
                        <span className="text-text-muted">{d.deleted_at?.slice(0, 10)}</span>{' '}
                        {d.text
                          ? (d.text.length > 120 ? d.text.slice(0, 120) + '…' : d.text)
                          : <span className="text-text-muted">(no text / media)</span>}
                      </div>
                    ))}
                  </Card>
                )}
                {changelog.additions.platform_links.length > 0 && (
                  <Card>
                    <div className="mb-1 text-sm font-semibold">🆕 New accounts linked ({changelog.additions.platform_links.length})</div>
                    <div className="flex flex-wrap gap-1">
                      {changelog.additions.platform_links.map((l, i) => (
                        <PlatformBadge key={i} source={l.source} label={`${l.source}:${l.username}`} />
                      ))}
                    </div>
                  </Card>
                )}
                {changelog.additions.alerts.length > 0 && (
                  <Card>
                    <div className="mb-1 text-sm font-semibold">⚠ New alerts ({changelog.additions.alerts.length})</div>
                    {changelog.additions.alerts.map((a, i) => (
                      <div key={i} className="mb-0.5 text-sm">
                        <span className="rounded-full bg-info/15 px-2 py-0.5 text-xs font-medium text-info">
                          {LABELS.alertType[a.alert_type] ?? a.alert_type}
                        </span>{' '}
                        {a.title || a.detail}
                      </div>
                    ))}
                  </Card>
                )}
                {changelog.additions.timeline_events > 0 && (
                  <Card>
                    <div className="text-sm">
                      <b>{changelog.additions.timeline_events.toLocaleString()}</b>{' '}
                      <span className="text-text-muted">new timeline events since last visit</span>
                    </div>
                  </Card>
                )}
              </>
            )}
          </div>
        )
      )}

      {/* ── Timeline ─────────────────────────────────────────────────── */}
      {tab === 'timeline' && (
        <div className="space-y-3">
          {lanes && lanes.total > 0 && (
            <Card>
              <div className="mb-2 flex items-center justify-between">
                <div className="text-sm font-semibold">Activity across platforms</div>
                <div className="text-xs text-text-muted">{lanes.total.toLocaleString()} events</div>
              </div>
              <TimelineLanes
                data={lanes}
                selectedRange={brushRange}
                onRangeChange={setBrushRange}
                highlightedTimes={selectedGeoEvent?.occurred_at ? [new Date(selectedGeoEvent.occurred_at).getTime() / 1000] : undefined}
              />
            </Card>
          )}
          {events.length === 0 ? (
            <EmptyState
              icon={<Bell className="h-10 w-10" />}
              title="No timeline events"
              description="Posts, activities, and messages appear here as the collector picks them up."
            />
          ) : (
            <>
              {events.map(ev => (
                <Card key={ev.id}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <PlatformBadge source={ev.source} />
                      <span className="rounded-full bg-hover px-2 py-0.5 text-xs text-text-secondary">{ev.event_type}</span>
                    </div>
                    <span className="text-xs text-text-muted">{formatDate(ev.occurred_at)}</span>
                  </div>
                  {ev.title && <div className="mt-2 text-sm">{ev.title}</div>}
                  {engagementMetrics(ev.metadata).length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {engagementMetrics(ev.metadata).map((metric) => (
                        <span key={metric.label} className="rounded-full bg-info/15 px-2 py-0.5 text-xs font-medium text-info">
                          {metric.value} {metric.label}
                        </span>
                      ))}
                    </div>
                  )}
                </Card>
              ))}
              <div className="flex items-center justify-between text-xs text-text-muted">
                <span className="tabular-nums">{eventsTotal} events</span>
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<ChevronLeft className="h-3 w-3" />}
                    disabled={eventsPage <= 1}
                    onClick={() => setEventsPage(p => p - 1)}
                  >
                    Prev
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<ChevronRight className="h-3 w-3" />}
                    disabled={eventsPage * 50 >= eventsTotal}
                    onClick={() => setEventsPage(p => p + 1)}
                  >
                    Next
                  </Button>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* ── Map ──────────────────────────────────────────────────────── */}
      {tab === 'map' && (
        geo && (geo.counts.routes > 0 || geo.counts.points > 0) ? (
          <Card>
            <div className="mb-2 flex items-center justify-between">
              <div className="text-sm font-semibold">Geo footprint</div>
              <div className="text-xs text-text-muted">
                {geo.counts.routes} routes · {geo.counts.points} places
              </div>
            </div>
            {lanes && lanes.total > 0 && (
              <div className="mb-3">
                <TimelineLanes
                  data={lanes}
                  selectedRange={brushRange}
                  onRangeChange={setBrushRange}
                  highlightedTimes={selectedGeoEvent?.occurred_at ? [new Date(selectedGeoEvent.occurred_at).getTime() / 1000] : undefined}
                />
              </div>
            )}
            <GeoMap data={geo} onEventSelect={setSelectedGeoEvent} />
            {selectedGeoEvent && (
              <div className="mt-3 rounded-lg border border-border bg-hover px-3 py-2 text-sm">
                <div className="font-medium">Selected map event</div>
                <div className="text-text-muted">
                  {selectedGeoEvent.source}
                  {selectedGeoEvent.label ? ` · ${selectedGeoEvent.label}` : ''}
                  {selectedGeoEvent.occurred_at ? ` · ${formatDate(selectedGeoEvent.occurred_at)}` : ''}
                </div>
              </div>
            )}
          </Card>
        ) : (
          <EmptyState
            icon={<MapPin className="h-10 w-10" />}
            title="No geo signals yet"
            description="Strava routes and Instagram places will appear here as the collector populates them."
          />
        )
      )}

      {/* ── Intersect ────────────────────────────────────────────────── */}
      {tab === 'intersect' && (
        <IntersectPanel entity={entity} />
      )}

      {/* ── Behavior ─────────────────────────────────────────────────── */}
      {tab === 'behavior' && (
        !behavior ? (
          <EmptyState
            title="No behavioral data yet"
            description="Run an analysis first to compute posting rhythm and behavioral fingerprints."
          />
        ) : (
          <div className="space-y-3">
            <Card>
              <StatRow label="Total events analyzed" value={behavior.total_events.toLocaleString()} />
              <StatRow
                label="Average posting interval"
                value={behavior.avg_post_interval_days < 1
                  ? `${Math.round(behavior.avg_post_interval_days * 24)}h`
                  : `${behavior.avg_post_interval_days.toFixed(1)} days`}
              />
              {behavior.last_computed_at && (
                <StatRow label="Last computed" value={formatDate(behavior.last_computed_at)} />
              )}
            </Card>

            <Card>
              <HeatmapGrid hourDist={behavior.posting_hour_dist} dowDist={behavior.posting_dow_dist} />
            </Card>

            <Card title="Activity by platform">
              {behavior.source_breakdown.map(s => {
                const pct = s.count / Math.max(1, behavior.total_events)
                return (
                  <div key={s.source} className="mb-2 flex items-center gap-2">
                    <PlatformBadge source={s.source} />
                    <div className="flex-1">
                      <div className="h-1.5 rounded-full bg-border">
                        <div className="h-full rounded-full bg-info" style={{ width: `${pct * 100}%` }} />
                      </div>
                    </div>
                    <span className="min-w-[60px] text-right font-mono text-xs tabular-nums">
                      {s.count.toLocaleString()}
                    </span>
                  </div>
                )
              })}
            </Card>

            {behavior.strava_patterns && (
              <Card title="Strava patterns">
                <StatRow label="Total activities" value={behavior.strava_patterns.total_activities} />
                {behavior.strava_patterns.avg_distance_km != null && (
                  <StatRow label="Avg distance" value={`${behavior.strava_patterns.avg_distance_km} km`} />
                )}
                {behavior.strava_patterns.avg_duration_min != null && (
                  <StatRow label="Avg duration" value={`${behavior.strava_patterns.avg_duration_min} min`} />
                )}
                {behavior.strava_patterns.preferred_hour != null && (
                  <StatRow label="Preferred hour" value={`${behavior.strava_patterns.preferred_hour}:00`} />
                )}
                {behavior.strava_patterns.preferred_day != null && (
                  <StatRow label="Preferred day" value={DOW_LABELS[behavior.strava_patterns.preferred_day]} />
                )}
                {Object.keys(behavior.strava_patterns.activity_types).length > 0 && (
                  <div className="mt-3">
                    <div className="mb-1 text-xs text-text-muted">Activity types</div>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(behavior.strava_patterns.activity_types)
                        .sort((a, b) => b[1] - a[1])
                        .map(([type, count]) => (
                          <span key={type} className="rounded-full bg-info/15 px-2 py-0.5 text-xs font-medium text-info">
                            {type}: {count}
                          </span>
                        ))}
                    </div>
                  </div>
                )}
                {behavior.strava_patterns.route_count > 0 && (
                  <div className="mt-3">
                    <div className="mb-1 text-xs text-text-muted">
                      Repeated routes ({behavior.strava_patterns.route_count})
                    </div>
                    {Object.entries(behavior.strava_patterns.repeated_routes)
                      .sort((a, b) => b[1] - a[1])
                      .map(([name, count]) => (
                        <div key={name} className="mb-0.5 text-sm">
                          {name} <span className="text-text-muted">({count}x)</span>
                        </div>
                      ))}
                  </div>
                )}
              </Card>
            )}

            {behavior.bio_nlp && (
              <Card title={`Bio analysis (${behavior.bio_nlp.bio_count} bio(s) from ${behavior.bio_nlp.bio_sources.join(', ')})`}>
                {Object.keys(behavior.bio_nlp.categories).length > 0 && (
                  <div className="mb-3">
                    <div className="mb-1 text-xs text-text-muted">Categories</div>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(behavior.bio_nlp.categories)
                        .sort((a, b) => b[1] - a[1])
                        .map(([cat, score]) => (
                          <span key={cat} className="rounded-full bg-success/15 px-2 py-0.5 text-xs font-medium text-success">
                            {cat} ({score})
                          </span>
                        ))}
                    </div>
                  </div>
                )}
                {behavior.bio_nlp.keywords.length > 0 && (
                  <div className="mb-3">
                    <div className="mb-1 text-xs text-text-muted">Keywords</div>
                    <div className="flex flex-wrap gap-1">
                      {behavior.bio_nlp.keywords.slice(0, 15).map(k => (
                        <span key={k.word} className="rounded-full bg-hover px-2 py-0.5 text-xs text-text-secondary">
                          {k.word} ({k.count})
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {behavior.bio_nlp.hashtags.length > 0 && (
                  <div className="mb-3">
                    <div className="mb-1 text-xs text-text-muted">Hashtags</div>
                    <div className="flex flex-wrap gap-1">
                      {behavior.bio_nlp.hashtags.map(h => (
                        <span key={h.tag} className="rounded-full bg-info/15 px-2 py-0.5 text-xs font-medium text-info">
                          #{h.tag} ({h.count})
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {behavior.bio_nlp.top_emojis.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs text-text-muted">Top emojis</div>
                    <div className="flex gap-2">
                      {behavior.bio_nlp.top_emojis.map((e, i) => (
                        <span key={i} className="text-2xl" title={`${e.count}x`}>{e.emoji}</span>
                      ))}
                    </div>
                  </div>
                )}
              </Card>
            )}

            {behavior.graph_analytics && (
              <Card title="Graph position">
                <StatRow
                  label="Connections (degree)"
                  value={behavior.graph_analytics.degree}
                  help="Number of other people directly connected to this person in the graph."
                />
                <StatRow label="Connection strength" value={behavior.graph_analytics.strength} />
                <StatRow
                  label="Betweenness centrality"
                  value={behavior.graph_analytics.betweenness.toFixed(4)}
                  help="How often this person sits between others on shortest paths through the graph — a bridge score."
                />
                <StatRow label="Clustering coefficient" value={behavior.graph_analytics.clustering.toFixed(4)} />
                <StatRow label="Community size" value={behavior.graph_analytics.component_size} />
              </Card>
            )}
          </div>
        )
      )}

      {/* ── Intelligence ─────────────────────────────────────────────── */}
      {tab === 'intelligence' && (
        !intelligence ? (
          <LoadingSpinner label="Loading intelligence report…" />
        ) : (
          <div className="space-y-3">
            {intelligence.location && (
              <Card title="Location">
                {intelligence.location.primary_country && (
                  <StatRow label="Primary country" value={intelligence.location.primary_country} />
                )}
                {intelligence.location.primary_timezone && (
                  <StatRow label="Primary timezone" value={intelligence.location.primary_timezone} />
                )}
                {intelligence.location.region && (
                  <StatRow label="Region" value={intelligence.location.region} />
                )}
                {intelligence.location.source_countries && intelligence.location.source_countries.length > 0 && (
                  <div className="mt-3">
                    <div className="mb-1 text-xs text-text-muted">Source countries</div>
                    <div className="flex flex-wrap gap-1">
                      {intelligence.location.source_countries.map(c => (
                        <span key={c} className="rounded-full bg-hover px-2 py-0.5 text-xs text-text-secondary">{c}</span>
                      ))}
                    </div>
                  </div>
                )}
              </Card>
            )}

            {intelligence.content_fingerprint && (
              <Card title="Content fingerprint">
                <StatRow label="Posts analyzed" value={intelligence.content_fingerprint.post_count ?? '—'} />
                <StatRow label="Vocabulary size" value={intelligence.content_fingerprint.vocab_size ?? '—'} />
                <StatRow label="Vocabulary richness" value={intelligence.content_fingerprint.vocab_richness ?? '—'} />
                {intelligence.content_fingerprint.top_words && intelligence.content_fingerprint.top_words.length > 0 && (
                  <div className="mt-3">
                    <div className="mb-1 text-xs text-text-muted">Top words</div>
                    <div className="flex flex-wrap gap-1">
                      {intelligence.content_fingerprint.top_words.slice(0, 15).map(w => (
                        <span key={w} className="rounded-full bg-hover px-2 py-0.5 text-xs text-text-secondary">{w}</span>
                      ))}
                    </div>
                  </div>
                )}
              </Card>
            )}

            {intelligence.community_id && (
              <Card>
                <div className="flex items-center justify-between">
                  <span className="inline-flex items-center gap-1 text-sm text-text-muted">
                    Community membership
                    <InfoTip text="A community is a tightly-connected cluster of people the graph engine grouped together." />
                  </span>
                  <Link to="/communities" className="text-sm">View communities →</Link>
                </div>
              </Card>
            )}

            <Card
              title="Possible same-person candidates"
              actions={
                <InfoTip text="Other entities the system thinks might be the same real person. Confirm to merge them, or dismiss to teach the scorer they're different." />
              }
            >
              {intelligence.same_person_candidates.length === 0 ? (
                <div className="text-sm text-text-muted">None detected.</div>
              ) : (
                <div className="overflow-auto">
                  <table className="w-full">
                    <thead className="border-b border-border">
                      <tr>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase tracking-wider text-text-muted">Entity</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase tracking-wider text-text-muted">Same-person probability</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase tracking-wider text-text-muted">Cross-platform</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase tracking-wider text-text-muted">Contributing evidence</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase tracking-wider text-text-muted">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {intelligence.same_person_candidates.map(c => (
                        <tr key={c.entity_id} className="hover:bg-white/5">
                          <td className="px-2 py-2">
                            <div className="flex items-center gap-2">
                              {c.contributing_signals.some(s => s.type === 'media_face_match') && intelligence.entity.face_crop_url && (
                                <FaceAvatar url={intelligence.entity.face_crop_url} name={intelligence.entity.canonical_name} size={30} />
                              )}
                              <FaceAvatar url={c.face_crop_url} name={c.canonical_name} size={30} />
                              <Link to={`/entities/${c.entity_id}`}>
                                {c.canonical_name || c.entity_id.slice(0, 8)}
                              </Link>
                            </div>
                          </td>
                          <td className="px-2 py-2">
                            <ConfidencePill score={c.score ?? 0} />
                          </td>
                          <td className="px-2 py-2">
                            {c.cross_platform
                              ? <StatusBadge status="success" label="yes" />
                              : <StatusBadge status="idle" label="no" />}
                          </td>
                          <td className="px-2 py-2 text-xs text-text-muted">
                            {c.contributing_signals
                              .map(s => `${LABELS.signalType[s.type] ?? s.type} (${s.confidence})`)
                              .join(', ')}
                          </td>
                          <td className="px-2 py-2">
                            <div className="flex gap-1">
                              <Button size="sm" onClick={() => handleConfirmSame(c.entity_id)}>
                                Same → merge
                              </Button>
                              <Button size="sm" variant="danger" onClick={() => handleDismissMatch(c.entity_id)}>
                                Not same
                              </Button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            {intelligence.timeline_summary && (
              <Card title="Timeline summary">
                <StatRow
                  label="First seen"
                  value={intelligence.timeline_summary.first_seen ? formatDate(intelligence.timeline_summary.first_seen) : '—'}
                />
                <StatRow
                  label="Last seen"
                  value={intelligence.timeline_summary.last_seen ? formatDate(intelligence.timeline_summary.last_seen) : '—'}
                />
                <div className="mt-3">
                  <div className="mb-1 text-xs text-text-muted">Events by source</div>
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(intelligence.timeline_summary.event_count_by_source).map(([source, count]) => (
                      <span key={source} className="rounded-full bg-info/15 px-2 py-0.5 text-xs font-medium text-info">
                        {source}: {count}
                      </span>
                    ))}
                  </div>
                </div>
              </Card>
            )}
          </div>
        )
      )}

      {/* ── Decisions ────────────────────────────────────────────────── */}
      {tab === 'decisions' && (
        decisions == null ? (
          <LoadingSpinner label="Loading decisions…" />
        ) : decisions.length === 0 ? (
          <EmptyState
            icon={<History className="h-10 w-10" />}
            title="No decisions recorded"
            description="Merge, split, reject, note, and confidence actions will appear here."
          />
        ) : (
          <Card
            title="Decision history"
            actions={<StatusBadge status="idle" label={`${decisionsTotal} shown`} />}
          >
            <div className="divide-y divide-border">
              {decisions.map((decision) => {
                const durability = decisionDurability(decision)
                const details = decisionKeyValues(decision.payload)
                return (
                  <div key={decision.id} className="py-3 first:pt-0 last:pb-0">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                      <div>
                        <div className="font-medium text-text-primary">{decision.action_label}</div>
                        <div className="mt-0.5 text-sm text-text-muted">{decision.summary}</div>
                      </div>
                      <div className="shrink-0 text-left sm:text-right">
                        <div className="text-xs text-text-muted">
                          {decision.created_at ? formatDate(decision.created_at) : 'time unknown'}
                        </div>
                        <div className="mt-1 flex sm:justify-end">
                          <StatusBadge status={durability.status} label={durability.label} />
                        </div>
                      </div>
                    </div>

                    <div className="mt-2 flex flex-wrap gap-1">
                      {decision.entity_ids.map((entityId) => (
                        <span key={entityId} className="rounded-full bg-hover px-2 py-0.5 text-xs text-text-secondary">
                          {decision.entity_names[entityId] || entityId.slice(0, 8)}
                        </span>
                      ))}
                    </div>

                    {details.length > 0 && (
                      <div className="mt-2 grid gap-1 text-xs text-text-muted sm:grid-cols-2">
                        {details.map((detail) => (
                          <div key={detail.key} className="min-w-0">
                            <span className="text-text-secondary">{detail.key.replace(/_/g, ' ')}:</span>{' '}
                            <span className="break-words">{detail.value}</span>
                          </div>
                        ))}
                      </div>
                    )}

                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-text-muted">
                      <span>Actor: {decision.actor || 'unknown'}</span>
                      {decision.decision_jsonl_path && (
                        <span className="font-mono">{decision.decision_jsonl_path}</span>
                      )}
                      {decision.decision_jsonl_error && (
                        <span className="text-error">{decision.decision_jsonl_error}</span>
                      )}
                    </div>

                    <details className="mt-2">
                      <summary className="cursor-pointer text-xs text-text-muted hover:text-text-primary">Payload</summary>
                      <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-background p-2 text-xs text-text-secondary">
                        {JSON.stringify(decision.payload, null, 2)}
                      </pre>
                    </details>
                  </div>
                )
              })}
            </div>
          </Card>
        )
      )}

      {/* ── Settings ─────────────────────────────────────────────────── */}
      {tab === 'settings' && (
        <Card title="Alert tuning">
          <div className="mb-3">
            <label className="mb-1 block text-xs text-text-muted">
              Custom silence threshold (days) — leave empty for automatic
            </label>
            <input
              type="text"
              placeholder="e.g. 14"
              value={silenceThreshold}
              onChange={(e) => setSilenceThreshold(e.target.value)}
              className="max-w-[200px] rounded-md border border-border bg-background px-2 py-1.5 text-sm text-text-primary"
            />
          </div>
          <div className="mb-3">
            <label className="mb-1 block text-xs text-text-muted">Notes</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              className="w-full max-w-[500px] resize-y rounded-md border border-border bg-background px-2 py-1.5 text-sm text-text-primary"
            />
          </div>
          <Button variant="primary" onClick={handleSaveSettings}>Save settings</Button>
        </Card>
      )}
    </div>
  )
}
