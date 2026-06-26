# UnifiedAnalyzer — System Overview

> A runthrough of how the analyzer works, written for a technical reader (or a
> second-opinion LLM) to critique and suggest improvements. Reflects the state
> of the codebase + live DB as of 2026-06-25. Numbers in the "Live state"
> section are real row counts from the running system.

---

## 1. Mission

The collector (`unifiedcollector`) is a multi-platform scraping firehose:
Instagram, Threads, TikTok, Lemon8, X/Twitter, Facebook, Telegram (4 accts),
Beeper, WhatsApp (2 bridges), YouTube, Strava, and a low-risk OSINT/search
collector (yearbooks, open buckets, PDFs, obituaries). It writes raw rows
(`*_posts`, `*_comments`, `*_profiles`, `social_users`, `telegram_messages`,
media files on disk, etc.) into the **`unifiedcollector`** Postgres DB.

The **analyzer** turns that raw firehose into **resolved identities and
intelligence**: it decides which accounts across all platforms are the *same
real person* (an `entity`), builds a unified cross-platform timeline per
person, profiles their behavior and location, and raises alerts. It is, in
essence, a cross-platform **identity-resolution + OSINT correlation engine**.

## 2. Architecture

- **Two Postgres databases, one server.** Both `unifiedcollector` and
  `unifiedanalyzer` databases live in the shared `unifiedcollector_postgres`
  container (port 5500 on host). The analyzer **reads** collector tables and
  **writes** its own `public.*` tables in the `unifiedanalyzer` DB. The
  vendored face engine writes to a separate **`facetracker` schema** inside the
  same analyzer DB.
- **Three analyzer processes** (Docker, image `unifiedanalyzer:latest`, all on
  the `unifiedcollector_default` network):
  1. **`analyzer`** (`python -m src.main serve`) — FastAPI API + bundled
     dashboard frontend on port **8002**. Read-only over the computed tables.
  2. **`scheduler`** (`python -m src.main scheduler`) — long-lived loop that
     drives the analysis pipeline. Split into its own process so its blocking
     cv2/ffmpeg/pdf/ONNX work never freezes the API event loop. `cpu_shares:512`
     (yields under host contention).
  3. **`face_worker`** (`python -m src.face_worker loop`) — separate
     SQLAlchemy+ONNX process running InsightFace over media. Owns the
     `facetracker` schema. Now **also scans local/network drives** (see §7).
- **Storage:** derived media artifacts (PDF page images, video frames, face
  crops, FAISS, ONNX models) live on **Z:** (`Z:/unifiedanalyzer/media_derived`),
  never C: (space-constrained). Collector source media is read-only from
  `Z:/unifiedcollector/media`.

## 3. The scheduler loop

`src/scheduler/scheduler.py` wakes on an interval and:
- Checks collector health (`collection_runs`: flags a source with no run in >6h
  or ≥3 failures/24h) → Telegram notification.
- Runs **`run_incremental()`** frequently (live cadence ≈ every 2h) and
  **`run_full_resolution()`** ~daily.
- Sends a daily digest (entity counts by tier, alerts, events, run health,
  most-active entity) and merge-candidate notifications.
- Uses a DB run-lock (`analysis_runs.status='running'`) with stale-lock
  cleanup so two runs never overlap; orphaned locks are cleared on startup.

## 4. The incremental pipeline (the core)

`src/pipeline/incremental_runner.py :: run_incremental()` runs these phases in
order. Each reads collector data and/or prior analyzer output and writes
signals/results:

1. **`resolve_entities`** (entity_resolver) — clusters platform accounts
   (`social_users`, profiles) into unified **`entities`**, writing
   `entity_platform_links`. **Conservative + target-anchored**: entities are
   seeded from your actual collection targets (not blind global clustering).
   Order of precedence is high-precision-first — **username-exact** match across
   platforms (Phase 1), then phone / commit-email / profile-photo-SHA256
   enrichment (Phase 3); **fuzzy name is only used as a *secondary* signal that
   requires a second independent signal** ("name alone is insufficient"), and
   `IDENTITY_MIN_SIGNALS=2` independent signals are required overall. So
   over-merging on a common name like "John Smith" is guarded against at the
   resolver level, not just down-weighted later.
2. **`build_timeline(since)`** — merges every dated item (posts, messages,
   activities, comments…) across platforms into **`timeline_events`** per
   entity. Incremental by `since`.
3. **`run_alerts`** (alert_engine) — pattern detection over the timeline →
   **`alerts`** (e.g. SILENCE_GAP, COORDINATED_POSTING, NEW_ACTIVITY_AFTER_SILENCE).
4. **`compute_behavioral_profiles`** — posting cadence, active hours →
   **`behavioral_profiles`** (incl. inferred timezone).
5. **Group graphs** — `build_whatsapp_group_graph`, `build_telegram_group_graph`
   → co-membership edges (a strong same-person / association signal).
