#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

docker exec unifiedanalyzer_scheduler \
    python -m src.main supabase-export --limit 100 --write --json

date -u +%Y-%m-%dT%H:%M:%SZ > "$repo_dir/last_sync.txt"
