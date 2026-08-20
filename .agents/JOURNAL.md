# UnifiedAnalyzer Agent Journal

- 2026-08-12 00:00 SGT: Started production-readiness completion across Analyzer and Collector; created Analyzer shared agent state because none existed and AGENTS.md requires durable cross-agent handoff state.
- 2026-08-12 00:18 SGT: Kept eval gates inside `metrics_json` instead of adding schema columns so existing eval API/table compatibility remains intact while CLI and UI can still fail/warn/pass production regressions.
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
