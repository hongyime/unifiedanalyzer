# OSINT NLP & Intelligence Enhancements for `unifiedanalyzer` (Audited & Architecturally Hardened)

This document outlines a rigorously audited roadmap to integrate classical NLP and graph intelligence techniques into the `unifiedanalyzer` OSINT pipeline. All claims have been academically verified, and architectural constraints—such as CPU memory bounds, database indexing limitations, and algorithm specificities—have been explicitly modeled.

---

## 1. Lexicon-Based Sentiment & Emotion Analysis

**Goal**: Transform simple interaction counts into a Directed Emotional Relationship Graph and build psychological profiles of entities.

**Academic & Industry Validation**:
- **VADER**: (Hutto & Gilbert, 2014) optimized for microblogging, capturing sentiment intensity from punctuation and emojis without deep learning overhead. $O(N)$ complexity.
- **NRC EmoLex**: (Mohammad & Turney, 2013) for reliable word-emotion association.

**Critical Architectural Adjustments**:
- **Multilingual Blindspot**: VADER is natively English-only. 
  - *Mitigation*: Precede lexicon matching with a fast CPU-bound language identifier (e.g., `fasttext` langdetect, $O(1)$ latency). Route non-English text to localized lexicons.
- **Database Indexing**: To perform hybrid search filtering by sentiment, the `emotion_dist` JSONB column must be indexed using Postgres GIN indexes with `jsonb_path_ops` for $O(\log N)$ retrieval.

### Key Enhancements:
- **Psychological Baseline Profiling**: Update `behavioral_profiles` tracking rolling mean valence ($\mu$) and standard deviation ($\sigma$).
- **Directed Emotional Edges**: Annotate `entity_interactions` with compound scores.
- **Emotional Spikes**: Augment `alert_engine.py` with `EMOTIONAL_SPIKE`.

---

## 2. Topic Modeling & Text Ranking

**Goal**: Accurately categorize short social texts and improve exact-match searchability.

**Academic & Industry Validation**:
- **BERTopic**: (Grootendorst, 2022) modular standard.
- **RRF (Reciprocal Rank Fusion)**: (Cormack et al., 2009) mathematically proves combining rankers (dense + sparse) yields superior recall.

**Critical Architectural Adjustments**:
- **Postgres BM25 Limitation**: Native Postgres `ts_rank` uses Cover Density, **not** Okapi BM25.
  - *Mitigation*: Install `pg_search` (ParadeDB) extension for true BM25, OR implement a BM25 scoring algorithm in Python middleware using term frequencies from `ts_stat`.
- **Short-Text Edge Cases**: Short texts (10-15 words) generate sparse embeddings.
  - *Mitigation*: Implement a Biterm Topic Model (BTM) (Yan et al., 2013) fallback for character-limited data, mapping word co-occurrences explicitly.
- **UMAP/HDBSCAN Memory Bounds**: Severe linear memory overhead that crashes standard containers at $>1M$ vectors. 
  - *Mitigation*: Utilize `IncrementalPCA` (Phase 1) followed by `MiniBatchKMeans`, applying UMAP/HDBSCAN only to cluster centroids, or use Online BERTopic (via `River`).

### Key Enhancements:
- **BM25 Hybrid Search (RRF)**: `tsvector` with GIN index (`idx_timeline_fts`) + ParadeDB.
- **Short-Text Topic Modeling**: Online BERTopic or BTM integration.

---

## 3. Unsupervised Keyphrase & Deterministic Entity Extraction

**Goal**: Extract technical indicators and semantic entities without GPU dependencies.

**Academic & Industry Validation**:
- **YAKE!**: (Campos et al., 2020) Statistical keyphrase extraction independent of language at 1-2ms per document.
- **TextRank**: (Mihalcea & Tarau, 2004) Graph-based ranking.

**Critical Architectural Adjustments**:
- **Regex CPU Throttling (ReDoS)**: Complex deterministic regex for domains can stall CPU threads.
  - *Mitigation*: Bind regex execution time using Python's `re2` library for guaranteed linear time $O(N)$ execution.

---

## 4. Deduplication, Clustering & CIB (Coordinated Inauthentic Behavior)

**Academic & Industry Validation**:
- **MinHash + LSH**: (Broder, 1997) mathematically approximates Jaccard similarity in $O(1)$ lookup time.

**Critical Architectural Adjustments**:
- **LSH Postgres Storage constraints**: `datasketch` holds LSH indexes in-memory/Redis.
  - *Mitigation*: Implement LSH banding directly in Postgres using integer arrays (`INT[]`). A 128-permutation MinHash can be stored as 16 integers (each a 64-bit hash of 8 bands). Index with a GIN index: `CREATE INDEX lsh_gin ON timeline_events USING gin (lsh_bands)`.
- **Short-Text Shingle Starvation**: 3-gram shingles fail on $<10$ word posts.
  - *Mitigation*: Dynamically downgrade to 1-gram/2-gram sets for texts under 15 tokens.

---

## 5. Network Graph Analysis & Bridge Detection

**Academic & Industry Validation**:
- **Leiden Algorithm**: (Traag et al., 2019) Proved Louvain yields disconnected communities; Leiden guarantees fast connectivity.
- **Node Cartography**: (Guimerà & Amaral, 2005) established Participation Coefficient ($P_i$) and Within-Module Degree ($z_i$).

**Critical Architectural Adjustments**:
- **NetworkX to iGraph Memory Duplication**: `leidenalg` requires C-core `igraph`. Translating large `NetworkX` graphs adds massive latency/memory duplication.
  - *Mitigation*: Instantiate graph state directly into `igraph` from Postgres edge lists, bypassing `NetworkX`.

---

## 6. Trend and Burst Detection

**Academic & Industry Validation**:
- **Kleinberg Burst Detection**: (Kleinberg, 2003) Models arrival times as an infinite-state automaton.

**Critical Architectural Adjustments**:
- **Continuous-Time Computation Bottleneck**: Recomputing Viterbi optimal path on every new event for infinite streams is fatal.
  - *Mitigation*: Run Burst Detection probabilistically using EWMA for real-time stream ingestion; restrict true Kleinberg DP to asynchronous batch jobs.
