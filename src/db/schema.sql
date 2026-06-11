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
CREATE INDEX IF NOT EXISTS idx_timeline_time ON timeline_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_timeline_source ON timeline_events(source);

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
