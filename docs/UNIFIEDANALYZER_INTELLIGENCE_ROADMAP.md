# UnifiedAnalyzer Intelligence Upgrade Roadmap

Date: 2026-08-08

Scope: research and planning only. This artifact does not implement code. It turns the current `unifiedanalyzer` repo state, the provided classical NLP notes, and subagent research passes into a concrete backlog for making the system closer to the entity life-graph vision in `VISION_PLAN.md`.

## Existing Plan Found

The likely Gemini-created markdown plan is `docs/OSINT_NLP_IMPROVEMENTS.md`.

That file already covers the right families of improvements: VADER/NRC emotional edges, BM25/RRF hybrid search, short-text topic modeling, YAKE/TextRank keyphrases, MinHash/LSH deduplication, Leiden graph analysis, and burst detection. This roadmap should be treated as the repo-specific expansion of that plan, not a replacement.

Two local documentation issues are worth fixing in a future doc cleanup:

- `README.md` still points at `docs/analyzer_overview.md`, `docs/media_analysis_plan.md`, `docs/storage_drive_plan.md`, and `docs/facetracker_merge_plan.md`, but the current tracked docs list does not include those files.
- `src/face/README.md` still references `docs/facetracker_merge_plan.md`. If the historical file is intentionally gone, replace that link with the current face-engine summary.

## Method

Research inputs:

- Repo scan of `README.md`, `VISION_PLAN.md`, `docs/OSINT_NLP_IMPROVEMENTS.md`, `src/db/schema.sql`, API routes, pipeline modules, frontend pages, and tests.
- Subagent passes for backend/API architecture, schema/tests, frontend UX, identity/provenance, sentiment/emotion, retrieval/topic modeling, and the added classical NLP keyword batch.
- Web research against primary or near-primary sources where possible: official library docs, papers, project docs, and standards.

The most relevant current repo facts:

- Core profiler goal is already defined in `VISION_PLAN.md`: any entity should have a time-scrubbable physical plus digital timeline, reciprocal interactions, and ranked explainable edges.
- `timeline_events` is the main event spine, partitioned by `occurred_at`, with JSONB `metadata`.
- `entity_interactions` already models directed actor-to-target interactions.
- `entity_relationships` is still the ranked aggregate edge table.
- `location_evidence`, `media_analysis`, `entity_faces`, `face_associations`, and `timeline_embeddings` already give the system enough primitives for stronger intelligence layers.
- `/api/search/timeline` exists, but it is dense-only, uses `timeline_embeddings`, joins to `timeline_events`, and appears not to have a strong frontend workflow yet.
- `topical_similarity.py` is not a real topic model. It is currently a centroid cosine identity signal and is marked by its own comments as O(E^2).
- `_secondary_phases()` in `src/pipeline/incremental_runner.py` is the largest orchestration bottleneck: graph, media, face, embedding, calibration, enrichment, and geocoding phases are mixed in one serial secondary chain.

## North Star

The next system shape should be:

1. Every meaningful text-bearing event gets cheap, deterministic, explainable text features.
2. Search becomes hybrid: exact tokens, social handles, keywords, and dense semantic recall all work together.
3. Reposts, near-duplicates, and cross-post storms stop polluting relationship and topic signals.
4. Topic, burst, emotion, and graph signals become first-class evidence that can be inspected, filtered, and reviewed.
5. The frontend becomes a fused life-graph workspace rather than separate timeline, map, graph, media, and review tabs.
6. Every generated claim has source provenance, algorithm version, confidence scale, and reviewer override history.

## Priority Roadmap

