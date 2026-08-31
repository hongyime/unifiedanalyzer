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
    face_bridge_audit?: {
      available: boolean
      ok: boolean | null
      face_entity_collisions: number | null
      cluster_entity_collisions: number | null
      contested_cluster_count: number | null
      samples?: Record<string, unknown>
    }
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
  wa_devices?: WaDevice[]
}

export interface WaDevice {
  phone_jid: string
  phone_number: string
  exists_on_wa: boolean
  device_count: number
  companion_count: number
  device_ids: number[]
  observed_at: string | null
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
  confidence: number | null
  confidence_source: string | null
}

export interface DecisionHistoryEntry {
  id: number
  action: string
  action_label: string
  actor: string | null
  entity_ids: string[]
  entity_names: Record<string, string | null>
  payload: Record<string, unknown>
  summary: string
  created_at: string | null
  decision_jsonl_path: string | null
  decision_jsonl_written_at: string | null
  decision_jsonl_error: string | null
  durable: boolean
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

export interface RunPhase {
  phase: string
  status: string
  duration_ms: number | null
  error: string | null
  created_at: string | null
}

export interface EvalRun {
  id: string
  name: string
  task_type: string
  model_or_rule_version: string
  status: string
  metrics: Record<string, unknown> | null
  started_at: string | null
  finished_at: string | null
}

export interface MultilingualStatus {
  text_rows: number
  profile_rows: number
  profile_coverage_pct: number
  code_mixed_rows: number
  unsupported_rows: number
  translation_rows: number
  translated_rows: number
  translation_coverage_pct: number
  failed_translation_rows: number
  skipped_translation_rows: number
  languages: { language: string; count: number }[]
  failures: { reason: string; count: number }[]
  language_detector?: Record<string, unknown>
  translation_worker?: Record<string, unknown>
}

export interface StreamAlertStatus {
  offsets: {
    source_name: string
    cursor_table: string
    cursor_value: string | null
    last_seen_at: string | null
    updated_at: string | null
  }[]
  sent_fingerprints: number
  suppressed_fingerprints: number
  active_suppressions: number
}

export interface AlertFingerprint {
  fingerprint: string
  alert_type: string
  entity_id: string | null
  source: string | null
  window_start: string | null
  window_end: string | null
  last_sent_at: string | null
  count: number
  status: string
  detail: Record<string, unknown>
  updated_at: string | null
}

export interface AlertSuppression {
  id: string
  scope: string
  alert_type: string | null
  entity_id: string | null
  source: string | null
  reason: string
  starts_at: string | null
  ends_at: string | null
  created_at: string | null
}

export interface AlertWindow {
  bucket_type: string
  bucket_key: string
  source: string | null
  window_start: string | null
  window_end: string | null
  count: number
  baseline: number | null
  detail: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
}

export interface CollectorCoverageRow {
  source: string
  expected_cadence: string | null
  latest_data_at: string | null
  latest_run_at: string | null
  status: string
  rows_24h: number
  media_24h: number
  errors_24h: number
  rate_limits_24h: number
  private_access_failures: number
  stale_targets: number
  seen_targets_total?: number
  seen_targets_backfilled?: number
  seen_targets_pending?: number
  seen_targets_fresh?: number
  seen_targets_stale?: number
  seen_targets_newly_discovered?: number
  created_at: string | null
}

export interface CollectorCoverageResponse {
  sources: CollectorCoverageRow[]
  total: number
  summary?: {
    fresh: number
    degraded: number
    stale: number
    unknown: number
  }
  snapshot_created_at?: string | null
  snapshot_age_seconds?: number | null
  snapshot_stale?: boolean
  collector_skipped?: boolean
  error?: string
}

export interface CollectorDashboardSurface {
  reachable: boolean
  available: boolean
  payload: Record<string, unknown> | null
  error?: string
}

export interface CollectorProductionSummary {
  instagram_stuck_stage?: string | null
  instagram_cooldown_active?: boolean
  realtime_queue_depth?: number
  realtime_failed_sources?: { source: string; failed: number; too_large: number; local_fallback: number }[]
  domain_pacing_sources?: number
  domain_robots_blocked?: number
  domain_429?: number
  quota_snapshots?: number
  quota_paused?: number
  optional_rollout_action?: string | null
  optional_rollout_can_proceed?: boolean | null
}

export interface CollectorProductionStatus {
  collector_dashboard: string
  surfaces: Record<string, CollectorDashboardSurface>
  summary: CollectorProductionSummary
}

export interface ProductionReadinessStory {
  actor: 'operator' | 'analyst' | string
  story: string
  value: string
  proves: string
}

export interface ProductionReadinessCheck {
  id: string
  title: string
  ok: boolean
  status: 'ok' | 'degraded' | string
  severity: 'critical' | 'warning' | string
  user_story: ProductionReadinessStory
  detail: string
  evidence: Record<string, unknown>
}

export interface ProductionReadinessReport {
  status: 'ok' | 'degraded' | string
  ok: boolean
  user_stories: Record<string, ProductionReadinessStory>
  checks: ProductionReadinessCheck[]
  summary: {
    total: number
    ok: number
    degraded: number
    critical_failed: number
  }
}

export interface GraphExplainEdge {
  id: string
  from_entity_id: string | null
  to_entity_id: string | null
  relationship_type: string
  weight: number
  cross_platform: boolean
  confidence_bucket: 'hard' | 'strong' | 'weak' | 'context-only' | string
  source: string
  sources: Record<string, unknown>
  why: string | null
  last_seen_at: string | null
  evidence_refs: unknown[]
}

export interface GraphPivots {
  entity_id: string
  total: number
  groups: Record<string, GraphExplainEdge[]>
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

export interface RelationshipDecisionRequest {
  entity_a: string
  entity_b: string
  relationship_type: string
  is_real: boolean
  confidence?: number | null
  notes?: string | null
  evidence_refs?: Record<string, unknown>
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

export interface IntersectionEntity {
  id: string
  name: string | null
  face: string | null
}

export interface IntersectionEvidencePoint {
  entity_id: string
  entity_name: string | null
  source: string
  record_id: string
  occurred_at: string | null
  lat: number
  lng: number
  label: string | null
  evidence_type?: string | null
  evidence_key?: string | null
  confidence?: number | null
  status?: string | null
}

export interface PhysicalIntersection {
  type: string
  locus: { lat: number; lng: number }
  radius_m: number
  max_distance_m: number
  time_gap_minutes: number
  sources: string[]
  evidence: IntersectionEvidencePoint[]
}

export interface DigitalIntersection {
  type: string
  source: string
  label: string | null
  entities: { id: string; name: string | null; role: string }[]
  count: number
  weight?: number | null
  group_id?: string | null
  peer?: { id: string; name: string | null }
  first_seen_at?: string | null
  last_seen_at?: string | null
  metadata?: Record<string, unknown>
}

export interface IntersectionResponse {
  entity_ids: string[]
  entities: IntersectionEntity[]
  params: {
    radius_m: number
    window_minutes: number
    from: string | null
    to: string | null
  }
  physical: PhysicalIntersection[]
  digital: DigitalIntersection[]
  counts: {
    physical: number
    digital: number
    physical_points_considered: number
    physical_points_raw?: number
    physical_points_suppressed?: number
    physical_points_materialized?: number
    physical_points_from_registry?: number
  }
  collector_skipped: boolean
  duration_ms: number
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

export interface EntityMediaAnalysisPreview {
  analysis_id: string | null
  analysis_type: string | null
  content_type: string | null
  source: string | null
  text_preview: string | null
  has_text: boolean
  has_gps: boolean
  gps_lat: number | null
  gps_lon: number | null
  taken_at: string | null
  processed_at: string | null
  thumbnail_url: string | null
}

export interface EntityLinkedMedia {
  media_item_id: string
  source: string
  entity_id: string
  entity_name: string
  content_type: string
  content_id: string
  filename: string
  file_size: number | null
  width: number | null
  height: number | null
  sha256: string | null
  kind: string
  collected_at: string | null
  analysis: EntityMediaAnalysisPreview | null
}

export interface EntityKnownFace {
  face_id: number
  face_crop_url: string | null
  media_item_id: string | null
  confidence: number | null
  method: string | null
  created_at: string | null
  analysis: EntityMediaAnalysisPreview | null
}

export interface EntityAssociatedFace {
  associated_face_id: number
  face_crop_url: string | null
  media_item_id: string
  source_platform: string | null
  quality_score: number | null
  first_seen_at: string | null
  matched_entity_id: string | null
  matched_entity_name: string | null
  matched_confidence: number | null
  analysis: EntityMediaAnalysisPreview | null
}

export interface EntityMediaFaces {
  entity_id: string
  collector_skipped: boolean
  collector_error: string | null
  linked_media: EntityLinkedMedia[]
  known_faces: EntityKnownFace[]
  associated_faces: EntityAssociatedFace[]
}

export interface MediaPersonDecisionRequest {
  role: 'owner' | 'person_in_photo'
  is_correct: boolean
  media_ref: Record<string, unknown>
  confidence?: number | null
  notes?: string | null
  evidence_refs?: Record<string, unknown>
}

export interface LocationDecisionRequest {
  is_correct: boolean
  location_ref: Record<string, unknown>
  confidence?: number | null
  notes?: string | null
  evidence_refs?: Record<string, unknown>
}

export interface SourceConfidenceRequest {
  confidence: number
  source?: string | null
  platform_id?: string | null
  notes?: string | null
  evidence_refs?: Record<string, unknown>
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

export interface MediaCoverageItem {
  key: string
  label: string
  status: string
  count: number
  processed: number
  basis: string
}

export interface MediaCoverage {
  generated_at: string
  rows_total: number
  items_total: number
  derived_rows: number
  phash_rows: number
  coverage: MediaCoverageItem[]
  contact_signals: {
    total: number
    by_source_column: { source_column: string; signal_type: string; n: number }[]
  }
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

export interface FaceSearchMatch {
  face_id: number
  cluster_id: number | null
  similarity: number
  quality: number
  detection_confidence: number
  crop_url: string
  entity: { id: string; name: string | null; confidence: number } | null
  source: {
    media_item_id: string | null
    platform: string | null
    content_type: string | null
    content_id: string | null
    filename: string | null
    file_path: string | null
    url: string | null
    date: string | null
    metadata: Record<string, unknown> | null
  }
}

export interface FaceSearchResponse {
  query: Record<string, unknown>
  matches: FaceSearchMatch[]
  count: number
  took_ms: number
  index: { method: string; name: string; operator: string }
  collector_skipped: boolean
}

export interface TimelineSearchResult {
  event_id: string
  entity_id: string | null
  platform: string
  occurred_at: string | null
  snippet: string | null
  score: number
  keyword_score: number | null
  semantic_score: number | null
  keyword_rank: number | null
  semantic_rank: number | null
  rrf_rank: number | null
  match_debug?: {
    matched_translation?: boolean
  }
}

export interface TimelineSearchResponse {
  results: TimelineSearchResult[]
  took_ms: number
  mode: 'hybrid' | 'keyword' | 'semantic'
  model: string | null
}

export interface FaceAuditReport {
  available: boolean
  ok: boolean | null
  error?: string
  face_entity_collisions: number | null
  cluster_entity_collisions: number | null
  contested_cluster_count: number | null
  counts: Record<string, number>
  samples: Record<string, unknown[]>
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

async function postForm<T>(path: string, body: FormData): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    body,
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

