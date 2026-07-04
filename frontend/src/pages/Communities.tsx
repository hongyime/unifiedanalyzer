import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, Community } from '../api'
import { PageHeader } from '../components/ui/PageHeader'

export default function CommunitiesPage() {
  const [communities, setCommunities] = useState<Community[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getCommunities()
      .then(r => setCommunities(r.data))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <PageHeader
        title="Communities"
        description="Clusters of people who interact — shared group chats, mutual connections, or coordinated activity. Each is a group that tends to move together."
        actions={<span className="text-sm text-muted">{communities.length} communities</span>}
      />

      {loading ? (
        <div className="empty-state">Loading...</div>
      ) : error ? (
        <div className="empty-state">Failed to load: {error}</div>
      ) : communities.length === 0 ? (
        <div className="empty-state">
          No communities detected yet — run graph analytics first.
        </div>
      ) : (
        communities.map((c, i) => (
          <div key={c.community_id} className="card">
            <div className="flex-between mb-1">
              <span style={{ fontWeight: 600 }}>Community {i + 1}</span>
              <span className="badge badge-blue">{c.member_count} members</span>
            </div>
            <div className="flex gap-1" style={{ flexWrap: 'wrap' }}>
              {c.members.map(m => (
                <Link key={m.entity_id} to={`/entities/${m.entity_id}`} className="badge badge-gray">
                  {m.canonical_name || m.entity_id.slice(0, 8)}
                </Link>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  )
}
