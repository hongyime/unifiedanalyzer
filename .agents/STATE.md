# UnifiedAnalyzer Agent State

Updated: 2026-08-20 07:49 UTC / 2026-08-20 15:49 SGT

Current task status: Supabase direct-Postgres compact indicator export is implemented and live. On 2026-08-20, the local ignored `.env` was switched from Supabase direct DB hostname to the IPv4-compatible Supavisor session pooler because Docker could reach IPv4 internet but the direct DB hostname resolved IPv6-only from inside the Analyzer container.

Implemented in this slice:
- Added `python -m src.main supabase-export` with `--dry-run`, `--write`, schema ensure control, and JSON output.
- Added `export_pending_supabase_indicators` for pending/retry `normalized_indicators`: direct Postgres upsert, idempotent remote conflict handling, local exported/retry marking, and bounded batch size.
- Wired scheduler export when `SUPABASE_INDICATOR_EXPORT_MODE` is enabled.
- Updated Supabase config detection to prefer direct Postgres credentials when present.
- Added non-secret `.env.example` placeholders. Local ignored `.env` now uses `SUPABASE_INDICATOR_EXPORT_MODE=postgres_direct` and `ANALYZER_SUPABASE_EXPORT_BATCH_SIZE=100`.

Verification completed:
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
- Supabase export remains compact normalized indicator rows only; no raw Collector DB mirror and no raw private chat bodies.
- Do not write Supabase credentials into `.agents/`, docs, commits, or logs.
- No Supabase login is needed for this direct-Postgres export path while the local ignored env has the DB URL.
- If Docker reports `[Errno 101] Network is unreachable` for Supabase direct DB, keep using the Supavisor session pooler host instead of the IPv6-only direct hostname unless the project has the paid IPv4 add-on or Docker gains IPv6 routing.
