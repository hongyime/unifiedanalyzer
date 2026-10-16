# UnifiedAnalyzer — Final Plan & Handoff (2026-10-14, approved)

## Status & How to Use

* **Status:** Plan approved by Bryan on 2026-10-14 (`[Approved] unifiedanalyzer_final_plan.md`). Phase 0 is **COMPLETE** (commit `e75a85d`). Next task: **Phase 1**.
* **Repository:** `~/Projects/unifiedanalyzer` on `3410`.
* **Cross-Session State:** Read `AGENTS.md` and `.agents/STATE.md` first.
* **Commit Cadence:** All work commits to `main` and pushes to `origin/main`. Update `.agents/STATE.md` and `.agents/JOURNAL.md` after each phase.
* **Collector Boundary:** UnifiedAnalyzer connects to UnifiedCollector strictly read-only via `COLLECTOR_DATABASE_URL` (`unifiedcollector_postgres`). Do NOT modify the Collector repo or Collector DB.
* **Testing Rules:** No live Telegram, Supabase, or production DB writes in tests. Fixtures + isolated test DBs only. Respect rate limits (backoff + Retry-After). Never print credentials/tokens.
* **Contract Specification:** See `docs/collector-contract.md` for table columns and operational baselines. Contract validator is in `src/pipeline/collector_contract.py` and covered by `tests/test_collector_contract.py`.

---

## Decisions Locked in by Bryan

1. **Collector Still Upgrading:** Analyzer waits for Collector to be stable. Do NOT one-pass against tables that are still changing.
2. **IG DM:** Deleted in Collector (`8197d1bd`). Do NOT redo deletion. Analyzer handles the consequence by freezing/deprecating ingestion and retaining history (Phase 7).
3. **Browser Queue:** Fixed in Collector (`86dc357a`). Analyzer already uses `browser_ingest_events` and `browser_ingest_*` state. Verified zero references to `instagram_ingest`, `collector_light`, or `collector_lowrisk` in Analyzer `src/`, `frontend/src/`, `tests/`, `docker/`.
4. **Bluesky:** Fixed in Collector (`08875146`). Analyzer has zero references to any `bluesky_*` table. Bluesky is new surface, not an extension.
5. **Search Documents:** Analyzer does the full massage. Not Collector-only. Analyzer must OCR / extract text and links, process documents, analyze photos, read EXIF if present, and run face recognition on photos (Phase 4).
6. **Airbnb Geo:** Listing location feeds host home-base as a weak, corroborated signal. One listing alone never sets home-base. Requires 2+ listings in area or 1 listing + independent signal (Strava, Telegram, IG, Search) (Phase 5c).
7. **Telegram Votes/Joins:** Poll votes and joins score identity as weak signals (never auto-merge alone) and produce relationship signals (co-presence / poll co-vote) (Phase 3).
8. **Sequencing:** Wait for Collector phases to land + soak, freeze Collector HEAD, then build Analyzer in order. Do NOT build Analyzer legs against unlanded or undocumented Collector tables.
9. **Dashboard Testing & Bot Parity:** Stand updated for all new sources (Phases 8–9).
10. **Deferred Audit Items:** Remain open for resolution (Phase 10).

---

## Verified Gaps the Agent Must NOT Assume Away

* Analyzer has zero references in `src/` or `tests/` to `telegram_poll_votes`, `telegram_join_leave_events`, `search_documents`, `pinterest_boards`, `pinterest_pins`, `snapchat_stories`, `snapchat_spotlights`, `snapchat_highlights`, `airbnb_listings`, `airbnb_hosts`, `airbnb_reviews`, or any `bluesky_*` table.
* Analyzer has zero references to `pinterest_profiles`, `snapchat_profiles`, `airbnb_profiles` in `src/`.
* Analyzer references `search_results` only in `data_quality_ledger.py` (freshness check), and `search_queries` only in eval seeds. It does not ingest Search output yet.
* Collector schema files: obtain DDL for each new Collector table from the Collector repo or live DB before building Analyzer legs. Do not build against undocumented tables.

---

## A. Rundown — What UnifiedAnalyzer Does

* **Architecture:**
  - FastAPI serving bundled React dashboard (`:8002`).
  - Scheduler running incremental (hourly) and full-resolution cycles with advisory locks, timeouts, and phase records.
  - Face worker running ONNX / InsightFace / FAISS clustering on Collector media items.
  - Dedicated PostgreSQL (pg16 + pgvector, `:5434`).
  - Optional stream-alert worker (`stream-alerts` compose profile).