  getEntityDecisions: (entityId: string, limit = 50) =>
    get<{ entity_id: string; decisions: DecisionHistoryEntry[]; total: number }>(
      `/entities/${entityId}/decisions?limit=${limit}`,
    ),

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
      routes: {
        name: string | null
        type: string | null
        date: string | null
        source: string
        evidence_type?: string | null
        evidence_key?: string | null
        status?: string | null
        confidence?: number | null
        source_table?: string | null
        source_record_id?: string | null
        points: [number, number][]
      }[]
      points: {
        lat: number
        lng: number
        label: string | null
        source: string
        occurred_at: string | null
        evidence_type?: string | null
        evidence_key?: string | null
        status?: string | null
        confidence?: number | null
        source_table?: string | null
        source_record_id?: string | null
      }[]
      events?: {
        kind: 'route' | 'point'
        evidence_key?: string | null
        source: string
        evidence_type?: string | null
        label: string | null
        occurred_at: string | null
        lat: number | null
        lng: number | null
        end_lat?: number | null
        end_lng?: number | null
        confidence?: number | null
        status?: string | null
        source_table?: string | null
        source_record_id?: string | null
      }[]
      counts: { routes: number; points: number; events?: number; evidence_types?: Record<string, number>; suppressed?: number }
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

  getTimelineLanes: (entityId: string, maxEvents = 2000, minConfidence?: number | null) => {
    const q = new URLSearchParams()
    q.set('max_events', String(maxEvents))
    if (minConfidence != null) q.set('min_confidence', String(minConfidence))
    return get<{
      lanes: { source: string; events: { t: number; type: string | null }[] }[]
      alerts: { type: string; t: number }[]
      min_t: number | null
      max_t: number | null
      total: number
    }>(`/entities/${entityId}/timeline-lanes?${q.toString()}`)
  },

