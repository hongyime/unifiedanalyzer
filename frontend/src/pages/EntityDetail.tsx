import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api, EntityDetail, TimelineEvent } from '../api'

function PlatformBadge({ source }: { source: string }) {
  return <span className={`platform-icon p-${source}`}>{source}</span>
}

function ConfidenceBar({ score }: { score: number }) {
  return (
    <div className="signal-bar" style={{ width: '140px' }}>
      <div className="signal-bar-fill" style={{ width: `${Math.round(score * 100)}%` }} />
    </div>
  )
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString()
}

export default function EntityDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [entity, setEntity] = useState<EntityDetail | null>(null)
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [eventsTotal, setEventsTotal] = useState(0)
  const [eventsPage, setEventsPage] = useState(1)
  const [tab, setTab] = useState<'identity' | 'timeline'>('identity')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    api.getEntity(id).then(setEntity).finally(() => setLoading(false))
  }, [id])

  useEffect(() => {
    if (!id || tab !== 'timeline') return
    api.getTimeline(id, eventsPage).then(r => {
      setEvents(r.data)
      setEventsTotal(r.total)
    })
  }, [id, tab, eventsPage])

  if (loading) return <div className="empty-state">Loading...</div>
  if (!entity) return <div className="empty-state">Entity not found</div>

  return (
    <div>
      <Link to="/entities" className="text-sm text-muted" style={{ marginBottom: '1rem', display: 'block' }}>
        &larr; Back to entities
      </Link>

      <div className="card">
        <div className="flex-between">
          <div>
            <h2 style={{ marginBottom: '0.25rem' }}>{entity.canonical_name || '(unnamed)'}</h2>
            <div className="text-sm text-muted">{entity.tier} &middot; {entity.platform_links.length} platforms</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="flex gap-1" style={{ alignItems: 'center', justifyContent: 'flex-end' }}>
              <ConfidenceBar score={entity.confidence_score} />
              <span style={{ fontWeight: 600 }}>{Math.round(entity.confidence_score * 100)}%</span>
            </div>
            <div className="text-sm text-muted">{entity.signal_count} signals</div>
          </div>
        </div>
      </div>

      <div className="flex gap-1 mb-2">
        <button className={tab === 'identity' ? 'primary' : ''} onClick={() => setTab('identity')}>Identity</button>
        <button className={tab === 'timeline' ? 'primary' : ''} onClick={() => setTab('timeline')}>Timeline</button>
      </div>

      {tab === 'identity' && (
        <>
          <h3 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>Platform Links</h3>
          <table>
            <thead>
              <tr>
                <th>Platform</th>
                <th>Username</th>
                <th>Name</th>
                <th>Confirmed</th>
                <th>Method</th>
              </tr>
            </thead>
            <tbody>
              {entity.platform_links.map(l => (
                <tr key={l.id}>
                  <td><PlatformBadge source={l.source} /></td>
                  <td>{l.platform_username || l.platform_id}</td>
                  <td>{l.platform_name || '-'}</td>
                  <td>
                    <span className={`badge ${l.is_confirmed ? 'badge-green' : 'badge-yellow'}`}>
                      {l.is_confirmed ? 'confirmed' : 'candidate'}
                    </span>
                  </td>
                  <td className="text-muted">{l.link_method}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3 style={{ fontSize: '1.1rem', margin: '1.5rem 0 0.75rem' }}>Identity Signals</h3>
          {entity.identity_signals.length === 0 ? (
            <div className="text-sm text-muted">No signals recorded</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Type</th>
                  <th>From</th>
                  <th>To</th>
                  <th>Value</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {entity.identity_signals.map(s => (
                  <tr key={s.id}>
                    <td><span className="badge badge-blue">{s.signal_type}</span></td>
                    <td><PlatformBadge source={s.source_platform} /></td>
                    <td><PlatformBadge source={s.target_platform} /></td>
                    <td className="text-sm" style={{ maxWidth: '300px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {s.value}
                    </td>
                    <td>{s.confidence}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {tab === 'timeline' && (
        <>
          {events.length === 0 ? (
            <div className="empty-state">No timeline events</div>
          ) : (
            <>
              {events.map(ev => (
                <div key={ev.id} className="card">
                  <div className="flex-between">
                    <div className="flex gap-1" style={{ alignItems: 'center' }}>
                      <PlatformBadge source={ev.source} />
                      <span className="badge badge-gray">{ev.event_type}</span>
                    </div>
                    <span className="text-sm text-muted">{formatDate(ev.occurred_at)}</span>
                  </div>
                  {ev.title && <div style={{ marginTop: '0.5rem' }}>{ev.title}</div>}
                </div>
              ))}
              <div className="flex-between" style={{ marginTop: '1rem' }}>
                <span className="text-sm text-muted">{eventsTotal} events</span>
                <div className="flex gap-1">
                  <button disabled={eventsPage <= 1} onClick={() => setEventsPage(p => p - 1)}>Prev</button>
                  <button disabled={eventsPage * 50 >= eventsTotal} onClick={() => setEventsPage(p => p + 1)}>Next</button>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
