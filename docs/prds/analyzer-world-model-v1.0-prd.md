# Analyzer World Model - Product Requirements Document (PRD)

## Requirements Description

### Background
- **Business Problem**: UnifiedAnalyzer should turn the collector vault into a human-auditable world model of people, accounts, relationships, locations, timelines, media, and evidence. It must avoid unsafe automatic merges while preserving user decisions in a durable form.
- **Target Users**: The project owner, future investigation workflows, future LLM/data tools, and maintenance agents.
- **Value Proposition**: The analyzer becomes a person-first OSINT workbench where claims are explainable, correctable, restorable, and useful for both digital and physical-world views.

### Feature Overview
- **Core Features**:
  - Treat the collector vault plus analyzer decision log as the recoverable source of truth.
  - Store all human corrections in analyzer DB and append-only JSONL under `Z:\unifiedanalyzer`.
  - Keep derived scores, clusters, maps, timelines, and relationship views rebuildable.
  - Use confidence bands from `0-100` and `X` for rejected/dismissed claims.
  - Never auto-merge people. Analyzer may auto-rank and aggressively promote collection priority hints for `95-99` evidence.
  - Separate identity evidence, relationship evidence, location evidence, and context evidence.
  - Make the primary UX person-first: open person, then timeline, map, relationships, media, evidence, and decisions.
  - Make maps and physical-world views evidence-backed: every pin, route, co-presence, and inferred location must show source and confidence.
- **Feature Boundaries**:
  - Analyzer does not scrape platforms and does not write back to source platforms.
  - Analyzer does not own raw media durability; collector vault owns source artifacts.
  - Analyzer can generate priority hints for collector, but collector owns per-platform collection scheduling.
  - Temporal posting overlap, shared topics, broad social graph overlap, and shared groups are context/relationship signals unless corroborated by identity evidence.
- **User Scenarios**:
  - A Telegram contact is Tier 1 and a probable Instagram account is found through discovery. At `95-99`, analyzer can ask collector to bump collection priority for that Instagram account, but the analyzer still asks before merging.
  - A wrong identity candidate is dismissed. It becomes `X`, is written to decision log, and does not resurface unless stronger new evidence appears.
  - A person page shows where they were at a time from GPS, photo EXIF, Strava route, tagged media, or inferred location, with confidence and source.
  - A full analyzer DB rebuild restores merges, dismissals, relationship confirmations, target tiers, notes, media-owner corrections, and location corrections from decision logs.

### Detailed Requirements
- **Input/Output**:
  - Inputs: collector DB, collector vault sidecars/raw payloads, analyzer DB, face/embedding outputs, user decisions.
  - Outputs: entities, candidate identity links, timelines, maps, relationships, alerts, priority hints to collector, decision logs, DB backups.
- **User Interaction**:
  - Primary landing model is person-first.
  - Person view must expose tabs or sections for overview, timeline, map, relationships, media/faces, accounts, evidence, notes, and decision history.
  - Review actions must be explicit: merge, split, dismiss match, confirm/reject relationship, confirm/reject location, mark media owner/person-in-photo, assign target tier, add note/source confidence.
  - Dashboard wording must distinguish confirmed fact, probable identity, weak candidate, relationship, and context.
- **Data Requirements**:
  - Canonical analyzer roots:
    - `Z:\unifiedanalyzer\decisions`
    - `Z:\unifiedanalyzer\exports`
    - `Z:\unifiedanalyzer\backups\db`
    - `Z:\unifiedanalyzer\media_derived`
  - Every human correction writes an analyzer DB row and an append-only JSONL event.
  - Decision events must include stable references when available:
    - platform/source IDs
    - account handles
    - collector source record IDs
    - media hashes and sidecar paths
    - phone/email hashes, not necessarily raw private values
    - route/activity IDs
    - old/new entity IDs
    - evidence snapshot and score at decision time
    - actor, timestamp, decision type, note
  - Decision event types:
    - `merge_confirmed`
    - `split_person`
    - `dismiss_identity_candidate`
    - `confirm_relationship`
    - `reject_relationship`
    - `confirm_location`
    - `reject_location`
    - `assign_media_owner`
    - `reject_media_owner`
    - `assign_person_in_photo`
    - `reject_person_in_photo`
    - `assign_target_tier`
    - `add_note`
    - `adjust_source_confidence`
  - Confidence bands:
    - `100`: confirmed by user or deterministic self-link.
    - `95-99`: very strong; can promote collection priority hints, but not auto-merge.
    - `70-94`: probable; reviewable.
    - `55-69`: weak candidate; shown only in review.
    - `<55`: weak signal/context; not surfaced as merge candidate.
    - `X`: rejected/dismissed; never resurface unless stronger new evidence appears.
