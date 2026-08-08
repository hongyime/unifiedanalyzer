# OSINT NLP, Monitoring, Face-Linking, and Location Intelligence Plan

Date updated: 2026-08-08

Status: implementation started. Treat only checklist items explicitly marked `[x]`
below as implemented; all other sections remain planning guidance until a later
commit adds code, tests, and live verification.

## Scope

This is the expanded Gemini plan for `unifiedanalyzer`. It incorporates the requested classical/non-AI NLP toolkit, and it also extends the plan into monitoring, alerting, face-linking, and location-inference paths.

The goal is not to add isolated algorithms. The goal is to make `unifiedanalyzer` a stronger subject profiler:

- richer timeline text intelligence,
- better search and topic discovery,
- lower-noise alerts,
- debuggable pipeline health,
- more reliable face-to-entity links,
- explainable location evidence,
- and frontend surfaces that let an analyst inspect why the system believes something.

## Current Repo Anchors

The plan should be implemented against the current system shape:

- `src/pipeline/incremental_runner.py` records per-phase status in `run_phase_status`, runs `run_alerts()`, then runs a long secondary phase chain.
- `src/pipeline/alert_engine.py` already emits `SILENCE_GAP`, `NEW_ACTIVITY_AFTER_SILENCE`, `PROFILE_CHANGE`, `NEW_IDENTITY_LINK`, `COORDINATED_POSTING`, and `LOCATION_MISMATCH`.
- `src/scheduler/scheduler.py` already builds status/digest summaries, checks collector health, reports failing phases, and sends Telegram notifications.
- `src/api/routes/health.py`, `src/api/routes/metrics.py`, `src/api/routes/collector_health.py`, and websocket health routes already provide a dashboard foundation.
- `src/api/routes/search.py` exposes dense timeline search over `timeline_embeddings`, but the search stack is not yet hybrid sparse+dense.
- `src/pipeline/timeline_embedder.py` embeds `timeline_events.title` into `timeline_embeddings`; this is useful but too narrow for social search.
- `src/pipeline/topical_similarity.py` is a weak identity-context signal, not a true topic model, and its own comments warn about O(E^2) scaling.
- `src/pipeline/bio_nlp.py`, `src/pipeline/contact_extraction.py`, `src/pipeline/entity_enrichment.py`, and `src/pipeline/content_fingerprint.py` already provide partial deterministic NLP patterns.
- `src/face_worker.py`, `src/face/*`, `src/api/routes/face_search.py`, `src/api/face_mount.py`, `src/pipeline/face_associations.py`, `src/pipeline/social_face_link.py`, `src/pipeline/face_pair_signals.py`, and `src/pipeline/media_analysis_tier1.py` are the face-linking spine.
- `public.entity_faces`, `face_associations`, and `facetracker.faces` are the key bridge tables between face detections and analyzer entities.
- `src/pipeline/location_evidence.py`, `src/pipeline/location_inference.py`, `src/pipeline/route_similarity.py`, `src/api/routes/intersections.py`, `src/api/routes/graph.py`, and `frontend/src/components/GeoMap.tsx` are the location and overlap spine.
- `location_evidence` has review states, but no first-class spatial index strategy is visible in the base schema.
- `/entities/{entity_id}/geo` still reads live collector-derived Strava/IG/Telegram/WhatsApp/EXIF data and then upserts/hides evidence; `/intersect` still does bounded point matching in Python with Haversine. Those are good correctness baselines, but they need read models and debug views as entity/location volume grows.
- `frontend/src/pages/Alerts.tsx`, `frontend/src/pages/Runs.tsx`, `frontend/src/pages/Faces.tsx`, `frontend/src/pages/Media.tsx`, `frontend/src/pages/Triage.tsx`, and `frontend/src/pages/EntityDetail.tsx` are the surfaces that should expose the new intelligence.

## Design Principles

1. Use CPU-cheap, deterministic methods first.
2. Store continuous scores and provenance, not single truth labels.
3. Treat sentiment, topics, temporal overlap, and face similarity as context unless stronger identity evidence exists.
4. Prefer derived side tables over unbounded JSONB growth inside hot tables.
5. Make every alert actionable, deduped, source-health-aware, and explainable.
6. Make every face and location inference reviewable and reversible.
7. Add measurement before adding heavier models.
8. Keep analyzer/collector boundaries intact: analyzer can prioritize and report collector gaps, but should not silently own collector scheduling.

## Priority Backlog

