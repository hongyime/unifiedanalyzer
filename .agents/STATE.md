# UnifiedAnalyzer Agent State


Updated: 2026-08-25 14:20 UTC / 22:20 SGT

Current live update:
- Resumed cross-repo work after the Codex usage-limit stop. Rebased live truth first: `/api/health` degraded only by a real Supabase export backlog (`ready_to_export=17`, `exported_count=6267`); Collector action queue `count=0`; baseline readiness measured 55.6s wall with three warning degradations before any change.
- Committed the previously uncommitted multi-session working tree as five atomic commits: production readiness/data-quality gates, exposure staging + Facebook attribution, bounded media scans + scheduler export drains, `/production` frontend page, and agent docs. Secret scan clean.
- Fixed the readiness load-timeout blocker: sequential recovery chains after the parallel gather (health isolated retry up to 90s plus fast fallback 35s; collector retry 90s plus fallback 45s) could exceed any client timeout. Added `ANALYZER_READINESS_TOTAL_BUDGET_SECONDS` (default 40) capping every stage including recovery; stages skipped by the deadline are recorded as `deadline_skipped_stages` evidence instead of hanging the route. RED→GREEN regression test pins a stalled-retry scenario returning bounded degraded evidence (commit `4a59b9c`).
- Live proof after container recreate: cold probe 41.4s bounded, warm probe 25.7s vs the 55.6s baseline. Remaining degraded checks are genuine signals, not deadline artifacts: `supabase_populated` backlog=17 (real), Collector maintenance terminal-degraded state (real Collector-side signal for follow-up), `data_quality_ledger` timeout under concurrent load with cache fallback active.
- Verification: `python -m pytest tests\test_readiness_route.py tests\test_collector_health_route.py tests\test_data_quality_ledger.py -q` passed 63; frontend `npm run build` passed pre-commit; live curl artifacts captured at 41.4s/25.7s.
- Remaining known work: re-run of the three Codex sidecar audits that died on the usage limit (reviewer/auditor/researcher), X `try_again_empty_state` page shell, Instagram removed-post/429 churn, FB/Threads/X stale browser-content suppression reasons, Supabase backlog drain watch.
Updated: 2026-08-21 23:46 UTC / 2026-08-22 07:46 SGT

Current live update:
- User reported normal Chrome tabs were not signed in. Verified again that Collector uses a separate managed Chrome-for-Testing CDP profile on port 9336, so normal desktop Chrome login state is not the Collector auth source of truth.
- Managed profile proof: tab budget remains clean with one platform tab each and one extension control tab; Facebook/Threads/TikTok/X/Strava content scripts are attached on audit, while Lemon8 has no content script by design/availability. Cookie vault proof still has an effective restorable snapshot (`count=86`, quality score 5169) and no missing auth platforms; do not log raw cookies.
- Real platform/page blockers remain warning-level, not auth-loss: X is still on a `try_again_empty_state` shell; Instagram is bouncing to a removed post/page shell and briefly returned HTTP 429; Facebook/Threads/X source-health rows are browser-content-stale watchdog warnings. Collector action queue is still `count=0`.
- Patched Analyzer readiness and Collector production summary so warning-only browser/source rows (`browser_capture_stalled`, `browser_page_error`, fresh heartbeat/tab evidence, or rollup-excluded diagnostics) stay visible but do not critical-fail `collector_production_surfaces`. Hard auth loss, hard source rows, unreachable Collector, inactive ingest, and hard realtime failures still fail.
- Recreated `unifiedanalyzer_analyzer`. Live proof after Collector Postgres finished recovery: `/api/health` is ok with Analyzer/Collector DB connected; `/api/production/readiness` is `status=ok`, `critical_failed=0`, `12/13` ok, only warning is data-quality ledger timeout; Supabase remains `status=ok`, `ready_to_export=0`, `raw_mirror=false`, remote row count `5720`.
- Operational note: Collector Postgres was rejecting connections during recovery until `2026-08-22T07:40:47+08:00`; do not treat readiness/source-matrix errors during that window as Chrome/cookie failures.
- Verification: `python -m pytest tests\test_collector_health_route.py tests\test_readiness_route.py -q` passed 56; compileall and diff-check passed for touched readiness/collector-health files.

Updated: 2026-08-21 21:38 UTC / 2026-08-22 05:38 SGT

Current live update:
- Rechecked the operator report that Chrome/social tabs looked logged out. Collector CDP `9336` is reachable and still uses the dedicated managed Chrome-for-Testing profile `ChromeCdpAutomationProfile_fresh_20260822_0325`, separate from ordinary desktop Chrome.
- Cookie vault remains healthy and restorable: latest health reports safe auth marker names for Facebook, Instagram, Strava, TikTok, and X, with an effective 89-cookie restorable snapshot preserved. No raw cookie values were logged.
- Repaired bad page-shell tabs without profile restart: reopened Instagram from a removed post URL to `https://www.instagram.com/explore/`, Threads from `?error=invalid_post` to the canonical Threads page, and Lemon8 to the Singapore topic URL. Follow-up tab audit is budget-clean: 8 page targets, one extension control tab, zero blank tabs, and one tab each for Facebook, Instagram, Lemon8, Strava, Threads, TikTok, and X. Content scripts are attached for Instagram, Threads, TikTok, X, Facebook, and Strava.
- Collector action queue remains clean (`count=0`). Collector browser auth is not the current blocker. A follow-up Analyzer `/api/production/readiness` call is still degraded because broad Analyzer/Collector readiness probes timed out under current load and returned empty/partial critical evidence; handle that as the next readiness/load issue, not as cookie loss.

Updated: 2026-08-21 21:20 UTC / 2026-08-22 05:20 SGT

Current live update:
- Hardened `/api/production/readiness` against false critical-red results under DB/browser load. If the broad Analyzer health probe times out, readiness now builds a fast critical health fallback from smaller DB checks for DB connectivity, scheduler freshness, local Supabase export state, backup proof, decision-log durability, face identity audit, and face-processing freshness.
- Added data-quality ledger last-good cache support. `/api/data-quality/ledger` writes a fresh ok cache, and readiness uses that cache only when the live ledger probe times out/errors. Live direct ledger is clean: `status=ok`, `gap_sources=0`, `total_sources=11`.
- Carried Collector `rate_limit` objects through Analyzer `/api/collector/production-status` media-yield rows and updated hourly-yield readiness logic so current active pressure is exempted, expired pressure is not, and headless/backend sources like Lemon8 can use current DB records as useful output when browser stored rolling output is not the source of truth.
- Recreated `unifiedanalyzer_analyzer`. Final live `/api/production/readiness` returned `status=ok`, `ok=true`, `13/13` checks ok, `degraded=0`, `critical_failed=0`. Supabase remains `status=ok`, `ready_to_export=0`, `raw_mirror=false`, remote row count `5720`.
- Subagent reviewer/auditor/researcher spawn was attempted again but failed with `agent thread limit reached`; local audit and patching continued.
- Verification: `python -m pytest tests\test_collector_health_route.py tests\test_readiness_route.py tests\test_data_quality_ledger.py -q` passed 56; compileall and diff-check passed for touched Analyzer readiness/data-quality/collector-health files.

Updated: 2026-08-21 20:52 UTC / 2026-08-22 04:52 SGT

Current live update:
- Rechecked the user report that visible Chrome tabs appear logged out. Live Collector-managed Chrome-for-Testing CDP on port 9336 is reachable and uses the dedicated profile `ChromeCdpAutomationProfile_fresh_20260822_0325`, separate from normal desktop Chrome.
- Cookie vault is healthy and restorable. Forced a restore of the preserved snapshot into CDP: 89 cookies pushed across TikTok, Instagram, Threads, Facebook, X/Twitter, Strava, and Lemon8. Fresh backup still has auth markers for Facebook (`c_user`, `xs`), Instagram (`sessionid`), Strava (`_strava4_session`), TikTok (`sessionid`, `ttwid`), and X (`auth_token`, `ct0`); the vault preserved the higher-quality 89-cookie snapshot.
- Live Collector health remains `status=ok`, `source_issues=[]`; browser ingest is active with fresh heartbeat/content and active platforms `bridge,facebook,instagram,lemon8,strava,threads,tiktok,x`. Source matrix rolling output proves Facebook, Instagram, Strava, Threads, and X are still collecting; TikTok remains the only open action due recent rate/access pressure.
- Tab audit shows no auth wall for Facebook, X, TikTok, Strava, Lemon8, and the extension. Instagram had been on a broken post URL and maintenance hard-reopened it to Explore, which can show a generic title even while cookies/content collection are intact.

Updated: 2026-08-21 20:47 UTC / 2026-08-22 04:47 SGT