  getTimeline: (entityId: string, page = 1, source = '', type = '', from?: string | null, to?: string | null, minConfidence?: number | null) => {
    let q = `/entities/${entityId}/timeline?page=${page}&per_page=50`
    if (source) q += `&source=${encodeURIComponent(source)}`
    if (type) q += `&event_type=${encodeURIComponent(type)}`
    if (from) q += `&from=${encodeURIComponent(from)}`
    if (to) q += `&to=${encodeURIComponent(to)}`
    if (minConfidence != null) q += `&min_confidence=${minConfidence}`
    return get<Paginated<TimelineEvent>>(q)
  },

  getAlerts: (page = 1, unreadOnly = false) =>
    get<Paginated<Alert>>(`/alerts?page=${page}&per_page=50${unreadOnly ? '&unread_only=true' : ''}`),

  markRead: (id: string) => post<{ ok: boolean }>(`/alerts/${id}/read`),
  markAllRead: () => post<{ ok: boolean }>('/alerts/read-all'),

  getRuns: (page = 1) => get<Paginated<RunInfo>>(`/runs?page=${page}`),
  getRunPhases: (runId: string) => get<{ run_id: string; phases: RunPhase[]; total: number }>(`/runs/${runId}/phases`),
  triggerRun: () => post<{ ok: boolean; stats: Record<string, number> }>('/runs/trigger'),

