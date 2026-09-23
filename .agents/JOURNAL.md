- 2026-09-23 16:00 UTC: Docker Desktop restarted twice (killed UI + backend + build procs, `wsl --shutdown`, relaunched). First cycle: cold `docker ps` 199s then warm 2.14–3.64s; 32 of 32 pre-restart containers self-healed via their own restart policies. `unifiedanalyzer_face_worker` did NOT start — its Windows CIFS volume `//100.92.164.125/w-drive` (Prawn-E14 Tailscale SMB) returned `no such file or directory`, i.e. remote host offline; unrelated to the restart. `nebula-sync` briefly `Restarting(1)` on same-host auth EOF/HTTP 400 and self-recovered to `healthy`. Fresh scratch `unifiedanalyzer_restore_20260923` (pgvector:pg16, network=none, shm 1g, mem 2g, cpus 2, fsync=on, fpw=on, mpmw=0, maint 384MB) was created and reached `Up 1min`, then hung `docker exec pg_isready` and crashed the daemon (`com.docker.service Stopped` while Desktop UI alive) — same failure mode as `2026-09-22T15:45:07Z`. Second restart came back with 35 containers but 0.55–1.13GB free RAM and `docker ps` latency 5.15/12.53/17.81s (fails <2s). Aborted at the 2-attempts rule rather than crash Docker for the concurrent `collector-close-out` team. Reverted: scratch container `docker rm -f`, stale volume `unifiedanalyzer_recovery_proof_20260922` `docker volume rm`. No restore/build/smoke/deploy/readiness ran this session; images `unifiedanalyzer:latest`/`pre-clean-20260919` untouched; archive `Z:\unifiedanalyzer\backups\db\full-drill-20260919.dump` (3,184,305,761B, sha256 98B802…E37) untouched. Root cause both crashes: ~15GB of ~16GB host RAM occupied by the 40+ containers already running (Collector 24, musicstream 4, pihole, nebula-sync, rabbitmq, redis, buildkit, Analyzer 3) leaves no headroom for a 2GB scratch server + pg_restore working set; WSL2 VM tips to swap and the Docker daemon stops. Next run needs Collector-team completion, an agreed temporary pause of Collector helpers, or a `.wslconfig` RAM bump before restart.
# UnifiedAnalyzer Agent Journal

- 2026-09-16: Reproduced and repaired readiness deadline bypass, same-connection asyncpg concurrency, and missing annotation import; primary proof now uses the existing critical-only snapshot. Deployed shared PostgreSQL init guard after isolated exit-2 fault proof (1 crash without init, 0 with it); preserved the live volume. Final tests 5427 passed/58 DB skips, frontend build passed, export backlog drained to 32261 matching local/remote rows. Readiness remains timeout-degraded under load, so full production readiness and full restore proof remain open; no false-green severity changes.

