import { useEffect, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { api, EntityDetail, TimelineEvent, BehaviorProfile, Relationship, IntelligenceReport } from '../api'
import { FaceAvatar } from '../components/FaceAvatar'

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

const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function HeatmapGrid({ hourDist, dowDist }: { hourDist: Record<string, number>; dowDist: Record<string, number> }) {
  const maxH = Math.max(1, ...Object.values(hourDist))
  const maxD = Math.max(1, ...Object.values(dowDist))

  return (
    <div>
      <div style={{ marginBottom: '1.5rem' }}>
        <div className="text-sm text-muted mb-1">Activity by Hour (UTC)</div>
        <div style={{ display: 'flex', gap: '2px', alignItems: 'flex-end', height: '80px' }}>
          {Array.from({ length: 24 }, (_, h) => {
            const val = hourDist[String(h)] || 0
            const pct = val / maxH
            return (
              <div key={h} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                <div
                  style={{
                    width: '100%',
                    height: `${Math.max(2, pct * 70)}px`,
                    background: `rgba(99, 102, 241, ${0.2 + pct * 0.8})`,
                    borderRadius: '2px',
                  }}
                  title={`${h}:00 — ${val} events`}
                />
                {h % 3 === 0 && <span style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: '2px' }}>{h}</span>}
              </div>
            )
          })}
        </div>
      </div>
      <div>
        <div className="text-sm text-muted mb-1">Activity by Day</div>
        <div style={{ display: 'flex', gap: '4px', alignItems: 'flex-end', height: '60px' }}>
          {Array.from({ length: 7 }, (_, d) => {
            const val = dowDist[String(d)] || 0
            const pct = val / maxD
            return (
              <div key={d} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                <div
                  style={{
                    width: '100%',
                    height: `${Math.max(2, pct * 50)}px`,
                    background: `rgba(99, 102, 241, ${0.2 + pct * 0.8})`,
                    borderRadius: '2px',
                  }}
                  title={`${DOW_LABELS[d]} — ${val} events`}
                />
                <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginTop: '2px' }}>{DOW_LABELS[d]}</span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default function EntityDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [entity, setEntity] = useState<EntityDetail | null>(null)
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [eventsTotal, setEventsTotal] = useState(0)
  const [eventsPage, setEventsPage] = useState(1)
  const [tab, setTab] = useState<'identity' | 'timeline' | 'behavior' | 'relationships' | 'intelligence' | 'settings'>('identity')
  const [loading, setLoading] = useState(true)
  const [behavior, setBehavior] = useState<BehaviorProfile | null>(null)
  const [intelligence, setIntelligence] = useState<IntelligenceReport | null>(null)
  const [selectedLinks, setSelectedLinks] = useState<Set<string>>(new Set())
  const [silenceThreshold, setSilenceThreshold] = useState('')
  const [notes, setNotes] = useState('')
  const [mergeTarget, setMergeTarget] = useState('')
  const [relationships, setRelationships] = useState<Relationship[]>([])
  const [actionMsg, setActionMsg] = useState('')

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
    api.getTimeline(id, eventsPage).then(r => {
      setEvents(r.data)
      setEventsTotal(r.total)
    })
  }, [id, tab, eventsPage])

  useEffect(() => {
    if (!id || tab !== 'behavior') return
    api.getBehavior(id).then(setBehavior).catch(() => setBehavior(null))
  }, [id, tab])

  useEffect(() => {
    if (!id || tab !== 'relationships') return
    api.getRelationships(id).then(r => setRelationships(r.data)).catch(() => setRelationships([]))
  }, [id, tab])

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

  if (loading) return <div className="empty-state">Loading...</div>
  if (!entity) return <div className="empty-state">Entity not found</div>

  return (
    <div>
      <Link to="/entities" className="text-sm text-muted" style={{ marginBottom: '1rem', display: 'block' }}>
        &larr; Back to entities
      </Link>

      <div className="card">
        <div className="flex-between">
          <div className="flex gap-1" style={{ alignItems: 'center' }}>
            <FaceAvatar url={entity.face_crop_url} name={entity.canonical_name} size={48} />
            <div>
              <h2 style={{ marginBottom: '0.25rem' }}>{entity.canonical_name || '(unnamed)'}</h2>
              <div className="text-sm text-muted">{entity.tier} &middot; {entity.platform_links.length} platforms</div>
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="flex gap-1" style={{ alignItems: 'center', justifyContent: 'flex-end' }}>
              <ConfidenceBar score={entity.confidence_score} />
              <span style={{ fontWeight: 600 }}>{Math.round(entity.confidence_score * 100)}%</span>
            </div>
            <div className="text-sm text-muted">{entity.signal_count} signals</div>
            <a
              href={api.exportEntity(entity.id)}
              className="text-sm"
              style={{ marginTop: '0.25rem', display: 'inline-block' }}
            >
              Export JSON
            </a>
          </div>
        </div>
      </div>

      {actionMsg && (
        <div className="card" style={{ background: 'var(--bg-hover)', padding: '0.75rem' }}>
          <div className="text-sm">{actionMsg}</div>
        </div>
      )}

      <div className="flex gap-1 mb-2">
        <button className={tab === 'identity' ? 'primary' : ''} onClick={() => setTab('identity')}>Identity</button>
        <button className={tab === 'timeline' ? 'primary' : ''} onClick={() => setTab('timeline')}>Timeline</button>
        <button className={tab === 'behavior' ? 'primary' : ''} onClick={() => setTab('behavior')}>Behavior</button>
        <button className={tab === 'relationships' ? 'primary' : ''} onClick={() => setTab('relationships')}>Relationships</button>
        <button className={tab === 'intelligence' ? 'primary' : ''} onClick={() => setTab('intelligence')}>Intelligence</button>
        <button className={tab === 'settings' ? 'primary' : ''} onClick={() => setTab('settings')}>Settings</button>
      </div>

      {tab === 'identity' && (
        <>
          <h3 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>Platform Links</h3>
          <table>
            <thead>
              <tr>
                <th style={{ width: '30px' }}></th>
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
                  <td>
                    <input
                      type="checkbox"
                      checked={selectedLinks.has(l.id)}
                      onChange={e => {
                        const next = new Set(selectedLinks)
                        e.target.checked ? next.add(l.id) : next.delete(l.id)
                        setSelectedLinks(next)
                      }}
                    />
                  </td>
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

          {selectedLinks.size > 0 && (
            <div className="flex gap-1" style={{ marginTop: '0.75rem' }}>
              <button onClick={handleSplit} style={{ borderColor: 'var(--orange)', color: 'var(--orange)' }}>
                Split {selectedLinks.size} selected into new entity
              </button>
            </div>
          )}

          <div style={{ marginTop: '1rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <input
              type="text"
              placeholder="Paste entity ID to merge with..."
              value={mergeTarget}
              onChange={e => setMergeTarget(e.target.value)}
              style={{ maxWidth: '340px' }}
            />
            <button onClick={handleMerge} disabled={!mergeTarget.trim()}>Merge</button>
          </div>

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

      {tab === 'behavior' && (
        <>
          {!behavior ? (
            <div className="empty-state">No behavioral data yet — run an analysis first</div>
          ) : (
            <>
              <div className="card">
                <div className="flex-between mb-1">
                  <span className="text-sm text-muted">Total events analyzed</span>
                  <span style={{ fontWeight: 600 }}>{behavior.total_events.toLocaleString()}</span>
                </div>
                <div className="flex-between mb-1">
                  <span className="text-sm text-muted">Avg posting interval</span>
                  <span style={{ fontWeight: 600 }}>
                    {behavior.avg_post_interval_days < 1
                      ? `${Math.round(behavior.avg_post_interval_days * 24)}h`
                      : `${behavior.avg_post_interval_days.toFixed(1)} days`}
                  </span>
                </div>
                {behavior.last_computed_at && (
                  <div className="flex-between">
                    <span className="text-sm text-muted">Last computed</span>
                    <span className="text-sm">{formatDate(behavior.last_computed_at)}</span>
                  </div>
                )}
              </div>

              <div className="card">
                <HeatmapGrid hourDist={behavior.posting_hour_dist} dowDist={behavior.posting_dow_dist} />
              </div>

              <div className="card">
                <div className="text-sm text-muted mb-1">Activity by Platform</div>
                {behavior.source_breakdown.map(s => {
                  const pct = s.count / behavior.total_events
                  return (
                    <div key={s.source} className="flex gap-1" style={{ alignItems: 'center', marginBottom: '0.5rem' }}>
                      <PlatformBadge source={s.source} />
                      <div style={{ flex: 1 }}>
                        <div style={{
                          height: '6px', borderRadius: '3px', background: 'var(--border)',
                        }}>
                          <div style={{
                            height: '100%', borderRadius: '3px', width: `${pct * 100}%`,
                            background: 'var(--accent)',
                          }} />
                        </div>
                      </div>
                      <span className="text-sm" style={{ minWidth: '60px', textAlign: 'right' }}>
                        {s.count.toLocaleString()}
                      </span>
                    </div>
                  )
                })}
              </div>

              {behavior.strava_patterns && (
                <div className="card">
                  <div className="text-sm text-muted mb-1" style={{ fontWeight: 600 }}>Strava Patterns</div>
                  <div className="flex-between mb-1">
                    <span className="text-sm text-muted">Total activities</span>
                    <span style={{ fontWeight: 600 }}>{behavior.strava_patterns.total_activities}</span>
                  </div>
                  {behavior.strava_patterns.avg_distance_km != null && (
                    <div className="flex-between mb-1">
                      <span className="text-sm text-muted">Avg distance</span>
                      <span style={{ fontWeight: 600 }}>{behavior.strava_patterns.avg_distance_km} km</span>
                    </div>
                  )}
                  {behavior.strava_patterns.avg_duration_min != null && (
                    <div className="flex-between mb-1">
                      <span className="text-sm text-muted">Avg duration</span>
                      <span style={{ fontWeight: 600 }}>{behavior.strava_patterns.avg_duration_min} min</span>
                    </div>
                  )}
                  {behavior.strava_patterns.preferred_hour != null && (
                    <div className="flex-between mb-1">
                      <span className="text-sm text-muted">Preferred hour</span>
                      <span style={{ fontWeight: 600 }}>{behavior.strava_patterns.preferred_hour}:00</span>
                    </div>
                  )}
                  {behavior.strava_patterns.preferred_day != null && (
                    <div className="flex-between mb-1">
                      <span className="text-sm text-muted">Preferred day</span>
                      <span style={{ fontWeight: 600 }}>{DOW_LABELS[behavior.strava_patterns.preferred_day]}</span>
                    </div>
                  )}
                  {Object.keys(behavior.strava_patterns.activity_types).length > 0 && (
                    <div style={{ marginTop: '0.75rem' }}>
                      <div className="text-sm text-muted mb-1">Activity types</div>
                      <div className="flex gap-1" style={{ flexWrap: 'wrap' }}>
                        {Object.entries(behavior.strava_patterns.activity_types)
                          .sort((a, b) => b[1] - a[1])
                          .map(([type, count]) => (
                            <span key={type} className="badge badge-blue">{type}: {count}</span>
                          ))}
                      </div>
                    </div>
                  )}
                  {behavior.strava_patterns.route_count > 0 && (
                    <div style={{ marginTop: '0.75rem' }}>
                      <div className="text-sm text-muted mb-1">Repeated routes ({behavior.strava_patterns.route_count})</div>
                      {Object.entries(behavior.strava_patterns.repeated_routes)
                        .sort((a, b) => b[1] - a[1])
                        .map(([name, count]) => (
                          <div key={name} className="text-sm" style={{ marginBottom: '0.25rem' }}>
                            {name} <span className="text-muted">({count}x)</span>
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              )}

              {behavior.bio_nlp && (
                <div className="card">
                  <div className="text-sm text-muted mb-1" style={{ fontWeight: 600 }}>
                    Bio Analysis ({behavior.bio_nlp.bio_count} bio(s) from {behavior.bio_nlp.bio_sources.join(', ')})
                  </div>
                  {Object.keys(behavior.bio_nlp.categories).length > 0 && (
                    <div style={{ marginBottom: '0.75rem' }}>
                      <div className="text-sm text-muted mb-1">Categories</div>
                      <div className="flex gap-1" style={{ flexWrap: 'wrap' }}>
                        {Object.entries(behavior.bio_nlp.categories)
                          .sort((a, b) => b[1] - a[1])
                          .map(([cat, score]) => (
                            <span key={cat} className="badge badge-green">{cat} ({score})</span>
                          ))}
                      </div>
                    </div>
                  )}
                  {behavior.bio_nlp.keywords.length > 0 && (
                    <div style={{ marginBottom: '0.75rem' }}>
                      <div className="text-sm text-muted mb-1">Keywords</div>
                      <div className="flex gap-1" style={{ flexWrap: 'wrap' }}>
                        {behavior.bio_nlp.keywords.slice(0, 15).map(k => (
                          <span key={k.word} className="badge badge-gray">{k.word} ({k.count})</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {behavior.bio_nlp.hashtags.length > 0 && (
                    <div style={{ marginBottom: '0.75rem' }}>
                      <div className="text-sm text-muted mb-1">Hashtags</div>
                      <div className="flex gap-1" style={{ flexWrap: 'wrap' }}>
                        {behavior.bio_nlp.hashtags.map(h => (
                          <span key={h.tag} className="badge badge-blue">#{h.tag} ({h.count})</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {behavior.bio_nlp.top_emojis.length > 0 && (
                    <div>
                      <div className="text-sm text-muted mb-1">Top Emojis</div>
                      <div className="flex gap-1">
                        {behavior.bio_nlp.top_emojis.map((e, i) => (
                          <span key={i} style={{ fontSize: '1.5rem' }} title={`${e.count}x`}>{e.emoji}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {behavior.graph_analytics && (
                <div className="card">
                  <div className="text-sm text-muted mb-1" style={{ fontWeight: 600 }}>Graph Position</div>
                  <div className="flex-between mb-1">
                    <span className="text-sm text-muted">Connections (degree)</span>
                    <span style={{ fontWeight: 600 }}>{behavior.graph_analytics.degree}</span>
                  </div>
                  <div className="flex-between mb-1">
                    <span className="text-sm text-muted">Connection strength</span>
                    <span style={{ fontWeight: 600 }}>{behavior.graph_analytics.strength}</span>
                  </div>
                  <div className="flex-between mb-1">
                    <span className="text-sm text-muted">Betweenness centrality</span>
                    <span style={{ fontWeight: 600 }}>{behavior.graph_analytics.betweenness.toFixed(4)}</span>
                  </div>
                  <div className="flex-between mb-1">
                    <span className="text-sm text-muted">Clustering coefficient</span>
                    <span style={{ fontWeight: 600 }}>{behavior.graph_analytics.clustering.toFixed(4)}</span>
                  </div>
                  <div className="flex-between">
                    <span className="text-sm text-muted">Community size</span>
                    <span style={{ fontWeight: 600 }}>{behavior.graph_analytics.component_size}</span>
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}

      {tab === 'relationships' && (
        <>
          {relationships.length === 0 ? (
            <div className="empty-state">No relationships found</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Connected Entity</th>
                  <th>Type</th>
                  <th>Weight</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {relationships.map(r => (
                  <tr key={r.id}>
                    <td>
                      <Link to={`/entities/${r.other_entity_id}`}>
                        {r.other_name || r.other_entity_id.slice(0, 8)}
                      </Link>
                    </td>
                    <td><span className="badge badge-blue">{r.relationship_type}</span></td>
                    <td style={{ fontWeight: 600 }}>{r.weight}</td>
                    <td className="text-sm text-muted">
                      {r.sources && typeof r.sources === 'object' && 'groups' in r.sources
                        ? (r.sources as { groups: string[] }).groups.join(', ')
                        : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {tab === 'intelligence' && (
        <>
          {!intelligence ? (
            <div className="empty-state">Loading intelligence report...</div>
          ) : (
            <>
              {intelligence.location && (
                <div className="card">
                  <div className="text-sm text-muted mb-1" style={{ fontWeight: 600 }}>Location</div>
                  {intelligence.location.primary_country && (
                    <div className="flex-between mb-1">
                      <span className="text-sm text-muted">Primary country</span>
                      <span style={{ fontWeight: 600 }}>{intelligence.location.primary_country}</span>
                    </div>
                  )}
                  {intelligence.location.primary_timezone && (
                    <div className="flex-between mb-1">
                      <span className="text-sm text-muted">Primary timezone</span>
                      <span style={{ fontWeight: 600 }}>{intelligence.location.primary_timezone}</span>
                    </div>
                  )}
                  {intelligence.location.region && (
                    <div className="flex-between mb-1">
                      <span className="text-sm text-muted">Region</span>
                      <span style={{ fontWeight: 600 }}>{intelligence.location.region}</span>
                    </div>
                  )}
                  {intelligence.location.source_countries && intelligence.location.source_countries.length > 0 && (
                    <div style={{ marginTop: '0.75rem' }}>
                      <div className="text-sm text-muted mb-1">Source countries</div>
                      <div className="flex gap-1" style={{ flexWrap: 'wrap' }}>
                        {intelligence.location.source_countries.map(c => (
                          <span key={c} className="badge badge-gray">{c}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {intelligence.content_fingerprint && (
                <div className="card">
                  <div className="text-sm text-muted mb-1" style={{ fontWeight: 600 }}>Content Fingerprint</div>
                  <div className="flex-between mb-1">
                    <span className="text-sm text-muted">Posts analyzed</span>
                    <span style={{ fontWeight: 600 }}>{intelligence.content_fingerprint.post_count}</span>
                  </div>
                  <div className="flex-between mb-1">
                    <span className="text-sm text-muted">Vocabulary size</span>
                    <span style={{ fontWeight: 600 }}>{intelligence.content_fingerprint.vocab_size}</span>
                  </div>
                  <div className="flex-between mb-1">
                    <span className="text-sm text-muted">Vocabulary richness</span>
                    <span style={{ fontWeight: 600 }}>{intelligence.content_fingerprint.vocab_richness}</span>
                  </div>
                  {intelligence.content_fingerprint.top_words && intelligence.content_fingerprint.top_words.length > 0 && (
                    <div style={{ marginTop: '0.75rem' }}>
                      <div className="text-sm text-muted mb-1">Top words</div>
                      <div className="flex gap-1" style={{ flexWrap: 'wrap' }}>
                        {intelligence.content_fingerprint.top_words.slice(0, 15).map(w => (
                          <span key={w} className="badge badge-gray">{w}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {intelligence.community_id && (
                <div className="card">
                  <div className="flex-between">
                    <span className="text-sm text-muted">Community membership</span>
                    <Link to="/communities" className="text-sm">View communities &rarr;</Link>
                  </div>
                </div>
              )}

              <div className="card">
                <div className="text-sm text-muted mb-1" style={{ fontWeight: 600 }}>Same-Person Candidates</div>
                {intelligence.same_person_candidates.length === 0 ? (
                  <div className="text-sm text-muted">None detected</div>
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>Entity</th>
                        <th>Probability</th>
                        <th>Cross-platform</th>
                        <th>Contributing Signals</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {intelligence.same_person_candidates.map(c => (
                        <tr key={c.entity_id}>
                          <td>
                            <div className="flex gap-1" style={{ alignItems: 'center' }}>
                              {c.contributing_signals.some(s => s.type === 'media_face_match') && intelligence.entity.face_crop_url && (
                                <FaceAvatar url={intelligence.entity.face_crop_url} name={intelligence.entity.canonical_name} size={30} />
                              )}
                              <FaceAvatar url={c.face_crop_url} name={c.canonical_name} size={30} />
                              <Link to={`/entities/${c.entity_id}`}>
                                {c.canonical_name || c.entity_id.slice(0, 8)}
                              </Link>
                            </div>
                          </td>
                          <td>
                            <div className="flex gap-1" style={{ alignItems: 'center' }}>
                              <ConfidenceBar score={c.score ?? 0} />
                              <span className="text-sm text-muted">{Math.round((c.score ?? 0) * 100)}%</span>
                            </div>
                          </td>
                          <td>
                            <span className={`badge ${c.cross_platform ? 'badge-blue' : 'badge-gray'}`}>
                              {c.cross_platform ? 'yes' : 'no'}
                            </span>
                          </td>
                          <td className="text-sm text-muted">
                            {c.contributing_signals.map(s => `${s.type} (${s.confidence})`).join(', ')}
                          </td>
                          <td>
                            <div className="flex gap-1">
                              <button onClick={() => handleConfirmSame(c.entity_id)}>Same &rarr; merge</button>
                              <button
                                onClick={() => handleDismissMatch(c.entity_id)}
                                style={{ borderColor: 'var(--orange)', color: 'var(--orange)' }}
                              >
                                Not same
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              {intelligence.timeline_summary && (
                <div className="card">
                  <div className="text-sm text-muted mb-1" style={{ fontWeight: 600 }}>Timeline Summary</div>
                  <div className="flex-between mb-1">
                    <span className="text-sm text-muted">First seen</span>
                    <span className="text-sm">
                      {intelligence.timeline_summary.first_seen ? formatDate(intelligence.timeline_summary.first_seen) : '-'}
                    </span>
                  </div>
                  <div className="flex-between mb-1">
                    <span className="text-sm text-muted">Last seen</span>
                    <span className="text-sm">
                      {intelligence.timeline_summary.last_seen ? formatDate(intelligence.timeline_summary.last_seen) : '-'}
                    </span>
                  </div>
                  <div style={{ marginTop: '0.75rem' }}>
                    <div className="text-sm text-muted mb-1">Events by source</div>
                    <div className="flex gap-1" style={{ flexWrap: 'wrap' }}>
                      {Object.entries(intelligence.timeline_summary.event_count_by_source).map(([source, count]) => (
                        <span key={source} className="badge badge-blue">{source}: {count}</span>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}

      {tab === 'settings' && (
        <div className="card">
          <h3 style={{ fontSize: '1.1rem', marginBottom: '1rem' }}>Alert Tuning</h3>
          <div style={{ marginBottom: '1rem' }}>
            <label className="text-sm text-muted" style={{ display: 'block', marginBottom: '0.25rem' }}>
              Custom silence threshold (days) — leave empty for automatic
            </label>
            <input
              type="text"
              placeholder="e.g. 14"
              value={silenceThreshold}
              onChange={e => setSilenceThreshold(e.target.value)}
              style={{ maxWidth: '200px' }}
            />
          </div>
          <div style={{ marginBottom: '1rem' }}>
            <label className="text-sm text-muted" style={{ display: 'block', marginBottom: '0.25rem' }}>
              Notes
            </label>
            <textarea
              value={notes}
              onChange={e => setNotes(e.target.value)}
              rows={3}
              style={{
                width: '100%', maxWidth: '500px', padding: '0.5rem 0.75rem',
                borderRadius: '6px', border: '1px solid var(--border)',
                background: 'var(--bg)', color: 'var(--text)', fontSize: '0.875rem',
                resize: 'vertical',
              }}
            />
          </div>
          <button className="primary" onClick={handleSaveSettings}>Save Settings</button>
        </div>
      )}
    </div>
  )
}
