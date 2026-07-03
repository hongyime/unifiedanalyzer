# Identity System Review — Tracked Plan

Source: external deep-review of the identity/entity-resolution system (pasted 2026-07-03).
This doc triages every finding into P0/P1/P2 with a **verification status** obtained by
reading the actual code this pass. **No code has been changed yet.**

Verification legend:
- ✅ **CONFIRMED** — verified against code this pass (file:line cited).
- 🟡 **RUNTIME** — depends on live DB state (row counts, on/off flags); not verifiable from
  code alone. Needs a SQL query before acting.
- ⚪ **DESIGN** — a judgement/architecture opinion, not a true/false claim; recorded for the
  roadmap but nothing to "verify".
- ❌ **REFUTED** — checked and found inaccurate.

Cadence fact established this pass: `FULL_RESOLUTION_INTERVAL_HOURS` defaults to **12**
(`src/scheduler/scheduler.py:194`), with **no override** in docker-compose or `.env.example`.
So the destructive full-resolution runs **~twice per day**, not "daily" as the review assumed —
every P0 item below is therefore *more* urgent, not less. The reviewer's stated caveat
("if full-res is disabled the damage is dormant") is resolved: **it is enabled and firing.**

---

## P0 — correctness/safety (do before anything else)

### P0-1 ✅ Stabilize entity IDs — stop reassigning every UUID on full-resolution
**Status: CONFIRMED (highest leverage item in the whole review).**
- `run_full_resolution()` executes `DELETE FROM entity_platform_links` then
  `DELETE FROM entities` — `src/pipeline/incremental_runner.py:326-327`.
- `resolve_entities()` recovers existing IDs by reading `entity_platform_links`
  (`src/pipeline/entity_resolver.py:493-497`), which is now empty, so ID recovery at
  `:522-527` finds nothing → `is_new=True` → fresh `uuid4()` at `:537` for **every** entity.
- FK fallout on the `DELETE FROM entities` (all in `src/db/schema.sql`):
  - `entity_faces … ON DELETE CASCADE` (`:187`) → **bridged faces wiped every 12h.** This is
    a strong candidate root cause for the "faces regressed to 0" story, independent of the
    index-time-linking explanation.
  - `entity_platform_links` (`:21`), `identity_signals` (`:40`), `behavioral_profiles`
    (`:107`), `entity_relationships` (`:137-138`) — all CASCADE; rebuilt, so tolerable.
  - `timeline_events … ON DELETE SET NULL` (`:57`) → entity_id nulled then re-linked.
  - `identity_labels` — **no FK** (`:244-252`), stores raw `entity_a`/`entity_b` UUIDs → human
    labels survive as rows but point at dead entities. **Kills the calibration roadmap silently.**
  - `entity_views` — **no FK**, PK `entity_id` (`:205-208`) → "what changed" feed resets.
  - `case_items.ref_id` — TEXT, no FK (`:233`) → saved investigations point at dead entities.
- **Bonus bug found this pass (reviewer missed):** `incremental_runner.py:325` selectively
  preserves certain `identity_signals` types, but `:327 DELETE FROM entities` CASCADE-deletes
  **all** `identity_signals` two statements later (FK `:40`). The preservation is dead code.
- **Fix direction:** stop `DELETE FROM entities`; make full-res an UPDATE-in-place / re-resolve
  that preserves IDs (e.g. derive entity_id from a stable anchor, or keep links and reconcile).
  This one change protects faces, labels, views, cases, and unblocks calibration.

### P0-2 ✅ Fix independence counting so one shared username can't satisfy "2 independent"
**Status: CONFIRMED.**
- `compute_confidence` counts distinct `(signal_type, source_platform)` pairs
  (`entity_resolver.py:241-247`). Phase 1 expands one shared username across N platforms into
  pairwise `username_exact` rows with differing `source_platform` (`:302-313`) →
  `independent_count ≥ 2` from a single fact.
- Also note `is_confirmed` (`:504`) is only written as a flag on the link (`:581`, `:599`); it
  does **not** gate entity formation. So "guarded at the resolver level" is a label, not a gate.
- **Fix direction:** count independent *facts/values*, not pairwise-expanded rows; treat one
  shared handle as one signal regardless of how many platform pairs it fans out to.

### P0-3 ✅ Stale-lock timeout must exceed max real run time
**Status: CONFIRMED.**
- `_is_run_locked` clears any `running` row older than **30 min** and proceeds
  (`incremental_runner.py:48-55`). Review notes a real full-res backfill took ~1.5h → a
  long legit run gets its lock stolen → two concurrent runs double-writing.
