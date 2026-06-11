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
}

export interface EntityDetail {
  id: string
  tier: string
  canonical_name: string | null
  confidence_score: number
  signal_count: number
  last_seen_at: string | null
  primary_timezone: string | null
  metadata: Record<string, unknown>
  platform_links: PlatformLink[]
  identity_signals: Signal[]
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
  source_breakdown: { source: string; count: number }[]
  type_breakdown: { event_type: string; count: number }[]
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

export interface Paginated<T> {
  data: T[]
  total: number
  page: number
  per_page: number
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
  getEntities: (page = 1, search = '') =>
    get<Paginated<Entity>>(`/entities?page=${page}&per_page=50${search ? `&search=${encodeURIComponent(search)}` : ''}`),

  getEntity: (id: string) => get<EntityDetail>(`/entities/${id}`),

  getTimeline: (entityId: string, page = 1, source = '', type = '') => {
    let q = `/entities/${entityId}/timeline?page=${page}&per_page=50`
    if (source) q += `&source=${source}`
    if (type) q += `&type=${type}`
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

  updateEntitySettings: (entityId: string, settings: { silence_threshold_days?: number | null; notes?: string }) =>
    patch<{ ok: boolean }>(`/entities/${entityId}/settings`, settings),

  exportEntity: (entityId: string) => `${BASE}/entities/${entityId}/export`,
}