Current live update:
- Repaired the post-boot browser/auth confusion from Analyzer context. The visible normal Chrome profile can be logged out, but Collector uses the managed Chrome-for-Testing CDP profile on port 9336. Cookie vault restore/backup succeeded against CDP with auth markers for Facebook, Instagram, Strava, TikTok, X, and related domains. X was repaired from a stale `Try again` shell back to `https://x.com/home`.
- Fixed Analyzer exposure staging backlog. `src/pipeline/exposure_indicators.py` now collapses duplicate indicators before DB upsert and requeues exported rows to `pending` when new exposure evidence changes them. Live catch-up scanned 50,920 exposure findings, staged 112,905 extracted indicators as 38,727 unique upserts, and advanced the exposure cursor to Collector latest `2026-08-21T17:21:07.429300Z`.
- Drained Supabase compact indicator export after exposure catch-up. Live Supabase status is `status=ok`, `ready_to_export=0`, `raw_mirror=false`, remote row count `5720`, latest remote export `2026-08-21T20:25:43Z`. Direct `/api/data-quality/ledger` is clean: `status=ok`, `gap_sources=0`; exposure and WhatsApp are both ok.
- Patched shared indicator upsert to requeue previously exported rows when source-family/evidence metadata changes, preventing stale source-family export gaps.
- Latest serial `/api/production/readiness` is `status=ok`, `critical_failed=0`, `ok=true`. Remaining warning-level work: Lemon8 below rolling media floor, TikTok recent rate/access pressure action, and readiness data-quality timeout even though direct ledger is clean.
- Verification: Analyzer focused tests `python -m pytest tests\test_exposure_indicators.py tests\test_identity_truth_and_indicators.py -q` passed 20; compileall/diff-check passed for touched Analyzer files.

Updated: 2026-08-21 16:25 UTC / 2026-08-22 00:25 SGT

Current live update:
- Rechecked Analyzer readiness after Collector action-queue hardening and browser recovery. Supabase status remains ok (`ready_to_export=0`, `raw_mirror=false`, remote row count `2372`), proving Analyzer is still populating compact normalized indicators only.
- Analyzer `/api/production/readiness` is not green under current load: `status=degraded`, `critical_failed=7`, `degraded=9`. Several failures are timeout-derived (`analyzer_health`, Collector dashboard fallback, data-quality ledger, analyst API HTTP probes), and Collector action queue currently exposes two real open actions (`browser_extension/repair_browser`, `lemon8/source_blocked`).
- Collector side hardening now prevents new false `target_starved` queue actions from partial source-matrix payloads. Six stale false zero-window actions were resolved live in Collector; the remaining queue items are real browser/content repair work.

Updated: 2026-08-21 15:15 UTC / 2026-08-21 23:15 SGT

Current live update:
- Added `analyst_value_path_proven` to `/api/production/readiness`. It is a warning-level, read-only DB proof that checks review candidates, durable audit-log decisions, case items, and a case export path instead of only checking that analyst routes mount.
- Live Analyst proof was made concrete through the public API: created case `07507487-c513-4fb1-9c97-0e1e7bb89e3f` named `Production readiness proof - analyst workflow`, added entity item `87eaf4c4-fbff-4f14-b799-e64eb11cce18`, and verified `/api/cases/07507487-c513-4fb1-9c97-0e1e7bb89e3f/export` returns HTTP 200.
- Readiness now counts both current and legacy durable analyst decision actions (`merge_confirmed`/`dismiss_identity_candidate` and legacy `merge_entities`/`dismiss_match`). Live evidence: `review_candidate.count=386`, `durable_decision.count=42`, `case_item.count=1`, `case_export.ok=true`.
- Tightened `src/pipeline/timeline_builder.py` future timestamp filtering from 366 days to 1 day and added `_valid_timeline_time()` regression coverage. Corrected two live GitHub `timeline_events` rows that were future-dated `2026-12-31T17:00:00Z`, preserving the original timestamp in metadata.
- Recreated Analyzer API and Scheduler. Live `/api/data-quality/ledger` is clean (`status=ok`, `gap_sources=0`) and readiness data-quality timeout budget now defaults to 25s to avoid false warning under concurrent readiness fan-out.
- Final live `/api/production/readiness`: `status=ok`, `ok=true`, `13` checks, `critical_failed=0`, `degraded=1`. Only remaining warning is `collector_hourly_yield_floor`: Lemon8 has `stored_rolling_60m=0`; TikTok has `stored_rolling_60m=4`; Threads is exempt because a page-shell warning is present despite strong stored rolling output.
- Supabase remains ok: `ready_to_export=0`, `raw_mirror=false`, remote row count `2372`. Collector health remains top-level `status=ok`, `source_issues=0`.
- Verification: `python -m pytest tests\test_readiness_route.py tests\test_data_quality_ledger.py tests\test_facebook_timeline.py -q` passed 48; compileall and `git diff --check` passed for touched Analyzer files.

Updated: 2026-08-21 14:57 UTC / 2026-08-21 22:57 SGT

Current live update:
- User reported the visible Chrome tabs looked logged out. Verified this is the Collector-managed Chrome-for-Testing profile on CDP `9336`, not the normal desktop Chrome profile.
- Cookie vault was healthy and restorable. Restored the latest snapshot into CDP: 87 social cookies were pushed, covering safe auth markers for Facebook, Instagram, Strava, TikTok, and X. Follow-up vault backup improved to 88 restorable cookies with no error.
- Reopened/reloaded managed platform tabs for Instagram, TikTok, Lemon8, Threads, Facebook, Strava, and X. Fresh maintenance pass completed with `state=ok`, `detail=audit and reload completed`, `issues=[]`, and the maintenance loop alive.
- Live Collector `/health?include_sources=true` is `status=ok`, `source_issues=[]`. Browser extension ingest is active with fresh heartbeats/content for `facebook`, `instagram`, `strava`, `threads`, `tiktok`, and `x`; visible titles include Facebook, Instagram explore/feed, X home, Strava dashboard, Threads, Lemon8, and the UnifiedCollector control page.
- Caveat: normal desktop Chrome login state remains separate from Collector CDP profile. WhatsApp bridge 1 still waits for QR if a second device/session is wanted; bridge 2 is paired and collecting.

Updated: 2026-08-21 14:48 UTC / 2026-08-21 22:48 SGT

Current live update:
- Hardened `/api/production/readiness` under DB load. `_health_status()` is now bounded by `ANALYZER_READINESS_HEALTH_TIMEOUT_SECONDS` default `20s`; warning-only data-quality defaults to `10s`; warning-only Collector action queue defaults to `8s`; Collector fallback is bounded by `ANALYZER_READINESS_COLLECTOR_FALLBACK_TOTAL_TIMEOUT_SECONDS` default `12s`. Supabase remains strict/critical.
- Readiness timeout evidence is now preserved in check evidence for database health and Collector action queue. Slow data-quality becomes an explicit warning payload (`status=timeout`, `timeout_seconds=10`) instead of consuming the whole route budget.
- Recreated `unifiedanalyzer_analyzer`. Live `/api/production/readiness` returned in about 34s with `status=ok`, `ok=true`, `critical_failed=0`, `11/12` checks ok. The only degraded check is warning-level `data_quality_ledger` timeout at the bounded 10s budget.
- Live Supabase export status returned `status=ok`, `ready_to_export=0`, `raw_mirror=false`, remote readback reachable with `row_count=2372`, latest remote export `2026-08-21T04:18:42.072858+00:00`.
- Subagent product reviewer’s next highest-value gap: readiness currently proves analyst workflow routes are mounted, not an end-to-end analyst triage -> case -> provenance-backed export workflow. Implement that next before claiming the full production/user-value objective is complete.
- Verification: `python -m pytest tests\test_readiness_route.py -q` passed 40; focused timeout tests passed 5; compileall and diff-check passed for touched readiness files.

Updated: 2026-08-21 14:30 UTC / 2026-08-21 22:30 SGT

Current live update:
- Collector sign-in report was checked live from Analyzer context. The Collector cookie vault remains restorable with auth-bearing snapshots for Facebook, Instagram, Strava, TikTok, and X, and 87 cookies were restored into the managed CDP profile.
- Collector managed tab audit is budget-clean after reload/reopen. Triggering the extension `scrapeNow` path recovered Facebook output; Collector `/health?include_sources=true` now returns top-level `status=ok` and `source_issues=[]`, with Facebook live and producing this hour.
- Collector action queue is zero open after sync. Analyzer `/api/production/readiness` was attempted but timed out after 90s under current DB load, so Analyzer readiness is not revalidated in this slice.
- Next task: improve Analyzer readiness/source-matrix behavior under DB load and continue suppressing timeout-derived noise only when live source_health/output evidence proves coverage.

Updated: 2026-08-21 14:15 UTC / 2026-08-21 22:15 SGT

Current live update:
- Subagent auditor found that `data_quality_ledger` treated future timestamps as age zero. Fixed the ledger so future-dated evidence beyond the configured skew grace becomes `clock_skew` and degrades data-quality readiness.
- Corrected two live synthetic/future GitHub `timeline_events` rows (`CODE_COMMIT`, originally `2026-12-31T17:00:00Z`) by setting `occurred_at=now()` and adding metadata with the original timestamp and correction reason. Follow-up `/api/data-quality/ledger` returned `status=ok`, `ok=true`, `gap_sources=0`; GitHub is now `analyzer_only` with current corrected timestamps, not future-fresh.
- Supabase compact export remained healthy in readiness: `ready_to_export=0`, local exported `2372`, remote row count `2372`, `raw_mirror=false`.
- Latest `/api/production/readiness` returned `status=ok`, `ok=true`, `critical_failed=0`, `10/12` checks ok, `degraded=2`. Remaining warning checks were `collector_action_queue_visible` and `data_quality_ledger`; direct ledger later succeeded, while Collector action queue intentionally surfaced remaining warning actions from timeout-derived browser/source rows.
- Researcher gap to implement next: prove an analyst can complete triage -> case -> provenance-backed export, not only that routes mount.
- Verification: `python -m pytest tests\test_data_quality_ledger.py -q -vv` passed 4; compileall and diff-check passed for touched Analyzer ledger files.

Updated: 2026-08-21 13:23 UTC / 2026-08-21 21:23 SGT

