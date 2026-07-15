# VISION_PLAN.md — OSINT Entity Life-Graph

> **Living tracking doc. DO NOT DELETE until every task below is checked off.**
> Each task is handoff-ready for a coding agent: goal · source data (exact
> tables/columns/verified counts) · files to touch · approach · acceptance.
> Check `- [x]` as completed and append notes inline. Deploy after edits with
> `docker compose -f C:\unifiedanalyzer\docker\docker-compose.yml restart scheduler analyzer`
> (NO rebuild — src is volume-mounted, shared Docker vhdx must not grow).

## Goal
Turn `unifiedanalyzer` into a targeted-OSINT subject profiler: pick ANY entity
(sparse or rich) → replay everything they did across every platform on one
time-scrubbable timeline that **fuses physical movement (GPS/geo) with digital
interaction**, and show the **reciprocal** side — every other entity they
touched (reacted, replied, mentioned, tagged, commented, followed, co-appeared
in a photo) lights up with the interaction shown from both directions. Zoom
years → seconds.

## Architecture map (so an agent can navigate)
- **Timeline spine:** `timeline_events` (analyzer DB) — partitioned by
  `occurred_at` (second resolution), FK `entity_id`, `(source,event_type,source_record_id,occurred_at)` unique.
  Built by `src/pipeline/timeline_builder.py` → `SOURCE_QUERIES` list (add a
  block per new event type; threads/x were added the same way).
- **Identity:** `entities`, `entity_platform_links`, `identity_signals`
  (`src/pipeline/entity_resolver.py`, `beeper_bridge.py`, `cross_source_signals.py`).
  See `C:\unifiedcollector\IDENTITY_KEYS.md` for the per-source key contract.
- **Relationships (UNDIRECTED today):** `entity_relationships`
  (`entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources, last_seen_at`).
  Writers: `group_graph.py`, `graph_overlap.py`, `identity_scorer.py`,
  `social_face_link.py`, `graph_analytics.py`.
- **Pipeline orchestration:** `src/pipeline/incremental_runner.py`
  (resolve → beeper_bridge → cross_source_signals → build_timeline → … → graph).
- **API:** `src/api/routes/{entities,timeline,graph,behavior,intelligence,media}.py`.
  Key entity endpoints: `/entities/{id}`, `/entities/{id}/timeline`,
  `/entities/{id}/timeline-lanes`, `/entities/{id}/geo`, `/entities/{id}/network`,
  `/entities/{id}/relationships`, `/entities/{id}/behavior`, `/entities/{id}/intelligence`.
  Served on host **:8002** (collector dashboard is :8001).
- **Frontend:** `frontend/` (Vite+React). Entity page `frontend/src/pages/EntityDetail.tsx`
  uses `components/{TimelineLanes,NetworkGraph,GeoMap,IdentitySummary,FaceAvatar}.tsx`.

## Live audit baseline (2026-07-14, entity "Ryan Tan")
Working today: timeline (72 events, **50 Strava** — lopsided), `/geo`
(routes+points, Strava only, 4 pts), `/network` (weighted, faces), 63
`/relationships` (association-only), behavior heatmaps, intelligence aggregate.
**Under-fed:** interactions with contacts (network weight 96 to a whatsapp
neighbour) never reach the timeline; relationships are "near", not "did to";
geo is Strava-only. Verdict: ~75% architecture, ~40% data-depth, ~20% directed
interactions.

