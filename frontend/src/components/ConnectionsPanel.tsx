import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, List, Network, Users2, XCircle } from 'lucide-react'
import { FaceAvatar } from './FaceAvatar'
import { NetworkGraph } from './NetworkGraph'
import { Card } from './ui/Card'
import { Button } from './ui/Button'
import { EmptyState } from './ui/EmptyState'
import { InfoTip } from './ui/InfoTip'
import type {
  Relationship,
  InteractionPeer,
  SocialCircleEntry,
} from '../api'

/**
 * ConnectionsPanel — the single consolidated "Connections" view.
 *
 * Merges what used to be three overlapping tabs on the entity page:
 *   1. "Relationships"   → typed-edge list (relationship_type/weight/why/sources)
 *                          + the ego "Connection graph" (NetworkGraph on /network)
 *                          + "Seen with" co-tagged associates (/associates)
 *   2. "Interactions"    → directed reciprocal out/in by_type (/interactions),
 *                          windowable via the caller-provided time brush
 *   3. "Social Circle"   → face associations found in this person's photos
 *                          (/social-circle)
 *
 * All four API shapes are fused into one ranked "closest associates" list keyed
 * by the neighbour entity (default sort = descending combined strength — this is
 * the old social-circle ordering). A list⇄graph toggle switches presentation; a
 * filter-chips row narrows by connection kind. Every capability of the old tabs
 * is preserved:
 *   - typed relationships keep their type badge, weight, and "why"/sources
 *   - directed interactions keep the reciprocal out/in counts and by_type
 *   - the visual graph keeps click-to-pivot drill behaviour (NetworkGraph)
 *   - ranked closest-associates ordering is the default list sort
 *
 * IMPORTANT for future editors: `Relationship.sources` arrives from the API as a
 * JSON *string* (not an object), so we parse defensively for the group detail.
 * `Relationship.why` is reliably populated for every relationship_type and is the
 * primary explainability field.
 */

// ── Filter model ───────────────────────────────────────────────────────────
// Each chip is a coarse "kind" of connection. Interaction and relationship
// sub-types are rolled up so the chip row stays readable, but the underlying
// per-type counts are never dropped from the row detail.
type FilterKind =
  | 'all'
  | 'interaction' // directed replies/reactions/follows/mentions/tags/dms
  | 'relationship' // typed graph edges (group co-membership, temporal, etc.)
  | 'associate' // co-tagged in photos ("seen with")
  | 'face' // face associations from this person's own photos

type Associate = {
  username: string
  full_name: string | null
  shared: number
  entity_id: string | null
  entity_name: string | null
  face: string | null
}

type NetworkData = {
  center: { id: string; name: string | null; face: string | null }
  nodes: {
    id: string
    name: string | null
    weight: number
    types: string[]
    face: string | null
    why?: string | null
  }[]
}

/**
 * One row in the unified ranked list. Fuses every source that resolves to a
 * concrete neighbour entity. `strength` is the ranking key (higher = closer).
 */
type Connection = {
  entityId: string | null
  name: string | null
  face: string | null
  strength: number
  kinds: Set<FilterKind>
  // relationship (typed edge) facet
  relTypes: { type: string; weight: number; why: string | null; groups: string[] }[]
  // directed interaction facet (reciprocal preserved)
  interaction?: InteractionPeer
  // co-tagged associate facet
  associateShared?: number
  // face-association facet (may have no resolved entity)
  faceOnly?: SocialCircleEntry
}

type RelationshipDecisionHandler = (
  otherEntityId: string,
  relationshipType: string,
  isReal: boolean,
) => Promise<void> | void

const RELATIONSHIP_LABELS: Record<string, string> = {
  temporal_copost: 'activity overlap',
  temporal_hour_similarity: 'similar active hours',
  co_presence: 'tight co-presence',
  co_absence: 'quiet together',
  telegram_group_co_member: 'same Telegram group',
  whatsapp_group_co_member: 'same WhatsApp group',
  social_graph_overlap: 'shared network',
  same_person_probability: 'possible same person',
}

