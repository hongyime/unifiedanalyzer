-- P2-5 follow-up (docs/identity_system_review_plan.md): widen timeline_events
-- partition coverage. Migration 001 only created 2018-2027 monthly partitions, so
-- ~2.6M legitimate 2005-2017 rows (old GitHub commits / early posts) landed in the
-- DEFAULT partition. This creates the missing monthly partitions across 2005-2035
-- and moves the default's in-range rows into their proper partitions, leaving only
-- true outliers (far-future dates) in default.
--
-- Run bogus-row cleanup FIRST (DELETE FROM timeline_events WHERE occurred_at <
-- '2005-01-01') so the detached default has no pre-2005 rows to re-route.
--
-- SAFETY: idempotent-ish (skips months whose partition already exists), atomic
-- (one DO block). Holds ACCESS EXCLUSIVE during the ~2.6M-row re-insert — run with
-- the scheduler stopped, via psql (the app pool's 300s command_timeout would
-- cancel it). See migration 001's header for the exact procedure.
DO $$
DECLARE
    m date;
BEGIN
    IF to_regclass('public.timeline_events_default') IS NULL THEN
        RAISE EXCEPTION 'timeline_events_default not found — is timeline_events partitioned?';
    END IF;

    -- Detach default so partitions can be created for ranges it currently holds.
    ALTER TABLE timeline_events DETACH PARTITION timeline_events_default;

    -- Create every monthly partition 2005-01 .. 2035-12 that doesn't yet exist.
    FOR m IN SELECT generate_series('2005-01-01'::date, '2035-12-01'::date, '1 month')::date
    LOOP
        IF to_regclass('public.timeline_events_' || to_char(m, 'YYYY_MM')) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF timeline_events FOR VALUES FROM (%L) TO (%L)',
                'timeline_events_' || to_char(m, 'YYYY_MM'), m, (m + interval '1 month')::date
            );
        END IF;
    END LOOP;

    -- Fresh empty default for genuine outliers (dates outside 2005-2035).
    CREATE TABLE timeline_events_default2 PARTITION OF timeline_events DEFAULT;

    -- Re-route the old default's rows into the parent (2005-2035 -> monthly,
    -- anything else -> the new default). Same rows/ids, so no unique conflicts.
    INSERT INTO timeline_events
        SELECT * FROM timeline_events_default;

    DROP TABLE timeline_events_default;
    ALTER TABLE timeline_events_default2 RENAME TO timeline_events_default;

    RAISE NOTICE 'timeline partitions widened to 2005-2035; default now holds only true outliers';
END $$;