Current live update:
- Rechecked Analyzer after Collector managed-browser/cookie restore and action-queue false-positive hardening. Live `/api/production/readiness` returned `status=ok`, `ok=true`, `12/12` checks ok, `critical_failed=0`, and `degraded=0`.
- Collector action queue is now clean: live sync returned `derived=0`, `open=0`, `resolved=7`, and `GET /collectors/action-queue?status=open` returned `count=0`.
- Collector cookie-vault proof remains healthy and restorable with safe auth markers for Facebook, Instagram, Strava, TikTok, and X. The visible desktop Chrome login state is separate from the managed Collector Chrome-for-Testing profile on CDP `9336`.
- Supabase compact indicator proof remains ok: `ready_to_export=0`, remote readback reachable, remote row count `2372`, and `raw_mirror=false`.

Updated: 2026-08-21 12:46 UTC / 2026-08-21 20:46 SGT

Current live update:
- Fixed the production readiness user-story proof surface so open Collector actions and analyst workflow availability are visible in `/api/production/readiness` and `/production`.
- Core analyst APIs are now mounted before the SPA fallback: live `GET /api/entities?limit=1`, `/api/review/candidates?limit=1`, `/api/triage`, `/api/cases`, and `/production` all returned HTTP 200 after Analyzer recreate.
- Hardened readiness against live fallback edges: Collector fallback can use rolling browser yield plus effective cookie-vault restore proof, Supabase readback default timeout is 45s, and scheduler self-healing accepts a fresh full-resolution completion when incremental completion is stale but has no running error.
- Live Supabase status returned `status=ok`, `ready_to_export=0`, remote readback reachable, remote row count `2372`, and `raw_mirror=false`.
- Final live `/api/production/readiness` returned `status=ok`, `ok=true`, `12` checks, `critical_failed=0`, `degraded=1`. The remaining warning is the now-visible Collector action queue with open actions for TikTok pressure, Lemon8 browser-content staleness, and Website target starvation.
- Verification: `python -m pytest tests\test_readiness_route.py tests\test_identity_truth_and_indicators.py -q` passed 50; `python -m pytest tests\test_readiness_route.py -q` passed 36; `npm run build` in `frontend/` passed; compileall and diff-check passed for touched readiness/export/frontend paths.

Current live update:
- Added a user-facing frontend page for the readiness user-story proof: `frontend/src/pages/ProductionReadiness.tsx`, route `/production`, and an Operations sidebar item. The page calls `/api/production/readiness`, shows summary counters, open readiness work, critical/warning stories, per-check proof requirements, and compact live evidence.
- Added frontend API types/method `getProductionReadiness()` for the readiness report and story metadata. The Analyzer container bind-mounts `frontend/dist`, so the built Vite output is live without an image rebuild.
- Verification: `npm run build` in `frontend/` passed; `git diff --check` passed for touched frontend files. Live `GET /production` returned HTTP 200 and the container sees `ProductionReadiness-*.js` in `/app/frontend/dist/assets`.
- Live `/api/production/readiness` returned `status=ok`, `ok=true`, `10` checks, `critical_failed=0`, `degraded=1`; the warning is `collector_hourly_yield_floor` for TikTok below the rolling useful-output floor. Supabase remains covered by the readiness story metadata from the previous slice.
- Collector action queue sync after the current source matrix now shows real open actions for TikTok rate/access pressure and Lemon8 browser-content staleness, so the warning has an operator-action path again.

Current live update:
- Added machine-readable user-story metadata to `/api/production/readiness`. Every readiness check now carries `user_story.actor`, `story`, `value`, and `proves`, and the response includes a top-level `user_stories` map keyed by check id. This turns the production readiness endpoint into the current code-backed user-story proof surface instead of relying on stale markdown handoffs.
- Recreated `unifiedanalyzer_analyzer`. Live readiness returned `status=ok`, `ok=true`, `10` checks, `critical_failed=0`, `degraded=0`, `story_count=10`, and no checks missing story metadata. Supabase story explicitly states compact Analyzer indicators without raw Collector mirror.
- Supabase export remains drained/populated: status `ok`, `ready_to_export=0`, remote readback reachable, remote row count `2372`, `raw_mirror=false`.
- Collector action queue stayed empty after sync, and Collector production surfaces are still green from the live proof earlier in the slice.
- Requested reviewer/auditor/researcher subagents were attempted again through multi-agent tools, but spawn failed with `agent thread limit reached`; local implementation and verification continued.
- Verification: `python -m pytest tests\test_readiness_route.py -q` passed 32; compileall and diff-check passed for touched readiness files.

Current live update:
- Rechecked Analyzer after Collector stale-cooldown action fix. Live `/api/production/readiness` returned `status=ok`, `ok=true`, `critical_failed=0`, `degraded=0`.
- Supabase export status remains healthy: `ready_to_export=0`, remote readback reachable, remote row count `2372`, `raw_mirror=false`.
- Collector action queue now has one real open action: TikTok profile-metadata challenge/rate pressure until `2026-08-21T18:48:28.07476+08:00`. Lemon8 expired cooldown and Website slow-crawl false-positive are resolved.

Current live update:
- Rechecked Analyzer after Collector website slow-yield fix. Live `/api/production/readiness` returned `status=ok`, `ok=true`, `critical_failed=0`, `degraded=0`.
- Supabase export status remains healthy: `ready_to_export=0`, remote readback reachable, remote row count `2372`, `raw_mirror=false`.
- Collector action queue now has only two real pressure/cooldown actions: TikTok recent rate/access pressure and Lemon8 avatar-profile cooldown. Website no longer false-flags as hourly-starved while it has substantial 24h crawl output.

Current live update:
- Hardened Analyzer readiness scheduler logic: if incremental completion has just gone stale but full-resolution is actively running with a fresh heartbeat and no incremental running error, `scheduler_self_healing` is considered ok. This matches the live long full-resolution run state instead of marking a healthy busy scheduler as critical failed.
- Recreated `unifiedanalyzer_analyzer`. Final live `/api/production/readiness` returned `status=ok`, `ok=true`, `critical_failed=0`, `degraded=0`.
- Supabase export proof remains healthy: `ready_to_export=0`, remote readback reachable, remote row count `2372`, `raw_mirror=false`.
- Collector proof feeding readiness is green: Collector `/health?include_sources=true` is `status=ok`, `source_issues=0`, browser maintenance `ok`, only warning-level Instagram HTTP 429; action queue has real actions for TikTok pressure, Lemon8 cooldown, and Website target-starved.
- Verification: `python -m pytest tests\test_readiness_route.py -q` passed 31; compileall passed for readiness route/tests; `git diff --check` passed for touched Analyzer paths.

Current live update:
- Rechecked Analyzer after Collector action-queue yield and timeout-skeleton hardening. Live `/api/production/readiness` returned `status=ok`, `ok=true`, `critical_failed=0`, `degraded=0`.
- Supabase export status remains healthy: `ready_to_export=0`, remote readback reachable, remote row count `2372`, and `raw_mirror=false`.
- Collector now enforces the useful-output floor across primary collectors by default, so live-but-zero `website` output becomes a durable `target_starved` action instead of being silently ignored. Action-queue sync also suppresses source-matrix timeout skeleton rows so DB-load fallback does not create fake production blockers. Current Collector generated actions after live sync are TikTok rate/access pressure and Website target starvation.

Current live update:
- Rechecked Analyzer after managed Collector browser recovery. Live `/api/production/readiness` returns `status=ok`, `ok=true`, `critical_failed=0`, `degraded=1`; the only failed/warning check is `collector_hourly_yield_floor`.
- Collector production surfaces are passing again: Collector `/health?include_sources=true` returns `status=ok`, `source_issues=0`, browser maintenance `ok`, and no browser issues. The earlier normal-Chrome logout report was not Collector cookie loss; Collector uses its own managed CDP `9336` profile.
- Remaining generated Collector actions are real pressure/cooldown states: Instagram daily profile-view quota cooldown and TikTok recent rate/access pressure. They are not Supabase, Analyzer, or cookie-vault blockers.
- Collector action-queue false positives were reduced in `src/core/collection_action_queue.py` so warning-only live page-shell observations with recent output do not become production blockers. Focused Collector tests passed 41 and dashboard was recreated.

Current task status: Supabase direct-Postgres compact indicator export is implemented, live, drained, and has remote readback proof. Analyzer stages Collector `exposure_findings` into compact redacted `normalized_indicators` and exports those to Supabase. Exposure staging cursoring uses `(collected_at, id)` high-watermark semantics. Production startup and health hardening are live: Collector has partial index `idx_media_face_candidates_recent`; Analyzer face/media scans are bounded; API startup has explicit stage logs and notification fail-open; scheduler runs with `init: true`; `/api/health` reports scheduler freshness and local Supabase export health; `/api/production/readiness` maps verified user stories to live checks. Analyzer API cold-start edge case was fixed by mounting non-core routes and face API in background after core health/readiness routes are available. Latest live `/api/production/readiness` is `status=ok`, `ok=true`, critical checks all pass, and Supabase populated passes with local `exported_count=2372` matching remote `row_count=2372`. `/api/data-quality/ledger` proof surface is live and included in readiness as warning-level `data_quality_ledger`; it reports aggregate source/stage counts only. Current live ledger is `status=ok`, `gap_sources=0`; Facebook now has recent Analyzer timeline evidence for recent Collector raw rows and a bounded author resolver for nonblank Facebook authors. Collector browser auth/cookie recovery is verified live: cookie vault has auth markers for Facebook/Instagram/Strava/TikTok/X, tab budget is clean, and Collector browser ingest is active for `bridge,facebook,instagram,strava,threads,tiktok,x`. Ordinary visible Chrome can still be logged out because Collector uses a separate managed Playwright Chromium profile on CDP `9336`. Remaining production warnings: TikTok is currently below the 5 rolling-hour useful-output floor, and Facebook rows with blank `author_username` remain un-attributable from current Collector payload.

