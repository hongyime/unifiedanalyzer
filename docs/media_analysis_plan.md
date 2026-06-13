# Phase 6: Media Content Analysis Pipeline

Status: **PLANNED — not started**. No part of this exists yet. This doc is the
spec for the next agent (or future-me) to pick up.

## Why this exists

`unifiedcollector`'s `media_items` table has **120,355 rows** of collected
media (images, videos, PDFs, audio, profile photos) and **zero content
analysis** is performed on any of it. The only thing currently done with
media content is an exact-byte `profile_photo_sha256` match in
`src/pipeline/entity_resolver.py` (two entities with byte-identical profile
photos = same person, high confidence).

Everything else — text visible in screenshots, faces in photos, GPS embedded
in EXIF, text/images inside PDFs, near-duplicate (not byte-identical) images,
content inside videos — is currently invisible to the identity resolver.

## Current inventory (as of 2026-06-13, collector DB `media_items`)

| source    | content_type   | count  | total size |
|-----------|-----------------|--------|------------|
| lemon8    | image            | 28,523 | 1.1 GB |
| search    | image            | 25,580 | 6.4 GB |
| website   | image            | 23,541 | 5.5 GB |
| telegram  | photo            | 9,879  | 1.4 GB |
| beeper    | image            | 6,950  | 3.3 GB |
| youtube   | video            | 2,815  | 64.6 GB |
| search    | pdf              | 1,959  | 10.7 GB |
| strava    | activity_photo   | 1,633  | 0.4 GB |
| beeper    | video            | 1,605  | 1.2 GB |
| github    | profile_photo    | 1,435  | 0.1 GB |
| tiktok    | video            | 4,479  | 19.6 GB |
| tiktok    | post             | 4,202  | 1.7 GB |
| youtube   | thumbnail        | 4,738  | 0.6 GB |
| website   | pdf              | 616    | 1.4 GB |
| lemon8    | profile_photo    | 368    | 0.02 GB |
| whatsapp  | photo            | 608    | 0.07 GB |
| whatsapp  | sticker          | 122    | 0.01 GB |
| whatsapp  | video            | 68     | 0.66 GB |
| telegram  | profile_photo    | 89     | 0.01 GB |
| telegram  | document         | 37     | 0.02 GB |
| beeper    | file             | 73     | 0.4 GB |
| ...       | audio (various)  | ~13    | <0.1 GB |

Rough totals: **~95k images**, **~10k videos (~86 GB)**, **~2.6k PDFs (~12 GB)**,
small numbers of audio/documents/stickers.

`unifiedcollector/requirements.txt` only has `Pillow` — no OCR, vision, face,
or transcription libraries are installed anywhere in either repo.

## Design constraints

- **Non-fatal, try/except-wrapped pipeline steps** — every new step follows
  the pattern already used in `incremental_runner.py` (`try: await X() except
  Exception: logger.exception("X failed (non-fatal)")`).
- **"Ready to fire" / precision-first** — new signal types are added to
  `_TYPE_WEIGHT` in `src/pipeline/identity_scorer.py` even before they
  produce signals.
- **Fan-out filter** — any match (perceptual hash, face embedding, GPS
  cluster, extracted contact) shared by >2 entities is "public" and excluded.
- **Graceful offline** — file I/O against `COLLECTOR_DRIVE_PATH` must not
  crash the pipeline if the drive is unavailable; catch, skip, retry next run.
- **120k items cannot be processed in one incremental cycle.** Everything
  past Tier 0 is a **resumable batch job**: process N items per run, track via
  `media_analysis (media_item_id, analysis_type)` UNIQUE constraint as the
  "already done" cursor.
- **This machine is CPU-only with limited RAM** — this is the dominant
  constraint for this plan (see "Compute tiers" below). Anything that needs a
  GPU, a large model (>500MB), or would peg CPU for hours per cycle is
  explicitly deferred, not built-then-disabled.

## Compute tiers

Every phase is tagged with a tier. **Tiers 0 and 1 are the actual scope of
this rollout.** Tier 2 is documented for completeness but deliberately not
built yet — revisit only if Tier 0/1 prove valuable and either this machine
gets more headroom or the work is offloaded elsewhere.

- **Tier 0** — pure-Python / existing deps (Pillow), no ML models, runs in
  milliseconds per item. Safe to backfill the entire 120k-item backlog in one
  sitting.
- **Tier 1** — small CPU-only ML models (a few MB to ~50MB), tens of
  milliseconds to ~1s per item, batched/throttled across incremental cycles.
  May require one manual binary install (Tesseract, ffmpeg) — same pattern as
  the Tailscale firewall step: I give you the exact command, you run it once.
- **Tier 2** — large models (hundreds of MB to GB+), torch/transformers,
  multi-second-per-item, would peg this laptop for hours. **Deferred.**

