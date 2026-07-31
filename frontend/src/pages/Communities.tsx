import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Users } from 'lucide-react'
import { api, Community } from '../api'
import { PageHeader } from '../components/ui/PageHeader'
import { Card } from '../components/ui/Card'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { ErrorState } from '../components/ui/ErrorState'
import { EmptyState } from '../components/ui/EmptyState'
import { InfoTip } from '../components/ui/InfoTip'
import { LABELS } from '../lib/labels'

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

/**
 * Communities — clusters of people the graph engine has grouped together
 * (shared WhatsApp rooms, mutual connections, repeated activity overlap). Each is
 * rendered as a Card with the members as chip-links.
 *
 * The API surface (`api.getCommunities`) is unchanged.
 */
export default function CommunitiesPage() {
  const [communities, setCommunities] = useState<Community[]>([])
  const [overview, setOverview] = useState<Awaited<ReturnType<typeof api.getGraphOverview>> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    Promise.all([api.getCommunities(), api.getGraphOverview()])
      .then(([communityRes, overviewRes]) => {
        setCommunities(communityRes.data)
        setOverview(overviewRes)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  return (
    <div>
      <PageHeader
        title="Communities"
        description="Clusters of people who interact — shared group chats, mutual connections, or repeated activity overlap. Timing overlap is context, not identity evidence."
        actions={
          <span className="inline-flex items-center gap-1 text-sm text-text-muted">
            {communities.length} communities
            <InfoTip text="A community is a tightly-connected group of people the graph engine has clustered. Membership is inferred from shared chats, mutual links, or repeated timing patterns; timing alone does not mean two accounts are the same person." />
          </span>
        }
      />

      {loading ? (
        <LoadingSpinner label="Loading communities…" />
      ) : error ? (
        <ErrorState message={`Failed to load communities: ${error}`} onRetry={load} />
      ) : communities.length === 0 ? (
        <EmptyState
          icon={<Users className="h-10 w-10" />}
          title="No communities detected yet"
          description="Communities appear here once the graph analytics phase has run and found tightly-connected clusters of people. Trigger a run to populate this."
        />
      ) : (
        <div className="space-y-3">
          {overview && (
            <>
              <div className="grid gap-3 md:grid-cols-3">
                <Card>
                  <div className="text-xs text-text-muted">Relationships in graph</div>
                  <div className="mt-1 text-2xl font-semibold">{overview.total_relationships.toLocaleString()}</div>
                </Card>
                <Card>
                  <div className="text-xs text-text-muted">People connected</div>
                  <div className="mt-1 text-2xl font-semibold">{overview.entities_in_graph.toLocaleString()}</div>
                </Card>
                <Card>
                  <div className="text-xs text-text-muted">Top relationship type</div>
                  <div className="mt-1 text-sm font-semibold">
                    {relationshipLabel(Object.entries(overview.relationship_type_counts)[0]?.[0] || '—')}
                  </div>
                  <div className="text-xs text-text-muted">
                    {Object.entries(overview.relationship_type_counts)[0]?.[1]?.toLocaleString() || 0} edges
                  </div>
                </Card>
              </div>

              {overview.top_bridges.length > 0 && (
                <Card>
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-sm font-semibold">Top bridge entities</span>
                    <span className="text-xs text-text-muted">Highest betweenness centrality</span>
                  </div>
                  <div className="space-y-2">
                    {overview.top_bridges.slice(0, 8).map((b, i) => (
                      <div key={b.entity.id} className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
                        <div>
                          <div className="text-xs text-text-muted">#{i + 1}</div>
                          <Link to={`/entities/${b.entity.id}`} className="text-sm font-medium">
                            {b.entity.name || b.entity.id.slice(0, 8)}
                          </Link>
                        </div>
                        <div className="text-right text-xs text-text-muted">
                          <div>betweenness {b.betweenness.toFixed(4)}</div>
                          <div>degree {b.degree} · strength {b.strength}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              )}

              {overview.top_connections.length > 0 && (
                <Card>
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-sm font-semibold">Top explained edges</span>
                    <span className="text-xs text-text-muted">Strongest relationships with reasons</span>
                  </div>
                  <div className="space-y-2">
                    {overview.top_connections.slice(0, 8).map((edge, i) => (
                      <div key={`${edge.entity_a.id}:${edge.entity_b.id}:${edge.type}:${i}`} className="rounded-lg border border-border px-3 py-2">
                        <div className="flex items-center justify-between gap-3">
                          <div className="text-sm font-medium">
                            <Link to={`/entities/${edge.entity_a.id}`}>{edge.entity_a.name || edge.entity_a.id.slice(0, 8)}</Link>
                            {' '}↔{' '}
                            <Link to={`/entities/${edge.entity_b.id}`}>{edge.entity_b.name || edge.entity_b.id.slice(0, 8)}</Link>
                          </div>
                          <div className="text-xs text-text-muted">
                            {relationshipLabel(edge.type)} · {edge.weight}
                          </div>
                        </div>
                        {edge.why && <div className="mt-1 text-xs text-text-muted">{edge.why}</div>}
                      </div>
                    ))}
                  </div>
                </Card>
              )}
            </>
          )}

          {communities.map((c, i) => (
            <Card key={c.community_id}>
              <div className="mb-2 flex items-center justify-between">
                <span className="inline-flex items-center gap-1 text-sm font-semibold">
                  Community {i + 1}
                  <InfoTip
                    text={`A cluster of people the graph engine grouped together. Members can be a mix of ${LABELS.tier.primary.toLowerCase()} and ${LABELS.tier.secondary.toLowerCase()} people. Click any member to open their profile.`}
                  />
                </span>
                <span className="rounded-full bg-info/15 px-2 py-0.5 text-xs font-medium text-info">
                  {c.member_count} members
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {c.members.map(m => (
                  <Link
                    key={m.entity_id}
                    to={`/entities/${m.entity_id}`}
                    className="rounded-full border border-border bg-background px-2 py-0.5 text-xs text-text-secondary hover:bg-hover hover:text-text-primary"
                  >
                    {m.canonical_name || m.entity_id.slice(0, 8)}
                  </Link>
                ))}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
