# UnifiedAnalyzer Documentation

Project-specific documentation for the analyzer. Read these in order for the
full picture:

| Doc | What it covers |
|-----|----------------|
| [`analyzer_overview.md`](analyzer_overview.md) | Full system runthrough — architecture, ~30 pipeline phases, identity model, live state, execution ledger. **Start here.** |
| [`identity_system_review_plan.md`](identity_system_review_plan.md) | External review triage — P0/P1/P2 findings, verification status, execution ledger for each. |
| [`dashboard_redesign_plan.md`](dashboard_redesign_plan.md) | Frontend reskin + plain-language layer. All three phases shipped as of 2026-07-07. |
| [`media_analysis_plan.md`](media_analysis_plan.md) | Phase-6 media analysis (EXIF / OCR / pHash / faces / PDF / video-frame). |
| [`facetracker_merge_plan.md`](facetracker_merge_plan.md) | Historical record of how the standalone facetracker face engine was merged in. |
| [`storage_drive_plan.md`](storage_drive_plan.md) | Storage layout — derived artefacts on Z:, source media on Z:, why C: is off-limits. |

## ⚠ Do NOT sync-delete this folder

These are project-specific docs, **not** boilerplate templates. The
sourcerepo config-sync workflow deleted every file in this directory once
(`1e4814f`, 2026-07-06) as part of a boilerplate propagation. If you
maintain the sourcerepo sync workflow, please ensure it excludes `docs/**`
from its rsync spec (or uses a whitelist mode rather than mirror mode).

Recovery pattern if it recurs:

```bash
# Find the last commit that had docs/ intact:
git log --diff-filter=D --all -- 'docs/analyzer_overview.md' | head -5
# Restore from the commit just BEFORE the deletion:
git checkout <good-commit> -- docs/
git commit -m "docs(rescue): restore docs/ from <good-commit> after sync wipe"
```