| Priority | Improvement | Why it matters | Likely files/tables | Verification |
|---|---|---|---|---|
| P0 | Create a `timeline_text_features` derived table | Avoid JSONB bloat and create one place for language, normalized text, sentiment, emotions, keyphrases, topics, duplicate hashes, and FTS data | `src/db/schema.sql`, new migration, new pipeline module | Scratch DB schema test, idempotent backfill test, row-count parity with eligible events |
| P0 | Build canonical searchable text | Current search appears to use titles/snippets too narrowly. Search should index `title + detail + selected metadata` with source-aware redaction | `timeline_builder.py`, new `text_normalizer.py`, `search.py` | Golden queries for handles, hashtags, place names, exact phrases, and paraphrases |
| P0 | Add sparse search with Postgres FTS and RRF hybrid retrieval | Dense search misses exact handles, IDs, URLs, hashtags, and slang. Sparse search fixes exact recall; RRF combines dense and sparse without fragile score calibration | `schema.sql`, migration, `src/api/routes/search.py`, frontend search UI | `EXPLAIN`, p95 latency, Recall@20/MRR on a curated query set |
| P0 | Normalize evidence and confidence contracts | Current confidence appears mixed between 0..1, 0..100, and relationship weights. That makes UI and audit semantics brittle | `identity_signals`, `entity_interactions`, `entity_relationships`, `location_evidence`, `audit_log` | DB CHECKs or normalized views, API schema tests, UI scale audit |
| P1 | Add CPU sentiment and emotion sidecar | VADER/AFINN/NRC can immediately turn plain interaction counts into affect-aware edges and baseline shifts | new `sentiment_emotion.py`, `timeline_text_features`, `entity_interactions.metadata`, `behavioral_profiles.metadata` | Fixtures for negation, emoji, ALL CAPS, sarcasm flags, multilingual skip paths |
| P1 | Run affect features before alert evaluation | `run_alerts()` currently happens before secondary phases. `EMOTIONAL_SPIKE` must not lag behind current text events | `incremental_runner.py`, `alert_engine.py` | Phase timing metrics and synthetic spike alerts |
| P1 | Add MinHash/SimHash duplicate features | Social data is dominated by reposts, forwarded messages, repeated captions, and cross-posts. Dedup reduces false trends and false relationships | `timeline_text_features`, new `dedup_text.py`, maybe `content_fingerprint.py` | Known duplicate clusters, candidate-pair count, exact Jaccard verification |
| P1 | Add deterministic keyphrase and entity extraction | RAKE/YAKE/TextRank plus spaCy Matcher/EntityRuler give fast explainable indicators, brands, handles, domains, tickers, products, venues, and OSINT clues | new `keyphrase_entity.py`, gazetteer files, `timeline_text_features` | Fixture rules, false-positive review sample, keyphrase stability tests |
| P1 | Add source attribution coverage reports | The largest bottleneck is not always algorithm quality; it is unresolved refs, thin entity links, and un-attributed collector rows | `build_timeline()`, `build_interaction_graph()`, `run_phase_status`, new coverage table/view | Live read-only report: processed, attributed, unresolved, top unresolved refs |
| P1 | Split secondary phases into a phase registry | CPU-heavy OCR/face/embedding work should not block identity/timeline freshness or run heartbeat | `incremental_runner.py`, scheduler modules | Phase DAG unit tests, timeout/fail-streak reporting, heartbeat under load |
| P2 | Add short-text topic and story detection | Chat/tweet captions are too short for classic LDA alone. Use GSDMM/BTM or bounded BERTopic-style c-TF-IDF for short story clusters | new `short_text_topics.py`, `topic_clusters`, `timeline_event_topics` | Topic coherence samples, cluster stability across seeds, wall-time/RSS budgets |
| P2 | Add trend and burst detection | Kleinberg batch bursts plus EWMA/z-score/CUSUM online checks can detect spiking hashtags, locations, handles, and emotions | new `burst_detection.py`, `alerts`, topic/keyphrase tables | Synthetic time series, minimum baseline gates, collection-gap suppression |
| P2 | Make review queues uncertainty-ranked | Current same-person review is score-thresholded. Active learning should prioritize uncertainty, conflicts, and coverage gaps | `entities.py`, `triage.py`, `Review.tsx`, `identity_labels` | Queue diversity tests, calibration bins, label distribution reports |
| P2 | Add graph community and influence layers | Leiden/Louvain/PageRank/betweenness can turn raw interactions into communities, bridges, hubs, and broker entities | new `community_graph.py`, `graph_analytics.py`, `entity_relationships`, graph API | Community stability samples, hub exclusion checks, frontend graph legend |
| P2 | Materialize `entity_life_events` | A single profiler read model should fuse timeline events, reciprocal interactions, media/faces, location evidence, and analyst decisions | new table/view, `timeline_builder.py`, `interaction_graph.py`, route modules | Entity-page p95 latency, reciprocal event fixtures, source provenance parity |
| P2 | Precompute geo/intersection read models | `/geo` and `/intersect` should become mostly cached reads, with rejected location evidence excluded by construction | `location_evidence`, new `entity_geo_events`, `intersections.py`, `graph.py` | Physical-overlap tests, `EXPLAIN`, p95 under rich entities |
| P3 | Add offline analytics path with DuckDB/Polars | Heavy audits, topic experiments, and snapshot reports should run outside the API/scheduler hot path | scripts, optional export tables/Parquet | Repeatable report generation, memory ceiling, no mutation of live DB |
| P3 | Make cases evidence bundles | Cases should pin timeline events, media, graph edges, map points, intersections, notes, and time windows, not just entities | `cases`, `case_items`, frontend `Cases.tsx`, export routes | Export snapshot tests, reopen case flow, evidence provenance checks |

## Keyword Taxonomy and Repo Fit

### Sentiment and Emotion

Recommended first stack:

- VADER for English social/chat sentiment.
- AFINN as a tiny corroborating valence score.
- NRC EmoLex for emotion distributions: anger, anticipation, disgust, fear, joy, sadness, surprise, trust, plus positive/negative.
- TextBlob/Pattern only for subjectivity/backoff, not as the main social text scorer.
- SentiWordNet only for formal text and bios where POS/sense handling is acceptable.
- LIWC only if licensed and only for aggregate-window profiling.
- SO-CAL only as an optional long-text validator because public implementations can bring Java/CoreNLP overhead.

