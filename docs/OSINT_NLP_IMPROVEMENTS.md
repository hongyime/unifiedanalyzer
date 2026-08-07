# OSINT NLP & Intelligence Enhancements for `unifiedanalyzer`

This document outlines a comprehensive roadmap to integrate classical, non-deep-learning NLP and graph intelligence techniques into the `unifiedanalyzer` OSINT pipeline. These enhancements are designed to run efficiently on CPU, process short, noisy social media streams at scale, and deliver actionable threat intelligence.

---

## 1. Lexicon-Based Sentiment & Emotion Analysis

**Goal**: Transform simple interaction counts into a Directed Emotional Relationship Graph and build psychological profiles of entities.

**Recommended Tools**: VADER (valence/polarity), NRC EmoLex (8 core emotions), AFINN (intensity fallback), Custom OSINT Dictionary.

### Key Enhancements:
- **Psychological Baseline Profiling**: Update `behavioral_profiles` to track rolling mean valence ($\mu$) and standard deviation ($\sigma$) per entity. Store an 8-emotion distribution (`emotion_dist` JSONB) to monitor escalation, volatility, or radicalization.
- **Directed Emotional Edges**: Annotate `entity_interactions` and `entity_relationships` with compound sentiment scores and dominant emotions. Distinguish between hostile interactions (trolling/harassment) and supportive affinity edges.
- **Emotional Spikes & Alerts**: Augment `alert_engine.py` with `EMOTIONAL_SPIKE` (sudden drop in compound sentiment or spike in Anger/Fear) and `COORDINATED_HOSTILITY` alerts.
- **Hybrid Search**: Allow filtering of timeline events by dominant emotion combined with semantic vector search.

---

## 2. Topic Modeling & Text Ranking

**Goal**: Accurately categorize short social texts and improve exact-match searchability for critical OSINT identifiers.

**Recommended Tools**: BERTopic (using existing HDBSCAN/UMAP) or BTM (Biterm Topic Model); Okapi BM25.

### Key Enhancements:
- **BM25 Hybrid Search (RRF)**: Dense vectors (`e5-small`) struggle with exact handles, phone numbers, and crypto wallets. Implement a `tsvector` column with GIN index (`idx_timeline_fts`) in `timeline_events`. Update `/api/routes/search.py` to use Reciprocal Rank Fusion (RRF), combining dense vector similarity with sparse BM25 text rank for precise keyword recall.
- **Short-Text Topic Modeling**: Use BERTopic (UMAP + HDBSCAN + c-TF-IDF over existing embeddings) or a Biterm Topic Model to extract coherent topics from 10-word chats. 
- **Entity Topical Divergence**: Upgrade `topical_similarity.py` to calculate Jensen-Shannon Divergence over topic distributions rather than just averaging dense centroids, reducing false positives caused by generic social media syntax.

---

## 3. Unsupervised Keyphrase & Deterministic Entity Extraction

**Goal**: Extract OSINT topics, brands, locations, and technical indicators without heavy GPU-dependent models.

**Recommended Tools**: YAKE! (event-level keyphrases), TextRank (entity-level summarization), spaCy EntityRuler, Regex.

### Key Enhancements:
- **Event-Level Extraction (Phase 1)**: Use YAKE! (1-2ms per doc) and deterministic Regex (crypto wallets, IP addresses, `.onion` links) during `timeline_builder.py`. Enrich `timeline_events.metadata` with an extracted entity JSON payload.
- **Entity-Level Profiling (Phase 2)**: Use TextRank in `entity_enrichment.py` over an entity's recent $N$ posts to synthesize an overall `osint_profile` (top keyphrases, unique wallets, frequent locations).
- **Topic Relationship Graph (Phase 3)**: Create a new step in `incremental_runner.py` to emit `entity_relationships` (e.g., `shared_crypto_wallet` or `shared_topic_cluster`) based on overlapping extracted identifiers.

---

## 4. Deduplication, Clustering & Coordinated Inauthentic Behavior (CIB)

**Goal**: Filter out cross-platform repost/forward noise and mathematically detect coordinated bot campaigns or sockpuppets.

**Recommended Tools**: datasketch (MinHash + LSH), MiniBatchKMeans, HDBSCAN.

