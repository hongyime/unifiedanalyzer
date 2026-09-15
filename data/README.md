# data/

Runtime data files loaded by pipeline phases. Kept in-repo so the runtime does
not depend on external services at boot time.

## `wmn-data.json`

Ruleset for the WhatsMyName account-existence check, consumed by
`src/pipeline/wmn_fanout.py` (Track-C, opt-in via `WMN_FANOUT_ENABLED=1`).

**Source:** [`WebBreacher/WhatsMyName`](https://github.com/WebBreacher/WhatsMyName)
`main` branch, `wmn-data.json`.

**License:** CC0 (public domain, per the upstream repo's LICENSE).

**Refresh:** run `python scripts/refresh_wmn_data.py` from the repo root. The
script fetches the latest upstream JSON, validates that every entry has
`name` + `uri_check`, and atomically writes to `data/wmn-data.json`.