  getHealth: () => get<HealthInfo>('/health'),

  getBehavior: (entityId: string) => get<BehaviorProfile>(`/entities/${entityId}/behavior`),

  getCollectorHealth: () => get<{ collectors: CollectorInfo[] }>('/collector/health'),
  getCollectorCoverage: () => get<CollectorCoverageResponse>('/collector/coverage'),
  getCollectorProductionStatus: () => get<CollectorProductionStatus>('/collector/production-status'),
  getProductionReadiness: () => get<ProductionReadinessReport>('/production/readiness'),

  getStreamAlertStatus: () => get<StreamAlertStatus>('/alerts/stream/status'),
  getAlertFingerprints: (status = '', alertType = '', limit = 50) => {
    const q = new URLSearchParams()
    q.set('limit', String(limit))
    if (status) q.set('status', status)
    if (alertType) q.set('alert_type', alertType)
    return get<{ data: AlertFingerprint[]; total: number }>(`/alerts/fingerprints?${q.toString()}`)
  },
  getAlertSuppressions: (activeOnly = true) =>
    get<{ data: AlertSuppression[]; total: number }>(`/alerts/suppressions?active_only=${activeOnly ? 'true' : 'false'}`),
  getAlertWindows: (bucketType = '', source = '', limit = 50) => {
    const q = new URLSearchParams()
    q.set('limit', String(limit))
    if (bucketType) q.set('bucket_type', bucketType)
    if (source) q.set('source', source)
    return get<{ data: AlertWindow[]; total: number }>(`/alerts/windows?${q.toString()}`)
  },
  createAlertSuppression: (body: {
    scope?: string
    alert_type?: string | null
    entity_id?: string | null
    source?: string | null
    reason: string
    starts_at?: string | null
    ends_at?: string | null
  }) => post<AlertSuppression>('/alerts/suppressions', body),
  updateAlertSuppression: (id: string, body: { reason?: string; ends_at?: string | null; status?: 'active' | 'expired' }) =>
    patch<AlertSuppression>(`/alerts/suppressions/${id}`, body),
  expireAlertSuppression: (id: string) => del<{ ok: boolean; suppression: AlertSuppression }>(`/alerts/suppressions/${id}`),

  getEvalLatest: () => get<{ data: EvalRun[]; total: number }>('/eval/latest'),
  getEvalRuns: (task = '', limit = 50) => {
    const q = new URLSearchParams()
    q.set('limit', String(limit))
    if (task) q.set('task', task)
    return get<{ data: EvalRun[]; total: number }>(`/eval/runs?${q.toString()}`)
  },
  getEvalRegressions: (task: string) => get<{ task: string; runs: EvalRun[]; delta: Record<string, number> }>(`/eval/${task}/regressions`),
  getMultilingualStatus: () => get<MultilingualStatus>('/multilingual/status'),

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

  getGraphPath: (
    fromEntityId: string,
    toEntityId: string,
    includeContextOnly = false,
    maxHops = 3,
    filters: { confidence_bucket?: string; relationship_type?: string; source?: string } = {},
  ) => {
    const q = new URLSearchParams()
    q.set('from_entity_id', fromEntityId)
    q.set('to_entity_id', toEntityId)
    q.set('max_hops', String(maxHops))
    q.set('include_context_only', includeContextOnly ? 'true' : 'false')
    if (filters.confidence_bucket) q.set('confidence_bucket', filters.confidence_bucket)
    if (filters.relationship_type) q.set('relationship_type', filters.relationship_type)
    if (filters.source) q.set('source', filters.source)
    return get<{ path: GraphExplainEdge[]; hops: number; found: boolean }>(`/graph/path?${q.toString()}`)
  },
  getGraphPivots: (
    entityId: string,
    includeContextOnly = false,
    filters: { confidence_bucket?: string; relationship_type?: string; source?: string } = {},
  ) => {
    const q = new URLSearchParams()
    q.set('include_context_only', includeContextOnly ? 'true' : 'false')
    if (filters.confidence_bucket) q.set('confidence_bucket', filters.confidence_bucket)
    if (filters.relationship_type) q.set('relationship_type', filters.relationship_type)
    if (filters.source) q.set('source', filters.source)
    return get<GraphPivots>(`/graph/pivots/${entityId}?${q.toString()}`)
  },