6. **`analyze_strava_patterns`** — Strava activity patterns.
7. **`analyze_bios`** (bio_nlp) — NLP over bios: keywords, handles, mentions.
8. **`compute_graph_analytics`** — graph metrics over the relationship graph.
9. **`detect_bio_mentions`** (bio_mention) — one entity's bio referencing
   another's handle.
10. **`infer_locations`** (location_inference) — geo from Strava city/state,
    YouTube country, (gated) IG post geo, inferred timezone.
11. **`fingerprint_content`** (content_fingerprint) — stylometry over text from
    TikTok/YouTube/Telegram/WhatsApp/Lemon8; similar writing style ⇒ same-author
    signal.
12. **`correlate_activity`** (temporal) — entities that post at correlated times.
13. **`extract_contacts`** (contact_extraction) — emails/phones/links from
    bios+text → high-confidence `email_match`/`whatsapp_phone`/`shared_website`
    signals.
14. **`analyze_route_similarity`** — Strava "home base": repeated start
    locations ⇒ same person.
15. **Phase 6 — media content analysis** (per-media, batched):
    `analyze_media_pdf_text` → `extract_pdf_images` → `extract_video_frames` →
    `analyze_media_exif` → `analyze_media_phash` → `analyze_media_ocr` →
    `analyze_media_faces`. Writes **`media_analysis`** rows (one per media item
    per analysis kind). EXIF/OCR/pHash/faces all become correlation signals.
16. **`rebuild_face_match_signals`** — turns face-embedding matches into
    identity signals.
17. **`compute_identity_scores`** (identity_scorer, Phase 5A) — the fusion
    step: aggregates all `identity_signals` per `(entity_a, entity_b)` pair into
    a single **"same person" probability** via probabilistic OR over
    heterogeneous weak signals.

`run_full_resolution()` does the same but non-incrementally (full re-resolve).

## 5. The face engine (`face_worker`, `facetracker` schema)

- Loads collector image/profile media, runs **InsightFace** (ONNX) →
  embeddings → `facetracker.images` / `facetracker.faces` (512-d pgvector).
- Bridges a detected face to an entity via the media item's owner →
  `public.entity_faces` (media_attribution).
- FAISS outbox/reaper pattern for an ANN index alongside the pgvector column.
- **This is the least-developed link:** faces are detected in volume but very
  few are bridged to entities (see Live state). Face → identity clustering is
  the biggest open opportunity.

## 6. The identity model (how it all converges)

```
collector raw rows ──┐
 (posts, comments,   │   per-phase analyzers
  bios, social_users,│ ─────────────────────►  identity_signals
  messages, strava,  │   (weak, typed, per entity-pair)
  media, faces)      │                               │
                     │                               ▼
                     │                       identity_scorer (5A)
                     │                     probabilistic OR fusion
                     │                               │
                     ▼                               ▼
                 entities  ◄──── entity_platform_links, entity_faces,
                                  entity_relationships, behavioral_profiles
                     │
                     ▼
        timeline_events, alerts, dashboard (intelligence/timeline/metrics)
```

Entities are tiered (`primary`/`secondary`). Signals are typed and weighted;
the scorer fuses them so that several independent weak signals (same username +
co-posting time + shared group + similar writing) compound into a confident
"same person" link, while one weak signal alone stays low-confidence.

## 7. New: drive face-scanning (added 2026-06-25)

`face_worker.ingest_drive_media()` walks `DRIVE_SOURCES` (the W:/X:/Y:/Z: host
drives, mounted into the container — W/X via CIFS over Tailscale) and runs the
same InsightFace detect→index flow as collector media, writing
`facetracker.images/faces` (deduped by path; **no** entity bridging — a loose
drive file has no platform owner). **C: is deliberately excluded** (reading
OneDrive placeholders hydrates them and fills the disk). Caveat: a naive
top-down walk of the multi-TB network drives over Tailscale is slow; a
resumable cursor is a TODO. These drive faces are currently detected but not
yet clustered to entities — see Improvement ideas.

## 8. Live state (real counts, 2026-06-25)

| Table | Rows | Read |
|---|---|---|
| `timeline_events` | 2,669,227 | unified cross-platform timeline — very healthy |
| `media_analysis` | 178,122 | per-media analysis (exif/ocr/phash/faces/pdf) — healthy |
| `alerts` | 986 | SILENCE_GAP 924, COORDINATED_POSTING 59, NEW_ACTIVITY 3 |
| `entity_platform_links` | 842 | accounts mapped to entities |
| `entities` | 658 | resolved people |
| `behavioral_profiles` | 570 | active-hours/timezone profiles |
| `entity_relationships` | 368 | association graph edges |
| `identity_signals` | 242 | real_name_fuzzy 166, temporal_copost 34, group_cooccurrence 20, username_exact 12, cross_platform_link 3, content_similarity 3, shared_website 2, commit_email 1, whatsapp_phone 1 |
| `facetracker.faces` | ~2,266 | detected faces (collector + new drive faces) |
| `entity_faces` | 13 | **faces bridged to entities — very sparse (key gap)** |
| `entity_merge_log` | 0 | no merges executed yet |

