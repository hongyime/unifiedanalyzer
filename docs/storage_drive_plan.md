# Storage drive plan (Z:) — restart record

**As of 2026-06-19.** Single storage drive is **Z:** (reformatted to **NTFS**).
The earlier Z:→Y: migration is **abandoned** — everything stays on Z:.

## Why C: must stay clean
C: is space-constrained. Two failure modes filled it before:
1. Derived media (Phase 6) written to `C:\unifiedanalyzer\data\media_derived`.
2. **OneDrive Files-On-Demand hydration** — any broad filesystem walk over
   OneDrive/C: paths (a drive scan, or even `rg.exe`) downloads placeholder
   files onto C:. The old standalone facetracker drive-scan hydrated 138/283
   ingested OneDrive files onto C:.

**Rule:** every growing artifact writes under Z:; never scan C:/OneDrive.

> **Update 2026-06-26:** drive face-scanning is now live — `face_worker` scans
> the **W:/X:/Y:/Z:** drives (`DRIVE_SOURCES` in docker-compose). This rule still
> holds: **C: is deliberately excluded** from `DRIVE_SOURCES`, so no OneDrive
> hydration. W:/X: are SMB/CIFS mounts (Tailscale `Prawn-E14`).

## Layout (single project root)
```
Z:/unifiedanalyzer/media_derived/
├── faces/         all face artifacts: crops, FAISS, thumbs, InsightFace ONNX cache
├── models/        analyzer's own derived models
├── pdf_images/
└── video_frames/
Z:/unifiedcollector/media/   collector SOURCE media (input; wiped — see below)
```

## Config (.env + code defaults)
- `MEDIA_DERIVED_PATH=Z:/unifiedanalyzer/media_derived`
- `COLLECTOR_MEDIA_ROOT=Z:/unifiedcollector`
- `FACE_MODEL_ROOT=Z:/unifiedanalyzer/media_derived/faces`
- `src/face/config.py` `face_storage_root` + `src/face/engine/detector.py`
  model-cache fallback default to the same faces dir.
- `src/face/discovery/scanner.py` fails closed when `drive_sources` is empty
  (no C:/ default walk).

## Restart status (after Z: reformat)
The Z: reformat destroyed all Z: contents except (re-created) collector dir
skeleton and a few root datasets. Recovery:
- ✅ Z: artifact tree recreated; InsightFace models re-downloaded to Z:; stale
  face tables truncated; scope locked to collector media.
- ⚠️ **Collector SOURCE media is gone** (`Z:/unifiedcollector/media/*` empty,
  ~140k DB rows dangling). Re-derive + face re-index are blocked until it is
  restored (re-run unifiedcollector or restore backup). Analyzer DB survived.

Z: root also holds unrelated reference datasets (`nric.zip`, `paynow.zip`,
`pokemon.zip`, `postalcodes.zip`) — leave them alone.