* **Collector Data Flow:** Reads read-only Collector pool (`COLLECTOR_DATABASE_URL`), querying platforms: GitHub, Strava, Instagram, Telegram, WhatsApp, TikTok, YouTube, Lemon8, Threads, Facebook, X, and recon observations.
* **Analysis Pipeline:** Entity resolution, calibrated identity scoring with noisy-OR, timeline construction, media analysis (EXIF, OCR, hashes), face clustering/attribution, and intelligence/alert generation.

---

## B. Capability Inventory

### Dashboard Pages (17 Registered Routes in `frontend/src/App.tsx`)
1. `/` — **Triage:** Overview counts, recent review candidates, alerts, new entities.
2. `/review` — **Review:** Candidate queue with approve-merge and dismiss actions.
3. `/alerts` — **Alerts:** Alert list, stream fingerprints, windows, suppressions.
4. `/collector` — **Collector Coverage:** Collector health, source coverage, production status.
5. `/production` — **Production Readiness:** Readiness checks grouped by criticality.
6. `/multilingual` — **Multilingual:** Language, text, and translation coverage.
7. `/eval` — **Eval:** Evaluation runs, metrics, task regression views.
8. `/entities` — **Entities:** Entity directory, search, sort, platform filters.
9. `/entities/:id` — **Entity Detail:** Identity, timeline, confidence, connections, chat, map, media/faces, behavior, intelligence.
10. `/communities` — **Communities:** Community groups and graph overview.
11. `/graph` — **Graph:** Relationship/Telegram graph, path explanation, pivot exploration.
12. `/media` — **Media:** Media coverage, browsing, filtering, preview detail.
13. `/faces` — **Faces:** Face identity/gallery browsing, similarity, search, audit.
14. `/search` — **Search:** Keyword, semantic, and hybrid search.
15. `/cases` — **Cases:** Saved investigations, case items, export.
16. `/runs` — **Runs:** Run history, phase inspection, run triggers.
17. `/help` — **Help:** In-app glossary and reference.

### API Routes & Bot Command Parity
* Core & deferred routers mounted under `/api` and `/` (`src/api/app.py`). Face engine mounted under `/api/face`.
* Bot handler currently implements `/whois <query>` and `/digest`, plus inline merge/dismiss callbacks (`src/notifications/merge_bot.py`).
* Full dashboard capability parity via chat is planned in Phase 9.

---

## C. Bottlenecks & Audit Findings

* **Newest-first media selection window:** Bounded candidate window may starve older unprocessed items under high arrival rates (`src/pipeline/media_common.py:151-223`).
* **Timeline paging boundary ties:** Keyset comparison on timestamp alone (`occurred_at > last_ts`) risks omitting records with identical timestamps (`src/pipeline/timeline_builder.py:767-808`).
* **Stream alert cursor ties:** Polling on `occurred_at > cursor` risks boundary drops without a unique tiebreaker (`src/pipeline/stream_alerts.py:440-455`).
* **Timeline offset ordering:** `ORDER BY occurred_at` without secondary unique key allows row shifting across offset pages (`src/api/routes/timeline.py:267-364`).
* **Perceptual matching & clustering scaling:** Pairwise hash comparisons and full cluster passes scale with corpus size (`src/pipeline/media_analysis.py:481-609`, `src/pipeline/face_clustering.py:289-368`).
* **Face API auth & FAISS state:** Mounted face routes lack authentication wiring and in-process FAISS state present in the face worker (`src/api/face_mount.py:7-20`).

---

## D. Build Plan — Phases with Checklists

### Phase 0 — Contract Freeze + Stability Gate [COMPLETED]
* [x] **Stability gate check:** Pinned Collector HEAD (`c97391af`).
* [x] **Catalog Collector Tables:** Documented all 45 read tables and key columns in `docs/collector-contract.md`.
* [x] **Record Baselines:** 17 dashboard pages, API route inventory, bot commands, test suite baseline (5,535 tests across 242 files), data-quality ledger state.
* [x] **Contract check test:** Implemented `src/pipeline/collector_contract.py` and `tests/test_collector_contract.py` (7/7 passed).
* [x] **Committed & Pushed:** Commit `e75a85d` on `main`.

---

