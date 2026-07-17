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
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action  ON audit_log(action);

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

-- P2-3 (identity_system_review_plan.md): per-phase run status. Every secondary
-- pipeline phase is non-fatal (try/except), so a persistently failing step was
-- invisible. One row per (run, phase) records ok/failed + duration, enabling a
-- repeated-failure alert and latency visibility.
CREATE TABLE IF NOT EXISTS run_phase_status (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID,
    run_type    VARCHAR(30),
    phase       VARCHAR(64) NOT NULL,
    status      VARCHAR(20) NOT NULL,   -- ok | failed
    duration_ms INTEGER,
    error       TEXT,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_run_phase_status_phase ON run_phase_status(phase, created_at DESC);

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