`analysis_runs`: incremental every ~2h + full_resolution ~daily, completing
cleanly (one running now). **The pipeline is live and healthy.**

## 9. Known gaps / data-gated signals (candid)

> **Note (2026-06-26):** the `media_face_match`, `media_perceptual_match`
> (pHash), and `media_gps_colocation` signal *types already exist and are scored*
> — `incremental_runner` deletes+recomputes most signal types every run (so
> signals are re-evaluated, not fossilized). The gap below is **data**, not
> plumbing: the face signal is starved because faces aren't clustered/bridged.
> Active work to close these: see the face-clustering + new-signal workstreams.

- **Face → identity is barely wired** (`entity_faces`=13 vs 2,266 faces). The
  `media_face_match` signal pipeline exists (scorer weight 0.50) but is starved
  because `rebuild_face_match_signals` only joins faces already bridged to
  entities. **Fix in progress: cluster the face index → auto-propagate
  `entity_faces` → emit cross-entity `media_face_match` signals** (signals-only,
  no auto-merge). Drive faces (no owner) get cross-referenced against the
  collector index.
- **Several signals are data-gated**, not broken: `location_inference` notes IG
  post geo + Strava timezone activate "once scraper backfills". `commit_email`,
  `whatsapp_phone`, `shared_website` fire rarely (1–2 rows).
- **No face clustering** across the unified face index (no
  Chinese-Whispers/HDBSCAN pass turning face neighborhoods into person clusters
  that then become identity signals).
- **`entity_merge_log`=0** — merge candidates are surfaced but (apparently) not
  auto-applied; human-in-the-loop only.

## 10.5 Implemented from the second-opinion review (2026-06-26)

Acted on after a fine-tooth-comb external review. Each ships behind its run gate
and was verified on live data:

- **Face clustering → `media_face_match`** (`face_clustering.py`): clusters the
  ArcFace corpus (agglomerative, average-linkage cosine) and propagates
  `entity_faces` attribution across single-dominant-entity clusters
  (group-photo guarded, signals-only). Grew `entity_faces` 13→308 on first run;
  the existing match signal now reads a real corpus. Drive faces cross-referenced.
- **Temporal significance gate** (`temporal_correlation.py`): `temporal_copost`
  now requires Poisson significance over the pair's overlapping window
  (Bonferroni-adjusted), not a raw count — dropped 34→9 chance-level pairs.
- **`social_graph_overlap`** (`graph_overlap.py`): Jaccard of interaction
  neighborhoods as an **association** `entity_relationship` (deliberately not a
  same-person signal — friends share group-mates). 63 pairs found.
- **Calibrated scoring** (`identity_calibration.py`): logistic-regression
  classifier over per-signal feature vectors replaces noisy-OR **when a model is
  trained** (`export`/`train` CLI), else noisy-OR fallback. Handles signal
  correlation + gives calibrated probabilities; `entity_relationships.sources.method`
  records which was used.

Review critiques that were **already handled** (the original doc oversimplified):
the resolver is target-anchored + exact-first + multi-signal (§4.1); `shared_website`
already covers cross-platform link matching; signals are deleted+recomputed each
run (not fossilized); `media_perceptual_match`/`media_gps_colocation` already exist.

## 10. Improvement ideas (remaining)

1. **Close the face→identity loop.** Cluster the whole face index (FAISS kNN →
   graph clustering), promote clusters to person candidates, and emit
   `face_match` identity signals between entities sharing a face cluster. This
   would make the 2k+ faces (and the new drive faces) actually contribute to
   resolution.
2. **Make the drive scan resumable** (mtime cursor / per-dir state) so the
   multi-TB W:/X: network drives index incrementally instead of re-walking.
3. **Cross-source media dedup via pHash** to link the same image posted on
   multiple platforms (or found on disk) to one entity.
4. **Weight tuning + calibration** of `identity_scorer` against a labeled set;
   right now signal weights are hand-set.
5. **Feed OSINT (yearbooks/obituaries/PDF OCR) names** into `real_name_fuzzy`
   and face links for ground-truth anchoring.
6. **Auto-merge above a confidence threshold** with `entity_merge_log` audit +
   undo, instead of leaving everything manual.

---

### Key files
- Orchestration: `src/scheduler/scheduler.py`, `src/pipeline/incremental_runner.py`
- Entity/identity: `entity_resolver.py`, `identity_scorer.py`, `contact_extraction.py`,
  `content_fingerprint.py`, `bio_nlp.py`, `bio_mention.py`
- Signals: `location_inference.py`, `route_similarity.py`, `strava_patterns.py`,
  `group_graph.py`, `behavioral_profiler.py`, `timeline_builder.py`, `alert_engine.py`
- Face engine: `src/face_worker.py`, `src/face/` (engine/detector, storage, discovery/scanner)
- API/dashboard: `src/api/routes/{intelligence,timeline,metrics,media}.py`