| Priority | Area | Improvement | Why | Likely files/tables | Verification |
|---|---|---|---|---|---|
| P0 | Monitoring | Add pipeline coverage snapshots | Current phase status says pass/fail/duration, but not what each phase processed, skipped, or failed to attribute | `run_phase_status`, new `pipeline_coverage_snapshots`, `incremental_runner.py` | Implemented 2026-08-08: `_run_phase()` writes per-phase/per-source snapshots and `/api/runs/{run_id}/coverage` reports processed/attributed/unresolved/skipped/error counts |
| P0 | Monitoring | Expose run phases as first-class API/UI data | `run_phase_status` exists, but operators need a run waterfall, latest completed phase, active phase, and stale heartbeat view | `analysis_runs`, `run_phase_status`, `alerts.py` or run routes, `Runs.tsx`, websocket health | `/api/runs/{id}/phases`, heartbeat-age display, stale-threshold fixtures |
| P0 | Search/NLP | Add canonical searchable text | `timeline_embeddings` currently embeds title-level text; social search needs title + detail + selected metadata | `timeline_events`, new `timeline_text_features`, `timeline_embedder.py`, `search.py` | Initial slice implemented 2026-08-08: added analyzer-owned `timeline_text_features`, `text_normalizer.py`, `lexical_nlp` phase, embedding-seeded bounded backfill command, and tests for collector-derived text/provenance. Full timeline cursor/index expansion remains before FTS rollout. |
| P0 | Search/NLP | Add sparse FTS and hybrid RRF | Dense-only search misses exact handles, URLs, usernames, hashtags, and IDs | `schema.sql`, `search.py`, frontend search page | Recall@20/MRR eval set, `EXPLAIN`, p95 latency |
| P0 | Face-linking | Add face-link audit dashboard | Face acceptance depends on bridge coverage, ANN recall, label coverage, junk filtering, and FAISS/pgvector drift | `face_search.py`, `src/face/api/routes/stats.py`, `Faces.tsx`, `Triage.tsx` | Exact-vs-index sample, drift counts, labeled hit-rate trend |
| P0 | Face-linking | Add face search index debug payload | `/api/faces/search` should prove catalog/index/planner health, not only report intended index metadata | `face_search.py`, `facetracker.faces`, `idx_faces_embedding_vec_ivfflat` | `pg_indexes`, partial predicate, `EXPLAIN JSON`, exact-vs-IVFFlat recall@20 |
| P0 | Location | Add location evidence quality report | Current location intelligence needs visibility into source, precision, review state, rejection rate, and public-place fan-out | `location_evidence`, `location_inference.py`, `intersections.py`, `GeoMap.tsx` | Counts by source/type/status, rejected points excluded from overlaps |
| P1 | Alerting | Add alert decision ledger | Alerts should explain trigger window, baseline, suppression reason, source health, duplicate key, and confidence | `alerts.detail`, `alert_engine.py`, `Alerts.tsx` | Snapshot tests per alert type, no duplicate alerts for same event/window |
| P1 | NLP | Add VADER/AFINN/NRC sidecar | Cheap sentiment/emotion features unlock emotional spikes and relationship tone | new `sentiment_emotion.py`, `timeline_text_features`, `entity_interactions.metadata` | Fixtures for negation, emoji, slang, ALL CAPS, sarcasm flags |
| P1 | NLP | Add language routing and normalization | VADER is English/social tuned; code-switching and multilingual text must not be scored blindly | new `text_normalizer.py`, `bio_nlp.py` reuse | Fixtures for English, code-mix, non-Latin, emoji, URLs, mentions |
| P1 | Location | Add spatial index strategy | Intersections and proximity should not depend on Python-side scans as data grows | `location_evidence`, new geo read model, optional PostGIS/H3 | `EXPLAIN`, p95 `/geo` and `/intersect`, overlap correctness tests |
| P1 | Location | Add geo debug endpoints | Analysts need to see source counts, suppression reasons, collector fallback, read-model age, and query timings | `graph.py`, `intersections.py`, `location_evidence.py` | `/entities/{id}/geo/debug` and `/entities/intersect/debug` fixtures |
| P1 | Face-linking | Add face bridge review queues | Unlinked faces, contested clusters, low-quality detections, and high-similarity cross-entity matches need one workflow | `Review.tsx`, `Faces.tsx`, `entity_actions.py`, face routes | Review action tests, audit log entries, rerun-safe suppression |
| P2 | NLP | Add keyphrase and deterministic entity extraction | RAKE/YAKE/TextRank and spaCy rules can extract indicators without GPU | `contact_extraction.py`, `entity_enrichment.py`, new `keyphrase_entity.py` | Pattern fixtures, false-positive sample review |
| P2 | NLP | Add MinHash/SimHash dedup | Reposts and forwarded content inflate topics, bursts, and relationships | new `text_dedup.py`, `timeline_text_features`, `content_fingerprint.py` | Known duplicate clusters, exact Jaccard verification |
| P2 | Topics | Add short-text topics | Chat/tweet text is sparse; classic LDA alone is weak | new `short_text_topics.py`, `topic_clusters`, `timeline_event_topics` | Topic coherence sample, stability across seeds |
| P2 | Topics | Add streaming story clustering | Real-time story detection needs incremental centroids or leader-follower clustering before heavier HDBSCAN/UMAP batches | `short_text_topics.py`, `topic_clusters`, `alerts`, search/topic APIs | Cluster stability, duplicate-adjusted story counts, bounded memory |
| P2 | Chat | Add conversation/thread analytics | Chat data needs reply-chain, turn-taking, latency, and participant-balance metrics; per-message NLP is too noisy alone | `interaction_graph.py`, `timeline_builder.py`, new `conversation_analytics.py` | Fixture threads, latency histograms, participant graph checks |
| P2 | Alerts | Add duplicate-aware trend/burst alerts | Spiking terms/hashtags/locations/emotions should account for repost storms and collector outages | `alert_engine.py`, new `burst_detection.py`, `alerts` | Synthetic bursts, collection-gap suppression, dedup-aware counts |
| P2 | Graph | Add community and influence metrics | Mention/reply/retweet/comment graphs need communities, bridges, centrality, and watchlist-aware influence views | `graph_analytics.py`, `entity_interactions`, `entity_relationships`, graph API/UI | Leiden/Louvain stability, PageRank/betweenness samples, hub guard |
| P2 | Monitoring | Add phase resource classes and timeout policy | Heavy OCR/face/embedding phases should not hide identity/timeline freshness problems | `incremental_runner.py`, scheduler status APIs | Heartbeat under load, phase timeout tests |
| P3 | Storage/Search | Add explicit engine decision matrix | Postgres is current production truth, but SQLite FTS5, DuckDB, Meilisearch, Typesense, OpenSearch, ClickHouse, and TimescaleDB each fit different sidecar/prototype roles | docs, optional export scripts, search eval harness | No second source of truth without sync plan |
| P3 | UX | Add fused evidence inspector | Analysts need one drawer for timeline events, map points, face hits, graph edges, alerts, and topics | `EntityDetail.tsx`, new `EvidenceInspector.tsx` | Frontend build, click-through tests |

## 1. Classical Sentiment and Emotion Analysis

### Recommended First Implementation

Start with a small ensemble:

- VADER for English social/chat text.
- AFINN as a tiny corroborating valence list.
- NRC EmoLex for emotion distributions.
- TextBlob/Pattern subjectivity as optional context.

Do not start with LIWC or SO-CAL in the hot path. LIWC is licensed/proprietary. SO-CAL is useful but may bring heavier parser/runtime assumptions. Use them only as optional batch validators after the basic sidecar works.

### Why VADER Fits

VADER is built for social text. It includes a validated lexicon and rules for negation, intensity, ALL CAPS, punctuation emphasis, emoticons, emoji, slang, and contrastive conjunctions. That fits Telegram, WhatsApp, Instagram captions/comments, TikTok/YouTube comments, and short bios better than generic polarity tools.

Repo fit:

- `timeline_events.title`, `timeline_events.detail`, and selected JSONB metadata can supply event text.
- `entity_interactions.metadata` can store actor-to-target tone for replies, mentions, comments, reactions, DMs, tags, and co-appearance context.
- `behavioral_profiles.metadata` can store aggregate baselines such as rolling valence mean/stddev and emotion distributions.
- `alert_engine.py` can later add `EMOTIONAL_SPIKE`, but only after the text sidecar runs before alerts or the alert explicitly accepts a one-cycle lag.

### Fields to Store

Prefer a side table, not unbounded JSONB on `timeline_events`:

```text
timeline_text_features
- event_id uuid primary key
- entity_id uuid not null
- occurred_at timestamptz not null
- source text not null
- text_sha1 text not null
- canonical_text text
- language_code text
- language_confidence real
- vader_compound real
- vader_pos real
- vader_neu real
- vader_neg real
- afinn_score real
- nrc_emotions jsonb
- subjectivity real
- flags jsonb
- method_versions jsonb
- processed_at timestamptz
```

For directed interactions:

```json
{
  "emotion": {
    "actor_to_target_compound": 0.72,
    "emotion_dist": {"joy": 0.4, "trust": 0.2},
    "flags": ["english", "emoji_positive"],
    "confidence": 0.68,
    "not_identity_evidence": true
  }
}
```

### Failure Modes and Cheap Fixes

| Failure | Risk | Mitigation |
|---|---|---|
| Sarcasm/irony | Wrong polarity | Flag low confidence; do not flip polarity blindly |
| Domain slang | "sick", "wicked", "long", "short" can invert meaning | Add reviewed domain lexicon overrides; VADER supports lexicon extension |
| Negation scope | Long-distance negation can be missed | Keep neutral dead-band; expose confidence |
| Multilingual/code-switching | English lexicon on non-English text is misleading | Run language ID first; route or skip |
| Emoji ambiguity | Emoji meaning depends on group/source | Track top emojis by source and allow reviewed overrides |
| Very short text | One token can dominate | Require min evidence for alerts; aggregate over windows |

### Alerting Use

Only add `EMOTIONAL_SPIKE` after:

- per-event features exist,
- per-entity baselines exist,
- source health is available,
- near-duplicate clusters are accounted for,
- and sarcasm/code-switch flags can reduce severity.

Candidate alert fields in `alerts.detail`:

```json
{
  "baseline_window_days": 60,
  "event_window_hours": 24,
  "metric": "nrc_anger",
  "baseline_mean": 0.08,
  "baseline_stddev": 0.03,
  "current_value": 0.21,
  "z_score": 4.33,
  "event_count": 18,
  "distinct_sources": 3,
  "duplicate_adjusted_count": 11,
  "source_health_ok": true,
  "confidence": 0.74
}
```

Sources:

- VADER paper: https://ojs.aaai.org/index.php/ICWSM/article/view/14550
- vaderSentiment package: https://pypi.org/project/vaderSentiment/
- AFINN microblog paper: https://arxiv.org/abs/1103.2903
- NRC Emotion Lexicon: https://saifmohammad.com/WebPages/NRC-Emotion-Lexicon.htm
- TextBlob docs: https://textblob.readthedocs.io/en/dev/quickstart.html
- Pattern paper: https://www.jmlr.org/papers/volume13/desmedt12a/desmedt12a.pdf
- SentiWordNet: https://aclanthology.org/L10-1531/
- SO-CAL paper: https://aclanthology.org/J11-2001.pdf
- LIWC overview: https://www.liwc.app/help/howitworks