## New table: `media_analysis` (analyzer DB)

One row per `(media_item_id, analysis_type)`. `media_item_id` references
`unifiedcollector.media_items.id` by value (cross-database, no FK — analyzer
already does this elsewhere). For **derived** media (an image extracted from
inside a PDF, a frame extracted from a video), `media_item_id` is a synthetic
string built from the parent: `"{parent_id}:pdf_img:{page}:{idx}"` or
`"{parent_id}:frame:{timestamp_sec}"`, with `parent_media_item_id` pointing
back at the real `media_items.id`.

```sql
CREATE TABLE media_analysis (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    media_item_id       text NOT NULL,
    parent_media_item_id text,           -- set for derived images/frames
    source              varchar NOT NULL,
    content_type        varchar NOT NULL,
    analysis_type       varchar NOT NULL,  -- see per-phase below
    extracted_text      text,
    result_json         jsonb,
    gps_lat             double precision,
    gps_lon             double precision,
    taken_at            timestamptz,
    perceptual_hash     varchar,
    face_embedding      double precision[],  -- Tier 1, 6F
    model_version       varchar,
    processed_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (media_item_id, analysis_type)
);
CREATE INDEX idx_media_analysis_type    ON media_analysis (analysis_type);
CREATE INDEX idx_media_analysis_phash   ON media_analysis (perceptual_hash);
CREATE INDEX idx_media_analysis_gps     ON media_analysis (gps_lat, gps_lon);
CREATE INDEX idx_media_analysis_parent  ON media_analysis (parent_media_item_id);
```

The `UNIQUE (media_item_id, analysis_type)` constraint doubles as the
"already processed" check for resumable batching.

New pipeline module: `src/pipeline/media_analysis.py`, one `async def
analyze_*()` per phase, all called (non-fatally) from `incremental_runner.py`
after `route_similarity`.

---

# TIER 0 — ship together, zero new system deps, full backfill OK

## 6A — EXIF GPS extraction (Pillow only)

- Target: `content_type IN ('image','photo','activity_photo')` not yet in
  `media_analysis` with `analysis_type='exif_gps'`.
- `PIL.ExifTags.GPSTAGS` → decimal lat/lon + `DateTimeOriginal`. Insert a row
  even when no GPS tag exists (nulls), so it's marked done and not retried.
- **Signal**: `media_gps_colocation` — entity pairs with GPS within ~100m and
  `taken_at` within a tight window (reuse `route_similarity.py`'s haversine +
  time-window helpers). `_TYPE_WEIGHT["media_gps_colocation"] = 0.40`.
- ~95k images × ~5-10ms ≈ 10-15 min one-time backfill.

## 6B — Perceptual hashing for near-duplicate images (`imagehash`)

- New dep: `imagehash>=4.3.1` (pure Python, built on Pillow).
- `imagehash.phash(img)` (64-bit) → `media_analysis.perceptual_hash`
  (`analysis_type='phash'`).
- Matching: Hamming distance ≤ 6, bucketed by top 16 bits to bound pairwise
  comparisons; same `content_type`/time-window scoping to keep it cheap.
- **Signal**: `media_perceptual_match` — same hash family across *different*
  `source` platforms for different entities. `_TYPE_WEIGHT["media_perceptual_match"] = 0.35`.
  Fan-out filter applies (hash shared by >2 entities = stock/meme image →
  excluded).
- ~95k images × ~10-20ms ≈ 15-30 min one-time backfill.

## 6C — PDF text extraction (`pypdf`)

- New dep: `pypdf>=4.0` (pure Python, no system binary).
- Target: `content_type='pdf'` (2,575 rows, ~12GB). Extract text per page →
  `media_analysis.extracted_text` (`analysis_type='pdf_text'`, capped 200KB).
- **No new signal type** — run existing `contact_extraction.py` regexes over
  the text and emit the existing `email_match` / `phone_match` /
  `cross_platform_link` with `source_table='media_items'`,
  `source_column='pdf_text'`.
- ~2,575 PDFs ≈ a few minutes one-time.

## 6C.2 — PDF embedded image extraction (NEW, `PyMuPDF`)

PDFs (especially `search` and `website` PDFs — often saved web pages/reports)
frequently embed photos, screenshots, scanned pages, or logos that 6C's text
extraction never sees.

- New dep: `PyMuPDF>=1.24` (`import fitz`; pure pip wheel, no system binary).
- For each PDF, `page.get_images()` + `doc.extract_image(xref)` → write each
  embedded image to `COLLECTOR_DRIVE_PATH/derived/pdf_images/` (new
  subdirectory analyzer creates/manages — does **not** touch collector's
  existing media tree) with synthetic id `"{pdf_id}:pdf_img:{page}:{idx}"`.
  Insert a `media_analysis` row (`analysis_type='pdf_embedded_image'`,
  `parent_media_item_id=pdf_id`).