Latest update:
- Batched the data-quality ledger so readiness no longer times out under normal load. `src/pipeline/data_quality_ledger.py` now groups raw Collector media/browser-ingest and Analyzer timeline/text/media/indicator summaries by source instead of running many per-source aggregate queries. Browser-ingest raw proof now counts stored non-heartbeat rows, avoiding zero-stored probe noise in the raw count.
- Recreated/restarted `unifiedanalyzer_analyzer`. Direct `/api/data-quality/ledger` returned `status=ok`, `ok=true`, `gap_sources=0`, `total_sources=11`, states `ok=10, analyzer_only=1`, in 6.7s on the compact verification pass.
- Isolated live `/api/production/readiness` returned `status=ok`, `ok=true`, `critical_failed=0`, `degraded=0`, in 21.36s. The `data_quality_ledger` check passed with `ok=true`; Supabase status stayed `status=ok`, `ready_to_export=0`, remote readback reachable with `row_count=2372`, and `raw_mirror=false`.
- Verification: `python -m pytest tests\test_data_quality_ledger.py tests\test_readiness_route.py -q` passed 31; compileall and diff-check passed for touched Analyzer files.

Latest update:
- Rechecked live production readiness after Collector browser repair. Final `/api/production/readiness` returned `status=ok`, `ok=true`, `critical_failed=0`, `9/10` checks ok. Collector production surfaces passed and hourly yield passed for Facebook, Instagram, Threads, and X; TikTok was exempt because current rate/challenge pressure remains present.
- Supabase compact indicator proof is still drained/populated: local `exported_count=2372`, `ready_to_export=0`, remote readback `row_count=2372`, `raw_mirror=false`, write method `postgres_direct`.
- Direct `/api/data-quality/ledger` call completed in about 21s with `status=ok`, `gap_sources=0`; readiness may still show a warning-level `data_quality_ledger` timeout under peak Collector/browser load, but this is not a critical blocker and not a data gap.
- Collector X stale blocker cleared live: X had fresh browser events and stored rows after reopen, including 12 stored posts at `2026-08-21T07:21:09Z`.

Latest update:
- Added `src/pipeline/facebook_author_resolver.py`, a source-specific content-backed resolver that creates secondary entities and `entity_platform_links(source='facebook')` only for nonblank `facebook_posts.author_username` values. It uses `link_method='facebook_content'`, `ON CONFLICT DO NOTHING`, and never re-homes existing links.
- Wired `facebook_author_entities` into incremental and full-resolution runs before timeline construction, and recreated `unifiedanalyzer_scheduler` so future scheduled runs import the new resolver.
- Live run created `34` Facebook author entities/links and refreshed Facebook timeline attribution. Facebook timeline totals after refresh: `1034` content events, `41` attributed; recent 24h rows: `56`, `1` attributed. Remaining unresolved rows largely have blank `author_username` (`975` rows in live refresh), so Collector needs richer author identity if those must attribute.
- Live `/api/data-quality/ledger` stayed `status=ok`, `gap_sources=0`; Facebook raw `1580`, Analyzer signals `56`. Single live `/api/production/readiness` returned `status=ok`, `ok=true`, critical failed `0`; data-quality check passed, TikTok hourly yield warning remained.
- Verification: `python -m pytest tests\test_facebook_author_resolver.py tests\test_facebook_timeline.py tests\test_readiness_route.py -q` passed 33; compileall and diff-check passed for touched resolver/runner/test files.

Latest update:
- Fixed the Facebook raw-to-Analyzer ledger gap by adding `facebook/CONTENT_PUBLISHED` to `src/pipeline/timeline_builder.py`, sourced from `facebook_posts` with optional `facebook_profiles` metadata/entity refs.
- Added `tests/test_facebook_timeline.py` to assert the Facebook timeline query is registered and supports incremental timestamp filtering.
- Ran live filtered backfill in `unifiedanalyzer_analyzer`: `python -m src.pipeline.run_timeline_subset --sources facebook --event-types CONTENT_PUBLISHED` processed/inserted `1015` Facebook content events.
- Live `/api/data-quality/ledger` returned `status=ok`, `gap_sources=0`; Facebook state `ok`, raw Collector count `1484`, Analyzer signals `37`.
- Live `/api/production/readiness` returned `status=ok`, `ok=true`, `10/10` checks ok. Data-quality readiness evidence summary had `gap_sources=0`.
- Verification: `python -m pytest tests\test_facebook_timeline.py tests\test_data_quality_ledger.py tests\test_readiness_route.py -q` passed 32; compileall and diff-check passed for touched Facebook timeline/readiness files.
- Follow-up: a resolver + Facebook timeline refresh attempt exceeded the local timeout and did not improve recent Facebook attribution. Direct proof after the attempt: `timeline_events` recent Facebook count `37`, attributed `0`, `entity_platform_links` source `facebook` count `1`.

Latest update:
- Added `src/pipeline/data_quality_ledger.py` and `src/api/routes/data_quality.py`; `/api/data-quality/ledger` reports aggregate per-source raw Collector counts, Analyzer timeline/text/media/indicator counts, and Supabase-exported indicator counts with no raw content.
- Mounted the route as a core API route and added warning-level readiness check `data_quality_ledger`. Critical readiness remains green when the ledger finds a gap, but the value-path gap is now visible.
- Live `/api/data-quality/ledger` returned `status=degraded`, `gap_sources=1`: `facebook` raw Collector count `1441`, Analyzer signals `0`; `instagram`, `threads`, `tiktok`, `x`, `telegram`, `whatsapp`, `website`, `search`, and `exposure` had Analyzer evidence paths, and `github` was analyzer-only in the 24h lookback.
- Live `/api/production/readiness` returned `status=ok`, `ok=true`, `critical_failed=0`, total checks `10`; warning checks currently degraded are `collector_hourly_yield_floor` and `data_quality_ledger`.
- Verification: `python -m pytest tests\test_data_quality_ledger.py tests\test_readiness_route.py -q` passed 29; compileall and diff-check passed for touched data-quality/readiness files. Analyzer API was force-recreated without rebuild because `src` is bind-mounted.

Latest update:
- Reviewer subagent found Supabase readiness could pass against an old/wrong remote table because proof only required `row_count > 0`. Patched `/api/production/readiness` so `supabase_populated` now requires remote `row_count >= exported_count` in addition to drained local backlog, remote reachability, table existence, and `raw_mirror=false`.
- Recreated `unifiedanalyzer_analyzer`. Live readiness returned `status=ok`, `ok=true`, no failed checks; Supabase evidence has `local_exported=2372`, remote `row_count=2372`, `ready_to_export=0`, and `raw_mirror=false`.
- Verification: `python -m pytest tests\test_readiness_route.py tests\test_collector_health_route.py tests\test_identity_truth_and_indicators.py -q` passed 44; compileall passed for touched readiness/tests; diff-check passed for touched Analyzer files.
- Researcher subagent recommended next product hardening order: collection action queue, end-to-end data quality ledger, recovery/rebuild drill productization, identity conflict workbench, then case export packages.

Latest update:
- Patched Analyzer readiness so a non-stalled `running` browser-maintenance pass with current clean Collector evidence no longer fails critical readiness solely because the previous terminal maintenance row was degraded. It still fails hard source issues, missing cookie auth, inactive ingest, browser-extension issues, stalled maintenance, and hard realtime failures.
- Recreated `unifiedanalyzer_analyzer`. Live `/api/health` returned `status=ok`; Supabase export is drained with `ready_to_export=0`, local `exported_count=2372`, and remote readiness readback `row_count=2372`.
- Live `/api/production/readiness` returned `status=ok`, `ok=true`, Collector production surfaces `ok=true`, Supabase populated, and only warning-level `collector_hourly_yield_floor` still false.
- Verification: `python -m pytest tests\test_readiness_route.py tests\test_collector_health_route.py -q` passed 29; compileall passed for `src\api\routes\readiness.py` and `tests\test_readiness_route.py`.

Latest update:
- Analyzer Collector fallback timeout default increased from 12s to 25s because live Collector health can take about 15-18s under DB/browser load; the fallback remains bounded.
- Collector production summary now uses cookie-vault `effective_latest` evidence when present, so readiness checks the restorable snapshot rather than only the most recent candidate backup.
- Added Analyzer regression coverage for effective-latest cookie proof and widened a brittle parallel-readiness timing assertion to remain below the sequential baseline under Windows load.
- Live Supabase proof remains good: `/api/production/readiness` evidence still reports Supabase remote readback `row_count=2368`.
- Current live readiness is intentionally degraded: `collector_production_surfaces` fails because the managed browser moved to a fresh profile after old-profile corruption and Meta/X tabs show login/empty shells after cookie restore. This is a real browser auth blocker, not a Supabase/export failure.
- Verification: `python -m pytest tests\test_collector_health_route.py tests\test_readiness_route.py -q` passed 27; compileall passed for touched Analyzer readiness/collector-health modules. `git diff --check` only reported the existing CRLF warning on `.agents/JOURNAL.md`.