- **Edge Cases**:
  - If entity IDs change during rebuild, decision replay must resolve against stable platform/media/source references.
  - If a decision cannot be replayed, it must become an unresolved decision requiring user review, not silently dropped.
  - If a person is split, downstream relationships, notes, face links, and location labels must be reassigned or marked ambiguous.
  - If a relationship is confirmed but identity remains unmerged, preserve that distinction.
  - If a location inference is rejected, keep the raw evidence but suppress that inferred claim.

## Design Decisions

### Technical Approach
- **Architecture Choice**: UnifiedAnalyzer is a person-first world model and decision system. It computes derived views from collector evidence, but durable human intent lives in an append-only decision log plus analyzer DB tables.
- **Key Components**:
  - Decision event writer that writes DB row and JSONL event.
  - Decision replay/import tool for rebuilds.
  - Stable reference resolver for platform IDs, media hashes, sidecar paths, routes, and contact hashes.
  - Identity candidate scorer with no auto-merge behavior.
  - Evidence taxonomy registry: identity, relationship, location, context.
  - Person-first dashboard and review actions.
  - Collection priority hint exporter to collector.
  - Analyzer DB backup scheduler.
  - Restore/rebuild validation report.
- **Data Storage**:
  - Decisions: append-only JSONL under `Z:\unifiedanalyzer\decisions`, plain text, never committed to git.
  - DB backups: `Z:\unifiedanalyzer\backups\db` with 7 daily, 4 weekly, 3 monthly retention; hourly lightweight exports if feasible.
  - Derived media: `Z:\unifiedanalyzer\media_derived`.
  - Scores/clusters/timelines/maps are rebuildable outputs, not the only durable state.
- **Interface Design**:
  - Analyzer reads collector DB and collector vault.
  - Analyzer emits collection priority hints for collector with evidence, confidence, and source entity/person.
  - Collector may aggressively consume `95-99` hints for collection priority, while analyzer still requires human approval for merging.

### Constraints
- **Performance Requirements**:
  - Person page must load primary identity, accounts, relationships, and latest timeline without scanning the whole corpus.
  - Map/timeline views should use indexed entity/time/location tables.
  - Decision replay should be batchable and resumable.
- **Compatibility**:
  - Existing analyzer DB remains the live operational store.
  - Existing collector DB and vault remain read-only from analyzer perspective.
  - Existing face and media-derived paths under `Z:\unifiedanalyzer\media_derived` remain valid.
- **Security**:
  - Plain local JSONL decision logs are acceptable.
  - Decision logs and DB dumps must be excluded from git.
  - Analyzer does not perform source-platform writes.
- **Scalability**:
  - Identity candidate generation must stay conservative as entity count grows.
  - Map and timeline queries need entity/time partitioning or indexes as data grows.

### Risk Assessment
- **Technical Risks**:
  - Decision logs that only reference volatile entity IDs will not replay after rebuild. Mitigation: stable references and evidence snapshots.
  - Auto-promoting collection priority from `95-99` evidence may collect a wrong account more aggressively. Mitigation: label as priority hint, not merge confirmation.
  - Splits are harder than merges. Mitigation: implement split audit events before broad merge workflow expansion.
  - Weak context can be mistaken for identity. Mitigation: evidence taxonomy and UI wording.
- **Dependency Risks**:
  - Collector sidecars may be missing for old media. Mitigation: replay best-effort, report unrecoverable references.
  - Face clusters and embeddings are derived and may change. Mitigation: decisions reference media hashes and face IDs where possible.
- **Schedule Risks**:
  - Full UI redesign can consume time before safety improves. Mitigation: harden decisions and backups before UI polish.

## Acceptance Criteria

