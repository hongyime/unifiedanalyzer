CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tier VARCHAR(20) NOT NULL DEFAULT 'primary',
    canonical_name VARCHAR(255),
    confidence_score FLOAT DEFAULT 0.0,
    signal_count INT DEFAULT 0,
    last_seen_at TIMESTAMP WITH TIME ZONE,
    primary_timezone VARCHAR(50),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_entities_tier ON entities(tier);
CREATE INDEX IF NOT EXISTS idx_entities_confidence ON entities(confidence_score DESC);

CREATE TABLE IF NOT EXISTS entity_platform_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    source VARCHAR(30) NOT NULL,
    platform_id VARCHAR(255) NOT NULL,
    platform_username VARCHAR(255),
    platform_name VARCHAR(255),
    confidence FLOAT DEFAULT 0.0,
    link_method VARCHAR(50),
    is_confirmed BOOLEAN DEFAULT FALSE,
    retracted_at TIMESTAMP WITH TIME ZONE,
    retraction_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(source, platform_id)
);
CREATE INDEX IF NOT EXISTS idx_epl_entity ON entity_platform_links(entity_id);
CREATE INDEX IF NOT EXISTS idx_epl_source ON entity_platform_links(source);
CREATE INDEX IF NOT EXISTS idx_epl_source_platform_id_active
    ON entity_platform_links(source, platform_id)
    WHERE retracted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_epl_source_platform_username_lower_active
    ON entity_platform_links(source, lower(platform_username))
    WHERE retracted_at IS NULL AND platform_username IS NOT NULL;

CREATE TABLE IF NOT EXISTS identity_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    signal_type VARCHAR(50) NOT NULL,
    source_platform VARCHAR(30) NOT NULL,
    source_table VARCHAR(100),
    source_column VARCHAR(100),
    source_record_id VARCHAR(255),
    target_platform VARCHAR(30),
    target_record_id VARCHAR(255),
    value TEXT,
    confidence FLOAT DEFAULT 0.0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_signals_entity ON identity_signals(entity_id);
CREATE INDEX IF NOT EXISTS idx_signals_type ON identity_signals(signal_type);
-- Track-C (2026-07-08): free-form JSONB metadata per signal so enrichments
-- (phone country/carrier/line-type, breach names, Sherlock/Holehe evidence)
-- can attach without stringifying into the `value` column. Nullable to keep
-- every pre-existing signal row valid.
ALTER TABLE identity_signals ADD COLUMN IF NOT EXISTS metadata JSONB;

CREATE TABLE IF NOT EXISTS identity_truth_assertions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assertion_type VARCHAR(50) NOT NULL DEFAULT 'same_person',
    entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    value TEXT NOT NULL,
    truth_state VARCHAR(30) NOT NULL DEFAULT 'auto_truth',
    confidence FLOAT NOT NULL DEFAULT 0.0,
    evidence_count INT NOT NULL DEFAULT 0,
    evidence_signal_ids UUID[] NOT NULL DEFAULT '{}',
    evidence_summary JSONB NOT NULL DEFAULT '{}',
    source_platform VARCHAR(30) NOT NULL DEFAULT 'analyzer',
    source_table VARCHAR(100),
    source_record_id VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(assertion_type, entity_id, value, truth_state)
);
CREATE INDEX IF NOT EXISTS idx_identity_truth_entity
    ON identity_truth_assertions(entity_id, truth_state, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_identity_truth_value
    ON identity_truth_assertions(value);

CREATE TABLE IF NOT EXISTS normalized_indicators (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    indicator_type VARCHAR(30) NOT NULL,
    normalized_value TEXT NOT NULL,
    display_value TEXT,
    source_families TEXT[] NOT NULL DEFAULT '{}',
    evidence_count INT NOT NULL DEFAULT 0,
    confidence FLOAT NOT NULL DEFAULT 0.0,
    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}',
    supabase_exportable BOOLEAN NOT NULL DEFAULT FALSE,
    export_status VARCHAR(30) NOT NULL DEFAULT 'pending',
    exported_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(indicator_type, normalized_value)
);
CREATE INDEX IF NOT EXISTS idx_normalized_indicators_type_status
    ON normalized_indicators(indicator_type, export_status, supabase_exportable);
CREATE INDEX IF NOT EXISTS idx_normalized_indicators_last_seen
    ON normalized_indicators(last_seen_at DESC);