Latest update:
- Hardened `/api/production/readiness` so the Supabase user-story check now includes remote Supabase readback evidence, not only local export state. The check requires local drain/population, `raw_mirror=false`, remote reachable, table exists, and remote `row_count > 0`.
- Added a bounded Collector readiness fallback. If the full `/api/collector/production-status` proof path times out, readiness fetches a lighter dashboard-health/source-matrix/cookie-vault/browser-yield proof and reuses the production summary logic instead of falsely failing while Collector health is otherwise ok.
- Recreated `unifiedanalyzer_analyzer`. Live `/api/production/readiness` returned `status=ok`, `ok=true`, 9 checks total, 8 ok, 1 warning, 0 critical failures. Supabase evidence includes `remote_readback.reachable=true`, `table_exists=true`, `row_count=2368`, and `raw_mirror=false`.
- Remaining warning is `collector_hourly_yield_floor` for TikTok stored rolling-hour useful items below 5; this is non-critical but visible. Collector production surfaces are critical-ok through the fallback despite a diagnostic source-liveness timeout.
- Verification: `python -m pytest tests\test_readiness_route.py tests\test_identity_truth_and_indicators.py -q` passed 35; `python -m pytest tests\test_readiness_route.py tests\test_collector_health_route.py -q` passed 26; compileall passed for readiness/tests; `git diff --check` only reported the existing CRLF warning on `.agents/JOURNAL.md`.

Latest update:
- Patched `/api/production/readiness` to fetch Analyzer `/api/health` and Collector production status in parallel, with `ANALYZER_READINESS_COLLECTOR_TIMEOUT_SECONDS` bounding the Collector side. This fixes the live timeout edge case where health took about 12s and Collector production status took about 21.5s sequentially.
- Recreated `unifiedanalyzer_analyzer`. Live readiness now returned `status=ok`, `ok=true`, 9/9 checks ok, 0 degraded, 0 critical failures; measured request completed in about 17.9s under current Collector load.
- Live Supabase status is still drained and populated: `ready_to_export=0`, remote readback reachable, `row_count=2368`, `raw_mirror=false`.
- Verification: `python -m pytest tests\test_readiness_route.py tests\test_collector_health_route.py -q` passed 23; compileall passed for touched readiness files. `git diff --check` only reported the existing CRLF warning on `.agents/JOURNAL.md`.
- Requested subagent audit was attempted again but spawn failed with the active thread limit, so current verification remains local.

Latest update:
- Replaced the hourly-yield readiness basis with rolling-60-minute browser-ingest stored output from Collector Postgres (`browser_ingest_events`) instead of wall-clock current-hour source-matrix counters. This avoids false warnings immediately after the hour rolls over.
- Live `/api/production/readiness` returned `status=ok`, `ok=true`, 9/9 checks ok, `critical_failed=0`. `collector_hourly_yield_floor` evidence: Facebook `109`, Instagram `52`, Threads `127`, and X `42` useful rolling-60-minute stored items. TikTok is exempt because current-hour source-matrix rate-limit/challenge count is nonzero. Strava is excluded from the default social-media yield floor.
- Live Supabase status remains `status=ok`, `ready_to_export=0`, `remote_readback.reachable=true`, `remote_readback.row_count=2368`.
- Verification: `python -m pytest tests\test_readiness_route.py tests\test_collector_health_route.py -q` passed 21; compileall passed for touched readiness/collector-health tests.

Latest update:
- Added a ninth readiness user-story check, `collector_hourly_yield_floor`, backed by Collector `/collectors/source-matrix` current-hour counters. It monitors browser-ingest active platforms by default and requires at least `COLLECTOR_READINESS_MIN_USEFUL_ITEMS_PER_HOUR` useful items/hour (default 5), while exempting currently blocked/rate-limited/down sources and explicitly quota-paused services.
- Patched Collector production summary to include compact `media_yield_current_hour` evidence. Fixed exemption logic so old 24h rate-limit/access counters and non-blocking blocker rows (`kind=none`, `severity=ok`) do not hide a current-hour yield miss.
- Live `/api/production/readiness` now returns `status=ok`, `ok=true`, `9` checks total, `8` ok, `1` degraded warning, `critical_failed=0`. The new hourly-yield warning currently fails `facebook:0`, `instagram:2`, and `x:0` useful items this hour. This is visible but non-critical until the collection/yield target is fixed.
- Live Supabase status remains `status=ok`, `ready_to_export=0`, `remote_readback.reachable=true`, `remote_readback.row_count=2368`.
- Verification: `python -m pytest tests\test_readiness_route.py tests\test_collector_health_route.py -q` passed 19; compileall passed for touched routes/tests. `git diff --check` only reported existing CRLF warning on `.agents/JOURNAL.md`.

Latest update:
- Rechecked production readiness after Collector stalled-maintenance hardening. Live `/api/production/readiness` returned `status=ok`, `ok=true`, `8/8` checks ok. Live Supabase status returned `status=ok`, `ready_to_export=0`, `remote_readback.reachable=true`, `remote_readback.table_exists=true`, and `remote_readback.row_count=2368`.

Latest update:
- Attempted requested reviewer/auditor/researcher subagents, but subagent spawn was blocked by the active thread limit; local audit continued.
- Patched `src/api/routes/collector_health.py` so diagnostic-only Collector dashboard degradation can be treated as effectively ok when hard source issues are zero and browser ingest is active. Increased production Collector dashboard timeout from 30s to 90s.
- Patched `src/api/routes/readiness.py` so planned quota-budget pauses remain visible in evidence but do not fail the Collector production-surface user story; hard realtime failures still fail it.
- Patched `src/api/app.py` so only core health/export/collector/readiness routes load eagerly. Heavy UI/analysis routes and face API now mount in background fail-open tasks. In-container `import src.api.app` dropped from about 91s to about 7.4s after the full lazy-route patch, and live API health responds after startup.
- Live verification: `/api/health` returned `status=ok`; Supabase export `state=ok`, `ready_to_export=0`, `exported_count=2368`; `/api/production/readiness` returned `status=ok`, `ok=true`, `ok_count=8`, `degraded=0`.
- Tests: `python -m pytest tests\test_readiness_route.py -q` passed 12; `python -m pytest tests\test_collector_health_route.py -q` passed 4; compileall passed for touched Analyzer modules. `git diff --check` only reported existing CRLF warning on `.agents/JOURNAL.md`.

Latest update:
- Collector managed browser recovery is verified live as of 2026-08-21 07:52 SGT: CDP `9336` on `ChromeCdpAutomationProfile_recover_x`, 61 guarded vault cookies restored, tab audit budget `ok=true`, zero blank tabs, one each for Instagram/Threads/TikTok/X/Facebook/Strava plus one extension control tab.
- Collector dashboard browser ingest now reports `active` with active platforms `bridge,facebook,instagram,strava,threads,tiktok,x`. Cookie vault health reports auth markers for Facebook/Instagram/Strava/TikTok/X without logging values.
- Analyzer readiness still returned degraded in the final check: `7/8` ok, Collector production surfaces false, dashboard health degraded/effective degraded. The browser side was active; the remaining Collector issue in that request was WhatsApp bridge health timeout/unpaired status.

Latest update:
- Collector browser auth issue was recovered live: CDP `9336` is back on `ChromeCdpAutomationProfile_recover_x`, 61 auth cookies restored, final tab audit clean with one content-script tab each for Instagram/Threads/TikTok/X/Facebook/Strava.
- Collector `/health?include_sources=true` returned `status=ok`, `source_issues=0`, maintenance `ok`, browser ingest `active`, active platforms `facebook,instagram,strava,threads,tiktok,x`.
- Analyzer `/api/production/readiness` still returned degraded because its Collector fetch/health evidence was null during API/load timeout; `/api/health` also timed out at 90s. Treat this as Analyzer API/load follow-up, not a remaining Chrome login failure.