### Functional Acceptance
- [x] Every human review action writes both an analyzer DB record and an append-only JSONL event under `Z:\unifiedanalyzer\decisions`.
- [x] Decision events include stable references and evidence snapshots sufficient for replay or unresolved-review reporting.
- [x] Identity review uses `0-100` confidence and `X` rejected state.
- [x] Analyzer never auto-merges people except exact same-platform duplicate cleanup where explicitly approved by code policy.
- [x] `95-99` identity candidates can emit collector priority hints with provenance.
- [x] Temporal posting overlap and similar context do not contribute to same-person probability.
- [x] Person-first page shows accounts, confidence, evidence, timeline, map, relationships, media/faces, notes, and decision history.
- [x] Confirm/reject actions exist for identity, relationship, location, media owner, person-in-photo, target tier, and notes.
- [x] Analyzer DB backups land under `Z:\unifiedanalyzer\backups\db`.
- [x] A replay dry run reports restored decisions, unresolved decisions, and derived tables needing rebuild.

### Quality Standards
- [x] Decision writer has tests for DB success/JSONL failure, JSONL success/DB failure, retry, and idempotency.
- [x] Decision replay has tests for stable entity match, missing entity, legacy events, dismissed candidate, and unresolved references.
- [x] Scorer tests enforce no auto-merge and no context-only evidence in identity scoring.
- [x] UI labels distinguish identity evidence, relationship evidence, location evidence, and context.

### User Acceptance
- [ ] The user can answer: who is this person, why do we think so, what is confirmed, what is rejected, where were they, and who are they connected to.
- [ ] A DB wipe does not lose human review decisions after replay from JSONL.
- [x] Priority propagation to collector is explainable and does not silently merge identities.

## Execution Phases

### Phase 1: Decision Log Foundation
**Goal**: Make human corrections durable before expanding workflows.
- [x] Add analyzer decision-log config for `Z:\unifiedanalyzer\decisions`.
- [x] Add `.gitignore` coverage for decisions, exports, backups, and generated restore artifacts.
- [x] Define decision event JSON schema.
- [x] Build append-only decision writer with DB and JSONL writes.
- [x] Add idempotency keys for decision events.
- [x] Backfill event writer into existing merge/dismiss actions first.
- **Deliverables**: Decision event schema, writer, tests, first wired actions.
- **Time**: 2-4 days.

### Phase 2: Review Actions and Replay
**Goal**: Support corrections that can be restored after rebuild.
- [x] Wire decision events for merge, split, dismiss, relationship confirm/reject, location confirm/reject, media owner/person-in-photo, target tier, notes.
- [x] Add stable reference snapshots to every event.
- [x] Build replay dry-run command.
- [x] Build replay apply command guarded by backup requirement.
- [x] Add unresolved-decision report.
- **Deliverables**: Full decision action coverage, replay tooling, unresolved report.
- **Time**: 4-7 days.

### Phase 3: Identity and Priority Policy
**Goal**: Lock in conservative identity behavior while helping collector prioritize.
- [x] Enforce confidence bands in API and frontend labels.
- [x] Ensure no auto-merge path exists for cross-platform candidates.
- [x] Add `X` rejected state and stronger-new-evidence resurfacing rule.
- [x] Implement collector priority hint export for `95-99` candidates and confirmed people.
- [x] Track hint provenance and confidence.
- [x] Add tests that context-only evidence cannot create same-person probability.
- **Deliverables**: Confidence policy, rejected state, priority hint exporter, tests.
- **Time**: 3-5 days.

### Phase 4: Person-First Dashboard
**Goal**: Make the analyzer usable as the main investigation surface.
- [x] Redesign person overview with accounts, tiers, confidence, latest activity, and evidence summary.
- [x] Add timeline tab with filters by source, event type, confidence, and date.
- [ ] Add map tab with GPS/photo/route/location inference layers and evidence drawers.
- [x] Add relationships tab with why/how evidence and confirm/reject controls.
- [x] Add media/faces tab with owner/person-in-photo correction actions.
- [x] Add decision history and notes.
- **Deliverables**: Person-first UX, review controls, evidence drawers.
- **Time**: 5-10 days.

### Phase 5: Physical World Model
**Goal**: Turn GPS/routes/media into evidence-backed location and co-presence views.
- [ ] Normalize location evidence types: GPS, EXIF, route polyline, venue tag, caption-derived, inferred.
- [ ] Store confidence and source for each location claim.
- [ ] Link photos to where/when/who when evidence supports it.
- [ ] Build person-location timeline and map pins.
- [ ] Build group co-presence claims only when evidence supports same place/time.
- [ ] Add reject/suppress workflow for bad inferences.
- **Deliverables**: Location evidence registry, map layers, co-presence claims.
- **Time**: 5-10 days.