Repo-specific design:

- Do not store only one label like `positive` or `negative`.
- Store continuous scores, method names, method versions, language, confidence, and flags.
- Treat sarcasm/irony as a confidence reducer, not a polarity flip.
- Mark all sentiment/emotion relationship summaries as `context_only` so they cannot become identity evidence.

Candidate fields in `timeline_text_features`:

```text
event_id
language_code
language_confidence
vader_compound
vader_pos
vader_neu
vader_neg
afinn_score
nrc_emotions_json
subjectivity
sarcasm_flags_json
method_versions_json
processed_at
```

Candidate fields in `entity_interactions.metadata.emotion`:

```json
{
  "actor_to_target_compound": 0.72,
  "emotion_dist": {"joy": 0.4, "trust": 0.2},
  "flags": ["emoji_positive", "low_sarcasm_risk"],
  "not_identity_evidence": true
}
```

Useful sources:

- VADER paper page: https://ojs.aaai.org/index.php/ICWSM/article/view/14550
- `vaderSentiment` package: https://pypi.org/project/vaderSentiment/
- AFINN paper: https://arxiv.org/abs/1103.2903
- SentiWordNet: https://aclanthology.org/L10-1531/
- NRC EmoLex: https://arxiv.org/abs/1308.6297
- TextBlob docs: https://textblob.readthedocs.io/en/dev/quickstart.html
- Pattern JMLR paper: https://www.jmlr.org/papers/volume13/desmedt12a/desmedt12a.pdf
- SO-CAL paper: https://aclanthology.org/J11-2001.pdf
- LIWC official docs: https://www.liwc.app/help/howitworks

### Language Detection and Normalization

Language routing is required before lexicon scoring. Short posts and code-switching will make sentence-level language ID noisy, so store both document-level and token-level hints where practical.

Recommended:

- Use fastText `lid.176.ftz` first if model footprint matters.
- Consider CLD3 or Lingua as alternatives for short text benchmarks.
- Normalize Unicode, URLs, mentions, hashtags, emoji, repeated punctuation, domains, and phone/email patterns before downstream scoring.
- Preserve social tokens. Do not strip `@handle`, `#tag`, emojis, or domains before feature extraction; create normalized and raw-token views instead.

Candidate module:

- `src/pipeline/text_normalizer.py`

Candidate outputs:

- `normalized_text`
- `token_count`
- `char_count`
- `emoji_count`
- `url_count`
- `mention_count`
- `hashtag_count`
- `language_code`
- `language_confidence`
- `code_switch_hint`

Useful sources:

- fastText language ID: https://fasttext.cc/docs/en/language-identification.html
- CLD3: https://github.com/google/cld3
- Lingua-py: https://github.com/pemistahl/lingua-py

### Sparse Retrieval, BM25, and Hybrid Search

Current repo issue:

- `/api/search/timeline` is dense-only.
- It should be expanded to exact keyword retrieval and hybrid retrieval.

Recommended path:

1. Build canonical searchable text from `timeline_events.title`, `detail`, and selected metadata.
2. Add a Postgres FTS side table or generated `tsvector` with GIN index.
3. Return exact matches with snippets and matched fields.
4. Fuse sparse and dense results with Reciprocal Rank Fusion.
5. Add frontend search routes and result click-through into entity/timeline/map/media context.

Why not only pgvector:

- Dense vectors are good for paraphrase and semantic recall.
- They are weak for exact handles, hashtags, product codes, URLs, phone fragments, usernames, IDs, and quoted phrases.

Why not only FTS:

- Keyword search is brittle for paraphrases, aliases, and semantic questions.

Candidate APIs:

- `GET /api/search/timeline?mode=keyword`
- `GET /api/search/timeline?mode=semantic`
- `GET /api/search/timeline?mode=hybrid`
- `GET /api/search/topics`

Candidate result fields:

```json
{
  "event_id": "...",
  "entity_id": "...",
  "occurred_at": "...",
  "source": "telegram",
  "rank": 12,
  "sparse_rank": 3,
  "dense_rank": 24,
  "rrf_score": 0.041,
  "matched_fields": ["detail", "metadata.caption"],
  "snippet": "..."
}
```

Useful sources:

- Lucene BM25 similarity: https://lucene.apache.org/core/7_0_1/core/org/apache/lucene/search/similarities/BM25Similarity.html
- Stanford IR book on Okapi BM25: https://nlp.stanford.edu/IR-book/html/htmledition/okapi-bm25-a-non-binary-model-1.html
- PostgreSQL text search controls: https://www.postgresql.org/docs/current/textsearch-controls.html
- PostgreSQL GIN indexes: https://www.postgresql.org/docs/current/gin.html
- SQLite FTS5: https://www.sqlite.org/fts5.html
- pgvector: https://github.com/pgvector/pgvector

### Topic Modeling and Classification

Repo-specific recommendation:

- Do not start with a heavy unsupervised topic model across all raw events.
- Start with taxonomy labels and cheap features, then add exploratory topic models once evaluation exists.

Recommended layers:

1. Lightweight taxonomy classifier using `HashingVectorizer + SGDClassifier` or ComplementNB.
2. Seed-word taxonomy for known OSINT buckets: travel, work, school, family, finance, threat, identity clue, venue, health, relationship, media, bot/spam.
3. NMF/LSA on aggregated entity/source/week documents for interpretable entity-level topics.
4. GSDMM or BTM for short chat/social posts under a token threshold.
5. BERTopic-style c-TF-IDF as offline exploration using existing `timeline_embeddings` or static embeddings, not a new transformer dependency in the hot path.
6. Top2Vec only as a bounded exploratory sidecar.

Candidate tables:

```text
topic_seed_sets
topic_clusters
timeline_event_topics
entity_topic_profiles
topic_model_runs
```

Candidate verification:

- Top terms per topic reviewed by a human.
- Cluster stability across random seeds.
- Topic drift report by source and month.
- Runtime/RSS ceiling for topic jobs.
- Before/after false positives for `topical_similarity`.

Useful sources:

- scikit-learn NMF/LDA example: https://scikit-learn.org/stable/auto_examples/applications/plot_topics_extraction_with_nmf_lda.html
- gensim LDA docs: https://radimrehurek.com/gensim/models/ldamodel.html
- GSDMM paper: https://dbgroup.cs.tsinghua.edu.cn/wangjy/papers/KDD14-GSDMM.pdf
- BTM paper: https://xiaohuiyan.github.io/paper/BTM-WWW13.pdf
- GuidedLDA: https://github.com/vi3k6i5/GuidedLDA
- BERTopic c-TF-IDF docs: https://maartengr.github.io/BERTopic/getting_started/ctfidf/ctfidf.html
- BERTopic embeddings docs: https://maartengr.github.io/BERTopic/getting_started/embeddings/embeddings.html
- Top2Vec: https://github.com/ddangelov/top2vec

### Keyphrase and Deterministic Entity Extraction

Use this to extract indicators without GPU dependencies:

- RAKE for stopword-delimited candidate phrases.
- YAKE for single-document statistical keyphrases.
- TextRank for graph-ranked words/phrases.
- spaCy Matcher, PhraseMatcher, and EntityRuler for deterministic entities.
- Regex NER for emails, phones, URLs, domains, handles, tickers, wallet addresses, flight numbers, license plates, and platform-specific IDs.

Repo fit:

- `contact_extraction.py`, `bio_nlp.py`, and `handle_fanout.py` already point in this direction.
- This should become a shared extraction service so timeline, bios, captions, OCR, PDF text, and messages all use the same rules.

Candidate module:

- `src/pipeline/keyphrase_entity.py`

Candidate tables:

```text
extraction_patterns
timeline_extracted_entities
entity_extracted_indicators
```

Pattern governance:

- Store pattern name, version, label, confidence, source, and reviewer state.
- Keep high-risk regex behind tests and timeouts. Consider RE2-compatible patterns for untrusted large text.
- Separate exact gazetteer hits from inferred entities.

Useful sources:

- YAKE paper: https://www.sciencedirect.com/science/article/abs/pii/S0020025519308588
- YAKE implementation: https://github.com/INESCTEC/yake
- TextRank paper: https://web.eecs.umich.edu/~mihalcea/papers/mihalcea.emnlp04.pdf
- RAKE reference: https://www.pnnl.gov/publications/automatic-keyword-extraction-individual-documents
- spaCy rule-based matching: https://spacy.io/usage/rule-based-matching
- spaCy EntityRuler: https://spacy.io/api/entityruler
- spaCy Matcher: https://spacy.io/api/matcher

### Near-Duplicate Detection and Repost Suppression

Problem:

- Social data repeats heavily: forwarded messages, retweets, identical captions, reposted screenshots, copied bios, and OCR boilerplate.
- Duplicate content can inflate topic counts, burst alerts, relationship weights, and stylometry confidence.

Recommended:

- Use MinHash + LSH over shingles for near-duplicate candidate generation.
- Verify candidate pairs with exact Jaccard before storing duplicate edges.
- Use SimHash as a cheaper secondary fingerprint for near-exact text and fast bucketing.
- Use dynamic shingle strategy: char 5-grams for medium text, token 2-grams/3-grams for short text, and fallback exact normalization for very short posts.

Candidate fields:

```text
text_sha1
simhash64
minhash_signature
lsh_bands int[]
duplicate_cluster_id
duplicate_role
duplicate_confidence
```

Postgres design:

- If storing LSH in Postgres, store band hashes as integer arrays and index them with GIN.
- Candidate query: find rows whose `lsh_bands && query_bands`, then compute exact Jaccard in Python for a bounded candidate set.

Downstream use:

- Do not delete duplicates.
- Mark them and let downstream jobs choose whether to count originals only, collapse clusters, or treat repeated posting as a signal.