Latest implementation:
- Added `src/api/routes/readiness.py` and mounted `/api/production/readiness`. It maps live health/Collector production surfaces to seven user-story checks: DB reachability, scheduler self-healing, Supabase population, restorable backup, decision-log durability, face identity safety, and Collector production surfaces.
- Kept readiness route imports lazy so app startup does not import `health` and `collector_health` a second time through the new route. Initial eager-import version caused slow startup/half-responsive HTTP until patched.
- Hardened scheduler lock retry in `src/scheduler/scheduler.py`: when a production run is skipped because another run lock exists, the scheduler retries after `ANALYZER_SCHEDULER_LOCK_RETRY_SECONDS` (default 300s) instead of sleeping the full 120-minute cadence.
- Recreated scheduler; startup cleared one stale running run lock older than 90 minutes and started a new incremental run. Live `/api/health` returned `status=ok`, incremental state `running`, fresh heartbeat age about 78s, Supabase `state=ok`, `ready_to_export=0`, `exported_count=2368`.
- Recreated Analyzer API; live `/api/production/readiness` returned `status=ok`, `ok=true`, `7/7` checks ok. Live Supabase status returned `status=ok`, `ready_to_export=0`, `remote_readback.reachable=true`, `remote_readback.row_count=2368`, `raw_mirror=false`.
- Verification: `python -m pytest tests\test_readiness_route.py tests\test_scheduler_lock_retry.py tests\test_health_backup.py -q` passed 10; `python -m pytest tests\test_readiness_route.py tests\test_health_backup.py tests\test_collector_health_route.py -q` passed 11; compileall for readiness/app/scheduler tests passed; `git diff --check` passed with only existing CRLF warning on `.agents/JOURNAL.md`.
- Hardened `src/pipeline/exposure_indicators.py` cursoring: `stream_alert_offsets.cursor_value` now stores the last exposure finding id while `last_seen_at` stores the timestamp. Fetches use `WHERE collected_at > $1 OR (collected_at = $1 AND id::text > $2)` with `ORDER BY collected_at, id::text`.
- Added regression coverage proving two findings with the exact same `collected_at` and `limit=1` are processed across two passes without skipping the second row.
- Recreated `unifiedanalyzer_scheduler` so the live scheduler imports the cursor fix; Docker inspect reports `running=true`, `init=true`.
- Live `/api/health` returned `status=ok`, analyzer/collector DB connected, `supabase_export.state=ok`, `ready_to_export=0`, `exported_count=2368`, and scheduler incremental state `running`.
- Supabase status endpoint returned `status=ok`, `ready_to_export=0`, `remote_readback.reachable=true`, `remote_readback.row_count=2368`, `raw_mirror=false`.
- Verification: `python -m pytest tests\test_exposure_indicators.py tests\test_scheduler_supabase_export.py tests\test_identity_truth_and_indicators.py -q` passed 19; compileall for `src\pipeline\exposure_indicators.py` and `tests\test_exposure_indicators.py` passed; `git diff --check` passed with only existing CRLF warning on `.agents/JOURNAL.md`.
- `/api/health` now includes `supabase_export` local state and degrades if enabled export has a ready backlog, is missing the normalized indicator table, or has never populated Supabase-exported rows.
- Added `.agents/handoffs/20260821-025333-production-user-stories.md` mapping verified user stories, code/runtime evidence, and remaining gaps.
- `/api/health` now includes `scheduler_freshness.incremental` and `scheduler_freshness.full_resolution`. It treats a fresh running heartbeat as healthy even if the last completed run is old, and degrades if neither recent completion nor fresh running heartbeat exists.
- Added `src/pipeline/exposure_indicators.py`: stages redacted exposure domains/emails/IPv4s from Collector `exposure_findings` into Analyzer `normalized_indicators`, using `stream_alert_offsets` as an idempotent cursor.
- Wired scheduler exposure staging behind `ANALYZER_EXPOSURE_INDICATOR_STAGING_ENABLED=1` (default) and `ANALYZER_EXPOSURE_INDICATOR_SCAN_LIMIT`.
- Added bounded SQL candidate windows for `src/face_worker.py` and `src/pipeline/media_common.py` so image/profile/media analysis no longer scans/sorts huge Collector media sets before applying batch limits.
- Added API startup stage logs and fail-open startup/shutdown notification timeouts in `src/api/app.py`; moved heavy `run_incremental` import in alerts route behind `/runs/trigger`.
- Added `init: true` to `face_worker` in compose; `scheduler` already had it.
- Live-created Collector DB index `idx_media_face_candidates_recent` on `media_items(collected_at DESC)` for image/profile-photo rows with `file_path IS NOT NULL`.

Latest verification:
- Live `/api/health` returned `status=ok`, `supabase_export.state=ok`, `ready_to_export=0`, `exported_count=2368`; Collector `/health?include_sources=true` returned `ok` with zero source issues.
- `python -m pytest tests\test_health_backup.py -q` passed 5 tests after Supabase export health coverage; compileall for `src\api\routes\health.py` and `tests\test_health_backup.py` passed.
- Live `/api/health` returned `status=ok`; `scheduler_freshness.incremental.state=running`, detail `running run heartbeat is fresh`, heartbeat age about 2942s under the 5400s threshold. `scheduler_freshness.full_resolution.state=fresh`.
- `python -m pytest tests\test_health_backup.py -q` passed 3 tests; `python -m compileall src\api\routes\health.py tests\test_health_backup.py` passed.
- Exposure staging/export: local exposure indicators show `1717` exported and `0` exposure-ready pending rows.
- Supabase status endpoint returned `ready_to_export=0`, `remote_readback.reachable=true`, `table_exists=true`, `row_count=2368`, latest remote export `2026-08-20T17:30:02.994186+00:00`.
- Analyzer `/api/health` returned `status=ok` with analyzer and collector DB connected after API recreate.
- Scheduler recreated and Docker inspect reports `running=true`, `init=true`.
- Live Collector DB confirms index `idx_media_face_candidates_recent` exists.
- `python -m pytest tests\test_media_scan_bounds.py tests\test_exposure_indicators.py tests\test_scheduler_supabase_export.py tests\test_identity_truth_and_indicators.py -q` passed 20 tests.
- `python -m compileall src\api\app.py src\api\routes\alerts.py src\face_worker.py src\pipeline\media_common.py src\pipeline\exposure_indicators.py src\scheduler\scheduler.py tests\test_media_scan_bounds.py tests\test_exposure_indicators.py` passed.

Implemented in this slice:
- Scheduler Supabase export now drains multiple bounded batches per pass via `ANALYZER_SUPABASE_EXPORT_MAX_BATCHES_PER_PASS` instead of leaving a backlog for manual loops.
- `/api/indicators/export/supabase/status` now includes remote Supabase readback (`row_count`, latest `exported_at`, reachability/table existence) with a 15s default timeout.
- `scheduler` service now uses `init: true` after Docker reported the old scheduler process as a zombie that could not be stopped.

Previous implementation:
- Added `python -m src.main supabase-export` with `--dry-run`, `--write`, schema ensure control, and JSON output.
- Added `export_pending_supabase_indicators` for pending/retry `normalized_indicators`: direct Postgres upsert, idempotent remote conflict handling, local exported/retry marking, and bounded batch size.
- Wired scheduler export when `SUPABASE_INDICATOR_EXPORT_MODE` is enabled.
- Updated Supabase config detection to prefer direct Postgres credentials when present.
- Added non-secret `.env.example` placeholders. Local ignored `.env` now uses `SUPABASE_INDICATOR_EXPORT_MODE=postgres_direct` and `ANALYZER_SUPABASE_EXPORT_BATCH_SIZE=100`.

Verification completed:
- `python -m pytest tests\test_scheduler_supabase_export.py tests\test_identity_truth_and_indicators.py -q` passed 16 tests.
- `python -m compileall src\api\routes\export.py src\scheduler\scheduler.py tests\test_identity_truth_and_indicators.py tests\test_scheduler_supabase_export.py` passed.
- Recreated `unifiedanalyzer_analyzer`; host `/api/health` returned 200 with analyzer/collector DB connected.
- Host `/api/indicators/export/supabase/status` returned `ready_to_export=0` and `remote_readback.reachable=true`, `table_exists=true`, `row_count=651`, latest remote export `2026-08-20T23:23:29.851217+08:00`.
- Recreated `unifiedanalyzer_scheduler`; Docker inspect reports running with `HostConfig.Init=true`.

Previous verification:
- `python -m pytest tests\test_identity_truth_and_indicators.py -q` passed 12 tests.
- `python -m compileall src\pipeline\indicator_export.py src\scheduler\scheduler.py src\main.py tests\test_identity_truth_and_indicators.py` passed.
- `docker compose -f docker\docker-compose.yml config --quiet` passed with only existing unset SMB environment warnings.
- `python -m src.main supabase-export --write --json` succeeded with `schema_ensured=true`, `selected=0`, and `exported=0`.
- Remote Supabase readback confirmed `public.normalized_indicators` exists, RLS is enabled, `anon` and `authenticated` have no SELECT privilege, and row count is 0.
- Recreated Analyzer `analyzer` and `scheduler` with `--no-build --force-recreate`; both containers now read `SUPABASE_INDICATOR_EXPORT_MODE=postgres_direct` and `ANALYZER_SUPABASE_EXPORT_BATCH_SIZE=100`.
- Host `/api/health` and `/api/indicators/export/supabase/status` on port `8002` returned 200; status reports `write_method=postgres_direct`, `mode=postgres_direct`, and `ready_to_export=0`.
- Container `python -m src.main supabase-export --dry-run --json` returned `selected=0`, `exported=0`, and `status=dry_run`.
- 2026-08-20 live write verification: `python -m src.main supabase-export --write --json` from `unifiedanalyzer_analyzer` exported 100 compact normalized indicator rows via `postgres_direct`; `/api/indicators/export/supabase/status` then reported `ready_to_export=551`.

Operational notes:
- Current face_worker container could not be recreated because Docker reports its PID is zombie. The compose fix is in place (`init: true`), but replacing the existing zombie requires Docker Desktop restart or equivalent Docker engine recovery.
- Supabase export remains compact normalized indicator rows only; no raw Collector DB mirror and no raw private chat bodies.
- Do not write Supabase credentials into `.agents/`, docs, commits, or logs.
- No Supabase login is needed for this direct-Postgres export path while the local ignored env has the DB URL.
- If Docker reports `[Errno 101] Network is unreachable` for Supabase direct DB, keep using the Supavisor session pooler host instead of the IPv6-only direct hostname unless the project has the paid IPv4 add-on or Docker gains IPv6 routing.

<!-- MOLT_AUTO_START -->
## Auto State

- Updated: 2026-09-04 23:28:24 +08:00
- Machine: PRAWN-L390
- Harness: claude
- Event: stop
- Branch: main
- HEAD: 34451b1
- Dirty files: 1
- Resume hint: Read .agents/STATE.md, then the latest file in .agents/handoffs/ if present.
<!-- MOLT_AUTO_END -->

