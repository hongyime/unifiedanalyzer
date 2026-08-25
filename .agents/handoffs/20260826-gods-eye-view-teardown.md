# Competitive Teardown: gods-eye-view vs UnifiedAnalyzer

Generated: 2026-08-26 (SGT) · Scope: product/technical comparison + upgrade backlog.
Research basis: github.com/bilawalsidhu/gods-eye-view README + repo structure (fetched live).
Note: the planned deep web-research subagent pass was blocked by harness task timeouts;
market-gap section is an inline expert synthesis and is marked as such.

## 1. What gods-eye-view (GEV) is

A browser-based "spy satellite simulator": photorealistic 3D globe (Google
Photorealistic 3D Tiles + CesiumJS + Vite, vanilla JS, no framework) rendering
13 mostly-keyless live layers: flights (OpenSky/adsb.lol), military traffic,
vessels (AISStream), satellites (CelesTrak SGP4), earthquakes (USGS), traffic,
~800 public CCTV cameras projected into 3D, world radio, bikeshare, NASA fires,
space launches, OSM installations, bundled infra datasets (4,351 datacenters,
704 dams, 712 submarine cables). OpenAI Realtime voice agent with 28 tools
(camera direction, voice annotations, entity Q&A, visual grounding via
viewport screenshots). Cockpit mode, GLSL sensor reskins (NVG/FLIR/CRT),
detection-overlay HUD, cinematic scene director, shareable state URLs.

Maturity signals: ~1.4k stars / 270 forks but only **4 commits**; viral YouTube
marketing (5M+ views); client-only architecture; server exists solely as a
key-brokering proxy.

What GEV has **none** of: persistence/database, identity resolution, authenticated
social collection, historical timelines, face/media forensics, alerting,
export pipelines, production readiness gates.

## 2. Feature matrix

| Dimension | UnifiedAnalyzer + Collector | gods-eye-view | Edge |
|---|---|---|---|
| Collection model | Authenticated browser + backend collectors, 11+ sources incl. private surfaces | Public telemetry polling, anonymous | **UA** |
| Identity resolution | Cross-platform entity clustering + probabilistic signal fusion | None | **UA** |
| Persistence & history | Postgres timelines (millions), media, faces, audit logs | Ephemeral client state | **UA** |
| Temporal analysis | Behavioral profiles, silence-gap/coordinated-posting alerts | Live-only interpolation | **UA** |
| Media/face forensics | EXIF/OCR/pHash/video frames + InsightFace incl. drive scan | None | **UA** |
| Privacy posture | Compact redacted indicator export, RLS, raw_mirror=false | N/A (public data) | **UA** |
| Ops robustness | Readiness gates, action queue, cookie vault, deadline-bounded APIs | Demo-grade | **UA** |
| Geospatial visualization | Geo inference computed; **no map/globe UI** | Photorealistic 3D globe, best-in-class feel | **GEV** |
| Real-time breadth | Deep per-subject | Whole-planet simultaneity | **GEV** |
| AI interaction | Pipeline-internal (resolution/scoring) | Voice agent, scene Q&A, visual grounding | **GEV** |
| UX wow-factor | Analyst dashboards | Cinematic cockpit/HUD | **GEV** |
| Distribution story | Personal power-tool | Viral open-source demo | **GEV** |

Verdict: complementary, not head-to-head. GEV = real-time *presentation* of
public geospatial telemetry. UA = persistent *person-centric* intelligence.
The teardown value is importing GEV's presentation/AI ideas onto UA's depth.

## 3. SWOT (UA vs GEV threat frame)

- **S**: moat = years of hard collection/auth/persistence work GEV cannot fake;
  privacy-bounded export already production-gated.
- **W**: intelligence trapped in JSON/dashboard rows; geo assets invisible;
  interaction model lags 2026 UX expectations.
- **O**: UA's geo inference + timeline + entities are exactly the data a globe
  layer wants; readiness/data-quality machinery can drive "honest freshness"
  badges GEV already normalizes (live/modeled/unavailable labeling).