### Phase 6: Backups and Recovery Drills
**Goal**: Prove analyzer can recover from DB loss.
- [x] Add scheduled analyzer DB dumps to `Z:\unifiedanalyzer\backups\db`.
- [x] Implement retention: 7 daily, 4 weekly, 3 monthly.
- [x] Run restore into scratch DB.
- [x] Replay decision logs into scratch DB.
- [ ] Recompute derived scores/clusters/timelines.
- [x] Produce recovery gap report.
- **Deliverables**: Backup job, replay drill, recovery report.
- **Time**: 3-5 days.

---

## Implementation Status - 2026-07-24

- Decision JSONL outbox retry is implemented in the scheduler and surfaced in `/api/health`.
- Person pages now include a read-only Decisions tab backed by `/api/entities/{entity_id}/decisions`, showing audit actions, involved entities, payload detail, and JSONL durability.
- Connections now expose relationship evidence with one-click confirm/reject controls backed by `/api/entities/relationship-decision`; decisions write to audit DB rows and append-only JSONL.
- Person pages now include a Media/Faces tab backed by `/api/entities/{entity_id}/media-faces`, with account-linked media, face links, and owner/person-in-photo confirm/reject actions wired to durable media decision events.
- Timeline now supports source, event-type, confidence, and date filters. Confidence is derived from semantic metadata when present, with `entity_platform_links.confidence` as a source-attribution fallback; unknown rows remain visible as unscored unless a confidence floor is selected.
- Map geo payloads now classify route polylines, GPS starts, venue tags, venue geocodes, and message locations with confidence and source references; the map drawer shows those details before confirm/reject actions. A normalized durable location-evidence table is still pending.
- Platform links now show link confidence and expose a source-confidence adjustment menu backed by `/api/entities/{entity_id}/source-confidence`.
- Person pages now open on an Overview tab that summarizes confidence, account spread, identity evidence, latest activity, mapped evidence counts, relationship leads, and decision count before the deeper tabs.
- Replay apply safely restores audit rows, dismissed identity candidates, same-person relationship confirm/reject labels, target tiers, notes, and source-confidence adjustments. Merge/split, location decisions, and media/person-in-photo decisions still require derived-table rebuilds or future normalized destination tables.
- Live decision replay dry-run on 2026-07-23 scanned 42 legacy JSONL decisions: `Invalid=0`, `Unresolved=42`. These were old `dismiss_match`/`merge_entities` events with empty payloads and no stable platform refs, so replay now reports them correctly instead of treating them as schema-invalid.
- Scheduler now mounts `Z:\unifiedanalyzer\decisions` at `/app/decisions`, so scheduler-side recovery drills and replay tools read the same durable JSONL as the API.
- Live recovery drill on 2026-07-24 restored `/app/backups/db/daily/unifiedanalyzer_daily_20260723T074929Z.dump` into scratch DB `ua_restore_drill_20260724_011451` in `2292.021s`, then dropped the scratch DB. Restored table counts included `entities=8849`, `entity_platform_links=9648`, `identity_labels=75`, `audit_log=42`, `analysis_runs=358`, and `timeline_events=9443756`.
- The drill intentionally skipped derived restore items `TABLE DATA public timeline_embeddings` and `idx_timeline_emb_hnsw`; those are rebuildable artifacts, not source-of-truth decision rows.
- The same drill replayed `/app/decisions` and scanned 42 legacy JSONL decisions: `Invalid=0`, `Ambiguous=0`, `Unresolved=42`, `Restorable=0`. The restored backup already contained the 42 audit rows, but JSONL effects were skipped because the legacy events do not include stable platform references. The gap report lists derived rebuild needs for `identity_scores=42`, `review_candidates=28`, `entity_graph=14`, and `timeline_events=14`.
- The remaining recovery milestone is proving replay with new-schema decisions that include stable references, then running a derived-table recompute pass after restore.

**Document Version**: 1.0
**Created**: 2026-07-20
**Clarification Rounds**: 5
**Quality Score**: 99/100
