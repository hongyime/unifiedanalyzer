const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export interface Entity {
  id: string
  tier: string
  canonical_name: string | null
  confidence_score: number
  signal_count: number
  last_seen_at: string | null
  platform_count: number
  platforms: string[]
  created_at: string | null
  face_crop_url?: string | null
}

export interface SocialCircleEntry {
  associated_face_id: number
  face_crop_url: string | null
  media_item_id: string
  matched_entity_id: string | null
  matched_entity_name: string | null
  matched_confidence: number | null
  first_seen_at: string
}

export interface ReviewCandidate {
  entity_a: string
  name_a: string | null
  display_a: string
  handles_a: string[]
  entity_b: string
  name_b: string | null
  display_b: string
  handles_b: string[]
  score: number | null
  cross_platform: boolean
  same_platform: boolean
  signals: { type: string; confidence: number }[]
  face_a: string | null
  face_b: string | null
}

export interface TriageAlert {
  id: string
  alert_type: string
  severity: string | null
  title: string | null
  detail: string | null
  entity_id: string | null
  entity_name: string | null
  detected_at: string | null
  is_read: boolean
  face: string | null
}

export interface TriageData {
  coverage: {
    entities: number
    with_faces: number
    with_faces_pct: number
    multi_platform: number
    multi_platform_pct: number
    merge_backlog: number
    unread_alerts: number
  }
  merge_candidates: ReviewCandidate[]
  alerts: TriageAlert[]
  new_entities: { id: string; canonical_name: string | null; tier: string; platforms: number; face: string | null }[]
}

export interface EntityDetail {
  id: string
  tier: string
  watch_status?: string | null
  canonical_name: string | null
  confidence_score: number
  signal_count: number
  last_seen_at: string | null
  primary_timezone: string | null
  metadata: Record<string, unknown>
  platform_links: PlatformLink[]
  identity_signals: Signal[]
  face_crop_url?: string | null
}

export interface PlatformLink {
  id: string
  source: string
  platform_id: string
  platform_username: string | null
  platform_name: string | null
  confidence: number
  link_method: string
  is_confirmed: boolean
}

export interface Signal {
  id: string
  signal_type: string
  source_platform: string
  target_platform: string
  value: string
  confidence: number
}

export interface TimelineEvent {
  id: string
  source: string
  event_type: string
  source_record_id: string
  occurred_at: string
  title: string | null
  metadata: Record<string, unknown>
}

export interface Alert {
  id: string
  entity_id: string | null
  entity_name: string | null
  alert_type: string
  severity: string
  source: string | null
  title: string
  detail: Record<string, unknown>
  detected_at: string
  is_read: boolean
  read_at: string | null
}

export interface RunInfo {
  id: string
  run_type: string
  status: string
  started_at: string | null
  finished_at: string | null
  entities_processed: number
  events_created: number
  alerts_created: number
  signals_created: number
  error_message: string | null
}

export interface HealthInfo {
  status: string
  analyzer_db: string
  collector_db: string
  last_incremental_run: string | null
  last_full_resolution: string | null
  entity_count: number
  alert_count_unread: number
}

export interface BehaviorProfile {
  entity_id: string
  posting_hour_dist: Record<string, number>
  posting_dow_dist: Record<string, number>
  avg_post_interval_days: number
  total_events: number
  inferred_timezone: string | null
  timezone_confidence: string
  last_computed_at: string | null
  strava_patterns: StravaPatterns | null
  bio_nlp: BioNlp | null
  graph_analytics: GraphAnalytics | null
  source_breakdown: { source: string; count: number }[]
  type_breakdown: { event_type: string; count: number }[]
}

export interface BioNlp {
  keywords: { word: string; count: number }[]
  hashtags: { tag: string; count: number }[]
  categories: Record<string, number>
  top_emojis: { emoji: string; count: number }[]
  language_hints: string[]
  bio_sources: string[]
  bio_count: number
}

export interface GraphAnalytics {
  degree: number
  strength: number
  betweenness: number
  clustering: number
  component_size: number
}

export interface CollectorInfo {
  source: string
  last_started: string | null
  last_completed: string | null
  items_24h: number
  failed_24h: number
  runs_24h: number
  latest_status: string | null
  targets: { status: string; count: number; last_collection: string | null }[]
}

export interface Relationship {
  id: string
  other_entity_id: string
  other_name: string | null
  relationship_type: string
  weight: number
  sources: Record<string, unknown>
  why?: string | null
}

export interface InteractionPeer {
  entity_id: string
  name: string | null
  face: string | null
  total: number
  last_ts: string | null
  out: {
    total: number
    by_type: Record<string, number>
    last_ts: string | null
  }
  in: {
    total: number
    by_type: Record<string, number>
    last_ts: string | null
  }
}