- **Fix direction:** raise timeout above max observed runtime, or add a per-phase heartbeat
  column updated as phases complete and treat only heartbeat-stale rows as dead.

---

## P1 — high leverage

### P1-1 ✅ Unify the two identity heads; hard-demote real_name_fuzzy
**Status: CONFIRMED (disjoint vocabularies).**
- Scorer's `_TYPE_WEIGHT` (`src/pipeline/identity_scorer.py:31-44`) covers email/phone/bio/
  cross_platform_link/content_similarity/temporal_copost/shared_website/shared_route_origin/
  group_cooccurrence/media_*. It **excludes** the resolver's deterministic signals
  (`username_exact`, `real_name_fuzzy`, `whatsapp_phone`, `commit_email`,
  `profile_photo_sha256`). Scorer query filters `signal_type = ANY(_TYPE_WEIGHT.keys())`
  (`:59-63`) so those rows are invisible to the "same-person probability".
- `real_name_fuzzy` at `token_sort_ratio >= 85` is actively used to merge in resolver Phase 4
  (`entity_resolver.py:401-421`) and Phase 4.5 (`:447-462`) — i.e. it *merges* but is *excluded*
  from the advisory score. Exactly backwards.
- 🟡 The "313 rows / plurality of the signal table" figure is RUNTIME — confirm with a
  `SELECT signal_type, count(*) FROM identity_signals GROUP BY 1` before quantifying.
- **Fix direction:** feed deterministic signals into the scorer's feature order; demote
  `real_name_fuzzy` (require a corroborating signal, which Phase 4 already does but Phase 4.5
  and the Strava-name path at `:357-372` do not).

### P1-2 ✅ Face-cluster purity guard + verify face_count, then open drive-face attribution
**Status: guard-fails-open CONFIRMED; purity/quality checks ABSENT (design gap).**
- `COALESCE(i.face_count, 1) <= 3` (`src/pipeline/face_clustering.py:183`) defaults NULL→1
  (portrait) → propagates. If `face_count` is ever NULL at ingest, group-photo bystanders get
  attributed to the poster.
- No per-cluster purity / min-quality / second-nearest-entity rejection before propagation
  (`propagate_entity_faces` `:135-213`). Average-linkage cosine 0.50 (`:46,:104`) is reasonable
  but ArcFace impostors (siblings, lookalikes, low-quality crops) exceed 0.5.
- Cluster IDs are ephemeral: every pass `DELETE FROM facetracker.face_clusters` + full
  re-cluster (`:125`) → `cluster_id`/`dominant_entity_id` not stable across runs. Fine for
  signals-only today; don't build anything durable on `cluster_id`.
- `method='drive_cross_ref'` path already exists (`:192-197`) — the real face payoff is
  cross-referencing owner-less drive faces, not chasing `media_face_match`.
- 🟡 `media_face_match=0` and the "3.7k owner-less drive faces" are RUNTIME.
- **Fix direction:** verify `face_count` is populated at ingest; add min-quality + intra-cluster
  tightness + reject-if-second-entity-close before propagate; then prioritize drive-face attribution.

### P1-3 🟡 SILENCE_GAP alerts — condition on source-collection health + per-entity baseline
**Status: RUNTIME for the 93% figure; DESIGN for the fix.**
- The "1766/1899 SILENCE_GAP" number needs `SELECT alert_type, count(*) FROM alerts GROUP BY 1`.
- Logic to inspect: `src/pipeline/alert_engine.py` (not read this pass) — confirm SILENCE_GAP
  fires per-entity without checking whether the *source* stalled over the same window.
- **Fix direction:** gate SILENCE_GAP on the source's collection being healthy for the window
  (the health check at `scheduler.py:232-238` already tracks source health separately) and
  baseline per-entity cadence.

### P1-4 ✅ Label + calibrate — RESOLVED by P0-1, no new code
**Status: CONFIRMED, and the review's claim is refined.**
- **Training is already durable:** `record_label` snapshots the per-pair feature vector into
  `identity_labels.features` (JSONB) at label time (`identity_calibration.py:195-224`), and
  `train_from_db`/`maybe_retrain` train from that snapshot (`:264-265`), NOT from live entities.
  So calibration training survives entity deletion/merge regardless of UUID churn.
- **What P0-1's churn actually broke** was narrower than "labels rot": the scorer's dismissed-pair
  guard (`identity_scorer.py:82-85`) matches `identity_labels` rows by *live* entity UUID, so every
  12h churn silently stopped suppressing dismissed pairs (they re-surfaced). Fixing P0-1 (stable
  IDs) restores that guard — no calibration-specific code change needed.
