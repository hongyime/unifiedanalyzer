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
- [ ] **T1.2 Replies → `REPLIED`.** Source `telegram_messages` where
  `reply_to_message_id IS NOT NULL` (144,095): `entity_ref`=sender; self-join to
  the replied-to message for target author/preview in `metadata`.
  *Accept:* reply events present; metadata carries the target.
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
- [ ] **T1.7 Verify pipeline picks up new blocks.** After T1.1–1.6, run one full
  build (`docker exec unifiedanalyzer_analyzer python -m src.main full` or wait
  for scheduler); spot-check a rich entity's timeline is no longer Strava-only.
  *Accept:* `timeline_events` event_type variety ≥ 8 per active entity sample.

## PHASE 2 — Directed interaction graph (the reciprocal vision)
Goal: model "X did ACTION to Y", queryable both directions. `entity_relationships`
is undirected (a/b) → build a NEW directed layer.

- [ ] **T2.1 New model `entity_interactions`.** New additive migration
  (`src/db/migrations/…` — nullable columns only; never edit an applied migration
  — checksum drift bricks migrate-on-boot). Columns:
  `id, actor_entity_id, target_entity_id, interaction_type
  (reacted|replied|commented|mentioned|tagged|followed|dm|forwarded|face_coappear),
  source, source_record_id, occurred_at, weight, metadata, created_at`;
  unique `(interaction_type, source, source_record_id)`; indexes on
  `(actor_entity_id, occurred_at)` and `(target_entity_id, occurred_at)`.
- [ ] **T2.2 Builder `src/pipeline/interaction_graph.py`.** Populate from:
  reactions (reactor→msg author), replies (`reply_to_message_id`), comments
  (author→post owner), IG `mentions` (parse @handle → resolve to entity via
  `entity_platform_links`), tags (T4.2), follows (`follow_edges`), forwards
  (`forward_from_*`), DMs (`instagram_dm`). Resolve both sides to `entity_id`;
  skip rows where either side is unresolved (log counts). Idempotent upsert.
  Wire into `incremental_runner.py` after `build_timeline`.
  *Accept:* `entity_interactions` populated; each `interaction_type` has rows;
  re-run doesn't duplicate.
- [ ] **T2.3 Aggregate directed edges into `entity_relationships`.** Roll up
  `entity_interactions` into a directed-aware `relationship_type='interaction'`
  (or keep separate) with per-type counts + `last_seen_at` in `sources` jsonb, so
  the graph weights reflect real interaction volume + recency.
  *Accept:* graph weights change to reflect interaction counts.
- [ ] **T2.4 API: reciprocal interactions.** New `/entities/{id}/interactions`
  (`src/api/routes/graph.py`) returning, per connected entity, both directions
  with type breakdown + counts + last_ts (e.g. `{out:{reacted:96}, in:{replied:12}}`).
  *Accept:* endpoint returns directed, typed, dated edges.
- [ ] **T2.5 Frontend: directed typed edges.** `NetworkGraph.tsx` — arrowheads +
  edge color/label by `interaction_type`; neighbour tooltip shows reciprocal
  summary. `EntityDetail.tsx` — an "Interactions" panel.
  *Accept:* directed typed edges render; reciprocal shown on both entities.

## PHASE 3 — Unified physical+digital scrubber (the "OSINT" feel)
Goal: one time-brush drives timeline + map + graph together; physical and
digital fused on the same axis.

- [ ] **T3.1 Master time-brush.** `TimelineLanes.tsx` emits a selected
  `[t0,t1]`; `EntityDetail.tsx` passes it to `GeoMap` + `NetworkGraph` as a
  filter. Backend: `/geo` and `/interactions` accept `?from=&to=`.
  *Accept:* scrubbing the timeline filters map + graph in sync.
- [ ] **T3.2 Zoom levels (year→second).** Timeline supports zoom + windowing on
  `occurred_at` (already second-resolution); bucket adaptively; virtualize dense
  windows.
  *Accept:* zoomable axis; dense windows don't collapse/lag.
- [ ] **T3.3 Fused map layer.** `GeoMap.tsx` renders Strava routes + IG geo-posts
  (4,512) + message-locations on one map, color-coded by source, time-filtered by
  the brush; clicking a pin cross-highlights the timeline event.
  *Accept:* multi-source, time-filtered map with cross-highlight.
- [ ] **T3.4 "Now-line" playback (optional).** A play button that sweeps the
  brush and animates map pins + graph edges appearing in temporal order.
  *Accept:* playback animates movement + interactions.

## PHASE 4 — Tagged photos (both senses) + collection gaps
- [ ] **T4.1 Face co-appearance → timeline + interaction.** Promote
  `mutual_social_face` (relationship) into a `face_coappear` interaction (T2.1)
  and a `PHOTO_COAPPEARANCE` timeline event on both entities, using media
  ownership (who posted) + face match. Confidence from face score.
  *Accept:* face-tag events on both entities' timelines.