export interface Community {
  community_id: string
  member_count: number
  members: { entity_id: string; canonical_name: string | null }[]
}

export interface IntelligenceReport {
  entity: { id: string; canonical_name: string | null; tier: string; face_crop_url?: string | null }
  platforms: { source: string; platform_id: string; platform_username: string | null }[]
  location: {
    primary_country: string | null
    primary_timezone: string | null
    region: string | null
    source_countries: string[] | null
  } | null
  behavioral_summary: {
    total_events: number
    posting_hour_dist: Record<string, number>
  } | null
  content_fingerprint: {
    vocab_size: number | null
    vocab_richness: number | null
    top_words: string[] | null
    post_count: number | null
  } | null
  identity_signals: {
    signal_type: string
    target_platform: string
    target_record_id: string
    value: string
    confidence: number
  }[]
  same_person_candidates: {
    entity_id: string
    canonical_name: string | null
    score: number | null
    cross_platform: boolean
    contributing_signals: { type: string; confidence: number }[]
    face_crop_url?: string | null
  }[]
  relationships: {
    relationship_type: string
    other_entity_id: string
    other_canonical_name: string | null
    weight: number
    cross_platform: boolean
  }[]
  community_id: string | null
  timeline_summary: {
    first_seen: string | null
    last_seen: string | null
    event_count_by_source: Record<string, number>
  } | null
}

export interface StravaPatterns {
  total_activities: number
  activity_types: Record<string, number>
  preferred_hour: number | null
  preferred_day: number | null
  avg_distance_km: number | null
  avg_duration_min: number | null
  repeated_routes: Record<string, number>
  route_count: number
}

export interface Paginated<T> {
  data: T[]
  total: number
  page: number
  per_page: number
}

// ── Phase 6 media analysis ──
export interface MediaItem {
  id: string
  media_item_id: string
  parent_media_item_id: string | null
  source: string
  content_type: string
  analysis_type: string
  text_preview: string | null
  has_text: boolean
  gps_lat: number | null
  gps_lon: number | null
  has_gps: boolean
  taken_at: string | null
  perceptual_hash: string | null
  has_face: boolean
  is_derived: boolean
  model_version: string | null
  processed_at: string | null
  result_json: Record<string, unknown> | null
  thumbnail_url: string
}

export interface MediaStats {
  totals: {
    rows_total: number
    items_total: number
    with_gps: number
    with_text: number
    with_face: number
    derived: number
    with_phash: number
  }
  by_analysis_type: { analysis_type: string; n: number }[]
  by_source: { source: string; n: number }[]
  by_content_type: { content_type: string; n: number }[]
}

export interface MediaFilters {
  analysis_types: string[]
  sources: string[]
  content_types: string[]
}

export interface MediaBrowseParams {
  page?: number
  per_page?: number
  analysis_type?: string
  source?: string
  content_type?: string
  has_gps?: boolean
  has_text?: boolean
  has_face?: boolean
  q?: string
}

// ── Faces (facetracker engine, mounted under /api/face) ──
export interface FaceStats {
  total_faces: number
  total_images: number
  total_identities: number
  total_videos: number
  indexing: {
    files_processed: number
    files_failed: number
    faces_per_image_avg: number
  }
}

export interface FaceIdentity {
  identity_id: string
  name: string | null
  face_count: number
  created_at: string
  updated_at: string
  avg_quality_score: number
  thumbnail_url: string | null
}

export interface FaceIdentityList {
  identities: FaceIdentity[]
  total: number
  page: number
  page_size: number
}

// ── Live health (websocket /ws/health) ──
export interface LiveHealth {
  status: string
  analyzer_db: string
  collector_db: string
  entity_count: number
  alert_count_unread: number
  last_completed_run: { run_type: string; finished_at: string } | null
  media_items_analyzed: number
  sources: {
    source: string
    health: 'green' | 'amber' | 'red'
    last_completed: string | null
    items_24h: number
    failed_24h: number
  }[]
}