### Phase 1 — Track Collector Phase 1: Split Containers + Browser Rename [NEXT]
**Goal:** Analyzer health, coverage, readiness, and ledger reflect the new Collector services (`collector_exposure`, `collector_lemon8`, `collector_search`, `collector_strava`, and `browser_ingest`). No stale service names.
* [ ] Verify Analyzer health/coverage/readiness pages do NOT hardcode old service names (`collector_light`, `collector_lowrisk`, `instagram_ingest`). Update display lists or watchdog checks to the four new names + `browser_ingest`.
* [ ] Verify `browser_ingest_events` freshness logic still maps to the renamed service. Update any remaining docs/labels.
* [ ] Verify data-quality ledger per-source freshness still reports for exposure, Lemon8, search, and Strava under the new service names (Strava idle without auth = idle, not error).
* [ ] Add tests: health/coverage renders new service names; ledger handles renamed/missing service without crashing; readiness handles `browser_ingest_state` values (`unknown`, `active`, `active_via_maintenance`).
* [ ] Commit to `main`, push. Update `.agents/STATE.md` and `.agents/JOURNAL.md`.
**Acceptance:** Dashboard shows the four new Collector services + `browser_ingest` correctly. No old service names remain.

---

### Phase 2 — Track Collector Phase 2: Beeper + Lemon8 Both
**Goal:** Handle Beeper resync burst and Lemon8 volume + provenance growth without double-counting or alert storms.
* **2a. Beeper:** Idempotent ingestion on `beeper_shadow_messages` (dedupe on stable message key), watermark cursor, alert suppression on resync window. Fixture test with 1,000 backfilled messages.
* **2b. Lemon8 Both (API + Extension):** Dedupe on Collector `content_id` / URL + content hash. Record Lemon8 provenance (`api` vs `extension`) in Analyzer timeline and media rows. Tests: API + extension post ingests once; volume fixture of 50+ posts per window processes within batch limits.
* [ ] Commit to `main`, push. Update `.agents/STATE.md` and `.agents/JOURNAL.md`.

---

### Phase 3 — Track Collector Phase 3: Telegram Advanced Intelligence
**Goal:** Ingest the four restored Telegram features (auto-join discussion groups, poll voters, full group member lists, join/leave messages).
* [ ] Obtain DDL for `telegram_poll_votes` and `telegram_join_leave_events` before building.
* [ ] Timeline legs: poll votes as `POLL_VOTE`, join/leave as `MEMBER_JOIN` / `MEMBER_LEAVE`.
* [ ] Member roster: fuller `telegram_chat_members` refreshes entity membership and group co-occurrence without overwriting manual entity links.
* [ ] Discussion group messages: record chat type for filtering.
* [ ] Signals:
  - **Identity signals (weak, review queue only, never auto-merge alone):** `poll_vote_identity_support`, `group_join_identity_support`. Low weight, require corroboration.
  - **Relationship signals:** `co_presence`, `poll_co_vote` for distinct entities co-occurring in polls/chats. Feeds graph/intersections, not identity scoring.
* [ ] Scorer weights + tests: duplicate votes ingest once; missing tables fail soft with ledger warning.
* [ ] Commit to `main`, push. Update `.agents/STATE.md` and `.agents/JOURNAL.md`.

---

### Phase 4 — Track Collector Phase 4: Search Dynamic Terms + Google Dorking
**Goal:** Analyzer does the full work on Search documents. OCR, extract text and links, process documents, analyze photos, read EXIF, run face recognition.
* [ ] Obtain DDL for `search_documents`.
* [ ] Ingest `search_documents` into timeline + media with URL + content-hash dedupe against existing media.
* [ ] Full document massage:
  - Text extraction: PDF text, DOCX/XLSX/PPTX text, image OCR via Tesseract. Extract links, emails, phones, usernames with Search provenance.
  - Photo analysis: EXIF (GPS, device, timestamp), perceptual hash, face detection/recognition into review queue.
* [ ] Safety caps: per-file timeouts, size limits, tombstone only on permanent decode failures.
* [ ] Tests: duplicate PDF ingests once; OCR extracts text/links; EXIF GPS lands in geo evidence; face links to review queue; missing table fails soft.
* [ ] Commit to `main`, push. Update `.agents/STATE.md` and `.agents/JOURNAL.md`.

---

### Phase 5 — Track Collector Phase 5: Native Pinterest, Snapchat, Airbnb
**Goal:** Onboard all three as full Analyzer sources (resolver, timeline, media, intelligence).
* **5a. Pinterest:** Obtain DDL (`pinterest_boards`, `pinterest_pins`, `pinterest_profiles`). Resolver leg (review queue), timeline legs (pin/board creation, comments, repins), media legs (images via EXIF/OCR/perceptual-hash/faces), intelligence.
* **5b. Snapchat:** Obtain DDL (`snapchat_profiles`, `snapchat_stories`, `snapchat_spotlights`, `snapchat_highlights`). Public-only boundary strictly enforced (NO DMs, NO private stories). Resolver leg, timeline legs (story expiry keeps history), media legs.
* **5c. Airbnb:** Obtain DDL (`airbnb_listings`, `airbnb_hosts`, `airbnb_reviews`, `airbnb_profiles`). Resolver leg (never auto-merge host to guest/reviewer), timeline legs, media legs.
  - **Geo Home-Base Rule:** Listing location recorded as `airbnb_listing` geo evidence for host. Weak signal, weighted below Strava start point and corroborated Telegram/IG geo. One listing alone NEVER sets home-base. Requires 2+ listings in area or 1 listing + independent signal (Strava, Telegram, IG, Search). Dashboard labels "Airbnb listing area" vs "Home-base (Airbnb + corroboration)".
