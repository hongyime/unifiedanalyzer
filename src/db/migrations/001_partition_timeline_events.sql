-- P2-5 (docs/identity_system_review_plan.md): convert timeline_events to a
-- monthly RANGE-partitioned table (partition key: occurred_at).
--
-- REQUIRES the matching build_timeline change (ON CONFLICT includes occurred_at)
-- to be deployed — a month-partitioned table can only have unique keys that
-- include the partition column. See src/pipeline/timeline_builder.py.
--
-- SAFETY / OPERATION:
--   * Idempotent: no-op if timeline_events is already partitioned.
--   * Atomic: runs in one transaction (DO block); any failure rolls back to the
--     original table — nothing is left half-migrated.
--   * Holds ACCESS EXCLUSIVE on timeline_events for the duration (several minutes
--     on ~6M rows). Run with NO pipeline writers active:
--       docker compose stop scheduler
--       docker exec -i <postgres> psql -U collector -d unifiedanalyzer \
--         < src/db/migrations/001_partition_timeline_events.sql
--       docker compose start scheduler
--   * "load then index": data is copied FIRST, then the PK/unique/secondary
--     indexes and FK are built in bulk — far faster than maintaining indexes
--     during 6M individual inserts.
--   * The original table is kept as timeline_events_old (backup). DROP it
--     manually once the partitioned table is verified:
--       DROP TABLE timeline_events_old;
--
-- Data spans bogus timestamps (1970 epoch rows, far-future 2042 rows), so a
-- DEFAULT partition catches everything outside the monthly window.
DO $$
DECLARE
    is_part boolean;
    m date;
    r record;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_partitioned_table pt
        JOIN pg_class c ON c.oid = pt.partrelid
        WHERE c.relname = 'timeline_events'
    ) INTO is_part;
    IF is_part THEN
        RAISE NOTICE 'timeline_events already partitioned — nothing to do';
        RETURN;
    END IF;

    ALTER TABLE timeline_events RENAME TO timeline_events_old;

    -- Index NAMES are schema-global, so the renamed backup table still holds the
    -- canonical names (idx_timeline_*, timeline_events_pkey, the 3-col unique).
    -- Free them (suffix _old) so the new partitioned table can reuse the exact
    -- names schema.sql's CREATE INDEX IF NOT EXISTS expects (else startup would
    -- rebuild duplicates on the partitioned table).
    FOR r IN SELECT indexname FROM pg_indexes WHERE tablename = 'timeline_events_old'
    LOOP
        EXECUTE format('ALTER INDEX %I RENAME TO %I', r.indexname, r.indexname || '_old');
    END LOOP;

    -- Parent table (no indexes yet — built after load).
    CREATE TABLE timeline_events (
        id               UUID DEFAULT gen_random_uuid(),
        entity_id        UUID,
        source           VARCHAR(30) NOT NULL,
        event_type       VARCHAR(50) NOT NULL,
        source_record_id VARCHAR(255) NOT NULL,
        occurred_at      TIMESTAMP WITH TIME ZONE NOT NULL,
        title            TEXT,
        detail           TEXT,
        metadata         JSONB DEFAULT '{}',
        created_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    ) PARTITION BY RANGE (occurred_at);

    -- Monthly partitions across a realistic window (2018-01 .. 2027-12).
    FOR m IN
        SELECT generate_series('2018-01-01'::date, '2027-12-01'::date, '1 month')::date
    LOOP
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF timeline_events FOR VALUES FROM (%L) TO (%L)',
            'timeline_events_' || to_char(m, 'YYYY_MM'),
            m, (m + interval '1 month')::date
        );
    END LOOP;

    -- Catch-all for out-of-range/bogus dates (1970 epoch, 2042, etc.).
    CREATE TABLE timeline_events_default PARTITION OF timeline_events DEFAULT;

    -- Copy the data FIRST (no index maintenance during the bulk load).
    INSERT INTO timeline_events
        (id, entity_id, source, event_type, source_record_id, occurred_at,
         title, detail, metadata, created_at)
    SELECT id, entity_id, source, event_type, source_record_id, occurred_at,
           title, detail, metadata, created_at
    FROM timeline_events_old;

    -- Now build keys/indexes in bulk (partition key must be in every unique key).
    ALTER TABLE timeline_events ADD PRIMARY KEY (id, occurred_at);
    ALTER TABLE timeline_events
        ADD CONSTRAINT timeline_events_uniq4
        UNIQUE (source, event_type, source_record_id, occurred_at);
    CREATE INDEX idx_timeline_entity_time ON timeline_events (entity_id, occurred_at DESC);
    CREATE INDEX idx_timeline_time        ON timeline_events (occurred_at DESC);
    CREATE INDEX idx_timeline_source      ON timeline_events (source);

    -- Restore the entity FK (ON DELETE SET NULL, same as the original).
    ALTER TABLE timeline_events
        ADD CONSTRAINT timeline_events_entity_id_fkey
        FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE SET NULL;

    RAISE NOTICE 'timeline_events partitioned; original kept as timeline_events_old (drop after verifying)';
END $$;