- **T**: GEV-style UX becomes the expectation for OSINT tooling; if UA stays
  table-shaped it looks legacy next to free demos. Low lock-in risk today
  (GEV persists nothing) but mindshare compounds.

Positioning map (temporal-depth × spatial-breadth): GEV = broad-shallow-now;
UA = deep-narrow-over-time. Target: deep-broad — person-centric history ON a
spatial canvas.

## 4. Upgrade backlog (ranked by value-per-effort)

1. **P0 — Globe/timeline layer for existing entities.** CesiumJS or deck.gl
   page in the existing React frontend fed by geo-inferred entities +
   `timeline_events` playback (scrub hours→days). No new collection needed;
   pure visualization over data already owned.
2. **P1 — Honest freshness badges.** Reuse `/api/production/readiness` +
   data-quality ledger to badge every dashboard source GEV-style
   (live/stale/export-gap/unavailable). Zero new compute; huge trust win.
3. **P1 — NL query over entities/timeline.** Tool-calling LLM onto existing
   APIs (`/api/entities`, `/api/review/candidates`, cases) — GEV proves the
   pattern; UA's schemas are far more query-worthy than flight feeds.
4. **P2 — Shareable investigation links.** Serialize case/filter/entity view
   into URLs (GEV's share-link mechanic applied to analyst workflow).
5. **P2 — Budget-governed proxy pattern.** Adopt GEV's credit-governor idea
   for metered external calls (YouTube quota, Telegram flood windows).
6. **P3 — Ops-console sensor theming.** Cosmetic HUD/NVG-style skins for the
   monitoring surface; cheap morale, zero risk.

## 5. Market capability gaps (inline synthesis — flagged lower-confidence)

Versus commercial OSINT suites (Maltego, ShadowDragon, Skopenow class) the
highest-value absences in UA today:

1. **Graph/link-analysis UI** — entity_relationships exist in DB; no
   force-graph exploration surface. (Highest analyst value.)
2. **Report/dossier generation** — case export exists (json/csv); add
   rendered PDF/HTML dossier with provenance citations.
3. **Playbook automation** — scheduled investigation recipes (watch-entity,
   periodic re-profile) on top of the existing scheduler.
4. **Collaboration/annotation layer** — single-operator today; case comments
   + shared annotations future-proof multi-operator.
5. **Disco/deception signals** — coordinated-posting alert exists; add
   bot-likeness + narrative-cluster features to identity signals.

(Deep web-research verification recommended when the agent-harness timeout
issue is resolved; treat rankings as directional.)

## 6. Dispositions from this run's audits (A2 inline triage)

| Finding | Disposition |
|---|---|
| Readiness unbounded recovery chains (55.6s live, 60s client death) | FIXED `4a59b9c` global deadline |
| Env-leaky collector health tests (persisted-cache leak) | FIXED collector `ec465d51` |
| Dashboard 500 escapes under DB load (/domain-pacing/status, /instagram/health) | FIXED collector `3bab003a` |
| False-red: stale terminal-degraded maintenance pass | FIXED `fff9601` |
| Facebook capture stall (stored 0, 166 unresolved) | Transient window; cleared live (stored60=176) with evidence |
| IG 429 permalink churn | FIXED cooldown guard (collector `3bab003a`) |
| X failedScript/uc_recover shell churn | FIXEED canonicality+escalation (same commit) |
| Suppressed stalled-warnings opacity | covered_warning_notes shipped |
| DQ ledger gaps: telegram/whatsapp/exposure export_gap (non-exportable families), website no-derived-evidence | TICKET: export-policy decision + website→analyzer pipeline phase |
| Supabase remote rows (6284) ≥ local exported — verify idempotency drift | TICKET: reconciliation check |
| WhatsApp bridge-1 pairing; face_worker zombie post-Docker-restart | Operator actions carried in handoff |

Verification at close: analyzer focused suites 88 passed; collector affected
suites 119+72 passed; clean isolation readiness ok/crit=0/22.4s; zero
Tracebacks across analyzer/scheduler/dashboard/watchdog logs; scraping proven
(facebook stored60=176, instagram 288, threads 127, x 12); Supabase drained.