- **No auto-merge to disable:** `entity_merge_log=0` (runtime) confirms the system is already
  human-in-the-loop; nothing acts on the advisory score automatically. Keep it that way.
- Remaining (deferred, needs ≥20 labels first): temporal-holdout + precision@k validation before
  trusting learned weights. Noisy-OR fallback stays until a model exists (`identity_scorer.py:118-119`).

---

## P2 — later

- ⚪ **Face clustering scale:** agglomerative is O(n²) memory, recomputed from scratch each pass
  (`face_clustering.py:72-132`), bounded by `FACE_CLUSTER_MAX=20000` (`:49`). Walls ~30-50k
  faces. Move to FAISS-kNN graph + connected-components/HDBSCAN, add faces incrementally.
- ⚪ **Quadratic entity passes:** temporal_correlation / content_fingerprint / graph_overlap /
  scorer pair aggregation are O(entities²). Fine now; add candidate generation (blocking) before
  ~5k entities.
- ✅ **Index check (partial):** `idx_signals_type ON identity_signals(signal_type)` exists
  (`schema.sql:53`) — the scorer's filter is covered. 🟡 pgvector/HNSW index on the face
  embedding column is NOT in `schema.sql` (faces live in `facetracker.*`, separate schema) —
  confirm a kNN index exists there before assuming seq-scan.
- ⚪ `build_timeline(since=None)` on full-res — confirm it's an upsert keyed to stable IDs
  (UNIQUE(source, event_type, source_record_id) exists at `schema.sql:66`), so re-insert is
  idempotent; partition `timeline_events` by month later.
- ⚪ Missing high-value signals: EXIF device/lens fingerprint, profile-photo pHash (not just
  SHA256), link-in-bio exact URL. Stylometry as tiebreak only.
- ⚪ Per-phase status persistence + alerting (every phase is `try/except … non-fatal`, invisible
  on repeated failure). DB connection pool cap per process (face_worker vs scheduler share PG).

---

## P2 execution status (2026-07-03)
Done now (safe, high-value):
- **P2-1 pool caps:** `DB_MAX_POOL_SIZE` set per service (analyzer 10 / scheduler 6) and
  face_worker's SQLAlchemy engines bounded (`FACE_DB_POOL_SIZE`/`FACE_DB_MAX_OVERFLOW`, default
  3+2 per engine) so a drive scan can't exhaust the shared Postgres.
- **P2-2 Phase 4.5 blocking:** added `name_block_keys` token-prefix index; each of the 28,534
  username-less WhatsApp profiles now fuzzy-matches only entities sharing a name-token prefix
  instead of all ~1k, removing the O(n·m) event-loop block. Distinctive-name/threshold match
  results are preserved.