## 2. Language Detection and Social Text Normalization

Add one shared normalizer before sentiment, topics, search, dedup, keyphrases, and bot heuristics.

Candidate module:

```text
src/pipeline/text_normalizer.py
```

Responsibilities:

- Unicode normalization.
- Preserve raw text and canonical text separately.
- Normalize repeated punctuation and whitespace.
- Count emojis, mentions, hashtags, URLs, domains, phones, and emails.
- Preserve hashtags and handles as tokens for search and entity extraction.
- Detect language at document level.
- Add token-level hints for code-switching when cheap enough.

Do not strip all social tokens. `@handle`, `#tag`, domains, emojis, and URLs are often the evidence.

Candidate fields:

```text
canonical_text
token_count
char_count
emoji_count
url_count
mention_count
hashtag_count
language_code
language_confidence
code_switch_hint
normalizer_version
```

Sources:

- fastText language ID: https://fasttext.cc/docs/en/language-identification.html
- CLD3: https://github.com/google/cld3
- Lingua-py: https://github.com/pemistahl/lingua-py

## 2A. NLP Phase Insertion Points

Add text intelligence as non-fatal secondary phases, following the same operational style as `timeline_embedder.py`.

Candidate phase names near `content_embedding`:

```text
lexical_nlp
sparse_text_index
topic_modeling
text_dedup
```

Rules:

- Return structured `ok/skipped/failed` stats through `_run_phase()`.
- Keep each phase bounded by env caps for backfills.
- Never overwrite previous good scores with a failed recompute unless `text_sha1` changed.
- Track row-level skip/error categories: `unsupported_language`, `empty_text`, `missing_lexicon`, `parse_failed`, `dictionary_license_missing`.
- Track coverage: matched lexicon tokens / eligible tokens. Suppress NLP-driven alerts below a coverage floor.
- Store entity aggregates in `behavioral_profiles.metadata`.
- Store interaction-level emotion in `entity_interactions.metadata`.
- Use side tables for event-level features because `timeline_events` is partition-sensitive.

## 3. Search, BM25, and Hybrid Retrieval

### Current Gap

`src/api/routes/search.py` provides semantic timeline search through `timeline_embeddings`. That is useful for paraphrase, but it is weak for exact OSINT retrieval.

Exact search matters for:

- usernames,
- phone/email fragments,
- URLs/domains,
- hashtags,
- platform IDs,
- stock/ticker/product codes,
- venue names,
- quoted phrases,
- slang,
- short messages.

### Proposed Search Stack

1. Build canonical searchable text from `title`, `detail`, and safe selected metadata.
2. Add Postgres FTS with GIN index as the baseline sparse index.
3. Optionally evaluate true BM25 via ParadeDB/`pg_search` later; do not make this the first dependency.
4. Keep pgvector dense search.
5. Fuse dense and sparse result lists with Reciprocal Rank Fusion.
6. Add snippets, matched fields, source/date filters, and result click-through.

Candidate API:

```text
GET /api/search/timeline?mode=keyword
GET /api/search/timeline?mode=semantic
GET /api/search/timeline?mode=hybrid
```

Candidate result shape:

```json
{
  "event_id": "...",
  "entity_id": "...",
  "occurred_at": "...",
  "source": "telegram",
  "sparse_rank": 3,
  "dense_rank": 24,
  "rrf_score": 0.041,
  "matched_fields": ["detail", "metadata.caption"],
  "snippet": "..."
}
```

### BM25 Notes

BM25 improves on plain TF-IDF by saturating term frequency and normalizing document length. Lucene documents the `k1` parameter as term-frequency saturation and `b` as document-length normalization. Native Postgres `ts_rank`/`ts_rank_cd` is not the same as Okapi BM25, so this plan should be precise about which ranker is active.

Sources:

- Lucene BM25Similarity: https://lucene.apache.org/core/9_9_1/core/org/apache/lucene/search/similarities/BM25Similarity.html
- Stanford IR book on BM25: https://nlp.stanford.edu/IR-book/html/htmledition/okapi-bm25-a-non-binary-model-1.html
- PostgreSQL text search: https://www.postgresql.org/docs/current/textsearch-controls.html
- PostgreSQL GIN: https://www.postgresql.org/docs/current/gin.html
- pgvector: https://github.com/pgvector/pgvector

## 4. Topic Modeling and Classification

### Do Not Start With Heavy BERTopic Everywhere

The repo already has `timeline_embeddings`, `hdbscan`, and `umap-learn`, but the safe path is to measure first and keep heavy clustering out of the scheduler hot path.

Recommended layers:

1. Rule/seed taxonomy for obvious OSINT buckets.
2. `HashingVectorizer` or TF-IDF with lightweight classifier for known labels.
3. NMF or LSA on aggregated entity/source/week documents.
4. GSDMM or BTM for short chat/social texts.
5. BERTopic-style c-TF-IDF offline, using existing embeddings or static CPU embeddings.
6. Top2Vec only as exploratory offline analysis.

Classic library options:

- scikit-learn is the safest first choice because it is already present and covers TF-IDF, LSA via `TruncatedSVD`, NMF, LDA, MiniBatchKMeans, and classifiers.
- gensim is useful for LDA/LSI if its model APIs are preferred for experiments.
- tomotopy can be evaluated for faster topic-model experiments, but it adds a dependency to justify.
- MALLET remains a strong classic LDA baseline, but Java/runtime packaging makes it an offline benchmark rather than a scheduler dependency.

### Taxonomy Seeds

Start with buckets that map to existing product needs:

- identity clue,
- location clue,
- relationship clue,
- work/school,
- family,
- finance,
- travel,
- health/fitness,
- threat/risk,
- bot/spam,
- media/face clue,
- collector gap.

### Repo Integration

Tables:

```text
topic_seed_sets
topic_model_runs
topic_clusters
timeline_event_topics
entity_topic_profiles
```

Pipeline:

```text
src/pipeline/timeline_text_features.py
src/pipeline/topic_taxonomy.py
src/pipeline/short_text_topics.py
```

API/UI:

```text
src/api/routes/search.py
src/api/routes/topics.py
frontend/src/pages/Search.tsx
frontend/src/pages/EntityDetail.tsx
```

Verification:

- topic coherence sample,
- seed sensitivity report,
- cluster stability across seeds,
- phase runtime/RSS ceiling,
- before/after false positives for `topical_similarity`.

Sources:

- scikit-learn NMF/LDA example: https://scikit-learn.org/stable/auto_examples/applications/plot_topics_extraction_with_nmf_lda.html
- GSDMM paper: https://dbgroup.cs.tsinghua.edu.cn/wangjy/papers/KDD14-GSDMM.pdf
- BTM paper: https://xiaohuiyan.github.io/paper/BTM-WWW13.pdf
- BERTopic c-TF-IDF docs: https://maartengr.github.io/BERTopic/getting_started/ctfidf/ctfidf.html
- BERTopic embeddings docs: https://maartengr.github.io/BERTopic/getting_started/embeddings/embeddings.html
- Top2Vec: https://github.com/ddangelov/top2vec

## 4A. Clustering and Real-Time Story Detection

The topic section covers model families, but the repo also needs explicit clustering choices for story detection and alerting.

### Recommended Clustering Layers