Useful sources:

- Broder resemblance/containment paper: https://ieeexplore.ieee.org/document/666900
- datasketch LSH docs: https://ekzhu.com/datasketch/lsh.html
- SimHash paper: https://dl.acm.org/doi/10.1145/509907.509965
- Deduplicating training data: https://arxiv.org/abs/2107.06499
- RefinedWeb dataset: https://arxiv.org/abs/2306.01116

### Trend and Burst Detection

Recommended split:

- Online cheap detectors for near-real-time alerts: rolling z-score, EWMA, CUSUM, source-aware baseline, and minimum-count gates.
- Batch detectors for richer analysis: Kleinberg burst hierarchy, log-likelihood ratio term keyness, chi-square only when expected counts are safe.

Candidate burst targets:

- hashtags
- handles
- domains
- venues
- extracted people/orgs/products
- keyphrases
- emotion dimensions
- duplicate clusters
- communities
- location cells

Collection-gap safeguards:

- Suppress burst alerts when source health is poor.
- Require minimum historical baseline.
- Require a minimum number of distinct authors/entities.
- Collapse near-duplicates before computing term spikes unless the goal is coordinated repost detection.

Candidate tables:

```text
term_time_series
burst_runs
burst_events
trend_snapshots
```

Useful sources:

- Kleinberg burst detection: https://www.cs.cornell.edu/home/kleinber/kdd02.html
- Twitter AnomalyDetection: https://github.com/twitter/AnomalyDetection
- Seasonal-Hybrid ESD package docs: https://rdrr.io/github/twitter/AnomalyDetection/man/AnomalyDetectionTs.html
- Rayson/Garside log-likelihood reference: https://aclanthology.org/W00-0901.pdf

### Text Similarity and CPU Embeddings

Use the cheapest representation that answers the question:

- Exact keyword lookup: Postgres FTS or SQLite FTS5.
- Streaming classifier: `HashingVectorizer` plus partial-fit classifiers.
- Fast supervised labels: fastText.
- OOV-friendly word vectors: fastText subword vectors.
- Interpretable topics: NMF on TF-IDF.
- Entity-level semantic similarity: existing `timeline_embeddings`, but block candidates first.
- Offline semantic exploration: static pooled vectors or small CPU sentence models.

Repo-specific warning:

- Do not let `topical_similarity.py` remain O(E^2). Block candidate pairs by topic bucket, source, time, watch status, community, or ANN nearest neighbors.

Useful sources:

- scikit-learn HashingVectorizer: https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.HashingVectorizer.html
- fastText text classification: https://fasttext.cc/docs/en/supervised-tutorial.html
- fastText project: https://fasttext.cc/
- spaCy embeddings/static vectors: https://spacy.io/usage/embeddings-transformers
- MiniBatchKMeans: https://scikit-learn.org/stable/modules/generated/sklearn.cluster.MiniBatchKMeans.html

### Bot, Spam, and Coordinated Behavior Heuristics

Cheap features that fit this repo:

- duplicate-content ratio
- posting rate and burstiness
- account age where available
- follower/following skew
- fraction of posts with URLs
- hashtag entropy
- repeated caption templates
- language switching rate
- cross-platform copy delay
- graph reciprocity ratio
- source coverage gaps

Candidate storage:

- `behavioral_profiles.metadata.bot_heuristics`
- `entity_relationships.sources.coordinated_behavior`
- `alerts` for coordinated-posting and high-duplication clusters

Verification:

- Use known benign reposts and known spammy clusters as fixtures.
- Require human review before using bot/spam as a high-impact label.

### Network and Graph Analysis

Current repo already has:

- Directed `entity_interactions`.
- Aggregate `entity_relationships`.
- A frontend `ConnectionsPanel` and `NetworkGraph`.
- Graph-related pipeline phases.

Recommended next layer:

- Build graph snapshots by time window and edge family.
- Compute communities with Leiden for well-connected communities.
- Use Louvain only as a simple baseline.
- Compute degree, weighted degree, PageRank, betweenness, reciprocity, and bridge roles.
- Store community and centrality results with run IDs and graph filters.

Candidate tables:

```text
graph_snapshots
entity_graph_metrics
entity_communities
community_edges
bridge_findings
```

Frontend improvements:

- Time-windowed graph semantics: clearly distinguish standing relationships from in-window interactions.
- Selectable nodes and edges.
- Edge evidence drawer.
- Community legend.
- Top-N and edge-family controls.

Useful sources:

- Leiden paper: https://www.nature.com/articles/s41598-019-41695-z
- leidenalg docs: https://leidenalg.readthedocs.io/en/stable/intro.html
- Louvain paper: https://arxiv.org/abs/0803.0476
- NetworkX betweenness centrality: https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.centrality.betweenness_centrality.html
- CDlib: https://cdlib.readthedocs.io/

### Geospatial and Intersection Intelligence

Current risk:

- `location_evidence` has no explicit spatial index model.
- `/intersect` computes physical/digital overlap from current tables and Python-side logic.

