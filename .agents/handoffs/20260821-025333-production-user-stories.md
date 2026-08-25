# Production User Story Coverage

Updated: 2026-08-21 02:53 SGT

Purpose: map the operator request to current code/runtime evidence and remaining work. Do not add secrets here.

## User Stories Verified This Run

1. As an operator, I can log into Meta once and have Collector preserve the browser auth state.
   - Code/runtime coverage: `unifiedcollector_browser_cookie_vault` backs up CDP cookies; latest snapshot contained Instagram/Facebook/X/Strava auth-cookie names without logging values.
   - Evidence: Collector state notes the snapshot restore into managed CDP `9336`; current source health is ok.
   - Remaining risk: cookie autorestore still needs stronger guardrails to avoid restoring stale cookies over a better live session.

2. As an operator, I can see whether browser collectors are actually producing content, not just whether tabs are open.
   - Code/runtime coverage: Collector source matrix reports browser content freshness; `tools/browser_tab_reload.py` now consumes dashboard stale-browser source issues and reloads/hard-reopens healthy-but-stale tabs.
   - Evidence: live recovery cleared Threads and X; `/health?include_sources=true` returned `ok` with zero source issues.
   - Remaining risk: X can still regress into external frontend shells; recovery is now automatic, but repeated shell loops need a clearer escalation state.

3. As an analyst, exposure findings become useful Analyzer indicators without mirroring raw private data.
   - Code/runtime coverage: `src/pipeline/exposure_indicators.py` stages redacted domain/email/IPv4 indicators from Collector `exposure_findings` into Analyzer `normalized_indicators`.
   - Evidence: local exposure indicators exported; Supabase health shows `exported_count=2368`, `ready_to_export=0`.
   - Remaining risk: cursor currently uses timestamp ordering; same-timestamp rows could be hardened with timestamp plus id.

4. As an operator, Analyzer health tells me if the scheduler is making progress.
   - Code/runtime coverage: `/api/health` includes `scheduler_freshness.incremental` and `scheduler_freshness.full_resolution`; fresh running heartbeat prevents false degradation, stale/missing progress degrades.
   - Evidence: live health returned `status=ok`, incremental `state=running`, full resolution `state=fresh`.
   - Remaining risk: UI should surface the new fields prominently if it does not already.

5. As an operator, Analyzer health tells me whether Supabase export is actually drained and populated.
   - Code/runtime coverage: `/api/health` includes `supabase_export` local state; enabled export degrades if the normalized indicator table is missing, empty, or has exportable pending/retry backlog over threshold.
   - Evidence: live health returned `supabase_export.state=ok`, `ready_to_export=0`, `exported_count=2368`.
   - Remaining risk: remote reachability proof remains in `/api/indicators/export/supabase/status`; health intentionally avoids remote blocking calls.

6. As an operator, heavy Analyzer media/face work should not starve the API.
   - Code/runtime coverage: bounded media scan windows in `src/face_worker.py` and `src/pipeline/media_common.py`; live Collector DB index `idx_media_face_candidates_recent`; API startup logs stages and notifications fail open.
   - Evidence: Analyzer API restarted and bound successfully after changes.
   - Remaining risk: existing `unifiedanalyzer_face_worker` container is a Docker zombie; compose has `init: true`, but the current zombie needs Docker engine recovery before replacement.

## Remaining High-Value Work

1. Guard browser-cookie autorestore.
   - Desired behavior: do not restore an older/stale cookie snapshot over a live authenticated session; compare auth-cookie presence and snapshot age first.
   - Suggested files: `C:\unifiedcollector\src\tools\browser_cookie_vault.py`, `tests\tools\test_browser_cookie_vault.py`.

2. Harden exposure cursor ordering.
   - Desired behavior: no missed `exposure_findings` when multiple rows share the same `collected_at`.
   - Suggested files: `C:\unifiedanalyzer\src\pipeline\exposure_indicators.py`, `tests\test_exposure_indicators.py`.

3. Surface production-readiness user stories in the UI.
   - Desired behavior: dashboard displays scheduler freshness, Supabase export readiness, browser ingest status, cookie vault status, and backup/restore status as first-class cards.
   - Suggested repos: Analyzer frontend and Collector dashboard.

4. Resolve the current face_worker zombie.
   - Desired behavior: after Docker Desktop restart, recreate `face_worker` with `init=true` and verify it can stop/restart cleanly.
   - Suggested verification: `docker inspect unifiedanalyzer_face_worker --format "init={{.HostConfig.Init}} running={{.State.Running}}"`.

5. Complete second WhatsApp bridge pairing only if the operator wants another device.
   - Desired behavior: bridge 2 remains live; bridge 1 is either paired or explicitly marked optional so it does not distract health.

6. Add a production-readiness route/checklist endpoint.
   - Desired behavior: one machine-readable endpoint reports collector health, Analyzer scheduler freshness, Supabase export, backups, vault, cookie-vault, and known manual actions.
