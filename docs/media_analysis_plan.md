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

Everything else — text visible in screenshots, GPS embedded in photo EXIF,
text inside PDFs, near-duplicate (not byte-identical) images reposted across
platforms — is currently invisible to the identity resolver.

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

`unifiedcollector/requirements.txt` only has `Pillow` — no OCR, vision, or
transcription libraries are installed anywhere in either repo.

## Design constraints (carry over from existing pipeline conventions)

- **Non-fatal, try/except-wrapped pipeline steps** — every new step follows
  the pattern already used in `incremental_runner.py` (`try: await X() except
  Exception: logger.exception("X failed (non-fatal)")`).
- **"Ready to fire" / precision-first** — new signal types should be added to
  `_TYPE_WEIGHT` in `src/pipeline/identity_scorer.py` even if they produce 0
  signals initially. Don't gate the wiring on having data.
- **Fan-out filter** — any match (perceptual hash, GPS cluster, extracted
  contact) shared by >2 entities is "public" and excluded, same as existing
  signal types.
- **Graceful offline** — file I/O against `COLLECTOR_DRIVE_PATH`
  (`C:/unifiedcollector/media`) must not crash the pipeline if the drive is
  temporarily unavailable; catch and skip, retry next run.
- **120k items cannot be processed in one incremental cycle (every 120 min).**
  Everything heavier than EXIF must be a **resumable batch job**: process N
  items per run, track a cursor/flag so the next run picks up where the last
  left off. A full backfill of the existing backlog runs across many cycles
  (or as a one-off background script, like `check_route_similarity.py` is for
  Phase 4E).
- Each phase below is independently committable and should ship with its own
  `scripts/check_*.py` validation script, following the existing pattern.

## New table: `media_analysis` (analyzer DB)

One row per `(media_item_id, analysis_type)`. `media_item_id` references
`unifiedcollector.media_items.id` by value (cross-database, so no FK —
analyzer already does this for other collector-sourced data).

```sql
CREATE TABLE media_analysis (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    media_item_id   text NOT NULL,
    source          varchar NOT NULL,
    content_type    varchar NOT NULL,
    analysis_type   varchar NOT NULL,   -- 'exif_gps' | 'phash' | 'pdf_text' | 'ocr_text' | 'caption'
    extracted_text  text,
    result_json     jsonb,
    gps_lat         double precision,
    gps_lon         double precision,
    taken_at        timestamptz,
    perceptual_hash varchar,
    model_version   varchar,
    processed_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (media_item_id, analysis_type)
);
CREATE INDEX idx_media_analysis_type   ON media_analysis (analysis_type);
CREATE INDEX idx_media_analysis_phash  ON media_analysis (perceptual_hash);
CREATE INDEX idx_media_analysis_gps    ON media_analysis (gps_lat, gps_lon);
```

The `UNIQUE (media_item_id, analysis_type)` constraint doubles as the
"already processed" check for resumable batching:
`WHERE id NOT IN (SELECT media_item_id FROM media_analysis WHERE analysis_type = '...')`.

New pipeline module: `src/pipeline/media_analysis.py`, one `async def
analyze_*()` per phase below, all called (non-fatally) from
`incremental_runner.py` after `route_similarity`.

---

## Phase 6A — EXIF GPS extraction (no new dependencies)

**Pillow is already installed.** `PIL.Image._getexif()` /
`PIL.ExifTags.GPSTAGS` reads GPS lat/lon/timestamp from JPEG EXIF with zero
new deps.

- Target: all `content_type IN ('image','photo','activity_photo')` rows not
  yet in `media_analysis` with `analysis_type='exif_gps'`.
- For each, open the file (path = `COLLECTOR_DRIVE_PATH` + `file_path`'s
  relative portion — same root-confinement check as
  `unifiedcollector/src/dashboard/api.py:716-750`), read EXIF GPS tags,
  convert DMS → decimal degrees, store `gps_lat`/`gps_lon`/`taken_at` (from
  `DateTimeOriginal`) in `media_analysis`. If no GPS tag, still insert a row
  (with nulls) so it's marked processed and not retried every run.
