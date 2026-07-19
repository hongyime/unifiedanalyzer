-- Speed up analyzer dashboard media stats and browse queries.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_analysis_processed_at
    ON media_analysis (processed_at DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_analysis_type_processed
    ON media_analysis (analysis_type, processed_at DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_analysis_source_processed
    ON media_analysis (source, processed_at DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_analysis_content_processed
    ON media_analysis (content_type, processed_at DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_analysis_has_text
    ON media_analysis (id) WHERE extracted_text IS NOT NULL AND extracted_text <> '';
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_analysis_has_face
    ON media_analysis (id) WHERE face_embedding IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_analysis_has_gps
    ON media_analysis (id) WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_analysis_is_derived
    ON media_analysis (id) WHERE parent_media_item_id IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_analysis_has_phash
    ON media_analysis (id) WHERE perceptual_hash IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_faces_quality_desc
    ON facetracker.faces (quality_score DESC NULLS LAST);
