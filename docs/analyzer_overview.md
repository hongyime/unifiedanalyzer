# UnifiedAnalyzer — System Overview

> A runthrough of how the analyzer works, written for a technical reader (or a
> second-opinion LLM) to critique and suggest improvements. Reflects the state
> of the codebase + live DB as of 2026-07-03. Numbers in the "Live state"
> section are real row counts from the running system. Recent operational
> hardening + fixes are in §11.

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
  `public.entity_faces` (media_attribution). Two feeds populate the bridge:
  (a) `face_clustering.py` propagates attribution across single-dominant-entity
  clusters; (b) `face_worker.relink_entity_faces()` retroactively links
  already-indexed faces whose media item has *since* resolved to an entity —
  it runs every ~30min (`FACE_WORKER_RELINK_INTERVAL`) so the bridge stays
  current as `entity_platform_links` grows, plus a `relink` CLI for backfill.
- FAISS outbox/reaper pattern for an ANN index alongside the pgvector column.
- Bridge fullness is capped by `entity_platform_links` coverage: only person
  media (telegram/whatsapp/lemon8/instagram/github) can attribute; search/website/
  beeper media have query-hash/domain/group-JID "owners" that correctly never link.

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

## 8. Live state (real counts, 2026-07-03)

| Table | Rows | Read |
|---|---|---|
| `timeline_events` | 6,027,862 | unified cross-platform timeline — very healthy (2.7M→6.0M as collection caught up) |
| `media_analysis` | 350,054 | per-media analysis (exif/ocr/phash/faces/pdf) — healthy |
| `alerts` | 1,899 | SILENCE_GAP 1766, COORDINATED_POSTING 127, NEW_ACTIVITY 6 |
| `entity_platform_links` | 1,393 | accounts mapped to entities |
| `entities` | 1,047 | resolved people |
| `behavioral_profiles` | 957 | active-hours/timezone profiles |
| `entity_relationships` | 19,156 | association graph edges (jumped as `social_graph_overlap` matured) |
| `identity_signals` | 530 | real_name_fuzzy 313, group_cooccurrence 135, content_similarity 47, username_exact 17, whatsapp_phone 5, temporal_copost 5, cross_platform_link 3, commit_email 2, shared_website 2, phone_match 1 |
| `facetracker.faces` | 10,124 | detected faces (collector + drive faces) |
| `facetracker.face_clusters` | 6,515 | ArcFace clusters (face_clustering.py) |
| `entity_faces` | 574 | faces bridged to entities — recovered from a 0 regression (see §11) and now self-growing |
| `entity_merge_log` | 0 | no merges executed yet (human-in-the-loop) |

`analysis_runs`: incremental every ~2h + full_resolution ~daily, completing
cleanly. **The pipeline is live and healthy.** Note `media_face_match` is still 0
— the corpus is distinct people; the signal only fires on a genuine
same-person-across-two-entities face collision.

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

## 11. Operational hardening + fixes (2026-07-02/03)

A run of reliability/performance work after the Z: reformat + Docker migration:

- **Scheduler split into its own process.** The pipeline's blocking cv2/ffmpeg/
  pdf/ONNX work used to run inside the API's uvicorn loop and froze the dashboard
  for the whole run (~1.5h during backfill). Now `analyzer` (API only,
  `RUN_SCHEDULER=0`), `scheduler`, and `face_worker` are three independent
  processes — API stays ~220ms mid-run.
- **entity_faces relink fix (R7).** The bridge had regressed to **0** despite
  ~10k faces: faces were linked to entities ONLY at index time, but
  `entity_platform_links` keeps growing, so faces indexed before their owner's
  link existed were never bridged. Added `relink_entity_faces()` (retroactive,
  idempotent, wired into the loop) → bridge recovered 0→**574** and self-grows.
- **Phase-6 media stack fixed in-container.** Added `ffmpeg` (6H video frames) +
  `tesseract` (6D OCR) to the image; restored the YuNet/SFace ONNX models to Z:;
  decoupled the InsightFace `media_face_match` rebuild from the SFace models.