-- Track-C: hash-chained tamper-evident audit log for human decisions. Every
-- entity merge/dismiss/split writes one row whose sha256 covers (prev_sha256,
-- action, actor, entity_ids, payload, created_at). A gap or hash mismatch is
-- detectable at read time. Modeled after the FORGE OSINT pattern; scoped to
-- analyst decisions we want to preserve across UUID churn.
CREATE TABLE IF NOT EXISTS audit_log (
    id           BIGSERIAL PRIMARY KEY,
    prev_sha256  CHAR(64),
    sha256       CHAR(64) NOT NULL,
    action       VARCHAR(50) NOT NULL,        -- merge_entities | dismiss_match | split_entity | ...
    actor        VARCHAR(100),                 -- 'dashboard' | 'cli' | 'system' | operator name
    entity_ids   UUID[],                       -- the entities involved
    payload      JSONB NOT NULL DEFAULT '{}',  -- action-specific detail
    idempotency_key CHAR(64),
    decision_jsonl_path TEXT,
    decision_jsonl_written_at TIMESTAMPTZ,
    decision_jsonl_error TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action  ON audit_log(action);
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS idempotency_key CHAR(64);
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS decision_jsonl_path TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS decision_jsonl_written_at TIMESTAMPTZ;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS decision_jsonl_error TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_log_idempotency_key
    ON audit_log(idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_log_decision_jsonl_pending
    ON audit_log(id, created_at)
    WHERE decision_jsonl_written_at IS NULL;

-- Track-C: per-email breach findings from XposedOrNot (and any future breach
-- provider). Analyzer-owned so we can enrich, alert, and time-track without
-- re-querying the API on every run. UNIQUE guards dedup.
CREATE TABLE IF NOT EXISTS email_breach_findings (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        VARCHAR(320) NOT NULL,
    breach_name  VARCHAR(200) NOT NULL,
    breach_date  DATE,
    source       VARCHAR(30) NOT NULL DEFAULT 'xposedornot',
    detail       JSONB DEFAULT '{}',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (email, breach_name, source)
);
CREATE INDEX IF NOT EXISTS idx_email_breach_email ON email_breach_findings(email);
CREATE INDEX IF NOT EXISTS idx_email_breach_source ON email_breach_findings(source);

-- Track-C: Sherlock/Holehe handle-fanout discoveries staged before promotion
-- to entity_platform_links. Kept as a distinct table so we can review + accept
-- results without polluting the primary graph.
CREATE TABLE IF NOT EXISTS handle_discoveries (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id     UUID REFERENCES entities(id) ON DELETE CASCADE,
    source_query  VARCHAR(255) NOT NULL,     -- the handle or email queried
    tool          VARCHAR(30) NOT NULL,      -- 'sherlock' | 'holehe' | ...
    platform      VARCHAR(80) NOT NULL,       -- e.g. 'GitHub', 'Reddit'
    url           TEXT,
    confidence    FLOAT DEFAULT 0.6,
    promoted      BOOLEAN DEFAULT FALSE,      -- true after review + entity_platform_links write
    dismissed     BOOLEAN DEFAULT FALSE,
    detail        JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (entity_id, tool, platform, source_query)
);
CREATE INDEX IF NOT EXISTS idx_handle_discoveries_entity ON handle_discoveries(entity_id);
CREATE INDEX IF NOT EXISTS idx_handle_discoveries_pending
    ON handle_discoveries(entity_id) WHERE NOT promoted AND NOT dismissed;

CREATE TABLE IF NOT EXISTS timeline_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID REFERENCES entities(id) ON DELETE SET NULL,
    source VARCHAR(30) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    source_record_id VARCHAR(255) NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
    title TEXT,
    detail TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(source, event_type, source_record_id)
);
CREATE INDEX IF NOT EXISTS idx_timeline_entity_time ON timeline_events(entity_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_timeline_entity_time_lane_cover
    ON timeline_events(entity_id, occurred_at DESC) INCLUDE (source, event_type);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_partitioned_table pt
        JOIN pg_class c ON c.oid = pt.partrelid WHERE c.relname = 'timeline_events'
    ) THEN
        CREATE INDEX IF NOT EXISTS idx_timeline_entity_attributed_time
            ON timeline_events(entity_id, occurred_at DESC)
            WHERE entity_id IS NOT NULL;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_timeline_time ON timeline_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_timeline_source ON timeline_events(source);
-- P2-5 (identity_system_review_plan.md): 4-column unique key including the
-- partition key occurred_at. A month-partitioned timeline_events (see
-- src/db/migrations/001_partition_timeline_events.sql) REQUIRES occurred_at in
-- every unique key, so build_timeline upserts on this 4-col target. On the
-- NON-partitioned (fresh / pre-migration) table we add it as a plain index so the
-- upsert works before partitioning; on the partitioned table the migration
-- supplies the equivalent UNIQUE constraint (timeline_events_uniq4), so we must
-- NOT try to build idx_timeline_uniq4 there (it would rebuild a duplicate 4-col
-- index across all partitions on every startup). Hence the guard.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_partitioned_table pt
        JOIN pg_class c ON c.oid = pt.partrelid WHERE c.relname = 'timeline_events'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'idx_timeline_uniq4'
    ) THEN
        CREATE UNIQUE INDEX idx_timeline_uniq4
            ON timeline_events(source, event_type, source_record_id, occurred_at);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID REFERENCES entities(id) ON DELETE SET NULL,
    alert_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL DEFAULT 'info',
    source VARCHAR(30),
    title TEXT NOT NULL,
    detail JSONB DEFAULT '{}',
    detected_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_read BOOLEAN DEFAULT FALSE,
    read_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_alerts_entity ON alerts(entity_id);
