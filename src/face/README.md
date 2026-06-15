# src/face — vendored facetracker engine (merge in progress)

Vendored from `C:/facetracker` @ `47dba9a` (main) as **Stage 0** of the
facetracker→unifiedanalyzer full merge. See `docs/facetracker_merge_plan.md`
for the full plan and decisions.

## State (Stage 0)
- The facetracker `src/` tree is copied here verbatim, with internal imports
  rewritten `src.*` → `src.face.*`. All modules parse; **nothing is wired into
  the analyzer yet** (no behavior change). Reversible: delete this directory.
- Deps added to the repo `requirements.txt` (not installed by Stage 0).

## Scope decisions baked in (from the plan)
- **Collector media only (D3):** the `discovery/` (drive scan/watch/onedrive/
  manifest) modules and `engine/tracker.py` (DeepSORT) are vendored but **will be
  pruned** in a later stage along with their deps (watchdog, deep-sort-realtime,
  aiofiles). The merged system indexes faces from the collector's media_items +
  derived video frames (already on Z:), not by scanning drives.
- **DB unify (D2=start clean):** facetracker tables go in a `facetracker` schema
  inside the unifiedanalyzer DB; no data migrated — re-index from scratch.
- **Storage on Z: (D1):** face crops / FAISS / thumbnails → `Z:/facetracker/faces`
  (was `Y:/facetracker/faces`). InsightFace model cache → Z: too.

## Next stages (not done yet)
1. Stand up `facetracker` schema in the analyzer DB; `face_worker` entrypoint.
2. `entity_faces` bridge; ingest collector media into the engine.
3. Remove Phase 6 YuNet/SFace face code; re-embed with InsightFace (512-dim).
4. Mount face API under `/face`; Faces page; retire standalone dashboard/DB.
5. Prune `discovery/` + `tracker.py` + unused deps; single compose deployment.
