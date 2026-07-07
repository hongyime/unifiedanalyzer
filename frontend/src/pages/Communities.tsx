import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Users } from 'lucide-react'
import { api, Community } from '../api'
import { PageHeader } from '../components/ui/PageHeader'
import { Card } from '../components/ui/Card'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { ErrorState } from '../components/ui/ErrorState'
import { EmptyState } from '../components/ui/EmptyState'
import { InfoTip } from '../components/ui/InfoTip'
import { LABELS } from '../lib/labels'

/**
 * Communities — clusters of people the graph engine has grouped together
 * (shared WhatsApp rooms, mutual connections, coordinated posting). Each is
 * rendered as a Card with the members as chip-links.
 *
 * The API surface (`api.getCommunities`) is unchanged.
 */
export default function CommunitiesPage() {
  const [communities, setCommunities] = useState<Community[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    api.getCommunities()
      .then(r => setCommunities(r.data))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  return (
    <div>
      <PageHeader
        title="Communities"
        description="Clusters of people who interact — shared group chats, mutual connections, or coordinated activity. Each is a group that tends to move together."
        actions={
          <span className="inline-flex items-center gap-1 text-sm text-text-muted">
            {communities.length} communities
            <InfoTip text="A community is a tightly-connected group of people the graph engine has clustered. Membership is inferred from shared chats, mutual links, or synchronised behaviour." />
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