Recommended:

- Add `entity_geo_events` as a precomputed read model.
- Store accuracy radius, source, reviewer status, public-place flags, and location confidence.
- Use PostGIS if the deployment can accept the extension. If not, use geohash/S2/H3 style cell bucketing as a lower-friction option.
- Precompute digital/physical overlaps for watched entities and case members.

Candidate improvements:

- Public-place fan-out guard.
- Reviewer rejection excluded by construction.
- Staypoint detection for repeated locations.
- Co-travel and shared-origin features with source health gates.

Useful sources:

- PostGIS `ST_DWithin`: https://postgis.net/docs/ST_DWithin.html
- PostGIS radius query tip: https://postgis.net/documentation/tips/st-dwithin/

### Identity, Provenance, and Review

Highest-leverage review upgrades:

- Uncertainty-ranked review queue.
- Diversity-aware queue so one signal type does not dominate.
- "Not enough evidence" action distinct from "not same person".
- Calibration reliability metrics: Brier score, log loss, bins, reliability curves.
- Threshold evaluation at operating points: 55, 70, 85.
- Term-frequency adjusted name/username evidence.
- Blocking diagnostics for candidate generation.
- Transitive conflict detector: A close to B, B close to C, but A dismissed from C.
- Model/scorer fingerprint stored with every candidate.
- Append-only `identity_label_events` alongside latest labels.

Provenance fields to add consistently:

```text
source_table
source_record_id
source_column
algorithm
algorithm_version
parameter_hash
run_id
generated_at
confidence_0_1
confidence_0_100
review_state
audit_log_id
```

Standards fit:

- W3C PROV-O is useful for generated-from and was-derived-from semantics.
- STIX confidence, note, and opinion patterns are useful for export or analyst-facing confidence language.

Useful sources:

- Dedupe active learning/API docs: https://docs.dedupe.io/en/latest/API-documentation.html
- scikit-learn calibration docs: https://scikit-learn.org/stable/modules/calibration.html
- Splink term-frequency adjustments: https://moj-analytical-services.github.io/splink/topic_guides/comparisons/term-frequency.html
- Splink Fellegi-Sunter theory: https://moj-analytical-services.github.io/splink/topic_guides/theory/fellegi_sunter.html
- W3C PROV-O: https://www.w3.org/TR/prov-o/
- STIX 2.1: https://docs.oasis-open.org/cti/stix/v2.1/os/stix-v2.1-os.html

### Frontend Product Improvements

The frontend already has the right primitives:

- `EntityDetail`
- `TimelineLanes`
- `GeoMap`
- `ConnectionsPanel`
- `NetworkGraph`
- `Review`
- `Media`
- `Faces`
- `Cases`

The next product move is not another isolated tab. It is a fused investigation surface.

Recommended:

1. Add a first-class `Life Graph` tab that contains time brush, event feed, map, graph, media/faces, and selected-evidence drawer.
2. Promote the time brush to a persistent entity-page control.
3. Add a shared evidence inspector drawer for timeline events, map points, graph edges, media items, face matches, topic clusters, and burst hits.
4. Add bidirectional cross-highlighting.
5. Consolidate review into an analyst queue: identity, relationship, location, media-person, unmatched face, low-confidence source, and topic/burst review.
6. Make `Cases` evidence bundles with pinned events, media, graph edges, intersections, notes, and time windows.
7. Add a dedicated search workflow for keyword, semantic, hybrid, and topics.

Frontend risks to address:

- `People` has a client-side tier filter applied only to the fetched page. Move this server-side.
- Confidence display should encode whether a score is 0..1, 0..100, or edge weight.
- Connections graph needs explicit time semantics.
- Global Media/Faces pages should support entity/case/review pivots.

## Proposed Data Products

### `timeline_text_features`

Purpose: durable event-level text intelligence.

Columns:

```text
event_id uuid primary key
entity_id uuid not null
occurred_at timestamptz not null
source text not null
source_record_id text
text_sha1 text
canonical_text text
language_code text
language_confidence real
token_count int
features jsonb not null default '{}'
tsv tsvector
simhash64 bigint
lsh_bands bigint[]
processed_at timestamptz not null default now()
method_versions jsonb not null default '{}'
```

Indexes:

```text
(entity_id, occurred_at desc)
GIN(tsv)
GIN(lsh_bands)
GIN(features jsonb_path_ops)
(text_sha1)
```

### `timeline_event_topics`

Purpose: attach taxonomy and topic-cluster membership to events.

Columns:

```text
event_id
topic_id
topic_source
score
confidence
model_run_id
is_context_only
```

### `topic_clusters`

Purpose: durable topics/stories that the UI can inspect.

Columns:

```text
topic_id
run_id
label
method
top_terms_json
source_mix_json
entity_count
event_count
first_seen_at
last_seen_at
quality_json
```

### `duplicate_clusters`

Purpose: collapse repeats when needed without deleting evidence.