| Layer | Method | Fit | Repo Use |
|---|---|---|---|
| Online story detection | leader-follower / single-pass thresholded cosine | cheap, streaming, explainable | live "new story" detection for watched entities and sources |
| Incremental topic buckets | MiniBatchKMeans | streaming-friendly, bounded memory | rolling topic/story centroids from `timeline_text_features` |
| Dense exploratory clusters | HDBSCAN | finds cluster count and noise | offline review of dense embeddings or sampled events |
| Hierarchical review | agglomerative clustering | interpretable dendrogram-ish grouping | small case/corpus analysis |
| BERTopic-style clusters | UMAP + HDBSCAN + c-TF-IDF | strong exploratory labels | offline/sampled topic reports only |

### Repo-Specific Design

Add a bounded story table:

```text
story_clusters
- story_id
- method
- label
- centroid_vector_ref
- top_terms_json
- source_mix_json
- entity_count
- event_count
- duplicate_adjusted_event_count
- first_seen_at
- last_seen_at
- status
- created_at
- updated_at
```

Add event membership:

```text
timeline_event_story_membership
- event_id
- story_id
- score
- method
- text_sha1
- duplicate_cluster_id
- created_at
```

Rules:

- Collapse near-duplicates before calling something a trend, unless coordinated reposting is the signal.
- For live use, prefer incremental centroid assignment over full re-clustering.
- Run HDBSCAN/UMAP only on samples, centroids, or bounded case exports.
- Include source/entity diversity in every trend/story score.

Verification:

- synthetic streaming events should form stable clusters without a full-corpus rebuild,
- duplicate-heavy repost clusters should not look like broad organic stories,
- memory should stay bounded with a fixed centroid limit,
- story labels should be reproducible from top terms and examples.

Sources:

- MiniBatchKMeans: https://scikit-learn.org/stable/modules/generated/sklearn.cluster.MiniBatchKMeans.html
- HDBSCAN docs: https://hdbscan.readthedocs.io/en/latest/how_hdbscan_works.html
- scikit-learn HDBSCAN: https://scikit-learn.org/stable/modules/generated/sklearn.cluster.HDBSCAN.html
- UMAP docs: https://umap-learn.readthedocs.io/en/latest/api.html

## 5. Keyphrase and Deterministic Entity Extraction

### Recommended Toolkit

- RAKE for stopword-delimited phrases.
- YAKE for single-document statistical keyphrases.
- TextRank for graph-ranked phrase salience.
- KeyBERT can be useful when embedding-backed keyphrases are needed, but it should reuse existing/staged embeddings and stay out of the first CPU-only hot path.
- spaCy Matcher/PhraseMatcher/EntityRuler for rule-based entities.
- Regex NER for emails, phones, URLs, domains, handles, tickers, wallet addresses, license plates, flight numbers, and platform IDs.

### Repo Fit

Existing code already has partial equivalents:

- `contact_extraction.py` extracts structured contact/link signals.
- `bio_mention.py` extracts mentioned handles.
- `bio_nlp.py` extracts hashtags/emojis and simple language hints.
- `entity_enrichment.py` can use spaCy NER over profiles.

The upgrade is to consolidate extraction into a shared module so posts, comments, messages, OCR, PDF text, media captions, and bios do not each reinvent extraction.

Candidate module:

```text
src/pipeline/keyphrase_entity.py
```

Candidate tables:

```text
extraction_patterns
timeline_extracted_entities
entity_extracted_indicators
```

Pattern safety:

- Add fixtures for every regex family.
- Avoid catastrophic backtracking.
- Consider RE2-compatible patterns for large untrusted text.
- Store pattern version and matched span.
- Keep inferred entities separate from exact gazetteer hits.

Sources:

- RAKE: https://www.pnnl.gov/publications/automatic-keyword-extraction-individual-documents
- YAKE: https://www.sciencedirect.com/science/article/abs/pii/S0020025519308588
- YAKE implementation: https://github.com/INESCTEC/yake
- TextRank: https://web.eecs.umich.edu/~mihalcea/papers/mihalcea.emnlp04.pdf
- spaCy rule matching: https://spacy.io/usage/rule-based-matching
- spaCy EntityRuler: https://spacy.io/api/entityruler
- spaCy Matcher: https://spacy.io/api/matcher

## 6. Near-Duplicate Detection and Coordinated Repost Noise

### Why This Matters

Reposts, forwards, repeated captions, copied bios, OCR boilerplate, and cross-platform reposts can inflate:

- topic counts,
- burst alerts,
- coordinated-posting alerts,
- relationship weights,
- content similarity,
- and perceived behavioral intensity.

### Recommended Stack

- Exact `text_sha1` for identical canonical text.
- SimHash 64-bit for near-exact text.
- MinHash + LSH for near-duplicate candidate generation.
- Exact Jaccard verification after LSH candidate retrieval.

### Repo Design

Fields in `timeline_text_features`:

```text
text_sha1
simhash64
minhash_signature
lsh_bands bigint[]
duplicate_cluster_id
duplicate_role
duplicate_confidence
```

Important rule: do not delete duplicate events. Mark them and let each downstream consumer decide whether to collapse duplicates or treat repetition as a signal.

Postgres LSH option:

- Store band hashes in `bigint[]`.
- Add GIN index on `lsh_bands`.
- Query with overlap operator to get candidates.
- Verify candidates with exact Jaccard in Python.

Sources:

- Broder MinHash: https://ieeexplore.ieee.org/document/666900
- datasketch LSH: https://ekzhu.com/datasketch/lsh.html
- SimHash: https://dl.acm.org/doi/10.1145/509907.509965
- C4 data cleaning: https://arxiv.org/abs/1910.10683
- Deduplicating training data: https://arxiv.org/abs/2107.06499
- RefinedWeb dataset: https://arxiv.org/abs/2306.01116

## 6A. Chat-Specific Analysis

The repo has Telegram, WhatsApp, Beeper, reply, reaction, DM, and group evidence. Chat needs its own analytics because individual messages are often too short for TF-IDF, LDA, or sentiment labels to be reliable.

### Conversation Structure

Model threads and turns explicitly:

- parent-child message IDs,
- reply chains,
- forwards,
- reactions,
- mentions,
- quoted targets,
- participant joins/leaves where available,
- group vs direct conversation type.

Candidate module:

```text
src/pipeline/conversation_analytics.py
```

Candidate outputs:

```text
conversation_threads
- thread_id
- source
- chat_id
- root_message_id
- started_at
- ended_at
- participant_count
- message_count
- reaction_count
- topic_summary_json
- sentiment_summary_json

conversation_participant_metrics
- thread_id
- entity_id
- messages_sent
- replies_received
- replies_given
- avg_response_latency_seconds
- median_response_latency_seconds
- initiated_thread_count
- mention_count
- reaction_given_count
- reaction_received_count
- participant_balance_score
```

### Message vs Thread Aggregation

Compute both granularities:

- Per message: language, keyphrases, explicit entities, duplicate hash, reply target, sentiment confidence.
- Per thread: aggregate emotion, dominant topics, participant balance, response latency, duplicate-adjusted term bursts.
- Per participant: who replies to whom, who gets ignored, who initiates, who bridges subgroups.

Rule: one angry message does not make an angry thread. Alert on aggregate windows unless a message contains deterministic high-risk keywords or a watched entity is involved.

### Chat Graphs

Use `entity_interactions` as the directed base, then build:

- mention graph,
- reply graph,
- reaction graph,
- co-participation graph,
- thread initiation graph,
- subgroup communities.

Leiden communities can detect sub-groups in large chats. PageRank/betweenness can show brokers or bridges, but every metric must show the edge family and time window.

Verification:

- fixture with known reply tree,
- response-latency calculation across timezones,
- participant-balance metrics on small known chats,
- no double-counting forwarded messages,
- rejected/suppressed duplicate messages excluded from thread sentiment by default.

## 6B. Bot, Spam, and Automation Heuristics

Add bot/spam heuristics as reviewable context, not as hard labels.

Candidate features:

- account age where collector data exposes it,
- posting rate by source and time window,
- duplicate-content ratio,
- follower/following skew,
- URL-heavy post ratio,
- hashtag entropy,
- repeated caption/template ratio,
- near-duplicate cluster membership,
- cross-platform copy delay,
- reply/reaction reciprocity,
- group fan-out,
- source health at collection time.

Candidate storage:

```text
behavioral_profiles.metadata.bot_heuristics
entity_relationships.sources.coordinated_behavior
alerts.detail.bot_or_spam_features
```

Rules:

- Never suppress evidence solely because an entity looks bot-like.
- Use bot/spam flags to reduce alert noise, prioritize review, or explain coordinated behavior.
- Require human review before high-impact labels.
- Separate automation indicators from maliciousness.

Verification:

- known benign repost accounts should not become high-severity alerts,
- duplicate-heavy spam fixtures should cluster,
- follower/following skew should be source-specific,
- bot heuristics should include enough feature detail for review.

## 7. Monitoring and Pipeline Debuggability

### Current Strengths

`unifiedanalyzer` already has better-than-basic operational structure:

- `run_phase_status` records phase status and duration.
- `analysis_runs` tracks run state and heartbeat.
- Scheduler status distinguishes active run vs last completed run.
- Repeated phase failures can notify Telegram.
- Collector health is checked periodically.
- Frontend pages show runs, alerts, triage, and health.

### Gaps

The system still needs observability at the level an operator actually debugs:

- What did a phase process?
- What did it skip?
- What did it fail to attribute?
- Which source/table is starving the pipeline?
- Which indexes or derived artifacts are drifting?
- Which alerts were suppressed and why?
- Which phase is slow because of CPU, DB, collector, or media I/O?
- Is a long synchronous phase still alive, or has the run lock gone stale?
- Which collector/source blocker made an alert invalid or low-actionability?

### Proposed Additions

#### Run Phase API and UI

Add:

```text
GET /api/runs/{run_id}/phases
```

Expose:

- phase name,
- status,
- started_at,
- finished_at,
- duration_ms,
- error,
- skipped reason,
- latest completed phase,
- current active phase,
- phase heartbeat age,
- stale threshold,
- worker/container identifier if available.

Frontend:

- `frontend/src/pages/Runs.tsx` should show a phase waterfall.
- `/ws/health` should include active run, heartbeat age, latest phase, failing phases, and collector blockers.

Schema/API note:

- `run_phase_status.created_at` is completion-time today. Long phases can look silent until they finish.
- Add `started_at`/`finished_at`, or write a start row before running a phase and update it on completion.
- Add `current_phase`, `phase_started_at`, `worker_id`, and `stale_after_at` either in `analysis_runs.metadata` or a small `run_live_status` table.

#### `pipeline_coverage_snapshots`

```text
snapshot_id
run_id
run_type
phase
source
processed_count
attributed_count
unresolved_count
skipped_count
error_count
top_unresolved_json
duration_ms
resource_class
created_at
```

Emit from:

- `timeline_builder.py`,
- `interaction_graph.py`,
- `location_inference.py`,
- `face_associations.py`,
- `timeline_embedder.py`,
- `alert_engine.py`,
- `collector_priority_hints.py`.

#### Phase Resource Classes

Add metadata for each phase:

```text
resource_class: db | collector_db | cpu | media_io | vector | geocode | notification
timeout_seconds
criticality: critical | degraded-ok | optional
collector_required: true/false
derived_artifact: true/false
```

This should live beside `_secondary_phases()` or in a small phase registry. It makes scheduler status clearer and prevents CPU-heavy OCR/face/video/embedding work from obscuring identity/timeline freshness.

#### Metrics

Add metrics for:

- API request latency by route.
- Scheduler loop duration.
- Phase duration by phase/run_type.
- Phase fail streak.
- Rows processed by phase.
- Unresolved source refs.
- Alert counts by type/severity/suppressed reason.
- Face DB vs FAISS/pgvector searchable drift.
- Location evidence accepted/rejected/unreviewed counts.
- Geocode cache hit/miss/error counts.
- running run age in seconds.
- run heartbeat age in seconds.
- latest phase status by phase.
- stale lock cleanup count.
- collector source overdue state by source.

Candidate stack:

- Prometheus metrics endpoint for machine scraping.
- OpenTelemetry traces later if route/phase traceability is needed.

Candidate metric names:

```text
analyzer_running_run_age_seconds
analyzer_run_heartbeat_age_seconds
analyzer_phase_latest_status{phase}
analyzer_phase_duration_ms{phase,quantile}
analyzer_stale_lock_failures_total
analyzer_alerts_by_type{type,severity}
collector_source_overdue{source}
```

#### Source Health Snapshot

Create a shared `source_health_snapshot` function/table/view that alert detectors and dashboards consume.

Include:

- source cadence,
- last successful collection,
- blocker kind,
- rate-limit/access error,
- "zero items but healthy" distinction,
- collector unavailable state,
- stale source threshold.

Use this in all alert detectors, not only `SILENCE_GAP`.

Sources:

- Prometheus alerting rules: https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/
- Prometheus Python FastAPI/Gunicorn docs: https://prometheus.github.io/client_python/exporting/http/fastapi-gunicorn/
- OpenTelemetry Python instrumentation: https://opentelemetry.io/docs/languages/python/instrumentation/
- Google SRE monitoring guidance: https://sre.google/sre-book/monitoring-distributed-systems/

## 8. Alerting Upgrades

### Current Alert Types

`alert_engine.py` currently covers:

- silence gaps,
- new activity after silence,
- profile changes,
- new identity links,
- coordinated posting,
- location mismatches,
- calibration readiness via `calibration_watchdog.py`.

### Alert Quality Upgrades

Every alert should include:

```json
{
  "trigger_window": "...",
  "baseline_window": "...",
  "source_health": "...",
  "dedupe_key": "...",
  "suppression_reason": null,
  "confidence": 0.83,
  "evidence_refs": [],
  "phase_run_id": "...",
  "debug_query_hint": "..."
}
```

Also add:

- stable dedupe keys,
- cooldown windows by entity/type/source,
- confidence/actionability score,
- watchlist weighting,
- grouped duplicate alerts,
- bulk mute/cooldown in the UI,
- "source health at detection" in the alert detail.

### New Alert Types

| Alert | Trigger | Guardrails |
|---|---|---|
| `EMOTIONAL_SPIKE` | Sentiment/emotion deviates from baseline | min baseline, min distinct events, sarcasm/language confidence, source health |
| `TOPIC_BURST` | Topic/keyphrase spikes over baseline | collapse duplicates, min distinct entities/sources |
| `FACE_LINK_DRIFT` | DB face count and searchable index count diverge | alert only after sustained drift |
| `FACE_CONTESTED_CLUSTER` | One cluster maps to multiple entities | require review queue, no auto-merge |
| `LOCATION_EVIDENCE_SPIKE` | New high-volume location evidence for watched entity | public-place guard, accuracy radius |
| `LOCATION_CONFLICT` | credible sources imply incompatible regions | source freshness, confidence, reviewed rejections excluded |
| `GEOCODE_BACKLOG` | geocode cache misses/errors/backlog rising | service ToS/rate-limit aware |
| `PIPELINE_COVERAGE_DROP` | attribution drops sharply by source | collector health and schema-change context |

### Reduce Alert Fatigue

Follow SRE-style rules:

- Alert on symptoms and actionable failures.
- Aggregate noisy source-level causes into dashboards or daily digest.
- Deduplicate by stable keys.
- Add suppression reasons.
- Avoid paging-style notifications for expected source gaps.
- Use severity and review state consistently.

Sources:

- Google SRE monitoring: https://sre.google/sre-book/monitoring-distributed-systems/
- Prometheus alerting rules: https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/

## 9. Face-Linking Improvements

### Current Strengths

The repo already has:

- InsightFace-based detector/embedding path.
- Quality filters and junk-face flagging.
- Drive and collector media scanning.
- `entity_faces` bridge from face rows to analyzer entities.
- `face_associations` for associated faces in media.
- `face_pair_signals`, `social_face_link`, `mutual_social_face`, `PHOTO_COAPPEARANCE`, and face search.
- Face gallery/search frontend.
- Existing tests for bridge audit, contested clusters, coappearance queries, and scorer context-signal safety.

### High-Leverage Additions

#### 1. Face Link Audit Report

Add a report/page with:

- total facetracker faces,
- searchable faces,
- faces with embeddings,
- junk faces,
- unclustered faces,
- identities,
- `entity_faces` bridged faces,
- entities with faces,
- face associations,
- contested clusters,
- DB vs FAISS/pgvector drift,
- labeled search hit rate,
- uploaded-image search success examples.

Likely files:

- `src/face/api/routes/stats.py`
- `src/api/routes/face_search.py`
- `frontend/src/pages/Faces.tsx`
- `frontend/src/pages/Triage.tsx`

#### 2. Exact-vs-ANN Recall Probe

Approximate vector search can miss matches depending on IVFFlat lists/probes or FAISS index state. Add a bounded audit:

- sample query faces,
- run exact cosine over a bounded set,
- compare approximate search top-K,
- store recall estimate and probe/index settings.
- check `pg_indexes`,
- confirm the partial predicate matches the query,
- capture `EXPLAIN (FORMAT JSON)` for representative searches,
- verify `ivfflat.probes` and `enable_seqscan` assumptions are reflected in the plan.

Likely files:

- `src/api/routes/face_search.py`
- `src/pipeline/face_bridge_audit.py`
- `src/face/api/routes/stats.py`

pgvector notes:

- IVFFlat can return fewer results if the index was built with too little data or probes are too low.
- HNSW/IVFFlat settings should be recorded with the audit.

#### 3. Face Review Queue

Add a unified review queue for:

- unlinked high-quality face,
- suggested entity assignment,
- contested cluster,
- possible duplicate identity,
- false face/junk,
- low-quality but frequently repeated face,
- high-similarity cross-entity match.

Actions:

- assign face to entity,
- reject face-to-entity link,
- mark as junk,
- split contested cluster,
- mark as not enough evidence,
- pin to case.

Every action should write audit log entries and be replay-safe.

Review cards should show face audit flags directly:

- direct face collision,
- contested cluster,
- bridge method mix,
- `knn_propagation`/`cluster_propagation` involvement,
- sample face IDs,
- source media IDs,
- similarity,
- face count in media,
- junk/quality state.

#### 4. Face-Link Confidence Contract

Separate:

- detection confidence,
- crop quality,
- embedding similarity,
- cluster confidence,
- entity bridge confidence,
- reviewer confidence.

Do not collapse these into one number.

#### 5. Face-Derived Signals Stay Context Unless Hard Evidence Exists

Tests already guard context signals like `social_face_link` and `topical_similarity`. Keep that pattern:

- face coappearance can indicate relationship/context,
- primary-face exact match can be stronger,
- social face link is associative,
- contested or low-quality matches must not auto-merge.

#### 6. Social Face Link False-Positive Controls

`social_face_link` should remain associative. Escalate only when stronger context exists:

- bilateral association,
- multiple media items,
- different days,
- non-crowd media,
- corroborating platform signal,
- non-contested cluster,
- non-junk face quality.

Single one-way social face links should stay context-only.

#### 7. Threshold Audit

Current defaults such as `FACE_PAIR_KNN_THRESHOLD`, `FACE_PAIR_KNN_MIN_MATCHES`, `FACE_ASSOCIATIONS_THRESHOLD`, and `SOCIAL_FACE_LINK_THRESHOLD` should be evaluated by bucket before changing.

Bucket by:

- cosine/similarity,
- method,
- media type,
- face count in image/video,
- crop quality,
- source platform,
- reviewer label.

Output: precision/recall by bucket, plus examples for false positives and false negatives.

Sources:

- InsightFace project: https://github.com/deepinsight/insightface
- pgvector docs: https://github.com/pgvector/pgvector
- InsightFace evaluation guidance: https://www.insightface.ai/guides/choose-face-recognition-model-and-evaluate

## 10. Location-Inference Improvements

### Current Strengths

The repo already has:

- `location_evidence` with review decisions.
- Strava route-derived evidence.
- IG geo recovery work.
- EXIF GPS extraction in media analysis and face worker.
- `/geo` entity endpoint and Leaflet UI.
- `/intersect` for physical/digital overlap.
- `LOCATION_MISMATCH` alert.
- tests for location evidence keys, rejection handling, and intersection filtering.

### Gaps

- No explicit spatial index strategy is visible in the base schema.
- Location precision and accuracy radius need to be first-class.
- Public-place fan-out must be guarded everywhere.
- Staypoints and repeated locations are not first-class derived evidence.
- Geocode cache health/backlog is not prominent.
- Location claims need confidence decomposition by source type.

### Proposed Data Model Additions

#### `entity_geo_events`

Precomputed read model for `/geo`, `/intersect`, alerts, and case exports:

```text
geo_event_id
entity_id
occurred_at
source
source_record_id
lat
lng
accuracy_m
location_name
location_type
evidence_key
review_status
confidence_0_1
public_place_flag
payload_json
created_at
```

This table should be the main read path. `/geo` and `/intersect` can still fall back to live collector reads for backfill/debug, but normal dashboard and alert paths should prefer analyzer-owned read models so collector DB load and availability do not control UI latency.

#### Spatial Strategy

Option A: PostGIS

- Use geometry/geography columns.
- Use GiST indexes.
- Use `ST_DWithin` for radius and overlap queries.

Option B: H3/geohash cells

- Lower deployment friction.
- Store cells at multiple resolutions.
- Use cell joins for approximate candidate generation, then exact distance in Python/Postgres.

Recommendation:

- If Docker/Postgres extension management is acceptable, PostGIS is the cleanest long-term path.
- If extension drift is a concern, start with H3/geohash side columns and exact-distance verification.

### Location Inference Features

Add:

- staypoint detection,
- home/work/gym candidate scoring,
- commute/route cluster summaries,
- repeated venue confidence,
- source freshness,
- accuracy radius,
- public-place fan-out score,
- reviewed rejection suppression,
- collector gap awareness.

Do not infer sensitive labels too aggressively. Store "candidate_home_region" or "frequent_start_area" with confidence and reviewer state rather than making hard claims.

Suggested `entity_staypoints` fields:

```text
staypoint_id
entity_id
lat
lng
accuracy_m
geo_cell
staypoint_type
supporting_evidence_count
distinct_days
source_mix_json
public_place_score
confidence_0_1
review_status
created_at
updated_at
```

`route_similarity.py` should eventually consume `entity_staypoints` rather than maintaining separate rounded-cell logic. Strava starts, Telegram/WhatsApp message GPS, EXIF clusters, repeated IG venues, and reviewed locations can all contribute evidence.

### Location Debug Endpoints

Add:

```text
GET /api/entities/{entity_id}/geo/debug
POST /api/entities/intersect/debug
```

Return:

- source counts by collector/analyzer table,
- accepted/rejected/suppressed counts,
- top suppression reasons,
- read-model age,
- live collector fallback status,
- query timings,
- index path or plan summary,
- top dropped evidence rows,
- public-place fan-out stats,
- geocode cache hit/miss/error counts.

### Alerting Improvements

Add or refine:

- `LOCATION_CONFLICT`: source A and source B imply incompatible regions within an impossible time window.
- `LOCATION_NEW_COUNTRY`: watched entity has credible movement into a new country/region.
- `LOCATION_RISKY_MISMATCH`: stronger version of mismatch that requires high-confidence source conflict and good source health.
- `COLOCATION_HIGH_CONFIDENCE`: two watched/case entities overlap in time and place after public-place suppression.
- `LOCATION_EVIDENCE_SPIKE`: new burst of location evidence for watched entity.
- `PUBLIC_PLACE_FANOUT`: location cluster is too common to support identity or relationship evidence.
- `PUBLIC_PLACE_FALSE_POSITIVE_SPIKE`: many candidate overlaps are suppressed by the public-place guard.
- `GEO_PIPELINE_STALE`: geo read model or geocode cache has stopped refreshing.
- `GEOCODE_BACKLOG`: cache misses/errors/rate limits are blocking enrichment.

Sources:

- PostGIS spatial indexing: https://postgis.net/workshops/postgis-intro/indexing.html
- PostGIS `ST_DWithin`: https://postgis.net/docs/ST_DWithin.html
- H3 docs: https://h3geo.org/docs/
- Uber H3 overview: https://www.uber.com/us/en/blog/h3/
- scikit-mobility stay locations: https://scikit-mobility.github.io/scikit-mobility/reference/preprocessing.html
- GeoPy docs and rate-limit caution: https://geopy.readthedocs.io/

## 11. Trend and Burst Detection

Use two layers:

1. Online cheap detectors for near-real-time alerts:
   - z-score,
   - EWMA,
   - CUSUM,
   - rolling anomaly detection,
   - Seasonal-Hybrid ESD-style anomaly detection where seasonality exists,
   - duplicate-adjusted counts.
2. Batch detectors for richer reports:
   - Kleinberg burst detection,
   - log-likelihood ratio,
   - chi-square where expected counts are safe.

Candidate burst targets:

- hashtags,
- handles,
- domains,
- venues,
- extracted entities,
- topics,
- emotions,
- duplicate clusters,
- graph communities,
- location cells.

Guardrails:

- require minimum baseline,
- require minimum distinct entities/sources,
- account for source health,
- collapse duplicate clusters unless repeated posting is the signal,
- write suppression reasons.

Sources:

- Kleinberg burst detection: https://www.cs.cornell.edu/home/kleinber/kdd02.html
- Twitter AnomalyDetection: https://github.com/twitter/AnomalyDetection
- Seasonal-Hybrid ESD docs: https://rdrr.io/github/twitter/AnomalyDetection/man/AnomalyDetectionTs.html
- Rayson/Garside log-likelihood: https://aclanthology.org/W00-0901.pdf

## 11A. Network and Graph Analysis

The current graph stack already has `entity_interactions`, `entity_relationships`, `graph_analytics.py`, `ConnectionsPanel`, and `NetworkGraph`. The missing piece is a clear split between identity evidence, relationship context, community structure, and influence metrics.

### Graph Types

Build separate graph snapshots for:

- reply graph,
- mention graph,
- reaction graph,
- comment graph,
- follow graph,
- photo co-appearance graph,
- location co-presence graph,
- duplicate/repost graph,
- combined interaction graph.

Each snapshot should store:

```text
graph_snapshot_id
edge_family
time_window_start
time_window_end
entity_count
edge_count
source_mix_json
filters_json
created_at
```

### Metrics

Compute:

- degree and weighted degree,
- in-degree/out-degree for directed interactions,
- reciprocity,
- PageRank,
- eigenvector centrality where stable,
- betweenness centrality for bridge detection,
- community ID,
- participation coefficient,
- within-community degree.

### Community Detection

Use:

- Louvain as a simple baseline.
- Leiden for stronger community quality and well-connected communities.
- `igraph`/`leidenalg` for larger graphs instead of converting huge NetworkX objects late.

Guardrails:

- Do not call a person "influential" without showing edge family, source mix, and time window.
- Avoid letting huge public groups dominate communities.
- Apply public-place and group-size fan-out guards before location/group edges enter graph metrics.
- Store metrics as context, not identity evidence.

Candidate tables:

```text
graph_snapshots
entity_graph_metrics
entity_communities
community_edges
bridge_findings
```

Frontend:

- add community legend,
- selectable edge families,
- time-windowed metrics,
- evidence drawer for centrality/community findings,
- separate "standing relationships" from "in-window interactions".

Sources:

- Louvain paper: https://arxiv.org/abs/0803.0476
- Leiden paper: https://www.nature.com/articles/s41598-019-41695-z
- leidenalg docs: https://leidenalg.readthedocs.io/en/stable/intro.html
- NetworkX betweenness centrality: https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.centrality.betweenness_centrality.html
- CDlib docs: https://cdlib.readthedocs.io/

## 12. CPU-Only Stack Recommendation

Recommended Python stack:

- spaCy for tokenization, Matcher, PhraseMatcher, EntityRuler, and optional NER.
- scikit-learn for TF-IDF, HashingVectorizer, MiniBatchKMeans, NMF, LSA, online classifiers.
- VADER/AFINN/NRC for sentiment/emotion.
- datasketch for MinHash/LSH prototyping.
- YAKE/rake-nltk for keyphrases.
- fastText or Lingua/CLD3/langid for language detection.
- networkx for simple graph analysis; igraph/leidenalg for larger community detection.
- pandas/Polars and DuckDB for offline audit reports.
- Postgres/pgvector for production search and entity data.

### No-GPU Embedding Options

Use these only where they reduce cost or improve coverage:

- Existing `intfloat/multilingual-e5-small` ONNX path remains the primary dense embedding path because it already fits `timeline_embeddings vector(384)`.
- GloVe/word2vec/fastText static vectors can provide cheap word-level features and OOV handling, especially for fastText subwords.
- Model2Vec can distill sentence-transformer behavior into compact static embeddings and is promising for high-throughput CPU sidecars.
- `all-MiniLM-L6-v2` maps text to 384-dimensional vectors and is a reasonable CPU/hobby-volume baseline, but adding it would duplicate the existing 384d e5-small stack unless it wins an evaluation.
- `HashingVectorizer` remains the best memory-bounded streaming bag-of-words baseline.

Evaluation rule:

- Do not add another embedding model without a golden-query and clustering-quality comparison against current `timeline_embeddings`.
- Reuse stored vectors when possible.
- Record model name, version, text hash, and dimension in every derived table.

Dependency caution:

- Keep SQLite FTS5 as an offline/prototype option only. The production system already has Postgres, so avoid a second truth store unless exporting case bundles.
- ParadeDB/`pg_search` may be useful for true BM25, but native Postgres FTS should be the first implementation because it has lower deployment risk.
- fastText Python builds can be annoying on Windows/Linux containers; benchmark installation before committing to it.
- LIWC must be gated by license.
- SO-CAL should not be a hot-path dependency.

Sources:

- scikit-learn HashingVectorizer: https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.HashingVectorizer.html
- fastText supervised tutorial: https://fasttext.cc/docs/en/supervised-tutorial.html
- MiniBatchKMeans: https://scikit-learn.org/stable/modules/generated/sklearn.cluster.MiniBatchKMeans.html
- DuckDB: https://duckdb.org/why_duckdb.html
- Polars: https://docs.pola.rs/
- SQLite FTS5: https://www.sqlite.org/fts5.html
- GloVe: https://nlp.stanford.edu/projects/glove/
- model2vec: https://github.com/MinishLab/model2vec
- all-MiniLM-L6-v2: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2

## 12A. Storage and Search Engine Decision Matrix

The plan should not imply one search/storage engine is universally best. `unifiedanalyzer` already uses Postgres and pgvector, so production truth should remain there unless a specific workload justifies a sidecar.