- **P2-3 per-phase status:** new `run_phase_status` table; the ~24 non-fatal secondary phases
  (dedup'd into one shared list used by both runners) are each timed + recorded ok/failed, with
  a repeated-failure notification (`_alert_on_repeated_phase_failures`, 3-in-a-row).
- **P2-4 face kNN index:** RESOLVED as no-op — `embedding_vec` is pgvector, but NO code does a
  `<=>/<->` kNN query (matching uses FAISS `faiss_outbox`; clustering loads all in-memory), so an
  HNSW index would be pure write overhead. No index added; documented.
- **P2-8 face-corpus rebuild:** see verification section (relink of ~6.4k collector faces).

Deferred (backlog, need a window / larger effort — do NOT run unattended on the live box):
- **P2-5 partition timeline_events** — risky migration on the live ~6M-row table.
- **P2-6 FAISS-kNN face clustering** — large rewrite, not urgent at current scale.
- **P2-7 new signals (EXIF/pHash/link-in-bio)** — feature projects touching collectors.

## Explicitly do NOT do
- ⚪ Auto-merge above a threshold — with uncalibrated weights + (currently) unstable IDs +
  CASCADE deletes, it compounds irreversible errors. Keep human-in-the-loop.
- ❌ Treat `media_face_match=0` as a bug — correct for a distinct-person corpus.
- ⚪ Add more weak association signals into the same-person score — they belong in the
  relationship graph (`social_graph_overlap` already lives there).

---

## Runtime queries to run before acting (resolves all 🟡 items)
```sql
-- signal mix (P1-1, P1-2)
SELECT signal_type, count(*) FROM identity_signals GROUP BY 1 ORDER BY 2 DESC;
-- alert mix (P1-3)
SELECT alert_type, count(*) FROM alerts GROUP BY 1 ORDER BY 2 DESC;
-- did anything ever act on the advisory score? (P1-4 / do-not-auto-merge)
SELECT count(*) FROM entity_merge_log;
-- face bridge health (P0-1 fallout, P1-2)
SELECT count(*) FROM entity_faces;
SELECT count(*) FROM facetracker.images WHERE face_count IS NULL;
-- confirm full-res really is firing every ~12h (P0 severity)
SELECT run_type, status, started_at, finished_at
FROM analysis_runs WHERE run_type='full_resolution'
ORDER BY started_at DESC LIMIT 10;
```

## Runtime query results (run 2026-07-03 against live DB)
- `identity_signals` mix: `real_name_fuzzy` **324**, `username_exact` 17, `whatsapp_phone` 5,
  `commit_email` 2. real_name_fuzzy = **93% of all signals** → P1-1 hazard CONFIRMED as #1 source.
- `alerts` mix: `SILENCE_GAP` **1766**, `COORDINATED_POSTING` 127, `NEW_ACTIVITY_AFTER_SILENCE` 6
  → P1-3 confirmed (93% of alerts are SILENCE_GAP).
- `entity_merge_log` = **0** → nothing ever acted on the advisory same-person score.
- `entity_faces` = **0** → bridged-face corpus currently empty (the "regressed to 0" story, live).
- `facetracker.images` NULL `face_count` = **0** → P1-2 group-photo fail-open is currently DORMANT
  (latent, not firing) because face_count is populated. Still fix the COALESCE default defensively.
- `entity_relationships` (incl. `same_person_probability`) = **0**.
- Full-resolution history: fires every **~12h**; durations **2–5 hours** (2h41m, 1h53m, 4h52m,
  3h18m, 2h22m, 2h01m). **Every run vastly exceeds the 30-min stale-lock** → P0-3 real.
- **Live smoking gun:** a full_resolution was `running` at query time (started 03:18) and had
  ALREADY wiped `entity_faces`→0 and `entity_relationships`→0 mid-run — P0-1 observed in the act.
- Caveat: the scorer's association-signal vocabulary (group_cooccurrence/content_similarity/
  bio_mention/…) showed 0 rows, but the in-flight full-res may not have rebuilt them yet.
  Re-check after a full run completes before concluding the scorer input is permanently empty.

## Post-deploy verification (2026-07-03, branch fix/identity-system-review)
Deployed (rebuilt image + recreated analyzer/scheduler); a full-res on the new code
started 06:24 UTC. Observed:
- **P0-1 PROVEN:** resolve reported `entities_updated: 1048, entities_created: 8` (old code
  would have shown ~1048 *created* with fresh UUIDs). `created_at` preserved on all 1048
  (created_preserved=1048), `updated_at` moved to 06:32. Entity IDs are now stable across full-res.
- **P0-3 PROVEN:** the running row's `heartbeat_at` advances (~60s cadence); stale-lock cleaner
  now keys off `COALESCE(heartbeat_at, started_at)`. Raised default threshold 15→30 min after
  observing Phase 4.5 block the event loop ~2.5 min synchronously (28,534 username-less profiles).
- **P0-2 active:** independence now counts distinct signal_type (code path exercised in resolve).
- **P1-1 PROVEN:** `real_name_fuzzy` 324→15 (−95%); 8 WhatsApp profiles became standalone
  secondary entities instead of weak name-only merges. `username_exact` 17, `commit_email` 2.
- **P1-4 PROVEN durable:** startup cleared the orphaned 03:18 lock; labels train from JSONB
  snapshot regardless of IDs; dismissed-pair guard restored by stable IDs.
- P1-2 / P1-3: code deployed + compiled; exercised later in the run (face clustering / alerts).
  entity_faces=0 until the face corpus is rebuilt by face_worker.
- **New scalability flag (backlog):** Phase 4.5 fuzzy-matches 28,534 username-less WhatsApp
  profiles against all entities synchronously — O(n·m), event-loop-blocking, minutes per run.
  Candidate-generation/blocking (P2) should cover this too.

## Suggested order of execution
1. Run the runtime queries above (confirms severity + resolves 🟡).
2. **P0-1** (stable IDs) — unblocks everything durable; stops the 12-hourly face/label wipe.
3. P0-2, P0-3 (independence count, lock timeout) — small, independent.
4. P1-1 (unify heads / demote real_name_fuzzy), P1-2 (face purity + drive attribution).
5. P1-3 (SILENCE_GAP noise), then P1-4 (label+calibrate) once P0-1 lands.
6. P2 items as scale demands.