### Key Enhancements:
- **MinHash + LSH Deduplication**: Convert post texts into 3-gram shingles, generate 128-perm MinHash signatures, and index via LSH. If an incoming post matches an existing one with Jaccard $\ge 0.85$, mark `is_duplicate=TRUE` and set `canonical_event_id`. This saves up to 60% of downstream processing.
- **Content-Gated Coordinated Campaigns**: Enhance `COORDINATED_POSTING` in `alert_engine.py`. Instead of time-only checks, use LSH to find different entities posting highly similar content ($\ge 70\%$ Jaccard) within a 15-minute window. Build a campaign graph to emit high-confidence alerts.
- **Two-Stage Hybrid Clustering**: For massive embedding datasets, run Stage 1 `MiniBatchKMeans` (macro partitioning) followed by Stage 2 `HDBSCAN` (micro discovery). This reliably isolates fine-grained narrative clusters while discarding ambient noise (label `-1`).

---

## 5. Network Graph Analysis & Bridge Detection

**Goal**: Move beyond simple centrality to map hierarchical threat syndicates and isolate true cross-community liaisons (bridges) from internal community hubs.

**Recommended Tools**: Leiden Community Detection, Node Cartography (Participation Coefficient & Within-Module Z-Score).

### Key Enhancements:
- **Leiden Algorithm Migration**: Replace the current Label Propagation Algorithm (LPA) with Leiden. Leiden guarantees internally well-connected sub-communities and supports multi-resolution hierarchical trees (macro, meso, micro cells).
- **Functional Node Cartography**: Calculate Participation Coefficient ($P_i$) and Within-Module Degree $z$-score ($z_i$) for every entity:
  - **Connector Hub**: High $z_i$, High $P_i$ (Multi-group coordinator)
  - **Provincial Hub**: High $z_i$, Low $P_i$ (Single-cell admin)
  - **Liaison/Bridge**: Low $z_i$, High $P_i$ (Low-visibility cross-group courier)
- **UI Updates**: Display hierarchical community assignments and "Role Badges" on the entity details and network graph frontend panels.

---

## 6. Trend and Burst Detection

**Goal**: Mathematically detect sudden activity spikes, emerging hashtag usage, or behavioral regime shifts without arbitrary time-binning.

**Recommended Tools**: Kleinberg Burst Detection, Rolling Z-Score / EWMA, CUSUM.

### Key Enhancements:
- **ACTIVITY_BURST Alerts (Kleinberg)**: Run Kleinberg Burst Detection directly on exact `occurred_at` timestamps (continuous-time). This accurately identifies multi-scale temporal bursts (panic posting, bot sweeps) and outputs a burst state level ($s \ge 2$). Store in a new `entity_bursts` table.
- **HASHTAG_SPIKE Alerts (Rolling Z-Score)**: Calculate sliding 14-day rolling mean/stddev for daily hashtag usage. Emit alerts when $Z \ge 3.0$.
- **BEHAVIORAL_REGIME_SHIFT (CUSUM)**: Use Cumulative Sum control charts to detect permanent baseline shifts (e.g., entity permanently shifts active hours from day to night, or doubles daily volume).
- **UI Enhancements**: Add a "Trend & Burst Spotlight" widget to `EntityDetail.tsx`, and render translucent "Burst Range" shaded bands on the background of `TimelineLanes.tsx`.

---

### Implementation Path Summary
1. **Schema Migrations**: Add required JSONB columns (`sentiment_profile`, `osint_profile`, `emotion_dist`), FTS vectors (`fts_vec`), deduplication identifiers (`canonical_event_id`), and an `entity_bursts` table.
2. **Library Updates**: Add `datasketch`, `vaderSentiment`, `yake`, `networkx`, and `leidenalg` to `requirements.txt` (CPU-friendly, low-overhead).
3. **Pipeline Runner**: Insert the new lightweight extractors (`osint_extraction_engine`, `minhash_service`, `sentiment_analyzer`) into the event ingestion path. Map the aggregate/clustering steps (`topic_modeler`, `burst_detector`, `graph_analytics`) to the nightly/incremental scheduler phases.