- These derived images then flow through **the exact same Tier 0/1 pipeline**
  as any other image (6B perceptual hash immediately; 6D OCR and 6F face
  detection once Tier 1 is built) — no special-casing needed beyond the
  `parent_media_item_id` link.
- Skip trivial images (logos/icons) via a minimum-dimension filter (e.g.
  <100x100px) to avoid flooding the queue with favicons.
- ~2,575 PDFs, image extraction itself is fast (~0.1-0.5s/PDF); downstream
  cost is absorbed by 6B/6D/6F's existing batching.

---

# TIER 1 — small CPU models, batched, one manual binary install each

## 6D — OCR on images (`pytesseract` + Tesseract binary)

Screenshots (`search`, `website`, `telegram photo`, `lemon8 image`, plus
derived PDF images from 6C.2 and video frames from 6H) often contain visible
usernames, phone numbers, emails, handles.

- New dep: `pytesseract>=0.3.10`.
- **Manual step**: install Tesseract OCR for Windows
  (https://github.com/UB-Mannheim/tesseract/wiki), add `tesseract.exe` to
  `PATH` or set `TESSERACT_PATH` env var. Same shape as the firewall step —
  you run it once, I wire the config.
- Priority order: `search` images (25,580) → `website` images (23,541) →
  `telegram photo` (9,879) → PDF-embedded images (6C.2) → video frames (6H)
  → `lemon8 image` (28,523, lowest text density, do last).
- `pytesseract.image_to_string(img)` → `media_analysis.extracted_text`
  (`analysis_type='ocr_text'`). Same contact-regex reuse as 6C → existing
  `email_match`/`phone_match`/`cross_platform_link` with
  `source_column='ocr_text'`.
- Config: `MEDIA_OCR_ENABLED` (default `false` until Tesseract installed),
  `MEDIA_OCR_BATCH_SIZE` (default `200`/cycle).
- ~0.5-2s/image on CPU. Full ~95k backlog ≈ 13-50 CPU-hours → at 200
  images/cycle × 12 cycles/day ≈ **~40 days** to fully backfill at default
  batch size. New items get OCR'd within one cycle once steady-state is
  reached. Bump batch size or run an overnight `scripts/backfill_media_ocr.py`
  if faster coverage matters.

## 6F — Face detection + cross-platform face matching (NEW, `opencv-python` + `onnxruntime`)

**This is the highest-value new idea for an identity-resolution tool.** If
the same face appears in entity A's WhatsApp profile photo and entity B's
Telegram group photo, that's strong cross-platform identity evidence — even
when `profile_photo_sha256` doesn't match because the photos are different
crops/compressions of the same picture, or are different photos of the same
person entirely.

- New deps: `opencv-python>=4.9` (face *detection* — bundled small Caffe/ONNX
  SSD face detector, ~2.7MB, no system binary) + `onnxruntime>=1.17` (face
  *embedding* — small ArcFace-family ONNX model, ~5-15MB, CPU, no system
  binary, no torch). Both are plain `pip install`, no manual step.
- For each image (incl. profile photos, and derived images from 6C.2/6H):
  detect face bounding boxes via OpenCV DNN; for each face crop, compute a
  128/512-d embedding via the ONNX model → store in
  `media_analysis.face_embedding` (`analysis_type='face_embedding'`,
  `result_json` holds bbox + face index for multi-face images).
- Matching: cosine similarity ≥ threshold (e.g. 0.6, tune empirically) between
  embeddings belonging to *different entities* across *different platforms*.
- **Signal**: `media_face_match`. `_TYPE_WEIGHT["media_face_match"] = 0.50`
  (high — near-photographic identity evidence — but below email/phone 0.60 to
  account for look-alike false positives). Fan-out filter applies (a face
  embedding cluster spanning >2 entities → likely a group photo with multiple
  tagged people, a celebrity, or a meme template → excluded from pairwise
  matching, but still useful for the group-photo case if you later want a
  "co-appears in photos with" relationship — not in scope for v1).
- Priority: profile photos first (1,981 across github/lemon8/telegram/
  instagram — small, high-value, directly extends `profile_photo_sha256`),
  then `telegram photo`/`whatsapp photo`/`beeper image` (personal comms), then
  the large public-content buckets (`lemon8`, `search`, `website`) last.
- ~30-80ms/face on CPU. Profile photos (~2k) ≈ a few minutes. Personal-comms
  photos (~17k) ≈ 15-25 min. Defer the ~58k `lemon8`/`search`/`website` bulk
  to background batching like 6D.

## 6H — Sparse video keyframe extraction (NEW, `ffmpeg` binary)

"Video frame by frame" in full is Tier 2 (see below) — but **sparse keyframe
sampling** of the personal-communications video sources is cheap enough for
Tier 1.

- **Manual step**: install ffmpeg for Windows (static build from
  https://www.gyan.dev/ffmpeg/builds/ or `winget install ffmpeg`), add to
  `PATH` or set `FFMPEG_PATH`.
- Scope to **personal-comms video only** for Tier 1: `telegram video` (1,065,
  5.9GB), `beeper video` (1,605, 1.2GB), `whatsapp video` (68, 0.66GB) — ~7.8GB
  total. **Explicitly excludes** `youtube` (64.6GB) and `tiktok` (19.6GB) —
  those are Tier 2 (6K).
- Per video: `ffmpeg -i in.mp4 -vf "select='isnan(prev_selected_t)+gte(t-prev_selected_t,20)'" -vsync vfr frame_%03d.jpg`
  → ~1 frame every 20s, capped at 12 frames/video regardless of length, written
  to a temp working dir and deleted after processing.
- Each frame becomes a derived media item (`"{video_id}:frame:{sec}"`,
  `parent_media_item_id=video_id`) and flows through 6B (phash), 6D (OCR), and
  6F (face detection) exactly like any other image.
- ~2,738 videos × ~12 frames × (extraction + Tier 0/1 image pipeline per
  frame). ffmpeg extraction itself is fast (~1-3s/video for 12 sparse frames,
  since it seeks rather than decoding the whole file); the per-frame
  OCR/face cost dominates and is absorbed into 6D/6F's existing batch queues.

---

# TIER 2 — deferred (heavy compute/RAM, revisit later)

Not built now. Listed so the scope and the *reason* for deferral is explicit,
not forgotten.

- **6E — Audio transcription** (`faster-whisper`, small/base model). Even the
  "small" model is a few hundred MB and transcription is the single heaviest
  per-item operation in this whole plan. Audio volume here is tiny (~13
  files) but video audio tracks (10k videos) would be the real target, which
  compounds with 6K below.
- **6I — Image captioning** (BLIP-base via `transformers`+`torch`, ~1-2GB of
  deps+weights, ~1-3s/image CPU). Speculative value (semantic similarity of
  captions) vs. concrete value of 6D/6F/6C.2.
- **6J — Object/logo/scene detection** (YOLOv8 via `ultralytics`, needs
  torch). Lower identity-signal density than face matching for this use case;
  revisit only if a specific need arises (e.g. "find photos containing this
  logo/uniform").
- **6K — Full video coverage**: dense frame-by-frame analysis, and/or
  extending 6H's sparse sampling to `youtube` (64.6GB/2,815) and `tiktok`
  (19.6GB/4,479). These are also the *lowest* personal-identity-signal video
  sources (mostly public content vs. private messages), so they're both the
  most expensive and the least valuable — bottom of the list by a wide margin.
- **Face recognition at video scale**: running 6F over every frame of every
  video (vs. 6H's ~12 sparse frames) — only worth it once 6F's
  image-only results are validated as useful.

---

# Rollout order

| Step | Phase | Tier | New deps | Manual install | Backfill |
|------|-------|------|----------|-----------------|----------|
| 1 | `media_analysis` table + migration | — | — | — | — |
| 2 | 6A EXIF GPS | 0 | — (Pillow) | — | ~15 min |
| 3 | 6C PDF text + contact reuse | 0 | `pypdf` | — | ~5 min |
| 4 | 6B perceptual hash | 0 | `imagehash` | — | ~30 min |
| 5 | 6C.2 PDF embedded images | 0 | `PyMuPDF` | — | minutes (feeds 4/6/7) |
| 6 | 6D OCR + contact reuse | 1 | `pytesseract` | **Tesseract** | weeks, batched |
| 7 | 6F face detect + match | 1 | `opencv-python`, `onnxruntime` | — | profile photos: min; personal comms: ~20 min; bulk: batched |
| 8 | 6H sparse video frames (personal comms only) | 1 | — | **ffmpeg** | feeds 4/6/7 |
| — | 6E/6I/6J/6K | 2 | torch/transformers/whisper/ffmpeg-bulk | varies | **deferred** |

Steps 1-5 (Tier 0) need zero manual installs and can ship in one PR, fully
backfillable in under an hour total. Steps 6-8 (Tier 1) need two one-time
binary installs (Tesseract, ffmpeg — both lightweight, both same shape as the
Tailscale firewall step) but only small CPU-only Python deps otherwise.

## `_TYPE_WEIGHT` additions summary (`src/pipeline/identity_scorer.py`)

```python
"media_gps_colocation": 0.40,    # 6A
"media_perceptual_match": 0.35,  # 6B
"media_face_match": 0.50,        # 6F
# 6C, 6C.2 (text path), 6D, 6H reuse existing email_match / phone_match /
# cross_platform_link via contact_extraction — no new weights needed.
```