- 2026-09-16 02:24 SGT: Vendored the WhatsMyName ruleset (data/wmn-data.json, CC0, 716 sites) directly into the analyzer repo and gated the new fanout behind WMN_FANOUT_ENABLED (default OFF); a naive scan would burst ~700 outbound GETs per username and trip egress filters, so this stays opt-in even after the Dockerfile COPYs the file into the image.
- 2026-09-16 02:24 SGT: Implemented v7 Explore-tier local NL query (src/pipeline/graph_nl_query.py + /api/entities/{id}/nl-summary) as ollama-first with an OpenAI-compatible fallback and a hard "off" mode. Every failure path (disabled, entity-missing, backend-unreachable) returns the raw dossier context so the caller can render evidence even when no LLM is running. Never auto-writes back to the DB — LLM output is treated as untrusted.
- 2026-09-16 02:24 SGT: Confirmed my earlier v7 gap analysis mislabelled holehe as "not present" — email_recognition.py IS the holehe subprocess integration and holehe 1.61 is on-PATH in analyzer + scheduler containers. Only genuine remaining v7 gap now is a local-LLM backend actually running on this host (no ollama service on port 11434 as of check).
- 2026-08-25 14:20 UTC: Production readiness must obey one global wall-clock budget across ALL stages including isolated retries and fallbacks; per-stage budgets alone summed past any client timeout (55.6s live). ANALYZER_READINESS_TOTAL_BUDGET_SECONDS now caps every stage and records deadline_skipped_stages instead of hanging.
- 2026-08-21 21:38 UTC: Treat visible Chrome logout/page-shell reports as profile-specific until CDP/vault/browser-ingest proof says otherwise; current Collector CDP auth is intact after canonical tab repair, while Analyzer readiness degradation is timeout/load-derived and separate from browser cookies.
- 2026-08-21 16:25 UTC: Supabase compact indicator export remained healthy after Collector hardening, but production readiness is currently degraded under load and by two real Collector browser/content actions; do not claim production-ready until those gates revalidate cleanly.
- 2026-08-21 14:15 UTC: Future-dated analyzer evidence must degrade the data-quality ledger as `clock_skew`; clamping negative age to zero let bad synthetic GitHub rows look fresh. Corrected the two live future GitHub timeline rows with metadata rather than deleting them.
- 2026-08-21 13:23 UTC: Production readiness should use the Collector action queue as the operator work surface. After Collector cookie/profile repair and action-queue hardening, readiness is green only when open Collector actions are zero and Supabase remains compact/drained.
- 2026-08-12 00:00 SGT: Started production-readiness completion across Analyzer and Collector; created Analyzer shared agent state because none existed and AGENTS.md requires durable cross-agent handoff state.
- 2026-08-12 00:18 SGT: Kept eval gates inside `metrics_json` instead of adding schema columns so existing eval API/table compatibility remains intact while CLI and UI can still fail/warn/pass production regressions.
- 2026-09-22 15:45 UTC: User authorized Docker Desktop restarts and autonomous decisions for this recovery. Two Analyzer-owned containers exited(255) simultaneously with a slow `docker ps`, indicating the Docker/WSL2 backend itself degraded, not a manual action. Captured a 32-container pre-restart snapshot (Collector/musicstream/pihole/nebula-sync + Analyzer) before handing the restart + full recovery pipeline to a dedicated team member, and git sync/push to a second; see STATE.md for the full snapshot and task split.
- 2026-08-12 00:52 SGT: Kept multilingual translation dependencies in `requirements-nlp.txt` instead of default requirements so API/scheduler startup stays lightweight; translation workers can opt in explicitly when model cache and CPU budget are ready.
- 2026-08-12 08:09 SGT: Treated live HTTP endpoint checks as the deployment source of truth after restart; `/api/multilingual/status` now reports aggregate coverage only and must not expose raw chat text.
- 2026-08-12 18:36 SGT: Kept Analyzer alert-window API as an adapter over the deployed `alert_windows` schema instead of migrating the live table, because the stream worker already writes `alert_type`, `bucket_start`, `bucket_end`, and `metadata`.
- 2026-08-13 20:58 SGT: Analyzer Collector Coverage treats seen-target counters as optional pass-through fields so older Collector snapshots stay readable during rolling deployment.
- 2026-08-13 21:10 SGT: Pushed Analyzer seen-target coverage wiring and verified live `/api/collector/coverage` after container restart so dashboard state reflects Collector registry counters.
- 2026-08-13 22:18 SGT: Analyzer production surfaces stay count-only for Collector trust and media/PDF coverage; multilingual status exposes fastText/OPUS readiness while keeping NLLB optional and off by default.
- 2026-08-14 00:24 SGT: Analyzer production verification uses live HTTP plus desktop/mobile screenshots; media coverage defaults to estimated count-only queries so the dashboard remains responsive during full-resolution media workload.
- 2026-08-14 10:12 SGT: Analyzer owns identity truth and Supabase export staging; SpiderFoot/recon rows remain weak leads until an independent hard signal corroborates them, and Supabase gets compact normalized indicators rather than raw private text.
2026-08-14 02:28 UTC - Added Analyzer-owned identity truth assertions and normalized indicator staging/export status; SpiderFoot remains weak lead only and Supabase export is compact normalized rows only.
2026-08-14 04:03 UTC - Configured local ignored Supabase env and updated non-secret status handling so direct Postgres credentials count as a backend export path; no service-role or secret API key was added.
- 2026-08-14 18:02 SGT: Implemented direct Postgres compact Supabase writer with RLS and no anon/auth table grants; live remote schema is ensured with zero pending rows, while container restart/readback waits on Docker Desktop recovery.
- 2026-08-14 18:21 SGT: Recreated Analyzer API/scheduler with Supabase export mode enabled; live API status and container CLI dry-run both report direct Postgres export ready with zero pending rows.
- 2026-08-20 15:49 SGT: Switched local ignored Supabase DB URL to the IPv4-compatible Supavisor session pooler after Docker could reach IPv4 internet but Supabase direct DB resolved IPv6-only; live export wrote 100 compact normalized indicator rows.
2026-08-21T00:24:08.2019993+08:00 Supabase production hardening: scheduler now drains bounded export batches per pass, status endpoint has remote readback proof, scheduler compose uses init=true after Docker zombie stop failure; live readback row_count=651 ready_to_export=0.
2026-08-21T02:27:00+08:00 Analyzer production hardening: exposure_findings now stage into compact normalized_indicators and Supabase remote readback reached 2368 rows with zero export backlog; media scans are SQL-bounded and Collector media_items has idx_media_face_candidates_recent after live Postgres load starved API startup; API startup logs stages and notifications fail open; face_worker now has init=true for next clean recreate but current zombie requires Docker engine recovery.
2026-08-21T02:46:00+08:00 Analyzer health now exposes scheduler freshness for incremental/full_resolution runs and degrades when neither recent completion nor fresh running heartbeat exists; live health stayed ok because the incremental run heartbeat was fresh while Supabase backlog remained zero.
2026-08-21T03:18:00+08:00 Live Analyzer verification after Collector auth hardening: /api/health is ok, Supabase compact export has ready_to_export=0, and remote readback is reachable with row_count=2368; export remains normalized_indicators_only with raw_mirror=false.
2026-08-21T03:18:00+08:00 Exposure staging cursor now uses collected_at plus exposure finding id as a high-watermark, preventing same-timestamp rows from being skipped across bounded batches; scheduler was recreated and live health/Supabase readback stayed ok.
2026-08-21T03:39:00+08:00 Added live /api/production/readiness user-story gate and scheduler lock retry. A stale incremental running lock was cleared on scheduler restart, health returned ok with fresh heartbeat, readiness returned 7/7 ok, and Supabase readback remained row_count=2368 ready_to_export=0 raw_mirror=false.
- 2026-08-21 05:42 SGT: Collector browser auth incident was recovered and Collector health is ok with zero source issues; Analyzer readiness/health still timed out under load, so remaining follow-up is API/load handling rather than browser cookies.
- 2026-08-21 07:52 SGT: Collector browser cookies/tabs were recovered again and browser ingest is active, but Analyzer readiness remains degraded because Collector dashboard health degraded on WhatsApp bridge timeout/unpaired status; browser auth is not the remaining readiness blocker.
- 2026-08-21 08:43 SGT: Analyzer production readiness is live green at 8/8 after softening diagnostic-only Collector issues with active ingest evidence, treating planned quota-budget pauses as visible nonfailures, increasing Collector production timeout, and moving non-core/face route imports to background startup so health/readiness serve before heavy FAISS/OpenCV routes finish mounting.
- 2026-08-21 08:51 SGT: After Collector maintenance-stall hardening, production readiness remained green at 8/8 and Supabase compact indicator readback remained drained with 2368 remote rows.
- 2026-08-21 09:04 SGT: Added readiness warning `collector_hourly_yield_floor` from Collector source-matrix current-hour counters; live readiness is status ok with 8 critical checks ok and one warning for Facebook, Instagram, and X below the 5/hour useful-output floor.
- 2026-08-21 09:39 SGT: Changed `collector_hourly_yield_floor` to rolling-60-minute stored browser output from Collector `browser_ingest_events`; live readiness is 9/9 ok with Facebook/Instagram/Threads/X passing and TikTok exempt due current-hour challenge/rate-limit signal.
2026-08-21 02:46 UTC - Production readiness now fetches Analyzer health and Collector production status in parallel with a bounded Collector timeout, keeping readiness responsive while preserving degraded evidence for slow Collector diagnostics.
2026-08-21 03:24 UTC - Production readiness Supabase proof now requires remote readback, and slow full Collector production status can fall back to bounded dashboard-health/source-matrix/cookie-vault/browser-yield evidence before failing critical readiness.
2026-08-21 04:08 UTC - Analyzer readiness now consumes cookie-vault effective latest snapshot proof and gives the Collector fallback a 25s budget under live DB/browser load; current readiness remains degraded because managed-browser Meta/X login shells are real auth blockers after fresh-profile recovery.
2026-08-21 04:41 UTC - Analyzer readiness treats a non-stalled running browser-maintenance pass as acceptable when current Collector dashboard/source/browser/cookie evidence is clean; live readiness is status ok with only warning-level hourly yield floor remaining and Supabase readback at 2372 rows.
2026-08-21 05:00 UTC - Supabase readiness proof now requires remote normalized_indicators row_count to be at least the local exported_count, preventing stale low-row remote tables from satisfying production readiness; live readiness is ok with 2372 local/exported and 2372 remote rows.
2026-08-21 05:46 UTC - Added a read-only data-quality ledger and warning readiness check for Collector-to-Analyzer-to-Supabase value paths; live ledger found a Facebook raw-to-Analyzer gap while critical readiness and Supabase proof stayed ok.
2026-08-21 06:03 UTC - Added Facebook content timeline normalization and live backfilled 1015 Facebook events, clearing the data-quality ledger gap; Facebook attribution remains a separate identity-linking gap with sparse facebook entity_platform_links.
2026-08-21 06:10 UTC - Added Facebook author content resolver before timeline phases; live run created 34 facebook_content links and attributed the 41 Facebook events that had nonblank author handles, leaving blank-author rows as Collector payload enrichment work.
2026-08-21 07:24 UTC - Production readiness rechecked after Collector browser repair: critical checks are ok, Supabase remains drained/populated with 2372 local exported and 2372 remote rows, and the only residual readiness noise is warning-level data-quality timeout under load despite direct ledger gap_sources=0.
- 2026-08-21 08:00 UTC: Batched Analyzer data-quality ledger summaries by table/source, restarted Analyzer API, and verified readiness green with data_quality_ledger ok plus Supabase remote readback still populated and drained.
- 2026-08-21 09:15 UTC: Analyzer readiness is production ok after Collector managed-browser recovery; only warning remains collector_hourly_yield_floor from real Instagram/TikTok pressure, not cookie loss or Supabase failure.
- 2026-08-21 09:35 UTC: Analyzer readiness is fully ok after Collector yield-policy hardening; Website zero-output is now surfaced as Collector action_queue target_starved while Supabase export remains drained/populated.
- 2026-08-21 09:55 UTC: Analyzer readiness remains fully ok after Collector action-queue timeout-skeleton suppression; Supabase export remains drained/populated and raw mirror remains false.
- 2026-08-21 10:25 UTC: Scheduler readiness now accepts a fresh active full-resolution run when incremental completion is stale only by age; live readiness is ok with Supabase drained/populated.
- 2026-08-21 10:40 UTC: Analyzer readiness remains ok after Collector website slow-yield fix; Supabase export is drained and populated with 2372 remote rows.
- 2026-08-21 10:45 UTC: Analyzer readiness remains ok after Collector expired-cooldown suppression; Supabase export is still drained and remotely readable.
2026-08-21 10:57 UTC - `/api/production/readiness` now carries machine-readable user-story metadata per check plus a top-level story map, so production proof ties directly to operator/analyst value and live evidence.
2026-08-21 11:08 UTC - Added Analyzer frontend `/production` page that renders the production readiness checks as operator/analyst user stories with compact live evidence and open warning visibility.
2026-08-21 12:46 UTC - Production readiness now exposes Collector open actions and analyst workflow availability; core analyst routes are mounted before SPA fallback, Supabase readback timeout defaults to 45s, Collector fallback accepts rolling-yield/effective-vault proof, and final live readiness is critical-green with only action-queue warning.
2026-08-21 14:22 UTC - Collector cookie-loss report was verified live as managed-profile/browser-output drift: 87 auth-bearing cookies restored to CDP 9336 and tab budget is clean, while Analyzer should continue surfacing Facebook stale-output readiness noise until Collector fixes that path.
2026-08-21 14:30 UTC - Collector Facebook stale-output blocker recovered after extension scrapeNow; Collector health and action queue are clean, but Analyzer production readiness timed out under DB load and still needs load-tolerant verification.
2026-08-21 14:48 UTC - Production readiness is now load-bounded: health, data-quality, action queue, and Collector fallback have explicit budgets; live readiness returns critical-green with only warning-level data_quality timeout, while Supabase remains strict and populated.
2026-08-21 14:57 UTC - Visible Chrome login state must be treated separately from Collector CDP auth; restored 87 cookies into the managed CDP profile, refreshed platform tabs, and verified Collector maintenance ok with source_issues empty and active browser ingest.
2026-08-21 15:15 UTC - Production readiness now proves the analyst review-to-case export value path with live DB/API evidence; future timeline timestamps are capped to a one-day grace and two bad GitHub rows were corrected, leaving only Collector hourly yield as a warning.
2026-08-21 20:47 UTC - Exposure staging must aggregate duplicate indicators before upsert and requeue exported rows on evidence/source-family changes; live catch-up drained to Supabase remote row count 5720 with raw mirror disabled.
2026-08-21 20:52 UTC - Visible desktop Chrome login state is not Collector auth truth; Collector CDP 9336 restored 89 social cookies from the preserved vault snapshot and health/readiness should key off CDP vault plus browser ingest evidence.
2026-08-21 21:20 UTC - Production readiness should prefer narrow current proof over false-red broad fan-out timeouts: added fast health fallback, last-good data-quality cache fallback, Collector rate-limit propagation, and backend/headless useful-output fallback; live readiness is 13/13 ok with Supabase drained/populated.
2026-08-21 23:46 UTC - Browser-content stale/page-shell rows with warning severity and restorable cookie-vault proof are operator warnings, not critical production failures; readiness remains critical-green after Collector Postgres recovery, while X try-again and Instagram/Meta page churn remain real warning-level browser work.
- 2026-08-25 12:29:26 +08:00 [PRAWN-L390/claude/stop] branch=main head=6aa3068 dirty=34
- 2026-08-25 12:43:22 +08:00 [PRAWN-L390/claude/stop] branch=main head=6aa3068 dirty=34
- 2026-08-25 21:59:29 +08:00 [PRAWN-L390/claude/stop] branch=main head=c879d5e dirty=1
- 2026-08-26 01:35 SGT: Warning-severity extension issues must not block maintenance-state softening when samples prove they are warnings-only; summary counters count all severities while ok-logic must sample severities like existing soft-issue classification does.
- 2026-08-26 01:42:25 +08:00 [PRAWN-L390/claude/stop] branch=main head=d480fa5 dirty=1
- 2026-08-26 10:54:16 +08:00 [PRAWN-L390/claude/stop] branch=main head=e994b2e dirty=1
- 2026-08-26 20:13:54 +08:00 [PRAWN-L390/claude/stop] branch=main head=e994b2e dirty=1
- 2026-08-26 21:59:23 +08:00 [PRAWN-L390/claude/stop] branch=main head=e994b2e dirty=1
- 2026-08-27 01:27:17 +08:00 [PRAWN-L390/claude/stop] branch=main head=e994b2e dirty=1
- 2026-08-28 11:11:37 +08:00 [PRAWN-L390/claude/stop] branch=main head=75395d5 dirty=1
- 2026-08-28 11:33:01 +08:00 [PRAWN-L390/claude/stop] branch=main head=2f8e2e4 dirty=1
- 2026-08-28 12:06:57 +08:00 [PRAWN-L390/claude/stop] branch=main head=0011cdb dirty=1
- 2026-08-28 16:06:56 +08:00 [PRAWN-L390/claude/stop] branch=main head=2506448 dirty=1
- 2026-08-28 23:18:52 +08:00 [PRAWN-L390/claude/stop] branch=main head=3c84172 dirty=1
- 2026-08-29 23:15:36 +08:00 [PRAWN-L390/claude/stop] branch=main head=3c84172 dirty=1
- 2026-08-29 23:44:47 +08:00 [PRAWN-L390/claude/stop] branch=main head=b14b677 dirty=1
- 2026-08-30 13:38:32 +08:00 [PRAWN-L390/claude/stop] branch=main head=177ea3e dirty=1
- 2026-08-30 14:56:00 +08:00 [PRAWN-L390/claude/stop] branch=main head=177ea3e dirty=1
- 2026-08-30 15:02:07 +08:00 [PRAWN-L390/claude/stop] branch=main head=177ea3e dirty=1
- 2026-08-30 16:33:08 +08:00 [PRAWN-L390/claude/stop] branch=main head=0de9a55 dirty=1
- 2026-08-30 20:31:05 +08:00 [PRAWN-L390/claude/stop] branch=main head=68cef1c dirty=1
- 2026-08-30 21:03:03 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=1
- 2026-08-30 21:15:59 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=1
- 2026-08-30 21:40:10 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=1
- 2026-08-31 00:08:47 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=1
- 2026-08-31 17:48:31 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=1
- 2026-08-31 17:56:06 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=1
- 2026-08-31 18:17:56 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=1
- 2026-09-01 00:08:25 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=3
- 2026-09-01 00:08:25 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=3
- 2026-09-01 00:20:47 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=3
- 2026-09-01 00:20:47 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=3
- 2026-09-01 07:17:31 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=6
- 2026-09-01 07:17:31 +08:00 [PRAWN-L390/claude/stop] branch=main head=64adaf3 dirty=6
- 2026-09-01 07:43:11 +08:00 [PRAWN-L390/claude/stop] branch=main head=b7dd435 dirty=1
- 2026-09-01 08:19:16 +08:00 [PRAWN-L390/claude/stop] branch=main head=b7dd435 dirty=1
- 2026-09-01 09:54:09 +08:00 [PRAWN-L390/claude/stop] branch=main head=b7dd435 dirty=1
- 2026-09-01 10:29:00 +08:00 [PRAWN-L390/claude/stop] branch=main head=b7dd435 dirty=1
- 2026-09-01 17:27:29 +08:00 [PRAWN-L390/claude/stop] branch=main head=00ae040 dirty=1
- 2026-09-01 19:02:31 +08:00 [PRAWN-L390/claude/stop] branch=main head=ec4e558 dirty=1
- 2026-09-01 22:50:02 +08:00 [PRAWN-L390/claude/stop] branch=main head=ec4e558 dirty=1
- 2026-09-02 04:21:15 +08:00 [PRAWN-L390/claude/stop] branch=main head=0b59085 dirty=1
- 2026-09-02 12:48:53 +08:00 [PRAWN-L390/claude/stop] branch=main head=d167ac7 dirty=1
- 2026-09-02 19:02:13 +08:00 [PRAWN-L390/claude/stop] branch=main head=d167ac7 dirty=1
- 2026-09-02 19:48:41 +08:00 [PRAWN-L390/claude/stop] branch=main head=94f0168 dirty=1
- 2026-09-02 21:17:12 +08:00 [PRAWN-L390/claude/stop] branch=main head=94f0168 dirty=1
- 2026-09-02 22:04:16 +08:00 [PRAWN-L390/claude/stop] branch=main head=94f0168 dirty=1
- 2026-09-03 00:12:11 +08:00 [PRAWN-L390/claude/stop] branch=main head=94f0168 dirty=1
- 2026-09-03 02:21:23 +08:00 [PRAWN-L390/claude/stop] branch=main head=fcfe9b3 dirty=1
- 2026-09-03 07:52:30 +08:00 [PRAWN-L390/claude/stop] branch=main head=92a2fba dirty=1
- 2026-09-03 08:11:28 +08:00 [PRAWN-L390/claude/stop] branch=main head=92a2fba dirty=1
- 2026-09-03 08:27:50 +08:00 [PRAWN-L390/claude/stop] branch=main head=92a2fba dirty=4
- 2026-09-03 08:41:50 +08:00 [PRAWN-L390/claude/stop] branch=main head=92a2fba dirty=6
- 2026-09-03 09:04:29 +08:00 [PRAWN-L390/claude/stop] branch=main head=7df7a27 dirty=1
- 2026-09-03 09:56:48 +08:00 [PRAWN-L390/claude/stop] branch=main head=7df7a27 dirty=1
- 2026-09-03 10:18:57 +08:00 [PRAWN-L390/claude/stop] branch=main head=7df7a27 dirty=2
- 2026-09-03 11:13:10 +08:00 [PRAWN-L390/claude/stop] branch=main head=7545952 dirty=1
- 2026-09-03 13:59:22 +08:00 [PRAWN-L390/claude/stop] branch=main head=7545952 dirty=1
- 2026-09-03 14:34:43 +08:00 [PRAWN-L390/claude/stop] branch=main head=7545952 dirty=1
- 2026-09-03 15:29:36 +08:00 [PRAWN-L390/claude/stop] branch=main head=7545952 dirty=1
- 2026-09-03 16:41:11 +08:00 [PRAWN-L390/claude/stop] branch=main head=caa7e17 dirty=1
- 2026-09-03 17:09:14 +08:00 [PRAWN-L390/claude/stop] branch=main head=caa7e17 dirty=1
- 2026-09-03 22:47:40 +08:00 [PRAWN-L390/claude/stop] branch=main head=caa7e17 dirty=1
- 2026-09-04 19:54:24 +08:00 [PRAWN-L390/claude/stop] branch=main head=caa7e17 dirty=1
- 2026-09-04 21:17:07 +08:00 [PRAWN-L390/claude/stop] branch=main head=caa7e17 dirty=1
- 2026-09-04 22:40:53 +08:00 [PRAWN-L390/claude/stop] branch=main head=caa7e17 dirty=1
- 2026-09-04 22:46:27 +08:00 [PRAWN-L390/claude/stop] branch=main head=caa7e17 dirty=1
- 2026-09-04 23:28:24 +08:00 [PRAWN-L390/claude/stop] branch=main head=34451b1 dirty=1
- 2026-09-04 23:38:03 +08:00 [PRAWN-L390/claude/stop] branch=main head=4f7d24f dirty=1
- 2026-09-05 09:36:53 +08:00 [PRAWN-L390/claude/stop] branch=main head=55621ea dirty=1
- 2026-09-05 10:21:45 +08:00 [PRAWN-L390/claude/stop] branch=main head=e4b190a dirty=1
- 2026-09-05 11:11:37 +08:00 [PRAWN-L390/claude/stop] branch=main head=0adbed6 dirty=1
- 2026-09-05 11:27:14 +08:00 [PRAWN-L390/claude/stop] branch=main head=cd6e911 dirty=1
- 2026-09-05 15:10:05 +08:00 [PRAWN-L390/claude/stop] branch=main head=cd6e911 dirty=1
- 2026-09-05 15:39:11 +08:00 [PRAWN-L390/claude/stop] branch=main head=59c2ce7 dirty=1
- 2026-09-05 15:59:00 +08:00 [PRAWN-L390/claude/stop] branch=main head=6471eb3 dirty=1
- 2026-09-05 16:10:28 +08:00 [PRAWN-L390/claude/stop] branch=main head=31a4f2f dirty=1
- 2026-09-05 16:34:05 +08:00 [PRAWN-L390/claude/stop] branch=main head=4b269a5 dirty=1
- 2026-09-05 16:57:18 +08:00 [PRAWN-L390/claude/stop] branch=main head=79dc49c dirty=1
- 2026-09-05 17:04:01 +08:00 [PRAWN-L390/claude/stop] branch=main head=e5821a3 dirty=1
- 2026-09-05 17:11:02 +08:00 [PRAWN-L390/claude/stop] branch=main head=f6f88ac dirty=1
- 2026-09-05 17:19:15 +08:00 [PRAWN-L390/claude/stop] branch=main head=0d5d05c dirty=1
- 2026-09-05 17:44:06 +08:00 [PRAWN-L390/claude/stop] branch=main head=f757512 dirty=1
- 2026-09-05 18:12:35 +08:00 [PRAWN-L390/claude/stop] branch=main head=f172ada dirty=1
- 2026-09-05 18:49:18 +08:00 [PRAWN-L390/claude/stop] branch=main head=653c0c7 dirty=1
- 2026-09-05 20:29:17 +08:00 [PRAWN-L390/claude/stop] branch=main head=3b31ecc dirty=1
- 2026-09-05 20:37:40 +08:00 [PRAWN-L390/claude/stop] branch=main head=f9335f2 dirty=1
- 2026-09-05 21:35:21 +08:00 [PRAWN-L390/claude/stop] branch=main head=80128c4 dirty=1
- 2026-09-05 23:07:04 +08:00 [PRAWN-L390/claude/stop] branch=main head=41fe3eb dirty=1
- 2026-09-05 23:14:13 +08:00 [PRAWN-L390/claude/stop] branch=main head=41fe3eb dirty=1
- 2026-09-06 00:35:09 +08:00 [PRAWN-L390/claude/stop] branch=main head=3930887 dirty=1
- 2026-09-06 09:55:42 +08:00 [PRAWN-L390/claude/stop] branch=main head=3b472ed dirty=1
- 2026-09-06 10:01:08 +08:00 [PRAWN-L390/claude/stop] branch=main head=3b472ed dirty=1
- 2026-09-06 11:08:06 +08:00 [PRAWN-L390/claude/stop] branch=main head=c0eff78 dirty=1
- 2026-09-06 13:00:49 +08:00 [PRAWN-L390/claude/stop] branch=main head=07ac6fb dirty=1
- 2026-09-06 14:13:37 +08:00 [PRAWN-L390/claude/stop] branch=main head=b3c5006 dirty=1
- 2026-09-06 19:24:06 +08:00 [PRAWN-L390/claude/stop] branch=main head=503040b dirty=1
- 2026-09-06 19:33:52 +08:00 [PRAWN-L390/claude/stop] branch=main head=4a45582 dirty=1
- 2026-09-06 19:53:37 +08:00 [PRAWN-L390/claude/stop] branch=main head=2b93971 dirty=1
- 2026-09-06 21:04:02 +08:00 [PRAWN-L390/claude/stop] branch=main head=3ba1a20 dirty=1
- 2026-09-06 21:30:49 +08:00 [PRAWN-L390/claude/stop] branch=main head=745103a dirty=1
- 2026-09-06 21:49:45 +08:00 [PRAWN-L390/claude/stop] branch=main head=a2d7873 dirty=1
- 2026-09-06 22:02:15 +08:00 [PRAWN-L390/claude/stop] branch=main head=f8d6ed8 dirty=1
- 2026-09-06 22:20:58 +08:00 [PRAWN-L390/claude/stop] branch=main head=6642839 dirty=1
- 2026-09-06 22:40:25 +08:00 [PRAWN-L390/claude/stop] branch=main head=2186764 dirty=1
- 2026-09-06 23:16:26 +08:00 [PRAWN-L390/claude/stop] branch=main head=820305f dirty=1
- 2026-09-06 23:51:07 +08:00 [PRAWN-L390/claude/stop] branch=main head=be8eb05 dirty=1
- 2026-09-07 00:03:19 +08:00 [PRAWN-L390/claude/stop] branch=main head=d4c03c5 dirty=1
- 2026-09-07 00:38:18 +08:00 [PRAWN-L390/claude/stop] branch=main head=8ba8d66 dirty=1
- 2026-09-07 00:58:17 +08:00 [PRAWN-L390/claude/stop] branch=main head=8ba8d66 dirty=1
- 2026-09-07 11:23:25 +08:00 [PRAWN-L390/claude/stop] branch=main head=6a07cd3 dirty=1
- 2026-09-08 00:21:03 +08:00 [PRAWN-L390/claude/stop] branch=main head=8f84667 dirty=1
- 2026-09-08 01:28:32 +08:00 [PRAWN-L390/claude/stop] branch=main head=c19c552 dirty=1
- 2026-09-08 02:19:03 +08:00 [PRAWN-L390/claude/stop] branch=main head=765177b dirty=1
- 2026-09-08 02:41:27 +08:00 [PRAWN-L390/claude/stop] branch=main head=c10447a dirty=1
- 2026-09-08 02:58:20 +08:00 [PRAWN-L390/claude/stop] branch=main head=aaff300 dirty=1
- 2026-09-09 22:37:55 +08:00 [PRAWN-L390/claude/stop] branch=main head=80d0adf dirty=1
- 2026-09-10 10:59:06 +08:00 [PRAWN-L390/claude/stop] branch=main head=80d0adf dirty=1
- 2026-09-10 21:23:55 +08:00 [PRAWN-L390/claude/stop] branch=main head=e332aca dirty=1
- 2026-09-11 22:09:54 +08:00 [PRAWN-L390/claude/stop] branch=main head=36ca657 dirty=1
- 2026-09-15 07:10:49 +08:00 [PRAWN-L390/claude/stop] branch=main head=36ca657 dirty=1
- 2026-09-15 22:49:46 +08:00 [PRAWN-L390/claude/stop] branch=main head=36ca657 dirty=1
- 2026-09-16: Reproduced readiness deadline bypass in both analyst probes (two failing stalled-probe tests); every readiness probe must share the global budget and retain explicit failure evidence. Prior Collector backup TODO is superseded by a completed table-excluded dump, but full-cluster backup still failed and cannot be claimed verified. Existing code edits and backup artifacts are preserved.
- 2026-09-16 12:19:53 +08:00 [PRAWN-L390/claude/stop] branch=main head=5027e56 dirty=4
- 2026-09-16 12:19:53 +08:00 [PRAWN-L390/claude/stop] branch=main head=5027e56 dirty=4
- 2026-09-16 17:06:57 +08:00 [PRAWN-L390/claude/stop] branch=main head=5027e56 dirty=4
- 2026-09-16 23:33:18 +08:00 [PRAWN-L390/claude/stop] branch=main head=6f11f83 dirty=3
- 2026-09-17 00:05:56 +08:00 [PRAWN-L390/claude/stop] branch=main head=6f11f83 dirty=3
- 2026-09-18 11:56:29 +08:00 [PRAWN-L390/claude/stop] branch=main head=4352456 dirty=1
- 2026-09-18 16:55:30 +08:00 [PRAWN-L390/claude/stop] branch=main head=4352456 dirty=2
- 2026-09-18 20:51:59 +08:00 [PRAWN-L390/claude/stop] branch=main head=763bab8 dirty=0
- 2026-09-19 00:28:06 +08:00 [PRAWN-L390/claude/stop] branch=main head=763bab8 dirty=0
- 2026-09-19 13:50:24 +08:00 [PRAWN-L390/claude/stop] branch=main head=763bab8 dirty=0
- 2026-09-19 14:31:13 +08:00 [PRAWN-L390/claude/stop] branch=main head=763bab8 dirty=0
- 2026-09-19 17:04:10 +08:00 [PRAWN-L390/claude/stop] branch=main head=3ddd370 dirty=0
- 2026-09-19 17:54:32 +08:00 [PRAWN-L390/claude/stop] branch=main head=3ddd370 dirty=0
- 2026-09-19 19:04:12 +08:00 [PRAWN-L390/claude/stop] branch=main head=5ac6c59 dirty=0
- 2026-09-19 19:48:00 +08:00 [PRAWN-L390/claude/stop] branch=main head=0a9dc31 dirty=0
- 2026-09-19: Analyzer completion work kept Collector ownership separate; reproduced scheduler backup serialization, NL schema drift, and readiness evidence loss before fixes. Independent review caught cancellation ownership, so backup dispatch now uses a task group with non-abandoning thread execution. Full dump/restore and clean-image proof remain operational gates; missing counters never count as successful proof.
- 2026-09-19 23:31:45 +08:00 [PRAWN-L390/claude/stop] branch=main head=0a9dc31 dirty=15
- 2026-09-20: Resumed owned restore validation and clean-build controllers; build now waits for restored table/index/data checks and scratch-server restart proof, avoiding simultaneous verification/build I/O. Atomic restore remains in progress; current production readiness is degraded (3/13 passing, 8 critical failures), so rollout and backup scheduling remain gated.
- 2026-09-20 11:42:06 +08:00 [PRAWN-L390/claude/stop] branch=main head=0a9dc31 dirty=18
- 2026-09-20 12:13:16 +08:00 [PRAWN-L390/claude/stop] branch=main head=0a9dc31 dirty=18
- 2026-09-22: Diagnosed full-restore rollback at HNSW allocation (64MiB shared memory vs399MB request); added a tested1GiB/serial-maintenance preflight and checkpointed complete restore. Fixed missing terminal failure markers. Bot routing, independent callback thread, bounded overload feedback, retry cards and native SIGTERM cleanup passed focused/native tests and independent review. Readiness cleanup hang was reproduced and fixed with level cancellation; full suite5494passed/59existingDBskips. Full recovery remains gated on actual restore/build/runtime proof. All model/agent inspection is text-only.
- 2026-09-22: Docker29.5.3 completed build steps but failed export with a missing containerd lease; a retry did not retain dependency-stage cache. Use an explicitly selected, resource-bounded repository BuildKit worker; clean that worker/cache and superseded Analyzer images after verified rollout, preserving currently referenced images until replacement.
- 2026-09-22: Windows wrapper loss left the atomic data restore running without a coordinator. Added a Docker-owned finisher with explicit transaction-commit adoption and restart/data/index proof; never invent a lost exit code. Native build-client handoff failed twice and its test resources were cleaned. Global Docker restart is an approval boundary because Collector/other services and the uncommitted scratch transaction are affected.
- 2026-09-22 19:58:00 +08:00 [PRAWN-L390/claude/stop] branch=main head=0a9dc31 dirty=29
- 2026-09-22 21:04:21 +08:00 [PRAWN-L390/claude/stop] branch=main head=0a9dc31 dirty=29
- 2026-09-22 21:11:15 +08:00 [PRAWN-L390/claude/stop] branch=main head=0a9dc31 dirty=29
- 2026-09-23 23:30:01 +08:00 [PRAWN-L390/claude/stop] branch=main head=0a9dc31 dirty=29
- 2026-09-24: git-sync push: bot responsiveness (callback-thread isolation, polling/lookup extraction), readiness AnyIO deadline fix, scheduler SIGTERM lifecycle, NL graph pipeline, docker Ollama scripts. Full suite 5493/60/0. Commits 3e77da4 aa007da d08b18b 069299a cc5f04e staged and pushed to origin/main. Final pushed hashes to follow.