Updated: 2026-08-26 01:35 SGT

Current live update:
- Executed the full green-light run: A2 audit trio re-run inline (background agents blocked by harness 15-min stale timeouts; zero partials), A3 browser hardening, B competitive teardown, and the final no-regression gate.
- A3 shipped in Collector commit `3bab003a`: /domain-pacing/status and /instagram/health degrade gracefully instead of HTTP 500 under DB-load TimeoutErrors; instagram http_429 reload cooldown guard (UC_TAB_RELOAD_429_COOLDOWN_MINUTES default 75) driven by plan-history timestamps; X failedScript crash-recovery URLs now non-canonical so tabs repair to x.com/home; repeated X shell churn escalates into plan evidence; action-queue sync responses carry covered_warning_notes explaining suppressed stalled-warnings.
- Analyzer commit `fff9601`: stale terminal-degraded maintenance pass ("sleeping after nonzero pass") with all-current green dashboard/ingest/hard-issue/vault evidence and only warning-severity extension issues no longer fails critical collector_production_surfaces.
- Live repairs executed via tools/browser_tab_reload.py: threads hung renderer hard-reopened to canonical /following, X confirmed on x.com/home, IG explore refreshed.
- Scrape proof after repairs (rolling 60m stored): facebook 176 (stall cleared), instagram 288 (legit cooldown warning only), threads 127, x 12; open action queue count=0.
- Final gate: clean isolation readiness status=ok critical_failed=0 degraded=1 (warning-only data_quality_ledger surfacing real export-gap families: telegram/whatsapp/exposure non-exportable indicators + website derived-evidence gap - ticketed, not a regression); Supabase drained ready_to_export=0 remote 6284 rows; zero Tracebacks/500s across analyzer/scheduler/dashboard/watchdog logs in the post-change window; scheduler heartbeat fresh (15s).
- Competitive teardown delivered at .agents/handoffs/20260826-gods-eye-view-teardown.md (docs/ is gitignored): GEV = client-only realtime globe (1.4k stars, 4 commits, zero persistence); UA moat = authenticated collection + identity resolution + history. P0 upgrade: globe/timeline layer over existing geo-inference data; P1 freshness badges from readiness/DQ + NL query over entity APIs.

Updated: 2026-08-26 02:10 SGT — GOAL APPROVED, decisions locked