function relationshipLabel(type: string) {
  return RELATIONSHIP_LABELS[type] ?? type.replace(/_/g, ' ')
}

function parseGroups(sources: unknown): string[] {
  // sources is a JSON string in the live API; be defensive either way.
  try {
    const obj = typeof sources === 'string' ? JSON.parse(sources) : sources
    if (obj && typeof obj === 'object' && Array.isArray((obj as { groups?: unknown }).groups)) {
      return (obj as { groups: string[] }).groups
    }
  } catch {
    /* ignore malformed */
  }
  return []
}

function keyOf(id: string | null, fallback: string): string {
  return id ?? `anon:${fallback}`
}

function relationshipDecisionKey(otherEntityId: string, relationshipType: string, isReal: boolean): string {
  return `${otherEntityId}:${relationshipType}:${isReal ? 'real' : 'not-real'}`
}

export function ConnectionsPanel({
  entityId,
  centerName,
  centerFace,
  relationships,
  interactions,
  network,
  associates,
  socialCircle,
  onRelationshipDecision,
}: {
  entityId: string
  centerName: string | null
  centerFace: string | null
  relationships: Relationship[]
  interactions: InteractionPeer[]
  network: NetworkData | null
  associates: Associate[]
  socialCircle: SocialCircleEntry[]
  onRelationshipDecision?: RelationshipDecisionHandler
}) {
  const [view, setView] = useState<'list' | 'graph'>('list')
  const [filter, setFilter] = useState<FilterKind>('all')
  const [pendingDecision, setPendingDecision] = useState<string | null>(null)
  const [decisionMessage, setDecisionMessage] = useState('')

  // ── Fuse all sources into one keyed map of connections ────────────────────
  const connections = useMemo<Connection[]>(() => {
    const map = new Map<string, Connection>()
    const ensure = (id: string | null, fallback: string, name: string | null, face: string | null) => {
      const k = keyOf(id, fallback)
      let c = map.get(k)
      if (!c) {
        c = {
          entityId: id,
          name,
          face,
          strength: 0,
          kinds: new Set(),
          relTypes: [],
        }
        map.set(k, c)
      }
      if (!c.name && name) c.name = name
      if (!c.face && face) c.face = face
      return c
    }

    // Typed relationships → strength contribution = weight (log-damped so a
    // single huge group-membership edge doesn't drown out everything else).
    for (const r of relationships) {
      const c = ensure(r.other_entity_id, r.other_entity_id, r.other_name, null)
      c.kinds.add('relationship')
      c.relTypes.push({
        type: r.relationship_type,
        weight: r.weight,
        why: r.why ?? null,
        groups: parseGroups(r.sources),
      })
      c.strength += Math.log2(1 + Math.max(0, r.weight))
    }

    // Directed interactions → strength contribution = total volume.
    for (const it of interactions) {
      const c = ensure(it.entity_id, it.entity_id, it.name, it.face)
      c.kinds.add('interaction')
      c.interaction = it
      c.strength += Math.log2(1 + Math.max(0, it.total)) * 1.5
    }

    // Co-tagged associates ("seen with").
    for (const a of associates) {
      const c = ensure(a.entity_id, a.username, a.entity_name || a.full_name || a.username, a.face)
      c.kinds.add('associate')
      c.associateShared = (c.associateShared ?? 0) + a.shared
      c.strength += Math.log2(1 + Math.max(0, a.shared)) * 1.2
    }

    // Face associations from this person's own photos. Only fold matched faces
    // into the neighbour rows; unmatched faces are surfaced separately below so
    // no data is lost.
    for (const f of socialCircle) {
      if (!f.matched_entity_id) continue
      const c = ensure(f.matched_entity_id, f.matched_entity_id, f.matched_entity_name, f.face_crop_url)
      c.kinds.add('face')
      c.faceOnly = f
      c.strength += (f.matched_confidence ?? 0.5) * 2
    }

    return Array.from(map.values()).sort((a, b) => b.strength - a.strength)
  }, [relationships, interactions, associates, socialCircle])

  // Unmatched face associations (no resolved entity) — kept from the old
  // Social Circle tab so face-only leads are not lost.
  const unmatchedFaces = useMemo(
    () => socialCircle.filter((f) => !f.matched_entity_id),
    [socialCircle],
  )

  // ── Filter-chip counts ────────────────────────────────────────────────────
  const counts = useMemo(() => {
    const c: Record<FilterKind, number> = { all: 0, interaction: 0, relationship: 0, associate: 0, face: 0 }
    for (const conn of connections) {
      c.all++
      for (const k of conn.kinds) c[k]++
    }
    return c
  }, [connections])

  const filtered = useMemo(
    () => (filter === 'all' ? connections : connections.filter((c) => c.kinds.has(filter))),
    [connections, filter],
  )

  // ── Graph payload: build from the same filtered set so the toggle is honest.
  // Prefer the interaction facet (directed arrows) when present, else fall back
  // to the typed-relationship weight (undirected edge). Center comes from the
  // /network endpoint when available, else the entity props.
  const graphData = useMemo(() => {
    const center = network?.center ?? { id: entityId, name: centerName, face: centerFace }
    const nodes = filtered
      .filter((c) => c.entityId)
      .slice(0, 30)
      .map((c) => {
        const dominantRel = [...c.relTypes].sort((a, b) => b.weight - a.weight)[0]
        return {
          id: c.entityId as string,
          name: c.name,
          face: c.face,
          weight: Math.max(
            dominantRel?.weight ?? 0,
            c.interaction?.total ?? 0,
            c.associateShared ?? 0,
          ),
          types: dominantRel ? [dominantRel.type] : Array.from(c.kinds),
          why: dominantRel?.why ?? null,
          ...(c.interaction ? { out: c.interaction.out, in: c.interaction.in } : {}),
        }
      })
    return { center, nodes }
  }, [filtered, network, entityId, centerName, centerFace])

  const chips: { key: FilterKind; label: string; help: string }[] = [
    { key: 'all', label: 'All', help: 'Every connection we can resolve to a person.' },
    { key: 'relationship', label: 'Relationships', help: 'Typed graph edges — shared groups, temporal patterns, self-declared links.' },
    { key: 'interaction', label: 'Interactions', help: 'Directed replies, reactions, follows, mentions, tags, DMs (respects the time window).' },
    { key: 'associate', label: 'Seen with', help: 'People co-tagged with this person in photos.' },
    { key: 'face', label: 'Face matches', help: 'Faces in this person’s photos that matched a known person.' },
  ]

  const hasAnything =
    connections.length > 0 || unmatchedFaces.length > 0 || (network?.nodes.length ?? 0) > 0

  const runRelationshipDecision = async (otherEntityId: string, relationshipType: string, isReal: boolean) => {
    if (!onRelationshipDecision) return
    const key = relationshipDecisionKey(otherEntityId, relationshipType, isReal)
    setPendingDecision(key)
    setDecisionMessage('')
    try {
      await onRelationshipDecision(otherEntityId, relationshipType, isReal)
      setDecisionMessage(isReal ? 'Relationship confirmed.' : 'Relationship rejected.')
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setDecisionMessage(`Decision failed: ${message}`)
    } finally {
      setPendingDecision(null)
    }
  }

  if (!hasAnything) {
    return (
      <EmptyState
        icon={<Users2 className="h-10 w-10" />}
        title="No connections yet"
        description="Relationships, interactions, and co-tagged associates appear here as the graph engine and face matcher find them."
      />
    )
  }

  return (
    <div className="space-y-3">
      {decisionMessage && (
        <div className="rounded-md border border-border bg-hover px-3 py-2 text-sm text-text-secondary">
          {decisionMessage}
        </div>
      )}

      {/* Toolbar: list⇄graph toggle + filter chips */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant={view === 'list' ? 'primary' : 'ghost'}
            icon={<List className="h-3.5 w-3.5" />}
            onClick={() => setView('list')}
          >
            List
          </Button>
          <Button
            size="sm"
            variant={view === 'graph' ? 'primary' : 'ghost'}
            icon={<Network className="h-3.5 w-3.5" />}
            onClick={() => setView('graph')}
          >
            Graph
          </Button>
          <InfoTip text="Switch between the ranked list of closest associates and the visual connection graph. The filter chips apply to both." />
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {chips.map((chip) => {
            const n = counts[chip.key]
            const disabled = chip.key !== 'all' && n === 0
            return (
              <button
                key={chip.key}
                type="button"
                disabled={disabled}
                onClick={() => setFilter(chip.key)}
                title={chip.help}
                className={[
                  'rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors',
                  filter === chip.key
                    ? 'bg-white text-black'
                    : 'border border-border bg-background text-text-secondary hover:bg-hover',
                  disabled ? 'cursor-not-allowed opacity-40' : '',
                ].join(' ')}
              >
                {chip.label}
                {chip.key !== 'all' && ` (${n})`}
              </button>
            )
          })}
        </div>
      </div>

      {/* Graph view */}
      {view === 'graph' ? (
        graphData.nodes.length > 0 ? (
          <Card>
            <div className="mb-1 text-sm font-semibold">
              Connection graph{' '}
              <span className="text-text-muted">(click a neighbour to pivot · arrows = interaction direction)</span>
            </div>
            <NetworkGraph data={graphData} />
          </Card>
        ) : (
          <EmptyState
            icon={<Network className="h-10 w-10" />}
            title="Nothing to graph for this filter"
            description="Try the “All” chip, or switch back to the list."
          />
        )
      ) : (
        // List view — ranked closest associates (default sort), one card each.
        <Card>
          {filtered.length === 0 ? (
            <div className="text-sm text-text-muted">No connections match this filter.</div>
          ) : (
            <div className="space-y-2">
              {filtered.map((c) => (
                <ConnectionRow
                  key={keyOf(c.entityId, c.name ?? '')}
                  c={c}
                  onRelationshipDecision={onRelationshipDecision ? runRelationshipDecision : undefined}
                  pendingDecision={pendingDecision}
                />
              ))}
            </div>
          )}
        </Card>
      )}

      {/* Unmatched faces — face-only leads with no resolved person. */}
      {unmatchedFaces.length > 0 && (filter === 'all' || filter === 'face') && (
        <Card
          title={`Unmatched faces (${unmatchedFaces.length})`}
          actions={<InfoTip text="Faces found in this person’s photos that we could not yet match to a known person. Potential new leads." />}
        >
          <div className="grid grid-cols-3 gap-3 md:grid-cols-5 lg:grid-cols-8">
            {unmatchedFaces.map((f, i) => (
              <div key={i} className="flex flex-col items-center gap-1">
                <FaceAvatar url={f.face_crop_url || null} name="Unmatched" size={48} />
                <div className="truncate text-[0.65rem] text-text-muted" title={f.media_item_id}>
                  {f.media_item_id.slice(0, 8)}…
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

/** A single ranked connection, showing every facet that applies to it. */
function ConnectionRow({
  c,
  onRelationshipDecision,
  pendingDecision,
}: {
  c: Connection
  onRelationshipDecision?: RelationshipDecisionHandler
  pendingDecision: string | null
}) {
  const label = c.name || (c.entityId ? c.entityId.slice(0, 8) : 'Unknown')
  const relTop = [...c.relTypes].sort((a, b) => b.weight - a.weight)
  const why = relTop.find((r) => r.why)?.why
  const groups = relTop.find((r) => r.groups.length > 0)?.groups ?? []

  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-border px-3 py-2">
      <div className="flex min-w-0 items-start gap-2">
        <FaceAvatar url={c.face} name={label} size={32} />
        <div className="min-w-0">
          {c.entityId ? (
            <Link to={`/entities/${c.entityId}`} className="text-sm font-medium hover:underline">
              {label}
            </Link>
          ) : (
            <span className="text-sm font-medium">{label}</span>
          )}
          {/* Kind + type badges */}
          <div className="mt-0.5 flex flex-wrap items-center gap-1">
            {relTop.map((r, i) => (
              <span
                key={`r${i}`}
                className="rounded-full bg-info/15 px-2 py-0.5 text-[0.7rem] font-medium text-info"
                title={`weight ${r.weight}`}
              >
                {relationshipLabel(r.type)}
              </span>
            ))}
            {c.interaction && (
              <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[0.7rem] font-medium text-accent">
                interaction
              </span>
            )}
            {c.associateShared != null && (
              <span className="rounded-full bg-hover px-2 py-0.5 text-[0.7rem] font-medium text-text-secondary">
                seen with ×{c.associateShared}
              </span>
            )}
            {c.faceOnly && (
              <span className="rounded-full bg-success/15 px-2 py-0.5 text-[0.7rem] font-medium text-success">
                face match
                {c.faceOnly.matched_confidence != null
                  ? ` ${Math.round(c.faceOnly.matched_confidence * 100)}%`
                  : ''}
              </span>
            )}
          </div>
          {/* Explainability — the "why" plus shared-group detail. */}
          {why && <div className="mt-1 max-w-[520px] text-xs text-text-muted">{why}</div>}
          {groups.length > 0 && (
            <div className="mt-0.5 max-w-[520px] truncate text-[0.7rem] text-text-muted" title={groups.join(', ')}>
              Shared: {groups.slice(0, 6).join(', ')}
              {groups.length > 6 ? ` +${groups.length - 6} more` : ''}
            </div>
          )}
          {c.entityId && onRelationshipDecision && relTop.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1">
              {relTop.map((r) => {
                const confirmKey = relationshipDecisionKey(c.entityId as string, r.type, true)
                const rejectKey = relationshipDecisionKey(c.entityId as string, r.type, false)
                const busy = pendingDecision === confirmKey || pendingDecision === rejectKey
                return (
                  <div
                    key={r.type}
                    className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-1 py-0.5"
                  >
                    <span className="max-w-[150px] truncate px-1 text-[0.65rem] text-text-muted" title={relationshipLabel(r.type)}>
                      {relationshipLabel(r.type)}
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 w-6 px-0"
                      icon={<CheckCircle2 className="h-3.5 w-3.5" />}
                      aria-label={`Confirm ${relationshipLabel(r.type)} relationship`}
                      title={`Confirm ${relationshipLabel(r.type)} relationship`}
                      loading={pendingDecision === confirmKey}
                      disabled={pendingDecision != null && !busy}
                      onClick={() => onRelationshipDecision(c.entityId as string, r.type, true)}
                    />
                    <Button
                      size="sm"
                      variant="danger"
                      className="h-6 w-6 px-0"
                      icon={<XCircle className="h-3.5 w-3.5" />}
                      aria-label={`Reject ${relationshipLabel(r.type)} relationship`}
                      title={`Reject ${relationshipLabel(r.type)} relationship`}
                      loading={pendingDecision === rejectKey}
                      disabled={pendingDecision != null && !busy}
                      onClick={() => onRelationshipDecision(c.entityId as string, r.type, false)}
                    />
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Directed reciprocal counts — preserved from the Interactions tab. */}
      {c.interaction && (
        <div className="shrink-0 text-right text-xs text-text-muted">
          <div className="font-medium text-text-secondary">
            out {c.interaction.out.total} · in {c.interaction.in.total}
          </div>
          <div>
            out: {Object.entries(c.interaction.out.by_type).map(([k, v]) => `${k} ${v}`).join(', ') || 'none'}
          </div>
          <div>
            in: {Object.entries(c.interaction.in.by_type).map(([k, v]) => `${k} ${v}`).join(', ') || 'none'}
          </div>
        </div>
      )}
    </div>
  )
}
