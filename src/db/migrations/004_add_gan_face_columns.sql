-- 004: GAN synthetic-avatar columns on entity_faces (one-shot for the live DB).
--
-- entity_faces is analyzer-owned. This DDL previously lived in the COLLECTOR
-- repo (20260930_add_gan_face_columns.sql) and ran ALTER TABLE entity_faces
-- against the collector DB connection, which fails on a clean Postgres split.
-- It has been archived there and moved here (2026-10-02).
--
-- Fresh analyzer DBs get these columns from src/db/schema.sql on boot. This
-- file exists only to apply the same DDL to the already-live unifiedanalyzer
-- database before the instance split. apply_schema() does NOT glob this dir;
-- run it by hand (see .agents/STATE.md for the exact pgvector-image command).
--
-- Idempotent: safe to re-run.
--
-- GAN ONNX sha256: 5a28be5f56b576f524a8ae3a67e4909b237bdb82b60a71a03d30279e9d283888
ALTER TABLE entity_faces
    ADD COLUMN IF NOT EXISTS gan_score REAL NULL,
    ADD COLUMN IF NOT EXISTS gan_model_version TEXT NULL,
    ADD COLUMN IF NOT EXISTS gan_scored_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_entity_faces_gan_score_high
    ON entity_faces(gan_score DESC)
    WHERE gan_score IS NOT NULL AND gan_score > 0.5;

CREATE TABLE IF NOT EXISTS face_gan_overrides (
    face_id       INTEGER     PRIMARY KEY,
    is_synthetic  BOOLEAN     NOT NULL,
    reviewed_by   TEXT        NULL,
    reviewed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    note          TEXT        NULL
);
