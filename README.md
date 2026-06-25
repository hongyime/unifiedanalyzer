# UnifiedAnalyzer

Personal OSINT analysis engine. Reads the multi-platform firehose collected by
**`unifiedcollector`** and turns it into **resolved identities and
intelligence**: it decides which accounts across every platform belong to the
same real person (an `entity`), builds a unified cross-platform timeline,
profiles behavior/location, indexes faces, and raises alerts.

> 📖 **Full system writeup:** [`docs/analyzer_overview.md`](docs/analyzer_overview.md)
> — architecture, the ~17-phase pipeline, the identity-signal fusion model, a
> live-state snapshot, and a candid gaps/improvements list. Read that for depth;
> this README is the entry point.

## What it does (at a glance)

- **Entity resolution** — clusters platform accounts (`social_users`, profiles)
  into unified `entities` via fuzzy username/name match, shared emails/phones,
  cross-platform links, and more.
- **Identity-signal fusion** — every analyzer phase emits typed weak
  `identity_signals`; `identity_scorer` fuses them into a single "same person"
  probability per entity pair (probabilistic OR).
- **Unified timeline** — normalizes dated items from every platform into
  `timeline_events` (millions of rows).
- **Behavioral / location / content analysis** — active-hours + timezone
  profiles, geo inference, cross-platform stylometry, contact extraction,
  Strava home-base + group co-occurrence graphs.
- **Phase-6 media analysis** — per-media EXIF / OCR / pHash / PDF-text /
  video-frame extraction + **InsightFace** face detection → `media_analysis`.
- **Face engine** — `face_worker` indexes faces from collector media **and from
  the W:/X:/Y:/Z: drives** (see below) into the `facetracker` schema.
- **Alerts** — silence-gap, new-activity-after-silence, coordinated-posting,
  profile-change → `alerts` + Telegram notifications.

## Architecture

Runs **dockerized** via [`docker/docker-compose.yml`](docker/docker-compose.yml)
as **three** long-running processes (image `unifiedanalyzer:latest`, all
`restart: unless-stopped`), sharing the `unifiedcollector_postgres` server (DBs
`unifiedanalyzer` + `unifiedcollector`) on the external
`unifiedcollector_default` network:

| Service | Command | Role |
|---|---|---|
| **analyzer** | `python -m src.main serve` | FastAPI API + bundled React dashboard on **port 8002** (`RUN_SCHEDULER=0`) |
| **scheduler** | `python -m src.main scheduler` | The analysis pipeline loop (incremental ~2h + full ~daily). Separate process so its blocking cv2/ffmpeg/pdf/ONNX work never freezes the API. |
| **face_worker** | `python -m src.face_worker loop` | InsightFace over collector media + drive scanning → `facetracker.images/faces` + `public.entity_faces` |

Derived artifacts (face crops, FAISS, ONNX models, PDF/video frames) live on
**Z:** (`Z:/unifiedanalyzer/media_derived`); collector source media is read-only
from `Z:/unifiedcollector/media`. Nothing growing is written to the
space-constrained C: drive.

## Run it (Docker)

```bash
cp .env.example .env        # set ANALYZER/COLLECTOR DB URLs; SMB_* for drive scan
# Build + start all three services (the .env supplies CIFS creds for W:/X:):
docker compose -f docker/docker-compose.yml --env-file .env up -d --build
# Dashboard + API:  http://127.0.0.1:8002
```

The schema is applied idempotently on startup. To force a one-off:
`docker exec docker-analyzer-1 python -m src.main schema`.

### Host / dev commands (`python -m src.main …`)

| Command | Description |
|---|---|
| `serve` | FastAPI server (API only; scheduler is a separate process) |
| `scheduler` | Run the analysis scheduler loop |
| `run` | One incremental analysis cycle |
| `full` | Full identity re-resolution |
| `schema` | Apply database schema (idempotent) |

Face worker (`python -m src.face_worker …`): `loop` (continuous), `ingest [N]`
(one collector batch), `scan [N]` (one drive-scan batch), or no-arg (schema only).

## Drive face-scanning (W / X / Y / Z)

`face_worker.ingest_drive_media()` walks `DRIVE_SOURCES` and runs the same
InsightFace detect→index flow as collector media. Drives are mounted into the
container:

- **Y: / Z:** — local, bind-mounted read-only at `/mnt/y`, `/mnt/z`.
- **W: / X:** — SMB shares on the Tailscale host `Prawn-E14`, mounted as **CIFS
  volumes** (`docker_wdrive` / `docker_xdrive`). Credentials come from the
  gitignored `.env` (`SMB_HOST`, `SMB_USER`, `SMB_PASS`).

⚠️ **C: is deliberately excluded** from `DRIVE_SOURCES` — reading OneDrive
placeholders hydrates them and fills the disk. Never add it. Excluded subtrees
(analyzer's own derived dir, collector media, recycle bins) are set via
`EXCLUDE_PATHS` in the compose file.

## Frontend dev

```bash
cd frontend && npm install && npm run dev
```

The production build is bundled into the image (`docker/Dockerfile.dashboard`)
and served by the `analyzer` service.

## Docs

- [`docs/analyzer_overview.md`](docs/analyzer_overview.md) — **full system runthrough** (start here).
- [`docs/media_analysis_plan.md`](docs/media_analysis_plan.md) — Phase-6 media analysis design.
- [`docs/storage_drive_plan.md`](docs/storage_drive_plan.md) — storage layout (derived artifacts on Z:).
- [`docs/facetracker_merge_plan.md`](docs/facetracker_merge_plan.md) — historical: how the facetracker face engine was merged in (the standalone facetracker stack is now retired/wiped).
