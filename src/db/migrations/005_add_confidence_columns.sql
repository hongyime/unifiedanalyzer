-- Fudan pattern: quarantine low-confidence NLP output
-- Add confidence + evidence_excerpt to derived tables so uncertain
-- results are flagged for review instead of inserted as fact.

-- Sentiment/emotion results
ALTER TABLE IF EXISTS sentiment_results
    ADD COLUMN IF NOT EXISTS confidence FLOAT DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS evidence_excerpt TEXT;

-- Behavioral profiles
ALTER TABLE IF EXISTS behavioral_profiles
    ADD COLUMN IF NOT EXISTS confidence FLOAT DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS evidence_excerpt TEXT;

-- Timeline text features (if exists)
ALTER TABLE IF EXISTS timeline_text_features
    ADD COLUMN IF NOT EXISTS confidence FLOAT DEFAULT 1.0;

COMMENT ON COLUMN sentiment_results.confidence IS 'Fudan-pattern: 0.0-1.0, flag <0.5 for human review';