## Data availability (verified counts — the fuel that already exists)
| Signal | Table / column | Count | Status |
|---|---|---|---|
| Reactions | `telegram_reactions` (user_id→message) | **105,082** | collected, NOT in timeline |
| Replies | `telegram_messages.reply_to_message_id` | **144,095** | collected, NOT surfaced |
| IG mentions | `instagram_posts.mentions` | **23,519** | collected, NOT edges |
| IG geo-posts | `instagram_posts.location_lat/lng/name` | **4,512** | collected, NOT on map |
| Comments | `instagram_comments`(6,567)/`tiktok_comments`/`youtube_comments`/`strava_activity_comments` | 6.5k+ | partial |
| Follows | `follow_edges` (owner→target, direction) | 732 | collected, NOT edges |
| Msg geo | `telegram_message_locations`(54)/`whatsapp_message_locations`(0) | thin | pipeline exists |
| Strava GPS | `strava_activities` | 38,300 | on map ✅ |
| Faces | facetracker + `mutual_social_face` rel | — | partial |
| **Tagged photos** | `media_items` source=instagram `kind='tagged'` (entity_id, metadata.taken_at) | **33,806** (524 ents) | collected, NOT surfaced |
| **Stories** | `media_items kind='story'` (metadata.taken_at) | **1,206** (373 ents) | collected, NOT in timeline |
| **Highlights** | `media_items kind='highlight'` | **3,776** (47 ents) | collected, NOT in timeline |
| IG posts (media) | `media_items kind='post'` | 80,749 | on media page |
| Like COUNTS | `media_items.metadata->>'likes_count'` (per post/tagged/story) | present | aggregate only |
| Individual likers | — (IG hides liker lists) | 0 | GAP — infeasible w/o auth |

---

## Execution sequence (recommended order for the coding agent)
Dependency-ordered. Ship in this order; each step is independently
verifiable/deployable. `→` = "unblocks".

**START HERE ▶ Phase 1** (no new models, pure timeline blocks — same pattern as
threads/x; delivers the biggest visible win from already-collected data).
```
P1 (saturate timeline)
   T1.5 (IG geo metadata) ─────────────┐            (do early: also feeds T3.3)
   T1.1 reactions · T1.2 replies ·      │
   T1.3 comments · T1.4 follows ·       ├─→ T1.7 (full rebuild + verify)
   T1.6 stories/highlights ─────────────┘
        │
        ▼
P2 (directed interactions)              ← the reciprocal core
   T2.1 entity_interactions model
     → T2.2 builder (reactions/replies/comments/mentions/follows/tags/dms)
        → T2.3 aggregate to relationships   → T2.4 API   → T2.5 frontend edges
   (T4.2 tagged + T4.1 face-coappear FEED T2.2 — do them here, not later)
        │
        ▼
P3 (unified scrubber)                   ← needs P1 data + P2 edges on screen
   T3.1 master time-brush → T3.2 zoom → T3.3 fused map → T3.4 playback(opt)
        │
        ▼
P5 (edge intelligence)                  ← ranks/weights what P2 produced
   Quick wins first: T5.6 bio/link, T5.2 shared-route-origin, T5.4 group-size wt
   Then: T5.1 co-presence, T5.3 chain-depth, T5.5 content-reuse,
         T5.9 centrality, T5.10 explainable, T5.7 style, T5.8 silence
Cross-cutting CC1/CC2/CC3 run alongside every phase.
```
**Rationale:** P1 is cheap + high-impact (unlocks 105k reactions + 144k replies +
23k mentions + 34k tagged photos already on disk). P2 is the vision's heart but
needs P1's normalized events. P3 is UX that needs P1+P2 data to be worth
building. P5 is the intelligence layer that only makes sense once P2 edges exist.
**T4.1/T4.2 are pulled UP into P2** (they are interaction sources, not an
afterthought). **T4.3 is a no-op** (documented gap). Parallelizable: within P1
all Txx are independent; P5 tasks are independent of each other.

**Suggested milestones:** M1 = P1 done (timelines rich). M2 = P2 done (reciprocal
interactions visible). M3 = P3 done (fused scrubber). M4 = P5 done (ranked edges).

---

## PHASE 1 — Saturate the timeline (highest ROI; data already collected)
Goal: entity timelines reflect real activity, not just Strava. Pattern = add
blocks to `timeline_builder.py::SOURCE_QUERIES` (each: `source, event_type,
query` returning `record_id, occurred_at, title, entity_ref[, entity_ref2]`,
`time_col`). Attribution keys per `IDENTITY_KEYS.md`.