* [ ] Commit to `main`, push. Update `.agents/STATE.md` and `.agents/JOURNAL.md`.

---

### Phase 6 — Bluesky Onboarding
**Goal:** Bluesky flows into Analyzer from the fixed Collector probe.
* [ ] Obtain DDL (`bluesky_profiles`, plus posts/comments if present).
* [ ] Resolver leg with DID as stable key (handle changes do not duplicate entities).
* [ ] Timeline/media legs if post tables exist; otherwise explicitly record profile-only scope.
* [ ] Tests: DID stability; duplicate profiles ingest once; missing tables fail soft.
* [ ] Commit to `main`, push. Update `.agents/STATE.md` and `.agents/JOURNAL.md`.

---

### Phase 7 — IG DM Deletion Consequence
**Goal:** Analyzer stops ingesting IG DM and keeps history cleanly.
* [ ] Freeze/remove query legs targeting deleted `instagram_dm` and `instagram_dm_thread`.
* [ ] Retain historical Analyzer rows with deprecated/stale badge in UI; mark deprecated in ledger.
* [ ] Tests: missing/empty IG DM tables do not crash ingestion; historical rows remain queryable; zero new rows.
* [ ] Commit to `main`, push. Update `.agents/STATE.md` and `.agents/JOURNAL.md`.

---

### Phase 8 — Dashboard Testing (UI/UX + Unit + E2E)
**Goal:** Every dashboard page, component, and API route has tests, including new sources from Phases 2–6.
* [ ] Frontend unit/component test runner compatible with Vite and React component testing utilities.
* [ ] Dedicated CI job installing and testing `frontend/`.
* [ ] Component tests for all 17 pages covering success, empty, and error states.
* [ ] API contract tests for all registered routes including face routes and websocket health.
* [ ] Browser E2E suite covering critical analyst workflows (triage-to-review, search-to-detail, alert management, graph path/intersections, new sources browsing). Fixture data only.
* [ ] Commit to `main`, push. Update `.agents/STATE.md` and `.agents/JOURNAL.md`.

---

### Phase 9 — Bot Parity (Full Dashboard Parity via Chat)
**Goal:** Bot can do everything the dashboard can, via chat-friendly lists, details, pagination, and file/link responses.
* [ ] Help and read-only commands: triage overview, entity search/list, collector coverage, readiness, multilingual, eval, runs.
* [ ] Entity detail and search: extend `/whois` to cover timeline, changes, behavior, intelligence, relationships, location, media, and export across all new sources.
* [ ] Action handling: alert browsing/suppression, review queue actions, run triggering with confirmation.
* [ ] Parity matrix audit verifying 100% of dashboard capabilities are accounted for.
* [ ] Commit to `main`, push. Update `.agents/STATE.md` and `.agents/JOURNAL.md`.

---

### Phase 10 — Bottlenecks & Audit Closure
**Goal:** Close deferred audit items with evidence.
* [ ] Recent-window starvation: stable key paging until batch fills or source exhausted; retry/backoff/tombstone for missing media.
* [ ] Timeline keyset tie: composite keyset `(occurred_at, stable_id)` matching query sort order.
* [ ] Stream-alert tie: persist composite timestamp + primary key offset with deterministic order.
* [ ] Incremental perceptual matching & clustering: prove fixture equivalence to full rebuild before landing; keep full rebuild if equivalence fails.
* [ ] Face API auth: wire authentication for mounted `/api/face` routes.
* [ ] Face FAISS: resolve missing in-process FAISS state in API process (proxy or explicit degraded status).
* [ ] Commit to `main`, push. Update `.agents/STATE.md` and `.agents/JOURNAL.md`.

---

### Final Verification
* [ ] 15-minute soak with all Analyzer containers healthy and zero rate-limit events.
* [ ] Per-source 12h report verifying rows for all platforms.
* [ ] Re-run dashboard-versus-bot parity matrix.
* [ ] Re-verify contract against final Collector HEAD.
* [ ] Write summary to `/tmp/unifiedanalyzer-plan-complete-20261014.md`.