CREATE INDEX IF NOT EXISTS idx_alerts_unread ON alerts(is_read, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(alert_type);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_type VARCHAR(30) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    finished_at TIMESTAMP WITH TIME ZONE,
    entities_processed INT DEFAULT 0,
    events_created INT DEFAULT 0,
    alerts_created INT DEFAULT 0,
    signals_created INT DEFAULT 0,
    error_message TEXT,
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON analysis_runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_started ON analysis_runs(started_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_analysis_runs_one_running
    ON analysis_runs(status)
    WHERE status = 'running';
-- P0-3 (identity_system_review_plan.md): liveness heartbeat for run locks. A
-- background task bumps this every ~60s while a run executes; the stale-lock
-- cleaner keys off COALESCE(heartbeat_at, started_at) so a legitimately long run
-- (full-res is routinely 2-5h) is never mistaken for a dead one, while a truly
-- hung run is still reclaimed shortly after its heartbeat stops.
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP WITH TIME ZONE;

CREATE TABLE IF NOT EXISTS analyzer_backup_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(20) NOT NULL, -- running | success | failed
    kinds TEXT[] NOT NULL DEFAULT '{}',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    path TEXT,
    size_bytes BIGINT,
    deleted_count INTEGER NOT NULL DEFAULT 0,
    restore_validation TEXT,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_analyzer_backup_runs_started
    ON analyzer_backup_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_analyzer_backup_runs_status
    ON analyzer_backup_runs(status, started_at DESC);

-- P2-3 (identity_system_review_plan.md): per-phase run status. Every secondary
-- pipeline phase is non-fatal (try/except), so a persistently failing step was
-- invisible. One row per (run, phase) records ok/failed + duration, enabling a
-- repeated-failure alert and latency visibility.
CREATE TABLE IF NOT EXISTS run_phase_status (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID,
    run_type    VARCHAR(30),
    phase       VARCHAR(64) NOT NULL,
    status      VARCHAR(20) NOT NULL,   -- ok | skipped | failed
    duration_ms INTEGER,
    error       TEXT,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_run_phase_status_phase ON run_phase_status(phase, created_at DESC);

CREATE TABLE IF NOT EXISTS notification_audit (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel             VARCHAR(30) NOT NULL DEFAULT 'telegram',
    chat_id             TEXT,
    message_type        VARCHAR(80) NOT NULL DEFAULT 'general',
    text_preview        TEXT NOT NULL,
    status              VARCHAR(20) NOT NULL, -- sent | failed | skipped
    telegram_message_id BIGINT,
    related_run_id      UUID REFERENCES analysis_runs(id) ON DELETE SET NULL,
    related_alert_id    UUID REFERENCES alerts(id) ON DELETE SET NULL,
    error               TEXT,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notification_audit_created
    ON notification_audit(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notification_audit_type_created
    ON notification_audit(message_type, created_at DESC);

-- OSINT NLP/monitoring first slice (2026-08-08): per-phase coverage snapshots.
-- run_phase_status remains the pass/fail/duration ledger; this side table records
-- what each phase processed, attributed, skipped, or failed to resolve. Rows are
-- derived artifacts and are safe to regenerate on future runs.
CREATE TABLE IF NOT EXISTS pipeline_coverage_snapshots (
    snapshot_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              UUID,
    run_type            VARCHAR(30),
    phase               VARCHAR(64) NOT NULL,
    source              VARCHAR(128) NOT NULL DEFAULT 'all',
    phase_status        VARCHAR(20) NOT NULL DEFAULT 'ok',
    processed_count     BIGINT NOT NULL DEFAULT 0,
    attributed_count    BIGINT NOT NULL DEFAULT 0,
    unresolved_count    BIGINT NOT NULL DEFAULT 0,
    skipped_count       BIGINT NOT NULL DEFAULT 0,
    error_count         BIGINT NOT NULL DEFAULT 0,
    top_unresolved_json JSONB NOT NULL DEFAULT '[]',
    duration_ms         INTEGER,
    resource_class      VARCHAR(30) NOT NULL DEFAULT 'db',
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pipeline_coverage_run_phase
    ON pipeline_coverage_snapshots(run_id, phase, source);
CREATE INDEX IF NOT EXISTS idx_pipeline_coverage_phase_created
    ON pipeline_coverage_snapshots(phase, created_at DESC);

CREATE TABLE IF NOT EXISTS behavioral_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE UNIQUE,
    posting_hour_dist JSONB DEFAULT '{}',
    posting_dow_dist JSONB DEFAULT '{}',
    avg_post_interval_days FLOAT,
    total_events INT DEFAULT 0,
    frequent_locations JSONB DEFAULT '[]',
    inferred_home JSONB,
    inferred_timezone VARCHAR(50),
    timezone_confidence VARCHAR(20) DEFAULT 'none',
    is_gps_available BOOLEAN DEFAULT FALSE,
    last_computed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

ALTER TABLE entities ADD COLUMN IF NOT EXISTS silence_threshold_days FLOAT;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT '';
ALTER TABLE behavioral_profiles ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';

CREATE TABLE IF NOT EXISTS entity_merge_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action VARCHAR(10) NOT NULL, -- 'merge' or 'split'
    source_entity_ids UUID[] NOT NULL,
    target_entity_id UUID,
    reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS entity_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_a_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    entity_b_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    relationship_type VARCHAR(50),
    weight INT DEFAULT 1,
    cross_platform BOOLEAN DEFAULT FALSE,
    sources JSONB DEFAULT '[]',
    last_seen_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_relationships_a ON entity_relationships(entity_a_id);
CREATE INDEX IF NOT EXISTS idx_relationships_b ON entity_relationships(entity_b_id);
WITH ranked_relationships AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY entity_a_id, entity_b_id, relationship_type
               ORDER BY updated_at DESC NULLS LAST,
                        weight DESC NULLS LAST,
                        created_at DESC NULLS LAST,
                        id DESC
           ) AS rn
    FROM entity_relationships
    WHERE entity_a_id IS NOT NULL
      AND entity_b_id IS NOT NULL
      AND relationship_type IS NOT NULL
)
DELETE FROM entity_relationships er
USING ranked_relationships r
WHERE er.id = r.id
  AND r.rn > 1;
CREATE UNIQUE INDEX IF NOT EXISTS idx_relationships_pair_type_unique
    ON entity_relationships(entity_a_id, entity_b_id, relationship_type);

-- Fast-path index for GET /api/review/candidates: matches the exact predicate
-- (relationship_type='same_person_probability' AND COALESCE(score*100, weight) >= N).
-- Without this the planner scans the entire idx_relationships_pair_type_unique index
-- (346k+ rows) to find the 578 same_person rows. Partial+expression = ~200 rows scanned.
CREATE INDEX IF NOT EXISTS idx_relationships_same_person_score
    ON entity_relationships (
        (COALESCE(
            CASE WHEN jsonb_typeof(sources->'score') = 'number'
                 THEN (sources->>'score')::float8 * 100
            END,
            weight::float8
        ))
    )
    WHERE relationship_type = 'same_person_probability';

CREATE TABLE IF NOT EXISTS entity_interactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_entity_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    interaction_type VARCHAR(50) NOT NULL,
    source VARCHAR(30) NOT NULL,
    source_record_id VARCHAR(255) NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
    weight INT NOT NULL DEFAULT 1,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (interaction_type, source, source_record_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_interactions_actor_time
    ON entity_interactions(actor_entity_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_entity_interactions_target_time
    ON entity_interactions(target_entity_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_entity_interactions_type
    ON entity_interactions(interaction_type, occurred_at DESC);

CREATE TABLE IF NOT EXISTS account_proximity (
    platform VARCHAR(30) NOT NULL,
    account_id VARCHAR(255) NOT NULL,
    owner_account VARCHAR(255) NOT NULL,
    tier SMALLINT NOT NULL CHECK (tier BETWEEN 1 AND 4),
    reasons JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (platform, account_id, owner_account)
);
CREATE INDEX IF NOT EXISTS idx_account_proximity_tier
    ON account_proximity(tier);
CREATE INDEX IF NOT EXISTS idx_account_proximity_platform_owner
    ON account_proximity(platform, owner_account, tier);
CREATE INDEX IF NOT EXISTS idx_account_proximity_account
    ON account_proximity(platform, account_id);

CREATE TABLE IF NOT EXISTS collector_priority_hints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(30) NOT NULL,
    target_id VARCHAR(255) NOT NULL,
    target_username VARCHAR(255),
    priority SMALLINT NOT NULL DEFAULT 1,
    confidence FLOAT NOT NULL,
    hint_type VARCHAR(50) NOT NULL,
    entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    candidate_entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relationship_id UUID REFERENCES entity_relationships(id) ON DELETE SET NULL,
    evidence JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (source, target_id, hint_type, entity_id, candidate_entity_id)
);
CREATE INDEX IF NOT EXISTS idx_collector_priority_hints_status
    ON collector_priority_hints(status, priority, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_collector_priority_hints_target
    ON collector_priority_hints(source, target_id);
CREATE INDEX IF NOT EXISTS idx_collector_priority_hints_entity
    ON collector_priority_hints(entity_id);
CREATE INDEX IF NOT EXISTS idx_collector_priority_hints_candidate
    ON collector_priority_hints(candidate_entity_id);

-- Phase 6: media content analysis (see docs/media_analysis_plan.md).
-- One row per (media_item_id, analysis_type). media_item_id references
-- unifiedcollector.media_items.id by value (cross-database, no FK). For
-- derived media (image extracted from a PDF, frame extracted from a video),
-- media_item_id is synthetic ("{parent_id}:pdf_img:{page}:{idx}" or
-- "{parent_id}:frame:{sec}") with parent_media_item_id pointing at the real
-- media_items.id.
CREATE TABLE IF NOT EXISTS media_analysis (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_item_id        TEXT NOT NULL,
    parent_media_item_id TEXT,
    source               VARCHAR NOT NULL,
    content_type         VARCHAR NOT NULL,
    analysis_type        VARCHAR NOT NULL,
    extracted_text       TEXT,
    result_json          JSONB,
    gps_lat              DOUBLE PRECISION,
    gps_lon              DOUBLE PRECISION,
    taken_at             TIMESTAMP WITH TIME ZONE,
    perceptual_hash      VARCHAR,
    face_embedding       DOUBLE PRECISION[],
    model_version        VARCHAR,
    processed_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE (media_item_id, analysis_type)
);
CREATE INDEX IF NOT EXISTS idx_media_analysis_type   ON media_analysis (analysis_type);
CREATE INDEX IF NOT EXISTS idx_media_analysis_phash  ON media_analysis (perceptual_hash);
CREATE INDEX IF NOT EXISTS idx_media_analysis_gps    ON media_analysis (gps_lat, gps_lon);
CREATE INDEX IF NOT EXISTS idx_media_analysis_parent ON media_analysis (parent_media_item_id);
CREATE INDEX IF NOT EXISTS idx_media_analysis_processed_at
    ON media_analysis (processed_at DESC);
CREATE INDEX IF NOT EXISTS idx_media_analysis_type_processed
    ON media_analysis (analysis_type, processed_at DESC);
CREATE INDEX IF NOT EXISTS idx_media_analysis_source_processed
    ON media_analysis (source, processed_at DESC);
CREATE INDEX IF NOT EXISTS idx_media_analysis_content_processed
    ON media_analysis (content_type, processed_at DESC);
CREATE INDEX IF NOT EXISTS idx_media_analysis_has_text
    ON media_analysis (id) WHERE extracted_text IS NOT NULL AND extracted_text <> '';
CREATE INDEX IF NOT EXISTS idx_media_analysis_has_face
    ON media_analysis (id) WHERE face_embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_media_analysis_has_gps
    ON media_analysis (id) WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_media_analysis_is_derived
    ON media_analysis (id) WHERE parent_media_item_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_media_analysis_has_phash
    ON media_analysis (id) WHERE perceptual_hash IS NOT NULL;

-- facetracker merge (Stage 2): bridge linking analyzer entities to faces in the
-- facetracker face engine. face_id references facetracker.faces.id by value —
-- NO cross-schema FK, so the face engine owns its schema independently and can
-- be re-indexed/rebuilt without breaking analyzer rows. media_item_id records
-- which collector media the face came from (entity attribution traceability).
CREATE TABLE IF NOT EXISTS entity_faces (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id      UUID REFERENCES entities(id) ON DELETE CASCADE,
    face_id        INTEGER NOT NULL,
    media_item_id  TEXT,
    confidence     FLOAT DEFAULT 0.0,
    method         VARCHAR(50),
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (entity_id, face_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_faces_entity ON entity_faces(entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_faces_face   ON entity_faces(face_id);

DO $$
BEGIN
    IF to_regclass('facetracker.faces') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS idx_faces_embedding_vec_ivfflat
            ON facetracker.faces USING ivfflat (embedding_vec vector_cosine_ops)
            WITH (lists = 100)
            WHERE embedding_vec IS NOT NULL AND COALESCE(is_junk, FALSE) = FALSE;
    END IF;
END $$;

-- Calibration labels: ground-truth same/different decisions captured straight
-- from the dashboard (entity merge = same person; "not same" dismiss =
-- different). Drives src/pipeline/identity_calibration.py — no CSV needed.
-- NO FK to entities (a merge deletes the source entity, but the label must
-- survive). Pair is stored normalized (entity_a < entity_b) so each pair has a
-- single latest decision (upsert on PK).
-- Per-entity "last reviewed" marker for the what-changed-since-last-viewed feed.
CREATE TABLE IF NOT EXISTS entity_views (
    entity_id      UUID PRIMARY KEY,
    last_viewed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Geocode cache: Instagram only stores location_name (no lat/lng), so we geocode
-- place names (trickled via Nominatim, cached here) to put IG pins on the map.
CREATE TABLE IF NOT EXISTS geocode_cache (
    place_name  TEXT PRIMARY KEY,
    lat         DOUBLE PRECISION,
    lng         DOUBLE PRECISION,
    status      VARCHAR(20) DEFAULT 'pending',   -- pending | ok | notfound
    geocoded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_geocode_pending ON geocode_cache(status) WHERE status = 'pending';

-- Canonical, analyzer-owned location evidence registry. Collector tables remain
-- the raw source of truth; this table records the normalized claim shown on maps
-- and the human decision state for that claim.
CREATE TABLE IF NOT EXISTS location_evidence (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_key       CHAR(64) NOT NULL UNIQUE,
    entity_id          UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    source             VARCHAR(30) NOT NULL,
    evidence_type      VARCHAR(50) NOT NULL, -- gps | exif_gps | route_polyline | venue_tag | caption_derived | inferred | ...
    source_table       VARCHAR(100),
    source_record_id   TEXT,
    occurred_at        TIMESTAMP WITH TIME ZONE,
    lat                DOUBLE PRECISION,
    lng                DOUBLE PRECISION,
    label              TEXT,
    confidence         FLOAT DEFAULT 0.0,
    geometry           JSONB DEFAULT '{}',
    payload            JSONB DEFAULT '{}',
    status             VARCHAR(20) NOT NULL DEFAULT 'active', -- active | confirmed | rejected | suppressed
    decision_audit_id  BIGINT REFERENCES audit_log(id) ON DELETE SET NULL,
    decision_actor     VARCHAR(100),
    decision_notes     TEXT,
    decided_at         TIMESTAMP WITH TIME ZONE,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_location_evidence_entity_time
    ON location_evidence(entity_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_location_evidence_status
    ON location_evidence(status);
CREATE INDEX IF NOT EXISTS idx_location_evidence_source_record
    ON location_evidence(source, source_table, source_record_id);

-- Saved investigations ("cases"): a pinboard of entities/media/notes/links.
CREATE TABLE IF NOT EXISTS cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS case_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    item_type VARCHAR(20) NOT NULL,   -- entity | media | note | link
    ref_id TEXT,                      -- entity_id / media_item_id / url
    note TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_case_items_case ON case_items(case_id);

-- Watchlist tier (user-curated): priority | watching | archive | NULL.
-- The triage queue + alerts can prioritise/suppress by this.
ALTER TABLE entities ADD COLUMN IF NOT EXISTS watch_status VARCHAR(20);
CREATE INDEX IF NOT EXISTS idx_entities_watch ON entities(watch_status) WHERE watch_status IS NOT NULL;

CREATE TABLE IF NOT EXISTS identity_labels (
    entity_a    UUID NOT NULL,
    entity_b    UUID NOT NULL,
    features    JSONB NOT NULL,      -- {signal_type: max_confidence} snapshot at decision time
    label       SMALLINT NOT NULL,   -- 1 = same person, 0 = different
    source      VARCHAR(30),
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (entity_a, entity_b)
);

-- Axis-3 Change-4: resumable cursor for face_worker.ingest_drive_media(). One
-- row per configured DRIVE_SOURCES entry; the worker walks in deterministic
-- (mtime ASC, path ASC) order and checkpoints (last_mtime_walked,
-- last_path_walked) every FACE_WORKER_CHECKPOINT_EVERY successful files, so a
-- restart resumes where it left off instead of re-walking the whole tree.
CREATE TABLE IF NOT EXISTS drive_scan_state (
    drive_path        TEXT PRIMARY KEY,
    last_mtime_walked TIMESTAMPTZ,
    last_path_walked  TEXT,
    files_indexed     BIGINT DEFAULT 0,
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

-- OSINT NLP text foundation (2026-08-08): analyzer-owned canonical text side
-- table. timeline_events remains the normalized event ledger; this table keeps
-- searchable/NLP feature material out of the partitioned event rows while
-- preserving collector provenance through source/event_type/source_record_id.
CREATE TABLE IF NOT EXISTS timeline_text_features (
    event_id           UUID PRIMARY KEY,
    entity_id          UUID,
    occurred_at        TIMESTAMPTZ NOT NULL,
    source             VARCHAR(30) NOT NULL,
    event_type         VARCHAR(50) NOT NULL,
    source_record_id   VARCHAR(255) NOT NULL,
    source_fingerprint CHAR(64) NOT NULL,
    text_sha1          CHAR(40) NOT NULL,
    canonical_text     TEXT NOT NULL,
    language_code      TEXT,
    language_confidence REAL,
    vader_compound     REAL,
    vader_pos          REAL,
    vader_neu          REAL,
    vader_neg          REAL,
    afinn_score        REAL,
    nrc_emotions       JSONB NOT NULL DEFAULT '{}',
    sentiment_label    TEXT,
    sentiment_confidence REAL,
    sentiment_flags    JSONB NOT NULL DEFAULT '{}',
    search_vector      TSVECTOR,
    selected_metadata  JSONB NOT NULL DEFAULT '{}',
    token_count        INTEGER NOT NULL DEFAULT 0,
    char_count         INTEGER NOT NULL DEFAULT 0,
    emoji_count        INTEGER NOT NULL DEFAULT 0,
    mention_count      INTEGER NOT NULL DEFAULT 0,
    hashtag_count      INTEGER NOT NULL DEFAULT 0,
    url_count          INTEGER NOT NULL DEFAULT 0,
    domain_count       INTEGER NOT NULL DEFAULT 0,
    flags              JSONB NOT NULL DEFAULT '{}',
    method_versions    JSONB NOT NULL DEFAULT '{}',
    processed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE timeline_text_features ADD COLUMN IF NOT EXISTS language_code TEXT;
ALTER TABLE timeline_text_features ADD COLUMN IF NOT EXISTS language_confidence REAL;
ALTER TABLE timeline_text_features ADD COLUMN IF NOT EXISTS vader_compound REAL;
ALTER TABLE timeline_text_features ADD COLUMN IF NOT EXISTS vader_pos REAL;
ALTER TABLE timeline_text_features ADD COLUMN IF NOT EXISTS vader_neu REAL;
ALTER TABLE timeline_text_features ADD COLUMN IF NOT EXISTS vader_neg REAL;
ALTER TABLE timeline_text_features ADD COLUMN IF NOT EXISTS afinn_score REAL;
ALTER TABLE timeline_text_features ADD COLUMN IF NOT EXISTS nrc_emotions JSONB NOT NULL DEFAULT '{}';
ALTER TABLE timeline_text_features ADD COLUMN IF NOT EXISTS sentiment_label TEXT;
ALTER TABLE timeline_text_features ADD COLUMN IF NOT EXISTS sentiment_confidence REAL;
ALTER TABLE timeline_text_features ADD COLUMN IF NOT EXISTS sentiment_flags JSONB NOT NULL DEFAULT '{}';
ALTER TABLE timeline_text_features ADD COLUMN IF NOT EXISTS search_vector TSVECTOR;
CREATE INDEX IF NOT EXISTS idx_timeline_text_entity_time
    ON timeline_text_features(entity_id, occurred_at DESC)
    WHERE entity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_timeline_text_source_time
    ON timeline_text_features(source, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_timeline_text_sha1
    ON timeline_text_features(text_sha1);
CREATE INDEX IF NOT EXISTS idx_timeline_text_source_fingerprint
    ON timeline_text_features(source_fingerprint);
CREATE INDEX IF NOT EXISTS idx_timeline_text_fts
    ON timeline_text_features USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_timeline_text_sentiment_time
    ON timeline_text_features(sentiment_label, occurred_at DESC)
    WHERE sentiment_label IS NOT NULL;

CREATE TABLE IF NOT EXISTS timeline_language_profiles (
    event_id                 UUID PRIMARY KEY,
    primary_language         TEXT NOT NULL,
    primary_confidence       REAL NOT NULL DEFAULT 0,
    language_candidates_json JSONB NOT NULL DEFAULT '[]',
    code_mixed               BOOLEAN NOT NULL DEFAULT FALSE,
    flags                    JSONB NOT NULL DEFAULT '{}',
    detector                 TEXT NOT NULL,
    detector_version         TEXT NOT NULL,
    processed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_timeline_language_profiles_language
    ON timeline_language_profiles(primary_language, processed_at DESC);
CREATE INDEX IF NOT EXISTS idx_timeline_language_profiles_code_mixed
    ON timeline_language_profiles(code_mixed, processed_at DESC)
    WHERE code_mixed;

CREATE TABLE IF NOT EXISTS timeline_translations (
    event_id            UUID NOT NULL,
    source_language     TEXT NOT NULL,
    target_language     TEXT NOT NULL DEFAULT 'en',
    translated_text     TEXT,
    translator          TEXT NOT NULL,
    translator_version  TEXT NOT NULL,
    confidence          REAL,
    status              TEXT NOT NULL DEFAULT 'pending',
    error               TEXT,
    text_sha1           CHAR(40),
    metadata            JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (event_id, target_language, translator_version)
);
CREATE INDEX IF NOT EXISTS idx_timeline_translations_status
    ON timeline_translations(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_timeline_translations_language
    ON timeline_translations(source_language, target_language);
CREATE INDEX IF NOT EXISTS idx_timeline_translations_fts
    ON timeline_translations USING GIN(to_tsvector('simple', COALESCE(translated_text, '')))
    WHERE status = 'translated';

CREATE OR REPLACE VIEW timeline_translation_search AS
SELECT ttf.event_id,
       ttf.entity_id,
       ttf.source,
       ttf.occurred_at,
       ttf.canonical_text,
       tr.target_language,
       tr.translated_text,
       tr.translator,
       tr.translator_version,
       (COALESCE(ttf.search_vector, to_tsvector('simple', ttf.canonical_text)) ||
        COALESCE(to_tsvector('simple', tr.translated_text), ''::tsvector)) AS search_vector
FROM timeline_text_features ttf
LEFT JOIN LATERAL (
    SELECT *
    FROM timeline_translations tr
    WHERE tr.event_id = ttf.event_id
      AND tr.target_language = 'en'
      AND tr.status = 'translated'
    ORDER BY tr.updated_at DESC
    LIMIT 1
) tr ON TRUE;

CREATE TABLE IF NOT EXISTS stream_alert_offsets (
    source_name    TEXT NOT NULL,
    cursor_table   TEXT NOT NULL,
    cursor_value   TEXT,
    last_seen_at   TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source_name, cursor_table)
);

CREATE TABLE IF NOT EXISTS alert_fingerprints (
    fingerprint   TEXT PRIMARY KEY,
    alert_type    TEXT NOT NULL,
    entity_id     UUID,
    source        TEXT,
    window_start  TIMESTAMPTZ NOT NULL,
    window_end    TIMESTAMPTZ NOT NULL,
    last_sent_at  TIMESTAMPTZ,
    count         INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending',
    detail        JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_alert_fingerprints_type_window
    ON alert_fingerprints(alert_type, window_end DESC);
CREATE INDEX IF NOT EXISTS idx_alert_fingerprints_entity
    ON alert_fingerprints(entity_id, window_end DESC)
    WHERE entity_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS alert_suppressions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope       TEXT NOT NULL DEFAULT 'manual',
    alert_type  TEXT,
    entity_id   UUID,
    source      TEXT,
    reason      TEXT NOT NULL,
    starts_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ends_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_alert_suppressions_active
    ON alert_suppressions(alert_type, source, starts_at, ends_at);

CREATE TABLE IF NOT EXISTS alert_windows (
    bucket_start  TIMESTAMPTZ NOT NULL,
    bucket_end    TIMESTAMPTZ NOT NULL,
    alert_type    TEXT NOT NULL,
    bucket_key    TEXT NOT NULL,
    entity_id     UUID,
    source        TEXT,
    count         INTEGER NOT NULL DEFAULT 0,
    baseline      REAL,
    metadata      JSONB NOT NULL DEFAULT '{}',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bucket_start, alert_type, bucket_key)
);
CREATE INDEX IF NOT EXISTS idx_alert_windows_type_end
    ON alert_windows(alert_type, bucket_end DESC);

CREATE TABLE IF NOT EXISTS eval_sets (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL UNIQUE,
    task_type    TEXT NOT NULL,
    description  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS eval_items (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    set_id        UUID NOT NULL REFERENCES eval_sets(id) ON DELETE CASCADE,
    input_json    JSONB NOT NULL,
    expected_json JSONB NOT NULL,
    source_ref    TEXT,
    label_source  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_eval_items_set ON eval_items(set_id);
CREATE TABLE IF NOT EXISTS eval_runs (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    set_id                UUID NOT NULL REFERENCES eval_sets(id) ON DELETE CASCADE,
    model_or_rule_version TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'running',
    metrics_json          JSONB NOT NULL DEFAULT '{}',
    started_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_set_started
    ON eval_runs(set_id, started_at DESC);
CREATE TABLE IF NOT EXISTS eval_predictions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    item_id         UUID NOT NULL REFERENCES eval_items(id) ON DELETE CASCADE,
    prediction_json JSONB NOT NULL DEFAULT '{}',
    score_json      JSONB NOT NULL DEFAULT '{}',
    correct         BOOLEAN,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_eval_predictions_run
    ON eval_predictions(run_id);

CREATE TABLE IF NOT EXISTS conversation_threads (
    thread_id          TEXT PRIMARY KEY,
    source             VARCHAR(30) NOT NULL,
    entity_id          UUID REFERENCES entities(id) ON DELETE CASCADE,
    peer_entity_id     UUID REFERENCES entities(id) ON DELETE SET NULL,
    title              TEXT,
    started_at         TIMESTAMPTZ,
    last_message_at    TIMESTAMPTZ,
    message_count      INTEGER NOT NULL DEFAULT 0,
    reply_count        INTEGER NOT NULL DEFAULT 0,
    reaction_count     INTEGER NOT NULL DEFAULT 0,
    forwarded_count    INTEGER NOT NULL DEFAULT 0,
    avg_response_seconds REAL,
    sentiment_summary  JSONB NOT NULL DEFAULT '{}',
    preview            JSONB NOT NULL DEFAULT '[]',
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversation_threads_entity_time
    ON conversation_threads(entity_id, last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_threads_peer
    ON conversation_threads(peer_entity_id, last_message_at DESC)
    WHERE peer_entity_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS conversation_participant_metrics (
    thread_id          TEXT NOT NULL REFERENCES conversation_threads(thread_id) ON DELETE CASCADE,
    entity_id          UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    source             VARCHAR(30) NOT NULL,
    message_count      INTEGER NOT NULL DEFAULT 0,
    reply_count        INTEGER NOT NULL DEFAULT 0,
    reaction_count     INTEGER NOT NULL DEFAULT 0,
    avg_response_seconds REAL,
    sentiment_summary  JSONB NOT NULL DEFAULT '{}',
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (thread_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_conversation_participant_entity
    ON conversation_participant_metrics(entity_id, updated_at DESC);

-- Axis-1 MVP: sentence embeddings for semantic timeline search. Lives in a
-- SIDE TABLE (not on timeline_events itself) because pgvector HNSW cannot be
-- built on partitioned parent tables (timeline_events is monthly-partitioned
-- via migrations/001_partition_timeline_events.sql). Cascade-cleanup via a
-- FK cannot enforce across the partition; the embed phase re-syncs on each run.
CREATE TABLE IF NOT EXISTS timeline_embeddings (
    event_id     UUID PRIMARY KEY,
    occurred_at  TIMESTAMPTZ NOT NULL,
    entity_id    UUID,
    source       VARCHAR(30),
    embedding    vector(384) NOT NULL,
    model        VARCHAR(64) NOT NULL,
    text_sha1    VARCHAR(40) NOT NULL,  -- so re-embed detects text change
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_timeline_emb_hnsw
    ON timeline_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_timeline_emb_entity ON timeline_embeddings(entity_id);
CREATE INDEX IF NOT EXISTS idx_timeline_emb_occurred ON timeline_embeddings(occurred_at DESC);

-- Face social graph (2026-07-08). For each entity A that posts a media_item
-- with >=2 detected faces and whose primary face is among them, the OTHER
-- detected faces are stored here as A's "associates" (friends/co-appearing
-- people). At scoring time the `social_face_link` builder emits an identity
-- signal whenever entity B's primary face matches one of A's associates at
-- cosine >= 0.55 — either B is in A's social circle, or B is A viewed via a
-- friend's photo.
--
-- Cross-schema-by-value: associated_face_id references facetracker.faces.id by
-- value (no FK, so facetracker can be rebuilt without breaking analyzer rows);
-- media_item_id references unifiedcollector.media_items.id by value. UNIQUE
-- guards idempotent inserts across repeated pipeline passes.
CREATE TABLE IF NOT EXISTS face_associations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    associated_face_id INTEGER NOT NULL,
    media_item_id TEXT NOT NULL,
    source_platform VARCHAR(30),
    quality_score FLOAT,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (entity_id, associated_face_id, media_item_id)
);
CREATE INDEX IF NOT EXISTS idx_face_assoc_entity ON face_associations(entity_id);
CREATE INDEX IF NOT EXISTS idx_face_assoc_face ON face_associations(associated_face_id);

-- Face social graph: the entity's "primary face" — the highest-quality face
-- resolved via method='media_attribution' from a profile_photo media_item
-- (priority 1), or the largest bridged cluster's representative face
-- (fallback). Set by src/pipeline/face_associations.py; nullable because not
-- every entity has bridged faces yet. Partial index because most entities do
-- not carry a primary_face_id at any given time.
ALTER TABLE entities ADD COLUMN IF NOT EXISTS primary_face_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_entities_primary_face
    ON entities(primary_face_id) WHERE primary_face_id IS NOT NULL;

-- Per-entity active date-range, maintained by the timeline pipeline. timeline_events
-- has 373 monthly partitions but is queried by entity_id (not the partition key), so
-- an unbounded per-entity query MergeAppends ALL partitions (~6.6s). Bounding a query
-- to the entity's own [first_event_at, last_event_at] lets Postgres partition-prune
-- to just that entity's active months — fast for everyone, no global time window.
ALTER TABLE entities ADD COLUMN IF NOT EXISTS first_event_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS last_event_at TIMESTAMP WITH TIME ZONE;