- [x] **T1.1 Reactions → `REACTION_GIVEN`.** Source `telegram_reactions`
  (105,082): `entity_ref` = `user_id` (reactor's telegram platform id); JOIN
  `telegram_messages m ON m.id=message_id` to put target author + `emoji` +
  target `title` in `metadata`. `time_col`=`added_at`.
  *Accept:* `SELECT count(*) FROM timeline_events WHERE event_type='REACTION_GIVEN'` > 0; events show on reactor timelines. Notes 2026-07-14: filtered backfill runner completed with `REACTION_GIVEN=105082`, matching `telegram_reactions`.
- [x] **T1.2 Replies → `REPLIED`.** Source `telegram_messages` where
  `reply_to_message_id IS NOT NULL` (144,095): `entity_ref`=sender; self-join to
  the replied-to message for target author/preview in `metadata`.
  *Accept:* reply events present; metadata carries the target. Notes 2026-07-15:
  corrected the reply-parent join to use Telegram chat `platform_chat_id` plus the
  bare `reply_to_message_id`, then repaired historical analyzer rows in place via
  `dblink` from `unifiedcollector` -> `unifiedanalyzer`; live update touched
  `133,679` rows. Post-repair counts:
  `timeline_events.telegram/REPLIED total=133,767`, `with_target=93,184`,
  `reply_repair_checked=108,767`, exact sample `1372604510:434` now carries
  `target_platform_user_id=325199291`, `target_username=garethsome`.
- [x] **T1.3 Comments → `COMMENT_POSTED`.** `instagram_comments`(6,567),
  `tiktok_comments`, `youtube_comments`, `strava_activity_comments`. `entity_ref`
  = comment author; post/owner in metadata.
  *Accept:* comment events per platform present. Notes 2026-07-14: `timeline_builder.py` now emits owner metadata for Instagram + YouTube comments; live backfill produced `instagram=6567`, `youtube=1851`. Current collector tables hold `tiktok_comments=0` and `strava_activity_comments=0`, so those source blocks are present but no-op today.
- [x] **T1.4 Follows → `FOLLOWED`.** `follow_edges` (`owner_account`→`target_uid`,
  `direction`). Emit for `direction='following'`; store target handle in metadata.
  *Accept:* follow events present. Notes 2026-07-14: backfill produced `FOLLOWED=325`; metadata includes `target_uid`, `target_username`, `first_seen`, `last_seen`.
- [x] **T1.5 IG geo-posts carry location.** For `instagram_posts` with
  `location_lat` (4,512): ensure the existing IG post event puts
  `location_lat/lng/name` into `timeline_events.metadata` (needed by T3.3/geo).
  *Accept:* IG post events have geo in metadata. Notes 2026-07-14: `timeline_events` now has `4512` Instagram `CONTENT_PUBLISHED` rows with `metadata.location_lat`, matching the current source-row count.
- [x] **T1.6 Stories/highlights → `STORY_POSTED` / `HIGHLIGHT_POSTED`.**
  RESOLVED: stories = `media_items` source=instagram `kind='story'` (1,206),
  highlights `kind='highlight'` (3,776) — both fully entity-attributed
  (`entity_id`), timestamp in `metadata->>'taken_at'` (unix). Add timeline blocks
  keyed on `entity_id`; carry caption/likes_count in metadata.
  *Accept:* story + highlight events on entity timelines. Notes 2026-07-14: live rows do not currently carry `metadata.taken_at`, so the analyzer backfill falls back to `created_at`/`collected_at` and records `timestamp_source` in metadata. Backfill counts: `STORY_POSTED=1279`, `HIGHLIGHT_POSTED=3776`.
- [x] **T1.7 Verify pipeline picks up new blocks.** After T1.1–1.6, run one full
  build (`docker exec unifiedanalyzer_analyzer python -m src.main full` or wait
  for scheduler); spot-check a rich entity's timeline is no longer Strava-only.
  *Accept:* `timeline_events` event_type variety ≥ 8 per active entity sample.
  Notes 2026-07-15: added a `telegram/FORWARDED_MESSAGE` timeline block for
  already-collected forwarded Telegram messages, then reran a targeted live
  backfill. Analyzer now has `FORWARDED_MESSAGE=116,612` rows, and the richest
  active sample `bryanseah234` (`6c76d679-34d3-4da4-91f1-44e1c1a97b4e`) now
  reaches `8` types:
  `CODE_COMMIT, COMMENT_POSTED, FOLLOWED, FORWARDED_MESSAGE, MESSAGE_SENT, REACTION_GIVEN, REPLIED, STORY_POSTED`.

## PHASE 2 — Directed interaction graph (the reciprocal vision)
Goal: model "X did ACTION to Y", queryable both directions. `entity_relationships`
is undirected (a/b) → build a NEW directed layer.

- [x] **T2.1 New model `entity_interactions`.** New additive migration
  (`src/db/migrations/…` — nullable columns only; never edit an applied migration
  — checksum drift bricks migrate-on-boot). Columns:
  `id, actor_entity_id, target_entity_id, interaction_type
  (reacted|replied|commented|mentioned|tagged|followed|dm|forwarded|face_coappear),
  source, source_record_id, occurred_at, weight, metadata, created_at`;
  unique `(interaction_type, source, source_record_id)`; indexes on
  `(actor_entity_id, occurred_at)` and `(target_entity_id, occurred_at)`.
  Notes 2026-07-15: implemented in analyzer schema (`src/db/schema.sql`) and
  applied live; table now holds directed interaction rows and supports typed,
  directional aggregation.
- [ ] **T2.2 Builder `src/pipeline/interaction_graph.py`.** Populate from:
  reactions (reactor→msg author), replies (`reply_to_message_id`), comments
  (author→post owner), IG `mentions` (parse @handle → resolve to entity via
  `entity_platform_links`), tags (T4.2), follows (`follow_edges`), forwards
  (`forward_from_*`), DMs (`instagram_dm`). Resolve both sides to `entity_id`;
  skip rows where either side is unresolved (log counts). Idempotent upsert.
  Wire into `incremental_runner.py` after `build_timeline`.
  *Accept:* `entity_interactions` populated; each `interaction_type` has rows;
  re-run doesn't duplicate. Notes 2026-07-15: live populated types currently
  `telegram/reacted=6233`, `telegram/replied=1191`, `telegram/forwarded=14`,
  `instagram/followed=51`, `instagram/tagged=278`,
  `instagram/commented=36`, `instagram/mentioned=20`. The builder now also
  includes explicit `instagram/dm` and analyzer-side
  `facetracker/face_coappear` code paths; live verification after restart
  showed both are safe no-ops today (`instagram_dm=0`,
  `face_coappear_interactions=0`). Current raw-source gaps:
  `instagram_dm=0`, `strava_activity_comments=0`, `face_associations=0`;
  `youtube/commented` resolves to no tracked cross-entity pairs today. The
  collector-side Instagram DM worker is still scaffold-only
  (`src/collectors/instagram_dm/auth.py` intentionally unimplemented, compose
  profile default-disabled), so there is no in-repo path to generate real `dm`
  rows without an external collector activation + new source data.
- [x] **T2.3 Aggregate directed edges into `entity_relationships`.** Roll up
  `entity_interactions` into a directed-aware `relationship_type='interaction'`
  (or keep separate) with per-type counts + `last_seen_at` in `sources` jsonb, so
  the graph weights reflect real interaction volume + recency.
  *Accept:* graph weights change to reflect interaction counts. Notes
  2026-07-15: relationship rollup refreshed live to `1119` directed interaction
  relationships with per-type/per-source counts in `sources`.
- [x] **T2.4 API: reciprocal interactions.** New `/entities/{id}/interactions`
  (`src/api/routes/graph.py`) returning, per connected entity, both directions
  with type breakdown + counts + last_ts (e.g. `{out:{reacted:96}, in:{replied:12}}`).
  *Accept:* endpoint returns directed, typed, dated edges. Notes 2026-07-15:
  live `/api/entities/6c76d679-34d3-4da4-91f1-44e1c1a97b4e/interactions`
  returned `107` peers overall and `5` peers inside a 2026-07-13..2026-07-14
  time window, confirming typed directional filtering.
- [x] **T2.5 Frontend: directed typed edges.** `NetworkGraph.tsx` — arrowheads +
  edge color/label by `interaction_type`; neighbour tooltip shows reciprocal
  summary. `EntityDetail.tsx` — an "Interactions" panel.
  *Accept:* directed typed edges render; reciprocal shown on both entities.
  Notes 2026-07-15: frontend builds clean after adding the interactions panel,
  reciprocal summaries, and type-colored arrowheads.

## PHASE 3 — Unified physical+digital scrubber (the "OSINT" feel)
Goal: one time-brush drives timeline + map + graph together; physical and
digital fused on the same axis.

- [x] **T3.1 Master time-brush.** `TimelineLanes.tsx` emits a selected
  `[t0,t1]`; `EntityDetail.tsx` passes it to `GeoMap` + `NetworkGraph` as a
  filter. Backend: `/geo` and `/interactions` accept `?from=&to=`.
  *Accept:* scrubbing the timeline filters map + graph in sync. Notes
  2026-07-15: shared brush state now lives in `EntityDetail.tsx`; `TimelineLanes`
  emits the selected window via dual sliders and dims out-of-window events.
  Live API verification:
  `/geo` for entity `3bea0c54-d6c4-459a-be38-c61676df8868` changed from
  `routes=4, points=4` to `routes=0, points=0` for a non-overlapping window;
  `/interactions` for entity `6c76d679-34d3-4da4-91f1-44e1c1a97b4e` dropped from
  `107` peers overall to `5` peers in a 2026-07-13..2026-07-14 window.
- [x] **T3.2 Zoom levels (year→second).** Timeline supports zoom + windowing on
  `occurred_at` (already second-resolution); bucket adaptively; virtualize dense
  windows.
  *Accept:* zoomable axis; dense windows don't collapse/lag. Notes 2026-07-15:
  `TimelineLanes.tsx` now drives adaptive tick labels from year down to minute
  scale, adds preset windows (`1h/1d/1w/1m/1y/all`) plus pan controls, and
  buckets dense lanes into pixel-width aggregates instead of drawing every point.
  Frontend builds clean after the zoom/bucketing pass.
- [x] **T3.3 Fused map layer.** `GeoMap.tsx` renders Strava routes + IG geo-posts
  (4,512) + message-locations on one map, color-coded by source, time-filtered by
  the brush; clicking a pin cross-highlights the timeline event.
  *Accept:* multi-source, time-filtered map with cross-highlight. Notes
  2026-07-15: `/geo` now serves time-filtered Strava, Instagram, and Telegram
  message-location points, and the map tab now wires point/route clicks back into
  `TimelineLanes` as a highlighted timestamp. Frontend builds clean, and a live
  headless-browser verification against entity
  `3bea0c54-d6c4-459a-be38-c61676df8868` confirmed the map click path:
  the map rendered `4 routes · 4 places`, clicking a live Leaflet geometry
  surfaced the "Selected map event" panel, and the linked timeline lane
  highlight path executed in-browser.
- [x] **T3.4 "Now-line" playback (optional).** A play button that sweeps the
  brush and animates map pins + graph edges appearing in temporal order.
  *Accept:* playback animates movement + interactions. Notes 2026-07-15:
  `TimelineLanes.tsx` now exposes `Play/Pause` plus window step controls; because
  the map and interaction graph are already brush-driven from `EntityDetail.tsx`,
  playback advances those views in lockstep. Frontend builds clean.

## PHASE 4 — Tagged photos (both senses) + collection gaps
- [ ] **T4.1 Face co-appearance → timeline + interaction.** Promote
  `mutual_social_face` (relationship) into a `face_coappear` interaction (T2.1)
  and a `PHOTO_COAPPEARANCE` timeline event on both entities, using media
  ownership (who posted) + face match. Confidence from face score.
  *Accept:* face-tag events on both entities' timelines. Notes 2026-07-15:
  reran `face_associations` after the latest face pipeline work; it successfully
  populated `entities.primary_face_id` for `23` entities, but live source data
  still yields `media_scanned=0`, `face_associations=0`, and
  `mutual_social_face=0`, so there is nothing concrete to promote yet. The
  analyzer now has explicit no-op-safe code paths for both
  `facetracker/face_coappear` interactions and `PHOTO_COAPPEARANCE` timeline
  events; a targeted restart/backfill verified `face_coappear_interactions=0`
  and `PHOTO_COAPPEARANCE=0` with no skipped tables. A broader ownership audit
  also confirmed the live corpus still has no promotable examples:
  `facetracker.images face_count>=2 = 294`, `entities.primary_face_id = 23`,
  but `collector.media_items.entity_id` joined to those multi-face image hashes
  yields `owned_multiface_media = 0`.
- [x] **T4.2 Tagged photos + @-mentions → `tagged`/`mentioned` (both senses).**
  (a) Metadata sense — RESOLVED: `media_items kind='tagged'` (**33,806**, 524
  ents, `metadata.taken_at`) = photos an entity is tagged in by others → emit a
  `TAGGED_IN` timeline event on the tagged entity AND a `tagged` interaction
  (poster→tagged) once poster is resolvable from `source_url`/owner.
  (b) `instagram_posts.mentions` (23,519) + caption `@handle` parse → `mentioned`
  interactions (feeds T2.2).
  *Accept:* tagged-photo events on timelines; tag/mention edges present. Notes
  2026-07-15: analyzer backfill inserted `TAGGED_IN=33806` timeline rows; live
  interactions now include `instagram/tagged=278` and `instagram/mentioned=20`.
- [x] **T4.3 Likes: counts yes, per-liker no.** RESOLVED: individual liker lists
  are NOT collectable (IG hides them; scrape/mobile-limited like lemon8 — see
  `IDENTITY_KEYS.md`). BUT `media_items.metadata->>'likes_count'` +
  `comments_count`/`views_count` ARE present per post/tagged/story → use as an
  **engagement metric** on the entity/media, not as like-edges. Document; no
  per-liker collection task.
  *Accept:* engagement counts surfaced on entity/media; gap documented. Notes
  2026-07-15: timeline builder now preserves `likes_count/comments_count/views_count`
  on `TAGGED_IN` events, and the entity timeline UI renders them as engagement
  chips. Live backfill refreshed `TAGGED_IN=33806`; analyzer counts now show
  `TAGGED_IN likes=33640 comments=33806 views=1087`. Example live timeline row
  for entity `b479c472-012e-428e-95d8-adb34062e9e5` carries
  `likes_count=25 comments_count=0`.

## PHASE 5 — Edge & Relationship Intelligence (deepen the graph)
Many of these already exist as `identity_signals`/`entity_relationships` but are
under-surfaced or unweighted. Goal: turn them into ranked, explainable edges.
(Existing: `group_cooccurrence` 18,105 · `temporal_copost` 308 ·
`content_similarity` 191 · `shared_life_context` 374 · `temporal_hour_similarity`
644 · `social_graph_overlap` 2,197 · `bio_mention` 4 · `shared_website` 23.)

- [x] **T5.1 Co-presence in time (tight window).** Extend `temporal_correlation.py`:
  two entities repeatedly active within N seconds → strong `co_presence` edge
  (distinct from hourly `temporal_hour_similarity`). Weight by frequency + tightness.
  *Accept:* `co_presence` edges with sub-minute evidence. Notes 2026-07-15:
  `temporal_correlation.py` now writes `entity_relationships.relationship_type='co_presence'`
  with `window_seconds/coincident_events/copresence_days/p_value/confidence/why`
  in `sources`. Live rerun produced `co_presence=136`. Example top row carries
  `weight=135`, `coincident_events=35`, `copresence_days=8`, and reason
  `"Repeated activity within a sub-minute window suggests tight co-presence."`
- [ ] **T5.2 Shared route origin → strong edge.** `route_similarity.py` /
  `shared_route_origin` signal currently ~0 rows. Compute Strava route
  start-coordinate clustering across entities → `shared_home_or_gym` edge (very
  strong tie). *Accept:* edges where two entities share a start locus. Notes
  2026-07-15: `relationship_intelligence.py` now promotes
  `identity_signals.shared_route_origin` into `shared_home_or_gym` edges, but the
  live analyzer currently has `shared_route_origin=0`, so no edges yet. Latest
  rerun still reports `entities_with_gps=12` and `shared_route_origin_signals=0`.
  Follow-up audit using a broader 250m recurring-cluster model over
  `start_latlng` still found `pair_hits<=1km = 0`, and the collector has
  `start_latlng_full = 0` for all Strava activities, so there is no stronger
  in-repo coordinate source available today to manufacture real shared-origin
  edges without inventing noise.
- [x] **T5.3 Reply/mention chain depth & reciprocity.** From `entity_interactions`
  (T2), weight edges by back-and-forth depth + reciprocity ratio + recency, not
  raw count. *Accept:* edge weight reflects conversation depth, not volume.
  Notes 2026-07-15: `refresh_interaction_relationships()` now blends directed
  volume with reverse-direction totals and a `reciprocity_ratio`; live refresh
  produced `interaction=1129`, and all `1129` rows now carry
  `sources.reverse_total`, `sources.reciprocity_ratio`, and
  `sources.why="Weight blends directed volume with reciprocal depth and balance."`
- [x] **T5.4 Group-size-weighted co-membership.** Re-weight
  `{telegram,whatsapp}_group_co_member` (96k rows) inversely by group size — 5
  shared small groups ≫ 1 giant broadcast. Add participant_count join.
  *Accept:* small-group co-members rank above big-group ones. Notes 2026-07-15:
  both group builders now persist `group_sizes`, `weighted_total`,
  `shared_group_count`, and an explicit why-string. After restarting the long-
  running scheduler/analyzer processes and rerunning the builders, live weighted
  rows are `telegram_group_co_member=20441` and `whatsapp_group_co_member=33037`.
  Example WhatsApp edge now weighs `228` from `weighted_total=2.2807` across six
  shared groups; example Telegram edge weighs `1221` from `weighted_total=12.2076`
  across forty-two mostly small private groups.
- [x] **T5.5 Content-fingerprint reuse → coordination edge.** Surface
  `content_similarity` (191) + same image sha256 / identical caption / shared
  link across entities as `content_reuse` edges (coordination or same-person).
  *Accept:* content-reuse edges present + explainable. Notes 2026-07-15:
  `relationship_intelligence.py` now promotes `identity_signals.content_similarity`
  into `entity_relationships.relationship_type='content_reuse'`; live refresh
  produced `191` rows. Example live edge: `whatsapp:6588091286@s.whatsapp.net`
  ↔ `whatsapp:6596357375@s.whatsapp.net`, weight `90`, sources explain
  `"Their content fingerprints align across posts..."` with example
  `cosine:1.000`.
- [x] **T5.6 Bio/link cross-refs → self-declared edge.** Surface `shared_website`
  / `cross_platform_link` / `bio_mention` as explicit `self_declared_link` edges
  in the graph (strong, human-authored). *Accept:* bio/link edges shown. Notes
  2026-07-15: `relationship_intelligence.py` now promotes these signals into
  `entity_relationships.relationship_type='self_declared_link'`; live refresh
  produced `12` rows. Example live edge: `bryanseah234` ↔
  `SMU Foundations of Cybersecurity`, weight `35`, reason
  `"Human-authored cross-reference in a bio, link, or personal website."`
- [x] **T5.7 Style/emoji/language fingerprint → soft same-person.** Per-entity
  writing-style vector (emoji freq, n-gram, avg length, language) → similarity
  edge as a soft same-person / affinity signal. *Accept:* style-similarity edges.
  Notes 2026-07-15: `relationship_intelligence.py` now derives
  `style_similarity` edges from stored content-fingerprint metrics plus bio NLP
  emoji/language hints. Live refresh produced `style_similarity=454`. Example
  live row weighs `80` and explains fifteen shared top words such as
  `book/call/clerk/day/duty/ekms/...`.
- [x] **T5.8 Silence correlation.** Entities whose active/quiet windows move
  together (same travel/timezone shifts) → `co_absence` edge. *Accept:* edges
  from correlated silence. Notes 2026-07-15: `temporal_correlation.py` now emits
  `entity_relationships.relationship_type='co_absence'` with overlap/agreements
  in `sources`. Live rerun produced `co_absence=190`; top sample rows carry
  `shared_silent_days` in the `398..637` range with `agreement >= 0.925`.
- [x] **T5.9 Bridge/centrality scoring.** `graph_analytics.py` — compute
  betweenness/degree; flag entities bridging separate clusters as key targets;
  expose on entity page + `/graph/overview`. *Accept:* centrality scores stored +
  surfaced; bridges highlighted. Notes 2026-07-15: `behavioral_profiles`
  currently has `metadata.graph_analytics` on `1761` rows and `community_id` on
  the same set. `/api/graph/overview` now returns `top_bridges`; live top bridge
  sample is `@fullofsarahtonin` with `betweenness=0.000493`, `degree=20`,
  `strength=27`. Entity pages already surface degree/strength/betweenness.
- [x] **T5.10 Explainable edge weights.** Every edge carries a human-readable
  "why" (which signals + counts) in `sources` jsonb, shown on hover.
  *Accept:* hovering any edge explains its basis. Notes 2026-07-15:
  `/entities/{id}/relationships`, `/entities/{id}/network`, and
  `/graph/overview` now emit `why` strings derived from `sources` for
  interactions, social-graph overlap, temporal similarity, same-person
  probability, group co-membership, self-declared-link, and content-reuse edges.
  Live example from `/api/entities/0ca8cece-1a64-4112-bcee-2cf1b4a5a00f/relationships`:
  `telegram_group_co_member` now explains
  `"Shared group membership: IS KEBAB JIEJIE OPEN??, SMU .Hack Members 👾, SMU_IS (+5 more)."`

## Cross-cutting
- [x] **CC1 Sparse-entity UX.** Every entity (sparse→rich) renders gracefully;
  empty panels show "no data" not errors. *Accept:* a 1-event entity page is clean.
  Notes 2026-07-15: live sparse entity
  `002fbda3-5ac6-4d5a-b40c-07165a2aa58a` now returns clean API shapes across the
  entity page tabs: `timeline rows=1 total=1`, `timeline_lanes total=1`,
  `interactions=0`, `geo=0`, `associates=0`, `same_person_candidates=0`.
  `EntityDetail.tsx` already has explicit `EmptyState` branches for timeline,
  map, behavior, interactions, relationships, social-circle, and not-found
  cases, so sparse pages degrade cleanly instead of erroring.
- [ ] **CC2 Performance at scale.** Timeline/geo/interaction endpoints paginate +
  window; no full-table scans per entity (indexes on `(actor,occurred_at)` etc).
  *Accept:* rich-entity page < 2s. Notes 2026-07-15: timeline pages remain
  paginated, interaction + geo endpoints are brush-windowed, and the timeline UI
  now buckets dense lanes client-side. `src/api/routes/timeline.py` now walks
  concrete monthly partitions for `timeline` / `timeline-lanes` instead of
  planning a parent-table `Merge Append`, and the frontend now lazy-loads lanes
  only on `timeline`/`map`/`interactions` while shrinking the default request
  from `8000` to `2000`. `/interactions` was rewritten from an `actor OR target`
  full-table scan into two indexed branches, and the Strava geo path now uses
  bigint athlete ids instead of a text cast, letting the DB use
  `unique_platform_athlete_strava` plus `idx_strava_activities_athlete`.
  A new covering index
  `idx_timeline_entity_time_lane_cover(entity_id, occurred_at DESC) INCLUDE (source, event_type)`
  dropped the worst rich-sample partition scan from ~`2.5s` to ~`42ms`, and the
  cold live API path now lands under target
  (`/timeline ~0.80s`, `/timeline-lanes?max_events=2000 ~0.51s`,
  `/interactions ~0.57s`, `/geo ~0.69s`). Frontend route-level lazy loading
  also cut the initial JS bundle from ~`588kB` to ~`285kB` plus a dedicated
  `EntityDetail` chunk (~`196kB`). Even so, production-preview browser timings
  for the real entity page remain above the strict user-facing bar on first hit
  (`rich initial ~4.47s`, `timeline tab ~2.76s`, `interactions tab ~4.24s`,
  `geo initial ~2.47s`, `map tab ~1.19s`), so the "< 2s page" acceptance line
  is still not stable enough to close.
- [x] **CC3 Backfill after each phase.** Run full resolution + timeline rebuild;
  verify counts move. *Accept:* per-phase before/after metrics recorded here.
  Notes 2026-07-15: P1 counts are recorded inline per event type
  (`REACTION_GIVEN=105082`, `REPLIED=133767`, `COMMENT_POSTED instagram=6567 /
  youtube=1851`, `FOLLOWED=325`, `STORY_POSTED=1279`, `HIGHLIGHT_POSTED=3776`,
  `FORWARDED_MESSAGE=116612`); P2/T4 interaction counts are recorded inline
  (`interaction relationships=1119`, live typed rows including reacted/replied/
  forwarded/followed/tagged/commented/mentioned); P5 edge counts are likewise
  captured inline (`co_presence=136`, `content_reuse=191`,
  `self_declared_link=12`, `style_similarity=454`, `co_absence=190`, etc.).

## Definition of done
All boxes checked, a rich AND a sparse entity both render a fused
physical+digital timeline with reciprocal directed interactions and ranked
explainable edges. Then — and only then — delete this file.