  decideRelationship: (body: RelationshipDecisionRequest) =>
    post<{ ok: boolean; action: string }>('/entities/relationship-decision', body),

  getEntityMediaFaces: (entityId: string, limit = 40) =>
    get<EntityMediaFaces>(`/entities/${entityId}/media-faces?limit=${limit}`),

  decideMediaPerson: (entityId: string, body: MediaPersonDecisionRequest) =>
    post<{ ok: boolean; action: string }>(`/entities/${entityId}/media-person-decision`, body),

  decideLocation: (entityId: string, body: LocationDecisionRequest) =>
    post<{ ok: boolean; action: string }>(`/entities/${entityId}/location-decision`, body),

  adjustSourceConfidence: (entityId: string, body: SourceConfidenceRequest) =>
    post<{ ok: boolean }>(`/entities/${entityId}/source-confidence`, body),

  getInteractions: (entityId: string, from?: string | null, to?: string | null) => {
    const q = new URLSearchParams()
    if (from) q.set('from', from)
    if (to) q.set('to', to)
    const suffix = q.toString() ? `?${q.toString()}` : ''
    return get<{ data: InteractionPeer[] }>(`/entities/${entityId}/interactions${suffix}`)
  },

  getEntityIntersection: (
    entityA: string,
    entityB: string,
    opts: { radius_m?: number; window_minutes?: number; from?: string | null; to?: string | null } = {},
  ) => {
    const q = new URLSearchParams()
    if (opts.radius_m != null) q.set('radius_m', String(opts.radius_m))
    if (opts.window_minutes != null) q.set('window_minutes', String(opts.window_minutes))
    if (opts.from) q.set('from', opts.from)
    if (opts.to) q.set('to', opts.to)
    const suffix = q.toString() ? `?${q.toString()}` : ''
    return get<IntersectionResponse>(`/entities/${entityA}/intersect/${entityB}${suffix}`)
  },

  intersectEntities: (body: {
    ids: string[]
    radius_m?: number
    window_minutes?: number
    from?: string | null
    to?: string | null
  }) => post<IntersectionResponse>('/entities/intersect', body),

  searchTimeline: (query: string, mode: 'hybrid' | 'keyword' | 'semantic' = 'hybrid', limit = 25) => {
    const q = new URLSearchParams()
    q.set('q', query)
    q.set('mode', mode)
    q.set('limit', String(limit))
    return get<TimelineSearchResponse>(`/search/timeline?${q.toString()}`)
  },

  getEntityChatThreads: (entityId: string) =>
    get<{ entity_id: string; threads: {
      thread_id: string
      source: string
      title: string | null
      started_at: string | null
      last_message_at: string | null
      message_count: number
      reply_count: number
      reaction_count: number
      forwarded_count: number
      preview: { text?: string; interaction_type?: string; occurred_at?: string }[]
    }[] }>(`/entities/${entityId}/chat/threads`),

  getEntityGeoQuality: (entityId: string) =>
    get<{ entity_id: string; groups: { source: string; evidence_type: string; status: string; count: number; avg_confidence: number | null }[]; weak_samples: unknown[] }>(
      `/entities/${entityId}/geo/quality`,
    ),

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
  getMediaCoverage: () => get<MediaCoverage>('/media/coverage'),
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
  getFaceAudit: () => get<FaceAuditReport>('/faces/audit'),

  getSimilarFaces: (faceId: number, k = 40) =>
    get<{ matches: { face_id: number; cluster_id: number | null; similarity: number; crop_url: string; entity_id: string | null; entity_name: string | null }[] }>(
      `/face/gallery/faces/${faceId}/similar?k=${k}`,
    ),

  searchFacesByFaceId: (faceId: number, k = 48) =>
    post<FaceSearchResponse>('/faces/search', { face_id: faceId, k }),

  searchFacesByImage: (file: File, k = 48) => {
    const form = new FormData()
    form.set('image', file)
    form.set('k', String(k))
    return postForm<FaceSearchResponse>('/faces/search', form)
  },

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
