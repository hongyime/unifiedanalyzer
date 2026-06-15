# facetracker → unifiedanalyzer: Full-Merge Plan

**Status:** DRAFT for review. No code moves until the "Open decisions" below are
signed off.

**Goal (as decided):** *Full code merge* — fold facetracker's face-search engine
into this repo, *unify its data into the unifiedanalyzer Postgres database*, and
*replace* the analyzer's Phase 6 face code (YuNet + SFace) with facetracker's
engine. All growing artifacts on **Z:** (per [[storage-derived-on-z]]).

---

## 1. What we're merging

facetracker is ~8,650 LOC, well-modularized, and **substantially more capable**
than the analyzer's Phase 6 face code:

| | unifiedanalyzer (Phase 6) | facetracker |
|---|---|---|
| Detection | OpenCV **YuNet** | InsightFace (SCRFD) |
| Embedding | OpenCV **SFace → 128-dim** | InsightFace **ArcFace → 512-dim** |
| Vector store | `double precision[]` + O(n) numpy rebuild | **pgvector `Vector(512)` + FAISS IVFFlat** |
| Clustering | none | Chinese Whispers / HDBSCAN, identity mgmt, verification |
| Video | ffmpeg keyframes | **DeepSORT tracking** @ 3 fps |
| Scope | only collector `media_items` | full drive scan (C:/Z:/Y:) + OneDrive, RAW/HEIC |
| DB access | asyncpg, raw SQL, **UUID** PKs | SQLAlchemy ORM, **integer** PKs |
| DB | `unifiedanalyzer` (already has pgvector) | separate `facetracker` pg16 (port 5433) |
| Face storage | n/a (embeddings in DB) | **Y:/facetracker/faces** (crops, FAISS, thumbs) |
| API/UI | analyzer dashboard | own FastAPI + operations-dashboard (8700) |

facetracker modules: `discovery/` (scan/watch/onedrive/manifest), `engine/`
(detector/tracker/quality), `identity/` (clustering/verification/metrics),
`pipeline/` (processor/thumbnail), `readers/` (image/video/raw_heic), `search/`
(engine/multi_face/ranking), `storage/` (database/faiss_index/outbox/thumbnail_cache).

facetracker DB tables: `images`, `faces` (Vector(512) + bbox + quality + track),
`identities` (centroid Vector(512)), `face_identity_map`.

## 2. Critical incompatibilities & decisions (recommendations)

1. **Embedding spaces are incompatible (128-dim SFace vs 512-dim ArcFace).**
   → Standardize on facetracker's InsightFace/ArcFace. The analyzer's existing
   `media_analysis` face_embedding rows (sface-2021dec) are **discarded and
   re-embedded** with InsightFace. `media_face_match` signals get recomputed.

2. **ORM mismatch (asyncpg/raw + UUID vs SQLAlchemy/int).**
   → *Keep facetracker's SQLAlchemy storage layer intact* for its own tables
   (lowest risk, preserves FAISS/outbox/halfvec design). Run those tables inside
   the **unifiedanalyzer DB** (it already has `CREATE EXTENSION vector`). The
   analyzer's asyncpg code keeps using raw SQL on its own tables. Two access
   styles coexist against one database — acceptable and isolated.

3. **PK type mismatch (int vs UUID).**
   → Keep facetracker's integer PKs for `images/faces/identities`. Bridge to
   analyzer `entities` via a **new link table**
   `entity_faces(entity_id UUID, face_id INT, confidence, method)`. This is how
   face identities enrich resolved entities without rewriting either PK scheme.

4. **Namespace collisions.** facetracker `images`/`faces`/`identities` vs
   analyzer tables. → Put facetracker tables in a dedicated **`facetracker`
   Postgres schema** (`facetracker.faces`, …) inside the analyzer DB. Zero
   rename churn in facetracker code (set `search_path`/schema in its engine URL).

5. **Storage drives.** faces live on **Y:**, derived media on **Z:**.
   → Consolidate face storage onto **Z:** (`Z:/facetracker/faces`) to honor the
   "growing artifacts on Z:" rule, *or* keep Y: if it's a dedicated fast disk.
   **OPEN DECISION (see §6).**

6. **Two schedulers / watch loops.** facetracker has its own drive watcher +
   FAISS reaper; analyzer has its own scheduler. → Run facetracker's indexing
   workers as a **separate worker process** (own entrypoint) rather than cramming
   into the analyzer's asyncio scheduler — they have different concurrency models
   (SQLAlchemy sync + ONNX threads vs asyncio). One repo, two processes.