- **New signal**: `media_gps_colocation` — for pairs of entities with
  `media_analysis.gps_lat/lon` within ~100m and `taken_at` within a tight
  window (reuse the haversine + time-window helpers already written for
  `route_similarity.py`'s shared-route-origin clustering). Add to
  `_TYPE_WEIGHT`: `"media_gps_colocation": 0.40` (same tier as
  `shared_route_origin`).
- Throughput: ~95k images @ ~5-10ms each ≈ 10-15 min one-time backfill.
  Trivial incrementally (only new items per cycle).
- Validation script: `scripts/check_media_exif.py` (mirror
  `check_route_similarity.py`).

This is the cheapest, highest-signal-density phase — do it first.

---

## Phase 6B — Perceptual hashing for near-duplicate images (new dep: `imagehash`)

`profile_photo_sha256` only catches **byte-identical** files. The same photo
re-saved, re-compressed, or screenshotted on a different platform produces a
different SHA256 but a near-identical perceptual hash.

- New dep: `imagehash>=4.3.1` (pure Python, built on Pillow — no system
  binaries).
- For each image-like `media_items` row not yet hashed: compute
  `imagehash.phash(img)` (64-bit), store as hex string in
  `media_analysis.perceptual_hash` (`analysis_type='phash'`).
- Matching: group by hash with **Hamming distance <= 6** (configurable). Exact
  bucketing on 64-bit hashes is too sparse for fuzzy matches, so either (a)
  bucket by the top 16 bits and do pairwise Hamming compares within a bucket
  (cheap at this scale), or (b) only compare within the same `content_type`
  and a reasonable time window to bound the comparison set.
- **New signal**: `media_perceptual_match` — entity A's media perceptually
  matches entity B's media across *different* `source` platforms. Add to
  `_TYPE_WEIGHT`: `"media_perceptual_match": 0.35` (same tier as
  `shared_website`). Apply the fan-out filter (hash shared by >2 entities =
  generic meme/stock image → excluded).
- Throughput: ~95k images @ ~10-20ms ≈ 15-30 min one-time backfill.

---

## Phase 6C — PDF text extraction (new dep: `pypdf`)

- New dep: `pypdf>=4.0` (pure Python, no system binaries — lighter than
  `pdfplumber`).
- Target: `content_type = 'pdf'` (2,575 rows, ~12 GB — `search` + `website`
  sources).
- Extract all text via `pypdf.PdfReader(...).pages[i].extract_text()`, store
  full text in `media_analysis.extracted_text` (`analysis_type='pdf_text'`).
  Cap stored text at e.g. 200KB per doc to avoid pathological cases.
- **No new signal type needed.** Instead, run the *existing* regex helpers
  from `src/pipeline/contact_extraction.py` (email/phone/handle patterns)
  over the extracted text and emit the **existing** `email_match` /
  `phone_match` / `cross_platform_link` signals, just with
  `source_table='media_items'` and `source_column='pdf_text'`. This slots
  directly into the current `_TYPE_WEIGHT` weights — no scorer changes
  required.
- Throughput: 2,575 PDFs, fast (~0.1-1s each) ≈ a few minutes one-time.

This phase is low-effort and reuses 100% of the existing contact-matching
logic — good second target after 6A.

---

## Phase 6D — OCR on images (new dep: `pytesseract` + **Tesseract OCR binary**)

The highest-value, highest-cost phase. Screenshots (`search`, `website`,
`telegram photo`, `lemon8 image`) very often contain visible usernames, phone
numbers, emails, and handles — exactly the signals `contact_extraction.py`
already knows how to turn into `email_match`/`phone_match`/
`cross_platform_link`.

- New dep: `pytesseract>=0.3.10` (thin Python wrapper).
- **Manual step (like the firewall rule)**: install the Tesseract OCR engine
  binary for Windows
  (https://github.com/UB-Mannheim/tesseract/wiki — installer adds
  `tesseract.exe`, typically to `C:\Program Files\Tesseract-OCR`). Add to
  `PATH` or set `pytesseract.pytesseract.tesseract_cmd` via a new
  `TESSERACT_PATH` env var. **This step requires the user** — pip alone is
  not enough.
- Target priority order (by identity-signal density, not just volume):
  1. `search` images (25,580) — search-result screenshots, often contain
     profile text/usernames.
  2. `website` images (23,541) — webpage screenshots.
  3. `telegram photo` (9,879).
  4. `lemon8 image` (28,523) — likely lowest text density (social media
     photos), do last.
- Run `pytesseract.image_to_string(img)`, store in
  `media_analysis.extracted_text` (`analysis_type='ocr_text'`). Then run the
  same `contact_extraction.py` regex pass as Phase 6C → existing
  `email_match`/`phone_match`/`cross_platform_link` signals with
  `source_column='ocr_text'`.
- **Batching is mandatory here.** Config via env vars:
  - `MEDIA_OCR_ENABLED` (default `false` until Tesseract is installed)
  - `MEDIA_OCR_BATCH_SIZE` (default `200` per incremental cycle)
  - Cursor: `WHERE media_items.id NOT IN (SELECT media_item_id FROM
    media_analysis WHERE analysis_type='ocr_text') ORDER BY collected_at LIMIT
    :batch_size`
- Throughput estimate: ~0.5-2s/image on CPU. Full backlog of ~95k images ≈
  13-50 CPU-hours. At 200 images/cycle × 12 cycles/day (every 2h) = 2,400/day
  → full backlog backfill takes **~40 days** at default batch size. Bump
  `MEDIA_OCR_BATCH_SIZE` or run a dedicated overnight backfill script
  (`scripts/backfill_media_ocr.py`, loop with a sleep, same pattern as other
  one-off scripts) if faster coverage is wanted. Either way, **new** media
  items get OCR'd within one cycle of being collected once steady-state is
  reached.

---

## Phase 6E — Video/audio/captioning (OPTIONAL — defer)

Flagged for completeness, **not recommended to start now**:

- Image captioning (BLIP-base via `transformers`+`torch`) for images with no
  OCR text — heavy (~1-2 GB of deps + model weights), slow on CPU
  (~1-3s/image), and the identity-signal value (semantic similarity of
  captions) is speculative compared to 6A-6D's concrete contact/GPS/dup
  signals.
- Video: `ffmpeg` keyframe extraction + OCR/caption on keyframes, plus audio
  transcription via `faster-whisper` (CPU-friendly small/base model). 10k
  videos totaling 86 GB (youtube alone is 64 GB across 2,815 videos) — even
  at a few seconds per video for keyframe extraction this is a multi-day
  backfill, and whisper transcription is the heaviest operation in this whole
  plan.
- **Recommendation**: revisit only if 6A-6D prove valuable and there's a
  specific investigative need (e.g., "what did this person say in their
  TikTok videos"). If pursued, sample rather than process all 86 GB — e.g.,
  only videos from entities already at >=2 linked platforms.

---

## Rollout order

| Step | Phase | New deps | Manual install | Effort | Backfill time |
|------|-------|----------|-----------------|--------|---------------|
| 1 | `media_analysis` table + migration | — | — | trivial | — |
| 2 | 6A EXIF GPS | — (Pillow) | — | small | ~15 min |
| 3 | 6C PDF text + contact reuse | `pypdf` | — | small | ~5 min |
| 4 | 6B perceptual hash | `imagehash` | — | small-med | ~30 min |
| 5 | 6D OCR + contact reuse | `pytesseract` | **Tesseract binary** | medium | weeks (batched) |
| 6 | 6E video/audio | `ffmpeg`, `faster-whisper`, `transformers` | ffmpeg binary | large | defer |

Steps 1-4 require **zero manual installation** beyond `pip install` and can
ship together in one PR. Step 5 is the big payoff (contact info visible in
screenshots) but needs the Tesseract binary and a batching strategy. Step 6
is explicitly out of scope until 1-5 prove their worth.

## `_TYPE_WEIGHT` additions summary (`src/pipeline/identity_scorer.py`)

```python
"media_gps_colocation": 0.40,    # Phase 6A
"media_perceptual_match": 0.35,  # Phase 6B
# 6C and 6D reuse existing email_match / phone_match / cross_platform_link —
# no new weights needed.
```