Columns:

```text
cluster_id
method
canonical_event_id
event_count
entity_count
source_mix_json
first_seen_at
last_seen_at
confidence
```

### `entity_life_events`

Purpose: fast profiler read model that unifies physical, digital, media, relationship, and review evidence.

Columns:

```text
life_event_id
entity_id
occurred_at
lane
event_family
direction
counterparty_entity_id
source
source_record_id
title
detail
confidence_0_1
provenance_json
payload_json
```

### `pipeline_coverage_snapshots`

Purpose: expose the real bottlenecks: unresolved rows, missing links, skipped sources, and stale phases.

Columns:

```text
snapshot_id
run_id
phase
source
processed_count
attributed_count
unresolved_count
skipped_count
top_unresolved_json
duration_ms
created_at
```

## Candidate Code Modules

Do not implement all at once. These are natural boundaries for future work:

```text
src/pipeline/text_normalizer.py
src/pipeline/timeline_text_features.py
src/pipeline/sentiment_emotion.py
src/pipeline/keyphrase_entity.py
src/pipeline/text_dedup.py
src/pipeline/hybrid_search_index.py
src/pipeline/short_text_topics.py
src/pipeline/burst_detection.py
src/pipeline/community_graph.py
src/pipeline/pipeline_coverage.py
src/api/routes/search.py
src/api/routes/topics.py
src/api/routes/pipeline_status.py
frontend/src/pages/Search.tsx
frontend/src/components/EvidenceInspector.tsx
frontend/src/components/LifeGraphWorkspace.tsx
```

## Implementation Order

### Phase A: Measurement and Contracts

1. Add scratch-DB schema contract tests.
2. Add a live read-only verification report for counts, unresolved rates, orphan rates, rejected locations, and hot endpoint plans.
3. Normalize confidence and provenance contracts.
4. Add coverage snapshots from timeline and interaction builders.

Why first: every later feature needs trustworthy measurement.

### Phase B: Text Feature Foundation

1. Add canonical text extraction and `timeline_text_features`.
2. Add language detection and normalization.
3. Add VADER/AFINN/NRC sentiment and emotion.
4. Add keyphrase/rule-entity extraction.
5. Add MinHash/SimHash duplicate fingerprints.

Why second: this creates shared features for search, topics, bursts, bot heuristics, graph summaries, and UI filters.

### Phase C: Hybrid Search

1. Add Postgres FTS.
2. Keep dense pgvector search.
3. Fuse with RRF.
4. Add snippets, matched fields, and frontend search.
5. Add retrieval evaluation cases.

Why third: search is immediate product value and creates an evaluation harness.

### Phase D: Topics, Trends, and Dedup-Aware Alerts

1. Add taxonomy classifier.
2. Add short-text clusters.
3. Add burst detection.
4. Make alerts source-health-aware and duplicate-aware.

Why fourth: topics and bursts are noisy until duplicates, text normalization, and baseline windows exist.

### Phase E: Graph and Life-Graph Workspace

1. Materialize `entity_life_events`.
2. Add reciprocal target-side timeline materialization.
3. Precompute geo/intersection read models.
4. Add graph communities and bridge metrics.
5. Build the fused Life Graph frontend.

Why fifth: this is the visible expression of the vision, but it depends on clean evidence.

### Phase F: Review, Provenance, and Export

1. Add uncertainty-ranked review lanes.
2. Add append-only label events.
3. Add confidence reliability reports.
4. Make cases evidence bundles.
5. Add STIX/MISP/OpenCTI-inspired export shapes if useful.

Why sixth: once signals are richer, reviewer workflows need to prevent false certainty.

## Verification Gates

For each new intelligence feature, require:

- Unit tests for feature extraction edge cases.
- Idempotent backfill test.
- Scratch DB schema application test.
- Live read-only count report after deployment.
- Phase duration and memory ceiling.
- API response snapshot if exposed.
- Frontend build if UI touched.
- At least one golden query or fixture proving the feature handles social text, short text, emojis, handles, and duplicated content.

Suggested golden sets:

- Exact handle and hashtag queries.
- Known duplicate/cross-post clusters.
- English negation and emoji sentiment.
- Sarcasm/irony flags.
- Malay/Indonesian/Chinese/Singlish code-mix samples.
- Repeated group/public-location false positives.
- Multi-target mentions/tags/faces to prove source-record uniqueness.

## Dependency Notes

Low-risk additions:

- `vaderSentiment`
- `rank_bm25` for evaluation/prototyping, not production index
- `yake`
- `rake-nltk`
- `datasketch`
- `langid` or fastText model file if build friction is acceptable

Already aligned with repo dependencies:

- `scikit-learn`
- `hdbscan`
- `umap-learn`
- `spacy`
- `pgvector`
- `rapidfuzz`

Evaluate carefully:

- `fasttext` Python package can require compiler/build work on Windows and Linux containers.
- `python-Levenshtein` style accelerators are useful but should not change scoring semantics silently.
- `leidenalg` requires igraph and can add binary dependency complexity.
- `PostGIS` is the right geospatial tool but changes DB extension assumptions.
- `ParadeDB/pg_search` gives true BM25 but adds deployment and extension drift risk.
- LIWC is proprietary.
- SO-CAL can bring Java/CoreNLP overhead.
- Top2Vec/BERTopic should stay offline until memory/runtime budgets are proven.

Storage/search guidance:

- This repo already uses Postgres and pgvector, so first-class production search should stay in Postgres unless there is a strong reason to split.
- SQLite FTS5 is excellent for embedded hobby-scale search and can be used for offline prototypes or local artifact bundles, but it should not become a second production truth beside Postgres without a sync plan.
- DuckDB is a good fit for offline analytical SQL over exported snapshots/Parquet.
- Polars is a good fit for memory-conscious batch reports and feature audits.

Useful sources:

- DuckDB why page: https://duckdb.org/why_duckdb.html
- Polars docs: https://docs.pola.rs/
- SQLite FTS5: https://www.sqlite.org/fts5.html

## Risks and Guardrails

1. Do not present lexicon sentiment as ground truth.
2. Do not use emotion or topic features as identity evidence unless explicitly reviewed and modeled as weak context.
3. Do not let duplicate suppression delete evidence.
4. Do not add heavy topic models to the scheduler hot path before measuring memory and runtime.
5. Do not run regex extractors over untrusted large text without timeout or linear-time guarantees.
6. Do not allow confidence scales to remain implicit.
7. Do not let graph centrality label someone as influential without showing edge types, time windows, and source coverage.
8. Do not optimize UI visuals before defining evidence selection, provenance, and review semantics.

## Most Valuable First Slice

If only one engineering slice is funded first, do this:

1. `timeline_text_features` table.
2. Canonical text builder.
3. Language detection and normalizer.
4. VADER/AFINN/NRC sidecar.
5. Postgres FTS plus dense/FTS RRF in `/api/search/timeline`.
6. Minimal frontend search page with result click-through.
7. Retrieval and sentiment fixture tests.

That slice is small enough to implement safely, but it unlocks search, emotion baselines, keyphrases, topics, bursts, dedup, and analyst-facing evidence inspection.

## Research Link Index

Sentiment and emotion:

- VADER paper: https://ojs.aaai.org/index.php/ICWSM/article/view/14550
- VADER package: https://pypi.org/project/vaderSentiment/
- AFINN: https://arxiv.org/abs/1103.2903
- SentiWordNet: https://aclanthology.org/L10-1531/
- NRC EmoLex: https://arxiv.org/abs/1308.6297
- SO-CAL: https://aclanthology.org/J11-2001.pdf
- LIWC: https://www.liwc.app/help/howitworks

Search and topic modeling:

- Lucene BM25: https://lucene.apache.org/core/7_0_1/core/org/apache/lucene/search/similarities/BM25Similarity.html
- Stanford IR BM25: https://nlp.stanford.edu/IR-book/html/htmledition/okapi-bm25-a-non-binary-model-1.html
- PostgreSQL text search: https://www.postgresql.org/docs/current/textsearch-controls.html
- pgvector: https://github.com/pgvector/pgvector
- GSDMM: https://dbgroup.cs.tsinghua.edu.cn/wangjy/papers/KDD14-GSDMM.pdf
- BTM: https://xiaohuiyan.github.io/paper/BTM-WWW13.pdf
- BERTopic: https://maartengr.github.io/BERTopic/getting_started/ctfidf/ctfidf.html
- Top2Vec: https://github.com/ddangelov/top2vec

Keyphrases, dedup, and bursts:

- RAKE: https://www.pnnl.gov/publications/automatic-keyword-extraction-individual-documents
- YAKE: https://www.sciencedirect.com/science/article/abs/pii/S0020025519308588
- TextRank: https://web.eecs.umich.edu/~mihalcea/papers/mihalcea.emnlp04.pdf
- Broder MinHash: https://ieeexplore.ieee.org/document/666900
- datasketch LSH: https://ekzhu.com/datasketch/lsh.html
- SimHash: https://dl.acm.org/doi/10.1145/509907.509965
- Kleinberg burst detection: https://www.cs.cornell.edu/home/kleinber/kdd02.html
- Twitter AnomalyDetection: https://github.com/twitter/AnomalyDetection
- Rayson/Garside log-likelihood: https://aclanthology.org/W00-0901.pdf

Graph, geospatial, and provenance:

- Leiden: https://www.nature.com/articles/s41598-019-41695-z
- Louvain: https://arxiv.org/abs/0803.0476
- NetworkX centrality: https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.centrality.betweenness_centrality.html
- PostGIS `ST_DWithin`: https://postgis.net/docs/ST_DWithin.html
- W3C PROV-O: https://www.w3.org/TR/prov-o/
- STIX 2.1: https://docs.oasis-open.org/cti/stix/v2.1/os/stix-v2.1-os.html