/** Open the /ws/health websocket. Returns the socket so callers can close it. */
export function openHealthSocket(onMessage: (h: LiveHealth) => void): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws/health`)
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data) as LiveHealth)
    } catch {
      // ignore malformed frame
    }
  }
  return ws
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export const api = {
  getEntities: (page = 1, search = '', sort = 'confidence', order = 'desc', platform = '', minPlatforms = 0) => {
    let q = `/entities?page=${page}&per_page=50&sort=${sort}&order=${order}`
    if (search) q += `&search=${encodeURIComponent(search)}`
    if (platform) q += `&platform=${platform}`
    if (minPlatforms > 1) q += `&min_platforms=${minPlatforms}`
    return get<Paginated<Entity>>(q)
  },

  getEntity: (id: string) => get<EntityDetail>(`/entities/${id}`),

  getChangelog: (entityId: string) =>
    get<{
      since: string | null
      additions: {
        platform_links: { source: string; username: string | null; at: string | null }[]
        timeline_events: number
        alerts: { alert_type: string; title: string | null; detail: string | null; at: string | null }[]
      }
      deletions: { platform: string; text: string | null; deleted_at: string | null }[]
      total_changes: number
    }>(`/entities/${entityId}/changelog`),
  markReviewed: (entityId: string) => post<{ ok: boolean }>(`/entities/${entityId}/mark-reviewed`),

  getEntityGeo: (entityId: string, from?: string | null, to?: string | null) => {
    const q = new URLSearchParams()
    if (from) q.set('from', from)
    if (to) q.set('to', to)
    const suffix = q.toString() ? `?${q.toString()}` : ''
    return get<{
      routes: { name: string | null; type: string | null; date: string | null; source: string; points: [number, number][] }[]
      points: { lat: number; lng: number; label: string | null; source: string; occurred_at: string | null }[]
      counts: { routes: number; points: number }
    }>(`/entities/${entityId}/geo${suffix}`)
  },

  getEntitySocialCircle: (entityId: string) =>
    get<{ associations: SocialCircleEntry[] }>(`/entities/${entityId}/social-circle`),

  getEntityAssociates: (entityId: string) =>
    get<{ associates: { username: string; full_name: string | null; shared: number; entity_id: string | null; entity_name: string | null; face: string | null }[] }>(
      `/entities/${entityId}/associates`,
    ),

  getEntityNetwork: (entityId: string) =>
    get<{
      center: { id: string; name: string | null; face: string | null }
      nodes: { id: string; name: string | null; weight: number; types: string[]; face: string | null; why?: string | null }[]
    }>(`/entities/${entityId}/network`),

  getTimelineLanes: (entityId: string, maxEvents = 8000) =>
    get<{
      lanes: { source: string; events: { t: number; type: string | null }[] }[]
      alerts: { type: string; t: number }[]
      min_t: number | null
      max_t: number | null
      total: number
    }>(`/entities/${entityId}/timeline-lanes?max_events=${maxEvents}`),

  getTimeline: (entityId: string, page = 1, source = '', type = '', from?: string | null, to?: string | null) => {
    let q = `/entities/${entityId}/timeline?page=${page}&per_page=50`
    if (source) q += `&source=${source}`
    if (type) q += `&type=${type}`
    if (from) q += `&from=${encodeURIComponent(from)}`
    if (to) q += `&to=${encodeURIComponent(to)}`
    return get<Paginated<TimelineEvent>>(q)
  },

  getAlerts: (page = 1, unreadOnly = false) =>
    get<Paginated<Alert>>(`/alerts?page=${page}&per_page=50${unreadOnly ? '&unread_only=true' : ''}`),

  markRead: (id: string) => post<{ ok: boolean }>(`/alerts/${id}/read`),
  markAllRead: () => post<{ ok: boolean }>('/alerts/read-all'),

  getRuns: (page = 1) => get<Paginated<RunInfo>>(`/runs?page=${page}`),
  triggerRun: () => post<{ ok: boolean; stats: Record<string, number> }>('/runs/trigger'),

  getHealth: () => get<HealthInfo>('/health'),

  getBehavior: (entityId: string) => get<BehaviorProfile>(`/entities/${entityId}/behavior`),

  getCollectorHealth: () => get<{ collectors: CollectorInfo[] }>('/collector/health'),

  mergeEntities: (ids: string[], reason = '') =>
    post<{ ok: boolean; target_entity_id: string }>('/entities/merge', { source_entity_ids: ids, reason }),

  splitEntity: (entityId: string, linkIds: string[], reason = '') =>
    post<{ ok: boolean; new_entity_id: string }>(`/entities/${entityId}/split`, { link_ids: linkIds, reason }),

  dismissMatch: (entityA: string, entityB: string) =>
    post<{ ok: boolean }>('/entities/dismiss-match', { entity_a: entityA, entity_b: entityB }),

  getReviewCandidates: (limit = 50) =>
    get<{ candidates: ReviewCandidate[]; total: number }>(`/review/candidates?limit=${limit}`),

  getTriage: () => get<TriageData>('/triage'),

  getCases: () => get<{ cases: { id: string; name: string; notes: string | null; items: number; updated_at: string | null }[] }>('/cases'),
  createCase: (name: string) => post<{ ok: boolean; id: string }>('/cases', { name }),
  getCase: (id: string) =>
    get<{ id: string; name: string; notes: string | null; items: { id: string; item_type: string; ref_id: string | null; note: string | null; entity_name: string | null; face: string | null; created_at: string | null }[] }>(`/cases/${id}`),
  addCaseItem: (caseId: string, item: { item_type: string; ref_id?: string; note?: string }) =>
    post<{ ok: boolean; id: string }>(`/cases/${caseId}/items`, item),
  deleteCaseItem: (caseId: string, itemId: string) => del<{ ok: boolean }>(`/cases/${caseId}/items/${itemId}`),
  deleteCase: (id: string) => del<{ ok: boolean }>(`/cases/${id}`),
  exportCaseUrl: (id: string, format: 'json' | 'csv' = 'json') => `${BASE}/cases/${id}/export?format=${format}`,

  searchEntities: (q: string, limit = 12) =>
    get<{ results: { id: string; canonical_name: string | null; tier: string; platforms: number; face: string | null }[] }>(
      `/search/entities?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),

  updateEntitySettings: (entityId: string, settings: { silence_threshold_days?: number | null; notes?: string }) =>
    patch<{ ok: boolean }>(`/entities/${entityId}/settings`, settings),

  exportEntity: (entityId: string) => `${BASE}/entities/${entityId}/export`,

  getRelationships: (entityId: string) =>
    get<{ data: Relationship[] }>(`/entities/${entityId}/relationships`),

  getInteractions: (entityId: string, from?: string | null, to?: string | null) => {
    const q = new URLSearchParams()
    if (from) q.set('from', from)
    if (to) q.set('to', to)
    const suffix = q.toString() ? `?${q.toString()}` : ''
    return get<{ data: InteractionPeer[] }>(`/entities/${entityId}/interactions${suffix}`)
  },

  getGraphOverview: () => get<{
    total_relationships: number
    entities_in_graph: number
    whatsapp_co_members: number
    relationship_type_counts: Record<string, number>
    top_connections: {
      entity_a: { id: string; name: string | null }
      entity_b: { id: string; name: string | null }
      weight: number
      type: string
      why?: string | null
    }[]
    top_bridges: {
      entity: { id: string; name: string | null }
      betweenness: number
      degree: number
      strength: number
    }[]
  }>('/graph/overview'),

  getCommunities: () => get<{ data: Community[] }>('/graph/communities'),

  getIntelligence: (entityId: string) => get<IntelligenceReport>(`/entities/${entityId}/intelligence`),

  getMediaStats: () => get<MediaStats>('/media/stats'),
  getMediaFilters: () => get<MediaFilters>('/media/filters'),
  browseMedia: (params: MediaBrowseParams = {}) => {
    const q = new URLSearchParams()
    q.set('page', String(params.page ?? 1))
    q.set('per_page', String(params.per_page ?? 48))
    if (params.analysis_type) q.set('analysis_type', params.analysis_type)
    if (params.source) q.set('source', params.source)
    if (params.content_type) q.set('content_type', params.content_type)
    if (params.has_gps) q.set('has_gps', 'true')
    if (params.has_text) q.set('has_text', 'true')
    if (params.has_face) q.set('has_face', 'true')
    if (params.q) q.set('q', params.q)
    return get<Paginated<MediaItem>>(`/media/browse?${q.toString()}`)
  },

  // Faces — facetracker engine routes under /api/face (returns empty until the
  // collector media is restored (B1) + face re-index (R6) runs).
  getFaceStats: () => get<FaceStats>('/face/stats'),
  getFaceIdentities: (page = 1) =>
    get<FaceIdentityList>(`/face/identities?page=${page}&page_size=50`),

  getSimilarFaces: (faceId: number, k = 40) =>
    get<{ matches: { face_id: number; cluster_id: number | null; similarity: number; crop_url: string; entity_id: string | null; entity_name: string | null }[] }>(
      `/face/gallery/faces/${faceId}/similar?k=${k}`,
    ),

  setWatch: (entityId: string, status: string | null) =>
    patch<{ ok: boolean; watch_status: string | null }>(`/entities/${entityId}/watch`, { status }),

  getFaceGallery: (page = 1, pageSize = 60) =>
    get<{
      faces: { face_id: number; cluster_id: number | null; quality: number; crop_url: string; source: string }[]
      total: number
      page: number
      page_size: number
    }>(`/face/gallery/faces?page=${page}&page_size=${pageSize}`),
}