- [ ] **T4.2 Tagged photos + @-mentions → `tagged`/`mentioned` (both senses).**
  (a) Metadata sense — RESOLVED: `media_items kind='tagged'` (**33,806**, 524
  ents, `metadata.taken_at`) = photos an entity is tagged in by others → emit a
  `TAGGED_IN` timeline event on the tagged entity AND a `tagged` interaction
  (poster→tagged) once poster is resolvable from `source_url`/owner.
  (b) `instagram_posts.mentions` (23,519) + caption `@handle` parse → `mentioned`
  interactions (feeds T2.2).
  *Accept:* tagged-photo events on timelines; tag/mention edges present.
- [ ] **T4.3 Likes: counts yes, per-liker no.** RESOLVED: individual liker lists
  are NOT collectable (IG hides them; scrape/mobile-limited like lemon8 — see
  `IDENTITY_KEYS.md`). BUT `media_items.metadata->>'likes_count'` +
  `comments_count`/`views_count` ARE present per post/tagged/story → use as an
  **engagement metric** on the entity/media, not as like-edges. Document; no
  per-liker collection task.
  *Accept:* engagement counts surfaced on entity/media; gap documented.

## PHASE 5 — Edge & Relationship Intelligence (deepen the graph)
Many of these already exist as `identity_signals`/`entity_relationships` but are
under-surfaced or unweighted. Goal: turn them into ranked, explainable edges.
(Existing: `group_cooccurrence` 18,105 · `temporal_copost` 308 ·
`content_similarity` 191 · `shared_life_context` 374 · `temporal_hour_similarity`
644 · `social_graph_overlap` 2,197 · `bio_mention` 4 · `shared_website` 23.)

- [ ] **T5.1 Co-presence in time (tight window).** Extend `temporal_correlation.py`:
  two entities repeatedly active within N seconds → strong `co_presence` edge
  (distinct from hourly `temporal_hour_similarity`). Weight by frequency + tightness.
  *Accept:* `co_presence` edges with sub-minute evidence.
- [ ] **T5.2 Shared route origin → strong edge.** `route_similarity.py` /
  `shared_route_origin` signal currently ~0 rows. Compute Strava route
  start-coordinate clustering across entities → `shared_home_or_gym` edge (very
  strong tie). *Accept:* edges where two entities share a start locus.
- [ ] **T5.3 Reply/mention chain depth & reciprocity.** From `entity_interactions`
  (T2), weight edges by back-and-forth depth + reciprocity ratio + recency, not
  raw count. *Accept:* edge weight reflects conversation depth, not volume.
- [ ] **T5.4 Group-size-weighted co-membership.** Re-weight
  `{telegram,whatsapp}_group_co_member` (96k rows) inversely by group size — 5
  shared small groups ≫ 1 giant broadcast. Add participant_count join.
  *Accept:* small-group co-members rank above big-group ones.
- [ ] **T5.5 Content-fingerprint reuse → coordination edge.** Surface
  `content_similarity` (191) + same image sha256 / identical caption / shared
  link across entities as `content_reuse` edges (coordination or same-person).
  *Accept:* content-reuse edges present + explainable.
- [ ] **T5.6 Bio/link cross-refs → self-declared edge.** Surface `shared_website`
  / `cross_platform_link` / `bio_mention` as explicit `self_declared_link` edges
  in the graph (strong, human-authored). *Accept:* bio/link edges shown.
- [ ] **T5.7 Style/emoji/language fingerprint → soft same-person.** Per-entity
  writing-style vector (emoji freq, n-gram, avg length, language) → similarity
  edge as a soft same-person / affinity signal. *Accept:* style-similarity edges.
- [ ] **T5.8 Silence correlation.** Entities whose active/quiet windows move
  together (same travel/timezone shifts) → `co_absence` edge. *Accept:* edges
  from correlated silence.
- [ ] **T5.9 Bridge/centrality scoring.** `graph_analytics.py` — compute
  betweenness/degree; flag entities bridging separate clusters as key targets;
  expose on entity page + `/graph/overview`. *Accept:* centrality scores stored +
  surfaced; bridges highlighted.
- [ ] **T5.10 Explainable edge weights.** Every edge carries a human-readable
  "why" (which signals + counts) in `sources` jsonb, shown on hover.
  *Accept:* hovering any edge explains its basis.

## Cross-cutting
- [ ] **CC1 Sparse-entity UX.** Every entity (sparse→rich) renders gracefully;
  empty panels show "no data" not errors. *Accept:* a 1-event entity page is clean.
- [ ] **CC2 Performance at scale.** Timeline/geo/interaction endpoints paginate +
  window; no full-table scans per entity (indexes on `(actor,occurred_at)` etc).
  *Accept:* rich-entity page < 2s.
- [ ] **CC3 Backfill after each phase.** Run full resolution + timeline rebuild;
  verify counts move. *Accept:* per-phase before/after metrics recorded here.

## Definition of done
All boxes checked, a rich AND a sparse entity both render a fused
physical+digital timeline with reciprocal directed interactions and ranked
explainable edges. Then — and only then — delete this file.