- **PDF-image extraction perf.** `extract_pdf_images` writes to the Docker→Windows
  `Z:` bind-mount (~200ms+/file). Parallelized the independent writes over a
  thread pool: **~2700ms → 103ms per image** in-container (writes stay on Z:, so
  the vhdx doesn't grow).
- **Feature B — "what changed since last viewed"** (`api/routes/changelog.py`):
  per-entity feed of additions (new links/events/alerts by `created_at > last
  viewed`) UNION deletions from the collector's deletion tracking on the 3
  messaging platforms (telegram `metadata->>'deleted'`, beeper/whatsapp
  `is_deleted`+`deleted_at`). Fixed a 12s telegram seq-scan with a partial index
  (`idx_tg_messages_deleted_sender`) → 4.5ms; endpoint returns in ~2.7s.
  `entity_views.last_viewed_at` + `mark-reviewed` clear the feed.
- **C:/Docker disk safety** (C: is space-capped; the WSL2 vhdx lives on C:):
  container log caps (3×10MB/service), a safe weekly auto-prune
  (`docker/prune-safe.ps1`, dangling images + build cache only — never
  `--volumes`/`-a`), and analyzer `cpu_shares:512` so it can't starve host
  neighbours. See memory `docker-windows-bindmount-slow` for the storage model.

## 12. Improvement ideas (remaining — for review)

Superset of §10; the face→identity loop and calibration are the highest-leverage:

1. **`media_face_match` still emits 0.** entity_faces is populated (574) but no
   cross-entity face collision exists yet in this corpus. Widen bridge coverage
   (more `entity_platform_links`) and/or emit signals from **shared face
   clusters** (two entities whose faces land in the same cluster) rather than
   only same-media attribution.
2. **Drive-face → entity attribution.** 3.7k drive faces (W/X/Y/Z) are indexed
   but have no platform owner; cross-reference them against the collector face
   clusters to attribute + emit signals.
3. **Resumable drive scan** (mtime/dir cursor) so multi-TB SMB drives index
   incrementally instead of re-walking.
4. **Train the calibrated scorer** (`identity_calibration.py`) on a labeled pair
   set — currently noisy-OR fallback with hand-set weights.
5. **Auto-merge above a confidence threshold** with `entity_merge_log` audit +
   undo (currently human-in-the-loop; `entity_merge_log`=0).
6. **Cross-source pHash media dedup** to link the same image across platforms/disk.
7. **Deletion tracking beyond messaging** — social posts don't emit deletion
   events; a "post stopped appearing" heuristic could flag likely removals.

---

## 13. Execution status (2026-07-05 → 2026-07-07) — what has shipped since §12

This section is the ledger for the "Option A" workstream + follow-ups. Every
entry cites the shipping commit and the empirical evidence it was measured
against.

### 13.1 Axis 2 — auto-labeler + calibration activation
- `feat(calibration): IDENTITY_MODEL_ENABLED safety flag + LOSO + validate CLI` (`901a2ec`)
- `feat(pipeline): auto-label seeder for identity_labels ground-truth confluence` (`7cacc47`)
- **`IDENTITY_MODEL_ENABLED`** (default `0`) gates whether the scorer auto-loads a
  trained joblib from `IDENTITY_MODEL_PATH`. Prior behaviour silently activated
  any file on disk. See `identity_calibration.py::get_model`.
- **`IDENTITY_MIN_LABELS`** / **`IDENTITY_RETRAIN_EVERY`** env-configure the
  retrain threshold (defaults 20 / 10). Set `IDENTITY_MIN_LABELS=15` to train
  on today's live labels without a code change.
- **`_rows_to_xy` LOSO expansion** — auto-labelled positives (`source LIKE
  'auto_%'`) are expanded at train time by zeroing each contributing signal in
  turn. Human labels and auto-negatives pass through unchanged. Defends against
  feature-leak shortcuts.
- **`src/pipeline/auto_labeler.py`** — new phase wired into `_secondary_phases()`
  between `face_match_signals` and `calibration_retrain`. Predicate:
  `(n_hard >= 1 AND n_types >= 2) OR n_types >= 3` on cross-entity scoring
  signals; hard-anchor = phone/email/cross_platform_link/shared_website/media_*.
  Env-gated by `AUTO_LABEL_ENABLED` (default off, on in prod `.env` since
  2026-07-06).
- **`validate` CLI subcommand** — leave-one-out CV of LR vs noisy-OR AUC on
  `identity_labels`. Reports `lr_auc_loo`, `noisy_or_auc`, `delta`, full-fit
  LR weights. `--strict --min-delta 0.05` exits non-zero if LR does not beat
  noisy-OR. **Live measurement (2026-07-05, 15 labels): LR AUC 0.66 vs noisy-OR
  AUC 0.99, delta −0.33 → cutover correctly blocked.**
- **Scorer's dismissed-pair guard** now filters to human labels only
  (`identity_scorer.py:105`), so future `auto_negative_v*` rows don't suppress
  scored pairs.
- **Empirical**: 254 candidate pairs on live DB, all `n_types = 1` → 0 auto-
  labels today. Code activates automatically as data grows.

### 13.2 Axis 3 — face loop closure
- `feat(faces): purity guards + drive-face FAISS-kNN cross-ref + face_pair_knn registry` (`84dc9aa`)
- `feat(faces): face_pair_knn cross-entity same-person signal + phase wiring` (`2e044e7`)
- **`face_clustering._competing_entity_too_close`** — rejects entity propagation
  in a cluster if another entity has a face within
  `FACE_PURITY_2ND_NEAREST_THRESHOLD` (default 0.55) of any cluster member.
- **`face_clustering._cluster_too_loose`** — rejects propagation when the intra-
  cluster minimum pairwise cosine falls below `FACE_PURITY_MIN_TIGHTNESS`
  (default 0.35).
- **`propagate_drive_faces_via_knn`** — new sibling function in
  `face_clustering.py`. FAISS-kNN of owner-less drive faces against bridged
  collector-face anchors (top-K=5, threshold 0.55, top-margin 0.05, confidence
  attenuated 0.7× for indirect attribution). Method='drive_cross_ref_knn'.
- **`src/pipeline/face_pair_signals.py`** — new module. Cross-entity portrait-
  gated pairwise cosine. Emits `identity_signals.signal_type='face_pair_knn'`
  when max cosine ≥ `FACE_PAIR_KNN_THRESHOLD` (0.55) AND count ≥
  `FACE_PAIR_KNN_MIN_MATCHES` (2) — the ≥2 requirement is the impostor guard
  against ArcFace lookalike collisions.
- **`drive_scan_state` table** (`schema.sql:297-306`) + `_load_cursor` /
  `_save_cursor` helpers in `face_worker.py`. Drive scans now walk in
  deterministic `(mtime ASC, path ASC)` order, checkpointing every
  `FACE_WORKER_CHECKPOINT_EVERY` files. Multi-TB SMB shares no longer re-walk
  from scratch on restart.
- **Signal registry additions (append-only)**:
  - `identity_scorer._TYPE_WEIGHT["face_pair_knn"] = 0.60`
  - `identity_calibration.FEATURE_ORDER` — appended as 15th entry
  - `auto_labeler._HARD_SIGNALS` and `_SCORING_SIGNALS` — both include
    `face_pair_knn`
- **Pipeline wiring**: `drive_face_xref` (phase 20) and `face_pair_knn` (phase 21)
  inserted between `face_clustering` and `face_match_signals`.

### 13.3 Axis 1 MVP — semantic timeline search
- `feat(embed): multilingual-e5-small ONNX text embedder + timeline_embeddings schema` (`5288130`)
- `feat(api): /api/search/timeline semantic search + embed-backfill CLI` (`ff5dec3`)
- **`src/pipeline/text_embedder.py`** — wraps `intfloat/multilingual-e5-small`
  (384d, ~470MB fp32 ONNX; quantised variant not available on HF for this
  model). Lazy first-run download to `${MEDIA_DERIVED_PATH}/models/text_embedder/`
  via direct HF hub URLs (no `huggingface_hub` dep). Singleton with lock. Mask-
  weighted mean-pool + L2 normalise. `"query: " / "passage: "` prefix per e5
  conventions. Uses `tokenizers` (rust-backed, ~5MB) — no `transformers` dep.
- **`timeline_embeddings` side table** (`schema.sql:315-328`, HNSW cosine index
  on `embedding`). Side table because pgvector HNSW cannot be built on
  partitioned parents (`timeline_events` is monthly-partitioned via migrations
  `001` / `002`).
- **`src/pipeline/timeline_embedder.py`** — `content_embedding` phase.
  UPSERT with text_sha1 guard so unchanged rows skip the write.
  `TEXT_EMBED_BATCH_PER_RUN` (default 5000) caps per-run cost.
- **`src/pipeline/topical_similarity.py`** — `topical_similarity` phase.
  Per-entity centroid over up to `TOPICAL_MAX_EVENTS_PER_ENTITY` (default 200)
  most-recent embeddings, pairwise cosine, threshold `TOPICAL_SIMILARITY_THRESHOLD`
  (default 0.85), emits `identity_signals.signal_type='topical_similarity'` at
  scorer weight 0.15 (weak — topical overlap is weaker evidence than stylistic
  or deterministic). Appended as 16th entry in `_TYPE_WEIGHT` / `FEATURE_ORDER`
  / `_SCORING_SIGNALS` (not in `_HARD_SIGNALS`).
- **`GET /api/search/timeline?q=&entity_id=&since=&until=&source=&limit=`**
  (`src/api/routes/search.py`). Uses `pgvector` `<=>` cosine operator + HNSW.
  Empty q → 400; embedder unavailable → 503. Response includes `took_ms` and
  `model` fields.
- **`python -m src.main embed-backfill`** CLI subcommand — see §13.5 for the
  resilient loop wrapper baked around it.

### 13.4 Dashboard reskin (Phases 1 + 2 + 3)
- `feat(dashboard): Phases 1+2 - reskin foundation + plain-language labels` (`354ad78`)
- `feat(dashboard): Phase 3 - migrate legacy pages to UI kit + delete legacy CSS` (`c61c5b9`)
- **Phase 1** — Inter + JetBrains Mono via Google Fonts; palette tokens aligned
  to collector (`#0a0a0a` / `#111` / `#1e1e1e`); `AppShell` + `Sidebar`
  (grouped nav: Investigate / Evidence / Workspace) + `Header` (live pipeline
  pill driven by run heartbeat). UI kit ported: `Button`, `Card`, `MetricCard`,
  `StatusBadge`, `DataTable`, `EmptyState`, `ErrorState`, `LoadingSpinner`,
  `SkeletonLoader`, `FilterDropdown`, `SearchBar`, `PageIntro`, `InfoTip`,
  `PlatformBadge`.
- **Phase 2** — Central label map at `frontend/src/lib/labels.ts` covering 22
  signal types, 6 alert types, 2 tiers, 4-tier confidence bucketing (Weak /
  Possible / Likely / Very likely). `ConfidencePill` traffic-light bar
  (red<0.30 / amber<0.60 / green≥0.60). Per-page intros via
  `PageHeader.description`. `Help` page at `/help` with a glossary.
- **Phase 3** — `Entities`, `EntityDetail`, `Communities` migrated to the UI
  kit; also swept `Alerts`, `Review`, `IdentitySummary` for stray legacy CSS
  classes. Legacy `.card` / `.badge` / `.signal-bar` / `.empty-state` block
  deleted from `frontend/src/index.css`. Safety-check grep for
  `className="(card|badge|button|table)"` returns zero hits across
  `frontend/src/`. `DataTable` extended additively with `pageSize` +
  `onRowClick`.
- **All 15+ existing user actions preserved** on `EntityDetail`
  (setWatch / mergeEntities / dismissMatch / splitEntity / addCaseItem /
  markReviewed / updateEntitySettings / etc). All routes unchanged.

### 13.5 Ops — resilient backfill + calibration watchdog
- `feat(ops): resilient embed-backfill + calibration cutover watchdog` (`858599b`)
- **`main.py::embed-backfill`** now:
  - Wraps every iteration in try/except with `--fail-backoff`-second retry.
  - Exits after `--max-consecutive-failures` (default 5) in a row.
  - `--log-file` attaches a `RotatingFileHandler` (50 MB × 3 backups, append)
    and REMOVES root's default `StreamHandler` so each log line writes exactly
    once. No more duplicated log entries.
  - Per-batch telemetry: `batch_wall`, `rate ev/s`, `mem MB`, `pool state`.
  - SIGTERM / SIGINT handler — stops at the next iter boundary; no mid-batch
    rollback loss.
- **`docker/embed-backfill-loop.sh`** — sh wrapper baked into the image
  (`Dockerfile.dashboard` COPY + `chmod +x`). Respawns the python child on
  process death (OOM, ORT segfault, etc). Redirects python's stdout+stderr to
  a `.stdout` sidecar file so the primary log (owned by python's
  `RotatingFileHandler`) has zero duplicates. Distinct sleep between drain
  vs abnormal exit (`DRAIN_SLEEP` vs `RESPAWN_SLEEP`).
  Launch: `docker exec -d docker-scheduler-1 /app/embed-backfill-loop.sh`.
  Stop: `docker exec docker-scheduler-1 touch /tmp/embed-backfill.stop`.
- **`src/pipeline/calibration_watchdog.py`** — new secondary phase. Every
  cycle counts `identity_labels`; when ≥ `CALIBRATION_MONITOR_MIN_LABELS`
  (default 50) AND both classes present, runs the same LOO CV as `validate`.
  If LR beats noisy-OR by ≥ `CALIBRATION_MONITOR_MIN_DELTA` (default 0.05),
  emits a `CALIBRATION_READY` alert + Telegram notification. Dedup via
  `alerts.detail->>'label_count'` + `CALIBRATION_MONITOR_RECHECK_STRIDE`
  (default 10). Does NOT auto-flip `IDENTITY_MODEL_ENABLED` — human-in-the-loop.

### 13.6 Docs infrastructure
- The GitHub-Actions sourcerepo sync workflow (`sync-repo-settings.yml` run
  `1e4814f`) deleted the entire `docs/` folder along with its 1,418 lines
  when propagating boilerplate from the sourcerepo. Restored via
  `git checkout 7410671 -- docs/`. If this recurs, **exclude `docs/**` from
  the sync workflow's rsync spec in sourcerepo** — the analyzer-specific docs
  are not templates to be mirrored.

### 13.7 Live counts (2026-07-07 09:xx)
| Table | Rows | Notes |
|---|---|---|
| `timeline_events` | 6,282,072 (partitioned) | Monthly 2005-2035, DEFAULT for outliers |
| `timeline_embeddings` | 11,000+ (growing) | multilingual-e5-small, HNSW; backfill running |
| `identity_signals` | ~600 | Cross-entity mix per §8; face_pair_knn / topical_similarity queued |
| `identity_labels` | 15 (4 pos / 11 neg) | Watchdog threshold 50; validate delta −0.33 today |
| `alerts` | ~2000 | SILENCE_GAP rate collapsed 72/day → ≤1/day post-P1-3 |
| `entities` | 1047 | Stable IDs (P0-1 shipped 2026-07-03) |
| `drive_scan_state` | 0 | Populates lazily on next drive scan |

### 13.8 Environment defaults (`.env.example`)
All new env vars documented under `IDENTITY CALIBRATION`, `AXIS-3 FACE LOOP
CLOSURE`, and `AXIS-1 SEMANTIC TIMELINE SEARCH` sections. See `.env.example`
for the authoritative list.

---

### Key files
- Orchestration: `src/scheduler/scheduler.py`, `src/pipeline/incremental_runner.py`
- Entity/identity: `entity_resolver.py`, `identity_scorer.py`, `contact_extraction.py`,
  `content_fingerprint.py`, `bio_nlp.py`, `bio_mention.py`
- Signals: `location_inference.py`, `route_similarity.py`, `strava_patterns.py`,
  `group_graph.py`, `behavioral_profiler.py`, `timeline_builder.py`, `alert_engine.py`
- Face engine: `src/face_worker.py` (ingest + `relink_entity_faces`), `src/face/`
  (engine/detector, storage, discovery/scanner), `src/pipeline/face_clustering.py`
- API/dashboard: `src/api/routes/{intelligence,timeline,metrics,media,changelog}.py`
- Ops/deploy: `docker/docker-compose.yml` (3 processes, log caps, cpu_shares),
  `docker/Dockerfile.dashboard`, `docker/prune-safe.ps1` (safe weekly auto-prune)