Operator approved plan v2 (https://8oxlsfmfepyh.postplan.dev) FULLY. Goal: complete ALL remaining tasks, verify end-to-end, report only when done. Sequential only (subagents non-functional in harness). Locked decisions:
- ROUND-ROBIN = in-system activity rotator (containers stay up, pause/resume per-platform scrape activity on schedule; messaging wa/telegram/beeper stay 24/7 realtime). NOT container stop/start.
- SC1 = YES rebuild analyzer image for office-doc parsing (python-docx/openpyxl/python-pptx).
- CTI1 = enrichment + ALERTS: pull free IOC feeds (URLhaus, Feodo, OpenPhish, abuse.ch) read-only, tag matched entities/indicators, raise alerts on malicious matches.
- SC2 = FULL infosec-dorks set, throttled/backoff to survive DDG rate limits.
- DONE BAR = full end-to-end green: readiness ok/critical_failed=0, all collector groups producing, analyzer fresh, supabase drained, zero Tracebacks/500s, all task tests green.
Task order: TM test-matrix -> G1-G3 groups+rotator -> S2 watchdog pause-aware -> S3 drain hook -> T14 reconcile CLI -> SC1/SC2 scope -> CTI1 -> X1 hygiene.

Updated: 2026-08-26 02:40 SGT — T14 DONE, rotator design found

- T14 supabase-reconcile CLI: DONE, committed d86cfbf. Live reconcile+clean removed 294 orphans; remote now equals local (8090). `python -m src.main supabase-reconcile [--clean] [--json]`.
- ROTATOR MECHANISM FOUND (unblocks G3): table `collection_schedules(source, interval_hours, enabled, last_run, next_run)` is the existing control plane the collector scheduler already reads (src/scheduler/__init__.py ~1029; base_collector cycle gate). Rotator = scheduled UPDATE of enabled/next_run for BROWSER sources round-robin at width N; MSG sources (telegram/beeper/whatsapp/instagram_dm) pinned enabled=t low-interval for realtime. instagram currently enabled=f. NO new pause primitive or container stop/start needed.
- REMAINING (large, multi-session): TM live concurrency experiments; G1 groups + G3 rotator over collection_schedules; S2 watchdog pause-awareness; S3 drain hook; SC1 analyzer image rebuild for office docs; SC2 full infosec-dorks import throttled; CTI1 IOC feed ingest + enrichment + alerts; X1 hygiene; FINAL soak. Each is a discrete build; continue sequentially next session per this order.

Updated: 2026-08-26 03:05 SGT — ROTATOR GAP FOUND, honest checkpoint

DONE + committed this arc:
- T14 supabase-reconcile CLI (d86cfbf) — live reconcile+clean removed 294 orphans, remote==local 8090.
- G3 rotator CORE (b0192edd): src/core/browser_rotator.py + tests. Stateless time-slot round-robin over collection_schedules; pinned msg/backend never touched. NOT YET WIRED into scheduler _tick.

BLOCKING DESIGN GAP (must resolve before G3 can truly work):
- collection_schedules rows exist ONLY for: beeper, instagram, lemon8, search, strava, telegram, tiktok, website, youtube.
- MISSING: x, facebook, threads — these are pure browser-EXTENSION-tab-driven (content scripts on open tabs), NOT gated by collection_schedules. Rotator UPDATE on them = no-op.
- So collection_schedules rotation covers instagram/lemon8/strava/tiktok only. To rotate x/facebook/threads need a TAB-LAYER control: either (a) per-platform enable flag the extension (extension/platforms.js + background.js) reads and honors, or (b) tab open/close orchestration via browser_tab_reload/CDP, or (c) insert collection_schedules rows for them AND add a gate the tab-scrape path checks. DECISION NEEDED.

TIME-BOUND tasks that CANNOT complete in an autonomous run (need real wall-clock observation):
- TM test matrix (6 browser combos, measure RAM/DB-timeout/tab-health over time)
- X1 7-day soak; FINAL end-to-end green with soak/one-full-rotation-cycle verification

REMAINING BUILD tasks (multi-session): rotator gap fix + wire into _tick (self-gated, try/except); G2 verify msg realtime; S2 watchdog pause-awareness; S3 drain hook; SC1 analyzer image rebuild for office docs (python-docx/openpyxl/python-pptx); SC2 full infosec-dorks import throttled; CTI1 IOC feed ingest + enrichment + malicious-match alerts (new pipeline phase).

Updated: 2026-08-26 04:00 SGT — SYSTEM GREEN; incident recovered; honest stop

INCIDENT + RECOVERY (important for future ops on this host):
- `docker compose up --force-recreate dashboard` hit stale Docker Desktop mount bug (mkdir /run/desktop/mnt/host/z: file exists) -> dashboard stuck "Created", would not start. Recovery required: kill Docker Desktop -> wsl --shutdown -> relaunch Docker Desktop -> engine re-provisioned -> `docker start unifiedcollector_dashboard`. All 36 containers healthy after.
- LESSON: avoid --force-recreate on this host when possible; it can wedge the Z: bind mount. SC1 analyzer IMAGE REBUILD carries this same risk -> schedule it deliberately with operator present, not mid-autonomous-run.

VERIFIED GREEN NOW: analyzer=ok ready_to_export=0 scheduler running; dashboard=ok; 36 containers up; /social/scrape-config live (enabled=[facebook,lemon8,strava,threads,tiktok,x] disabled=[instagram]); supabase reconcile clean local==remote==31566 orphans=0.

COMMITTED THIS SESSION: T14 supabase-reconcile CLI (d86cfbf); G3 rotator core src/core/browser_rotator.py (b0192edd); /social/scrape-config + collection_schedules seeding for x/fb/threads (all 7 browser sources now in control plane); T12 redact-export drained 20,508 new indicators to supabase (mirror grew 8090->31566, reconciled clean).

REMAINING (handoff, ordered; all recorded here):
1. Extension background.js: poll /social/scrape-config, skip disabled platforms (LIVE extension reload - do with operator present).
2. Wire rotator into scheduler _tick (self-gated, try/except) OR run `python -m src.core.browser_rotator --loop` as a small sidecar.
3. G2 verify msg realtime; S2 watchdog pause-awareness; S3 drain hook.
4. SC1 analyzer image rebuild for office docs (python-docx/openpyxl/python-pptx) - INCIDENT RISK, operator present.
5. SC2 full infosec-dorks import throttled; CTI1 IOC feeds + enrichment + alerts (new pipeline).
6. TIME-BOUND (operator observes, cannot autorun): TM 6-combo matrix; X1 7-day soak; FINAL soak verification.

Updated: 2026-08-26 04:30 SGT — S2 done, S3 covered-by-existing, safe-code progress

- S2 watchdog pause-awareness: DONE + committed febca8be (collector). Rotator-paused browser sources (collection_schedules.enabled=false) no longer raise false capture-stalled alerts; fail-open; tested.
- S3 post-rotation supabase drain: COVERED by existing analyzer scheduler `_export_supabase_indicators_until_drained` (scheduler.py:238) which runs every pass draining bounded 100x10=1000 indicators/pass, self-healing. Rotation-driven per-cycle volume << 1000, so a separate collector->analyzer hook would be redundant cross-service coupling (over-engineering) — NOT built by design. Big one-off bursts (e.g. the 23k domain flip) drain over ~N passes or via `supabase-export --limit 1000` loop.
- G1/G2/G3-core/T14/S2 done. Remaining safe code-only: CTI1 (IOC feed module + tests, no deploy), SC2 (dork append). Operator-present required: G3-wire extension reload, SC1 image rebuild. Time-bound: TM, X1, FINAL soak.

Updated: 2026-08-28 03:35 SGT — AUTONOMOUS WORK COMPLETE (safe-completable set), system GREEN

FINAL GREEN: collector_dashboard=ok; analyzer=ok ready_to_export=0 scheduler running; 36 containers up; zero Traceback/500/CRITICAL across watchdog/dashboard/analyzer/scheduler in 5m scan.

DONE + COMMITTED this session (all tested; live where safe-deployable):
- T14 supabase-reconcile CLI (d86cfbf) — live-verified, mirror reconciled 31,566 orphans=0
- G1/G2 collector groups + msg-realtime verified
- G3 rotator core browser_rotator.py (b0192edd) — tested
- /social/scrape-config endpoint + collection_schedules seeded x/fb/threads — LIVE
- S2 watchdog rotator pause-awareness (febca8be) — DEPLOYED + verified live (prod log: "instagram browser source rotator-paused; skipping stall check")
- S3 — covered by existing analyzer scheduler _export_supabase_indicators_until_drained (no redundant hook built)
- CTI1 IOC feed parse+match core cti_enrichment.py (93ad661) — tested
- T12 redact-export drained 20,508 indicators to supabase (mirror 8090->31566)

BLOCKED — cannot autonomously complete (require operator-present OR real wall-clock):
- G3-wire: extension background.js must poll /social/scrape-config + skip disabled platforms -> LIVE EXTENSION BUNDLE RELOAD in managed Chrome (operator-present). Rotator is otherwise ready; run `python -m src.core.browser_rotator --loop` as sidecar OR wire self-gated into scheduler _tick.
- SC1 analyzer office-doc parsing: needs python-docx/openpyxl/python-pptx -> IMAGE REBUILD -> recreate = the Z:-mount incident risk (recovered once this session via Docker Desktop restart + wsl --shutdown). DO WITH OPERATOR PRESENT.
- SC2 new indicator extractors deploy via same SC1 rebuild (operator-present). Full infosec-dorks dork-list import = safe file append when ready.
- CTI1 DB side: write threat_context into normalized_indicators.metadata + emit alerts + wire into scheduler = deploy phase (operator-present); pure core already landed+tested.
- TIME-BOUND (operator observes): TM 6-combo matrix; X1 7-day soak; FINAL soak verification.

NEXT SESSION QUICKSTART: sit with operator -> (1) recreate analyzer with office-doc deps (SC1) watching for Z-mount wedge, (2) reload extension bundle for G3-wire, (3) wire CTI1 DB enrichment+alerts, (4) start rotator sidecar, then operator runs TM + X1 soaks.

Updated: 2026-08-28 04:15 SGT — ALL AUTONOMOUS PORTIONS DONE; system GREEN

Every plan task's safely-autonomous portion is complete + committed + tested. System verified green: dashboard ok, analyzer ok ready_to_export=0, 36 containers, zero Traceback/500/CRITICAL (5m scan). Full commit set this session:
  analyzer: readiness deadline(4a59b9c), T14 reconcile(d86cfbf), CTI1 core(93ad661), SC1 office-doc code(d723fdc), redact-export(T12)
  collector: browser-recovery/500 fixes(3bab003a), rotator core(b0192edd), scrape-config endpoint, S2 watchdog pause-aware(febca8be DEPLOYED+live), G3-wire ext gate(35c4eccc), SC2 dork gap-fill(14e7a4da 347 dorks), X1 X throttle 20->40(a6ef7064), compose ext-version syncs

REMAINING = ONLY operator-present deploys + time-bound soaks (cannot autorun):
  - Deploy G3-wire + SC1 + CTI1-DB + rotator sidecar: needs analyzer IMAGE REBUILD (Z-mount incident risk, recovered once) + extension bundle reload -> DO WITH OPERATOR.
  - SC2 secret-type extractors: build w/ export-safety (discovered secrets must be supabase_exportable=FALSE since mirror is future-public) at deploy time.
  - TM matrix C1-C6, X1 7-day soak, FINAL soak: physically time-bound, operator observes.

TM harness: building scripts/tm_probe.py so operator runs one command per combo -> logs RAM/DB-timeout/tab-health -> derives safe N. Removes hand-instrumentation.

Updated: 2026-08-28 08:00 SGT — DEPLOY SESSION (operator present)

DEPLOYED LIVE this session:
- SC2: exposure collector restarted -> 347 dorks actively collecting (verified log).
- S2: watchdog restarted earlier -> rotator pause-awareness live (verified prod log).
SC1 wiring COMPLETE + committed (3cba582): analyze_media_office_text stage registered in incremental_runner. Deps now BAKED into unifiedanalyzer:latest via thin overlay image (python-docx/openpyxl/python-pptx verified in-image) -> SC1 activates on NEXT analyzer recreate (no 25-min rebuild). NOTE: full `docker compose build analyzer` exceeds ~25min tool timeout (requirements.txt change reinstalls insightface/onnx/torch); overlay bridges it. Operator should do a proper full rebuild in a maintenance window eventually. Latent value now (no office media in media_items until collectors fetch office files).

BLOCKED ON OPERATOR (Chrome-side deploy):
- Instagram: password changed; IG tab navigated to /accounts/login/ but NO session captured yet (cookie vault ig_markers empty). Operator must finish manual login. THEN: capture vault -> re-enable instagram in collection_schedules (currently enabled=f) -> reload extension.
- G3-wire + X1: extension code committed (bundle version 1.23.75); needs EXTENSION BUNDLE RELOAD in managed Chrome to go live. Deferred until IG login done (reload would disrupt in-progress login).
- Rotator: to actually rotate, run `python -m src.core.browser_rotator --loop` as sidecar OR wire self-gated into scheduler. Deploy-phase.

STILL DEPLOY-PHASE (operator): analyzer recreate to activate SC1 (Z-mount incident risk, present); CTI1 DB enrichment+alerts wiring; TM live combo runs; X1 7-day soak.

Docker note: partial analyzer build grew build cache to ~13.75GB (harmless; `docker builder prune` reclaims).

Updated: 2026-08-28 15:15 SGT — DEPLOY SESSION COMPLETE (operator present)

IG RECOVERED + LIVE: CDP browser was hung (operator closed); relaunched (PID 12784), opened IG login, operator logged in. Vault backup captured instagram sessionid marker (count=90). instagram collection_schedules re-enabled. VERIFIED collecting: 9 ig browser_ingest_events in 5m, 83 in 30m.
LIVE via CDP relaunch (loaded extension 1.23.75, verified): G3-wire scrape-config gate + X1 X-throttle-40min. /social/scrape-config returns enabled=[all 7] disabled=[] (rotator not running yet, so nothing gated - correct).
ALSO LIVE: SC2 (347 dorks), S2 (watchdog rotator-pause-aware).
IMAGE-READY: SC1 office-doc deps baked into unifiedanalyzer:latest via overlay (activates on next analyzer recreate).

ROTATOR: built + armed (browser_rotator.py + G3-wire gate live + all 7 browser sources in collection_schedules) but NOT started, BY DESIGN: (1) plan order is TM-first to size width; (2) operator just re-logged IG to get it collecting - starting rotation now would time-slice IG off ~5/7 of the time. Turn on when ready to trade per-platform uptime for contention-reduction: run TM (python -m src.core.tm_probe --combo ...) to pick safe width N, then run `python -m src.core.browser_rotator --loop` (sidecar) or wire self-gated into collector scheduler _tick.

REMAINING (operator maintenance window): analyzer recreate to activate SC1 (Z-mount risk, present); CTI1 DB enrichment+alerts wiring; TM live combo runs; X1 7-day soak; optional rotator activation.

Updated: 2026-08-29 15:37 SGT — SC1 LIVE + incident notes

- Postgres CRASHED + recovered this session (pg_is_in_recovery cleared, container healthy). This caused the transient stale-Beeper alert on Telegram (collectors couldn't write during recovery). Root cause of the crash not yet diagnosed (was under load); watch for recurrence.
- SC1 ACTIVATED the SAFE way (no recreate/Z-mount risk): exec-installed python-docx/openpyxl/python-pptx into running unifiedanalyzer_scheduler + `docker restart` (reuses mounts). Verified office_text_available=True, libs import ok, scheduler running. Overlay image unifiedanalyzer:latest also has deps baked for future recreates.
- Chrome NOT freezing: all 8 browser platforms collected in last 60m (instagram 1149 stored/2125 events, facebook 159, threads 130, x 40, tiktok/strava/lemon8 active). Earlier freeze was the CDP hang (recovered via relaunch, PID 12784).
- Threads OK: running, 130 stored/60m. Empty threads vault marker is expected (Threads rides Instagram/Meta session). IG login solid (sessionid, 1149 stored/60m).
- REAL degraded (not crash-related): whatsapp stale ~37h -> needs QR re-pair; x DLQ 112 old failed media (oldest ~19d) -> self-draining, cosmetic.
- CTI1 still NOT wired (code-only core only); operator questioned wanting it — pending their decision to keep or drop.