7. **Two FastAPI apps + two dashboards.** → Mount facetracker's API as a
   **sub-application** under the analyzer API (`/face/*`), and surface a Faces
   page in the analyzer dashboard that calls it. Retire facetracker's standalone
   operations-dashboard.

## 3. Target architecture (end state)

```
unifiedanalyzer/  (one repo, one DB, Z: storage)
├── src/
│   ├── api/            analyzer API + mounted /face sub-app
│   ├── pipeline/       Phase 6 (face code REMOVED, calls face engine)
│   ├── face/           ← vendored facetracker engine/storage/search/identity/...
│   └── face_worker.py  ← indexing + FAISS reaper process (was facetracker main.py)
├── frontend/           + new Faces / Identities page
└── docker/             analyzer service + face_worker service (compose)
DB unifiedanalyzer:
  public.entities / media_analysis / ...   (existing)
  facetracker.images / faces / identities / face_identity_map  (vendored)
  public.entity_faces  (bridge: entity ↔ face)
Storage: Z:/unifiedanalyzer/media_derived/  +  Z:/facetracker/faces/ (faiss, crops, thumbs)
```

## 4. Staged execution (each stage independently shippable + reversible)

**Stage 0 — Vendor code, no behavior change.**
Copy facetracker `src/{engine,storage,search,identity,readers,discovery,pipeline,
utils,config}` into `unifiedanalyzer/src/face/`. Merge requirements (adds
insightface, faiss-cpu, hdbscan, umap-learn, deep-sort-realtime, sqlalchemy,
pgvector, pillow-heif, imageio-ffmpeg). Point InsightFace model cache at Z:.
Nothing wired yet; analyzer unchanged. *Rollback: delete src/face.*

**Stage 1 — facetracker tables in the analyzer DB.**
Create `facetracker` schema in the analyzer DB; run `Base.metadata.create_all`
against it. Migrate existing facetracker data (pg_dump from :5433 → restore into
schema) **or** start fresh + re-scan. Stand up `face_worker.py` pointed at the
analyzer DB. *Rollback: drop schema `facetracker`.*

**Stage 2 — Bridge + ingest collector media.**
Add `public.entity_faces`. Feed collector `media_items` (images/video frames
already extracted on Z:) into the face engine so collector faces land in
`facetracker.faces`. Populate bridge by matching faces→entities (reuse the
analyzer's entity attribution for media). *Rollback: stop ingest, drop bridge.*

**Stage 3 — Replace Phase 6 face code.**
Remove `analyze_media_faces` / YuNet+SFace from `media_analysis_tier1.py` and the
sface `media_face_match` rebuild. Re-point any consumers at the bridge +
facetracker similarity. Backfill: re-embed all media faces with InsightFace.
Drop `media_analysis` face_embedding/face_detection rows. *Rollback: revert
commit; sface code is in git history.*

**Stage 4 — Unified API + UI.**
Mount face API under `/face`; add Faces/Identities page to the analyzer frontend;
retire facetracker's operations-dashboard. *Rollback: unmount.*

**Stage 5 — Single deployment + decommission.**
Add `face_worker` to the analyzer compose; remove facetracker's standalone
compose + `facetracker` Postgres container (data already migrated). Auto-start
via the analyzer stack. Compact reclaimed Docker space.

## 5. Risks & rollback
- **Re-embedding cost:** InsightFace over the whole media corpus is CPU-heavy —
  run as a bounded backfill (like Phase 6 batches). Old sface data stays until
  re-embed completes, so search degrades gracefully, not breaks.
- **SQLAlchemy + asyncpg in one process:** keep them in *separate* processes
  (analyzer API/scheduler vs face_worker) to avoid event-loop/threading clashes.
- **Data migration:** pg_dump/restore of facetracker into the new schema is the
  riskiest single step — take a backup of both DBs first; the facetracker
  standalone DB is kept until Stage 5 as a fallback.
- Every stage is a separate PR; the standalone facetracker keeps running until
  Stage 5, so there's always a working system.

## 6. Open decisions — need your call before Stage 0
- **D1 — Face storage drive:** move `Y:/facetracker/faces` → `Z:/facetracker/faces`
  (honors "growing → Z:"), or keep Y:? How big is Y: face storage today?
- **D2 — Existing facetracker data:** migrate it (pg_dump→restore) into the
  analyzer DB, or start clean and re-scan the drives? (Re-scan is simpler but
  re-does all indexing work.)
- **D3 — Scan scope:** keep facetracker's full C:/Z:/Y: drive scan, or restrict
  the merged system to collector media + a narrower set of folders?
- **D4 — Timeline:** OK to land this as ~5-6 sequential PRs over multiple
  sessions (vs one big bang)? Recommended: yes.