| Engine | Best Use | Fit for UnifiedAnalyzer | Risk |
|---|---|---|---|
| SQLite FTS5 | zero-ops embedded full-text search with BM25 ranking | offline case bundles, prototypes, local artifact exports | second truth store if used as production index |
| DuckDB | embedded OLAP over Parquet/CSV | batch trend charts, recovery/audit snapshots, feature experiments | not a live transactional source |
| Postgres `tsvector` + GIN + pg_trgm | one server for relational, full-text, fuzzy matching | best first production sparse search path | not true Okapi BM25 by default |
| Meilisearch | typo-tolerant UI search box with simple operations | optional frontend search sidecar for exported/cached documents | sync, auth, and second-index drift |
| Typesense | typo-tolerant search with facets/filtering/sorting | optional faceted entity/media/case search sidecar | sync and schema drift |
| OpenSearch/Elasticsearch | Lucene-scale search, analyzers, heavy faceting | only if learning/operating Meltwater-style stack is a goal | JVM/ops burden, heap/shard sizing |
| ClickHouse | high-volume time-series and analytical aggregations | future billion-row trend/metric warehouse | another server and ingestion pipeline |
| TimescaleDB | Postgres time-series extension/hypertables | possible if timeline/metrics outgrow current partitions | extension/migration assumptions |

Recommendation:

1. Start with Postgres FTS + pgvector + RRF.
2. Use DuckDB/Polars for offline reports.
3. Use SQLite FTS5 for portable case exports or prototypes.
4. Consider Meilisearch/Typesense only for a dedicated typo-tolerant UI search layer.
5. Avoid OpenSearch/Elasticsearch unless the operational learning goal outweighs the JVM/cluster burden.
6. Consider ClickHouse/Timescale only after Postgres metrics prove aggregation limits.

Sources:

- SQLite FTS5: https://www.sqlite.org/fts5.html
- DuckDB: https://duckdb.org/why_duckdb.html
- Meilisearch typo tolerance: https://meilisearch.com/docs/resources/internals/typo_tolerance
- Typesense search API: https://typesense.org/docs/30.2/api/search.html
- OpenSearch install docs: https://docs.opensearch.org/latest/install-and-configure/install-opensearch/index/
- OpenSearch operational best practices: https://docs.aws.amazon.com/opensearch-service/latest/developerguide/bp.html
- ClickHouse time-series guide: https://clickhouse.com/docs/guides/use-cases/real-time-analytics/time-series
- TimescaleDB: https://github.com/timescale/timescaledb

## 12B. Coralytics Lessons to Copy

The provided Coralytics notes map well to `unifiedanalyzer`, but the repo should adapt them rather than copy them literally.

What to copy:

- Normalize heterogeneous sources into one internal document/event shape. `timeline_events`, `entity_interactions`, `media_analysis`, and future `timeline_text_features` are the right primitives.
- Use shape-based format detection for heterogeneous exports and collector payloads.
- Use VADER as the CPU-only social sentiment default.
- Use a fixed taxonomy plus TF-IDF/cosine or sparse classifier as the first interpretable classifier.
- Keep Boolean/rule taxonomies as first-class. This matches the spirit of Meltwater/CSDL/VEDO-style tagging: deterministic rules remain valuable beside ML.
- Keep rule-based red-flag detection for crisis/risk keywords because it is deterministic and auditable.
- Keep batch architecture as the starting point, then add bounded incremental passes where needed.
- Use visualizations for communication, but make the operational dashboard dense and evidence-oriented.

What to improve for UnifiedAnalyzer:

- Coralytics-style 12-category taxonomy should become configurable `topic_seed_sets`, not hard-coded constants.
- TF-IDF/cosine should not be used alone for short chat messages; pool into pseudo-documents or use short-text models.
- Visual novelty should not outrank evidence inspection. The primary UI need is a Life Graph workspace with timeline/map/face/graph/search evidence drawers.
- Red flags should write `alerts.detail` with evidence refs, source health, dedupe keys, and suppression reasons.

## 13. Implementation Sequence

### Phase A: Observability and Contracts

1. [x] Add `pipeline_coverage_snapshots`. Notes 2026-08-08: added the
   idempotent side table in `src/db/schema.sql`, generic coverage normalization
   in `src/pipeline/incremental_runner.py`, and `/api/runs/{run_id}/coverage`.
   Timeline `by_source` and interaction `by_type` results now produce immediate
   source/type-level coverage rows; other phases produce aggregate `all` rows
   until their native emitters grow richer counters.
2. Add alert detail contract and suppression reasons.
3. Add face-link audit report.
4. Add location evidence quality report.
5. Normalize confidence and provenance fields across new derived features.

### Phase B: Text Foundation

1. [x] Add `timeline_text_features`. Notes 2026-08-08: added analyzer-owned
   side table in `src/db/schema.sql`, a bounded embedding-seeded `lexical_nlp`
   phase before alerts, and `python -m src.main text-features-backfill`.
2. [x] Add `text_normalizer.py`. Notes 2026-08-08: normalizes timeline title,
   detail, and selected collector-derived metadata while preserving handles,
   hashtags, URLs/domains, captions, message previews, and location names.
3. Add language detection.
4. Add VADER/AFINN/NRC sidecar.
5. Add keyphrase/entity extraction.
6. Add exact hash, SimHash, and MinHash/LSH bands.

### Phase C: Search and Retrieval

1. Add Postgres FTS.
2. Expand canonical text coverage.
3. Add hybrid RRF search.
4. Add frontend search workflow.
5. Add retrieval evaluation fixtures.

### Phase D: Alert Intelligence

1. Add emotional spike alerts.
2. Add topic/keyphrase burst alerts.
3. Make coordinated-posting duplicate-aware.
4. Add source-health gates.
5. Add alert false-positive review tracking.
6. Add bot/spam heuristics as context and alert-noise controls.
7. Add streaming story clustering for watched entities and case scopes.

### Phase E: Face and Location Upgrade

1. Add face review queue.
2. Add exact-vs-ANN face recall probe.
3. Add contested cluster workflow.
4. Add `entity_geo_events`.
5. Add PostGIS or H3/geohash candidate generation.
6. Add staypoint and repeated-location inference.
7. Add chat-thread analytics for reply graphs, latency, and participant balance.
8. Add graph community and influence snapshots for edge-family/time-window views.

### Phase F: Product Surface

1. Add evidence inspector drawer.
2. Add entity Life Graph workspace.
3. Make cases evidence bundles.
4. Add graph/community/topic/location/face overlays.
5. Add storage/search sidecar only after Postgres/DuckDB/SQLite prototype evidence justifies it.

## 14. Verification Checklist

Before marking any future implementation complete:

- Run targeted unit tests for the changed pipeline/API/UI.
- Run `git diff --check`.
- For DB changes, apply schema/migrations to a scratch DB.
- Verify idempotent rerun behavior.
- For search, run golden query cases and latency checks.
- For alerts, prove dedupe and suppression behavior.
- For face-linking, compare exact vs approximate search on a sample.
- For location inference, prove rejected evidence is excluded.
- For chat analytics, prove reply chains, response latency, participant balance, and thread-level sentiment on fixtures.
- For graph analytics, prove community/centrality metrics are time-windowed and edge-family-specific.
- For storage/search sidecars, prove sync, rebuild, and drift behavior before using them operationally.
- For frontend changes, run TypeScript/build and inspect key UI states.
- For live services, verify current runtime state, not just source code.

## 15. First Slice Recommendation

The best first implementation slice is:

1. [x] `pipeline_coverage_snapshots` - first commit adds the side table,
   centralized writer, run coverage API, and targeted tests.
2. [x] `timeline_text_features` - analyzer-owned side table,
   embedding-seeded `lexical_nlp` phase, and bounded backfill command preserve
   collector provenance without bloating `timeline_events`.
3. [x] `text_normalizer.py` - shared canonical text builder for timeline title,
   detail, and selected metadata.
4. VADER/AFINN/NRC scoring.
5. Postgres FTS.
6. Hybrid RRF search.
7. Face-link audit report.
8. Location evidence quality report.

That slice improves monitoring, alerting readiness, face debugging, location debugging, and NLP/search foundations without introducing heavy model risk.
