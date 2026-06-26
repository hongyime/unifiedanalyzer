import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, Entity } from '../api'
import { FaceAvatar } from '../components/FaceAvatar'

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

const PLATFORMS = ['github', 'instagram', 'telegram', 'strava', 'youtube', 'tiktok', 'lemon8', 'whatsapp', 'website']
const SORT_OPTIONS = [
  { value: 'confidence', label: 'Confidence' },
  { value: 'name', label: 'Name' },
  { value: 'signals', label: 'Signals' },
  { value: 'platforms', label: 'Platforms' },
  { value: 'created', label: 'Created' },
]

export default function EntitiesPage() {
  const [entities, setEntities] = useState<Entity[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('confidence')
  const [order, setOrder] = useState('desc')
  const [platform, setPlatform] = useState('')
  const [minPlatforms, setMinPlatforms] = useState(0)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.getEntities(page, search, sort, order, platform, minPlatforms)
      .then(r => { setEntities(r.data); setTotal(r.total) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [page, search, sort, order, platform, minPlatforms])

  const toggleSort = (col: string) => {
    if (sort === col) {
      setOrder(o => o === 'desc' ? 'asc' : 'desc')
    } else {
      setSort(col)
      setOrder('desc')
    }
    setPage(1)
  }

  const sortArrow = (col: string) => sort === col ? (order === 'desc' ? ' v' : ' ^') : ''

  return (
    <div>
      <div className="flex-between mb-2">
        <h2>Entities</h2>
        <input
          type="search"
          placeholder="Search name or username..."
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
        />
      </div>

      <div className="flex gap-1 mb-2" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
        <select
          value={platform}
          onChange={e => { setPlatform(e.target.value); setPage(1) }}
          style={{
            padding: '0.4rem 0.6rem', borderRadius: '6px', border: '1px solid var(--border)',
            background: 'var(--bg)', color: 'var(--text)', fontSize: '0.8rem',
          }}
        >
          <option value="">All platforms</option>
          {PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
        </select>

        <select
          value={minPlatforms}
          onChange={e => { setMinPlatforms(Number(e.target.value)); setPage(1) }}
          style={{
            padding: '0.4rem 0.6rem', borderRadius: '6px', border: '1px solid var(--border)',
            background: 'var(--bg)', color: 'var(--text)', fontSize: '0.8rem',
          }}
        >
          <option value={0}>Any # platforms</option>
          <option value={2}>2+ platforms</option>
          <option value={3}>3+ platforms</option>
          <option value={4}>4+ platforms</option>
        </select>

        <span className="text-sm text-muted" style={{ marginLeft: '0.5rem' }}>
          {total} entities
        </span>
      </div>

      {loading ? (
        <div className="empty-state">Loading...</div>
      ) : entities.length === 0 ? (
        <div className="empty-state">
          No entities found.
        </div>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th onClick={() => toggleSort('name')} style={{ cursor: 'pointer' }}>
                  Name{sortArrow('name')}
                </th>
                <th onClick={() => toggleSort('platforms')} style={{ cursor: 'pointer' }}>
                  Platforms{sortArrow('platforms')}
                </th>
                <th onClick={() => toggleSort('confidence')} style={{ cursor: 'pointer' }}>
                  Confidence{sortArrow('confidence')}
                </th>
                <th onClick={() => toggleSort('signals')} style={{ cursor: 'pointer' }}>
                  Signals{sortArrow('signals')}
                </th>
              </tr>
            </thead>
            <tbody>
              {entities.map(e => (
                <tr key={e.id}>
                  <td>
                    <div className="flex gap-1" style={{ alignItems: 'center' }}>
                      <FaceAvatar url={e.face_crop_url} name={e.canonical_name} size={34} />
                      <div>
                        <Link to={`/entities/${e.id}`} style={{ fontWeight: 500 }}>
                          {e.canonical_name || '(unnamed)'}
                        </Link>
                        <div className="text-sm text-muted">{e.tier}</div>
                      </div>
                    </div>
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
            <span className="text-sm text-muted">Page {page} of {Math.ceil(total / 50)}</span>
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
