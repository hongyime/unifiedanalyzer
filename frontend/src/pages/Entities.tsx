import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, Entity } from '../api'

function PlatformBadge({ source }: { source: string }) {
  return <span className={`platform-icon p-${source}`}>{source}</span>
}

function ConfidenceBar({ score }: { score: number }) {
  return (
    <div className="signal-bar">
      <div className="signal-bar-fill" style={{ width: `${Math.round(score * 100)}%` }} />
    </div>
  )
}

export default function EntitiesPage() {
  const [entities, setEntities] = useState<Entity[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.getEntities(page, search)
      .then(r => { setEntities(r.data); setTotal(r.total) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [page, search])

  return (
    <div>
      <div className="flex-between mb-2">
        <h2>Entities</h2>
        <input
          type="search"
          placeholder="Search by name..."
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
        />
      </div>

      {loading ? (
        <div className="empty-state">Loading...</div>
      ) : entities.length === 0 ? (
        <div className="empty-state">
          No entities found. Run an analysis first.
        </div>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Platforms</th>
                <th>Confidence</th>
                <th>Signals</th>
              </tr>
            </thead>
            <tbody>
              {entities.map(e => (
                <tr key={e.id}>
                  <td>
                    <Link to={`/entities/${e.id}`} style={{ fontWeight: 500 }}>
                      {e.canonical_name || '(unnamed)'}
                    </Link>
                    <div className="text-sm text-muted">{e.tier}</div>
                  </td>
                  <td>
                    {e.platforms.map(p => <PlatformBadge key={p} source={p} />)}
                  </td>
                  <td>
                    <div className="flex gap-1" style={{ alignItems: 'center' }}>
                      <ConfidenceBar score={e.confidence_score} />
                      <span className="text-sm text-muted">{Math.round(e.confidence_score * 100)}%</span>
                    </div>
                  </td>
                  <td>{e.signal_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex-between" style={{ marginTop: '1rem' }}>
            <span className="text-sm text-muted">{total} entities</span>
            <div className="flex gap-1">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Prev</button>
              <button disabled={page * 50 >= total} onClick={() => setPage(p => p + 1)}>Next</button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
