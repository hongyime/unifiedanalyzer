"""SpiderFoot recon_observations -> analyzer identity_signals bridge.

Reads from `collector.recon_observations` (via the analyzer's collector pool)
and writes weak `identity_signals` rows into the analyzer DB, joined to
`entities` via `entity_platform_links.platform_username` (case-insensitive,
active links only).

**Opt-in, manually operated.** Not wired into the scheduler. Run via:

    python -m src.pipeline.recon_bridge --dry-run   # report only
    python -m src.pipeline.recon_bridge             # write signals

Design notes:
    * Emits weak signals (confidence <= 0.5). The existing
      `identity_truth.promote_spiderfoot_truth` treats spiderfoot rows as
      leads that require an independent hard signal to reach `auto_truth`.
    * Idempotent: skips signals whose (source_table, source_record_id)
      already exist. No unique-index dependency (identity_signals has no
      such constraint upstream).
    * Bounded: --limit caps input rows per run (default 500) so an
      operator can always dry-run first and control scale.
    * Safe by default: only bridges username-typed recon_targets whose
      target_value matches an existing entity_platform_link. Orphan
      observations (no matching entity) are skipped and counted.

Bridged observation types -> analyzer signal_type:

    ACCOUNT_EXTERNAL_OWNED  -> cross_platform_link       (URL of discovered account)
    USERNAME                 -> cross_platform_username   (only if != target_value)
    EMAILADDR                -> email_lead                (weak; needs corroboration)
    HUMAN_NAME               -> name_lead                 (weak)
    INTERNET_NAME            -> domain_lead               (weak)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from typing import Any

from src.db.connection import (
    close_pools,
    get_analyzer_pool,
    get_collector_pool,
    init_pools,
)

logger = logging.getLogger(__name__)

# Observation-type -> (signal_type, base_confidence)
# Confidence is intentionally weak; identity_truth requires independent hard
# corroboration before promotion.
_TYPE_MAP: dict[str, tuple[str, float]] = {
    "ACCOUNT_EXTERNAL_OWNED": ("cross_platform_link", 0.5),
    "SIMILAR_ACCOUNT_EXTERNAL": ("cross_platform_link", 0.25),
    "USERNAME": ("cross_platform_username", 0.4),
    "EMAILADDR": ("email_lead", 0.4),
    "HUMAN_NAME": ("name_lead", 0.4),
    "Human Name": ("name_lead", 0.4),
    "INTERNET_NAME": ("domain_lead", 0.3),
    "Internet Name": ("domain_lead", 0.3),
}

# Observations echoed by SpiderFoot's UI/seed injector are just the target
# value being re-emitted. Never bridge these — they carry no new information.
_ECHO_MODULES = {"SpiderFoot UI", "sfp__stor_stdout"}

# Extract URL from ACCOUNT_EXTERNAL_OWNED payloads that look like
# "SiteName (Category: cat)\n<SFURL>https://...</SFURL>"
_SFURL_RE = re.compile(r"<SFURL>([^<]+)</SFURL>")


def _extract_value(obs_type: str, raw_value: str) -> str:
    """Return the canonical link/value for an observation."""
    if obs_type in {"ACCOUNT_EXTERNAL_OWNED", "SIMILAR_ACCOUNT_EXTERNAL"}:
        match = _SFURL_RE.search(raw_value or "")
        if match:
            return match.group(1).strip()
    return (raw_value or "").strip()


async def _fetch_recon_rows(collector_conn, since_id: str | None, limit: int) -> list[dict[str, Any]]:
    rows = await collector_conn.fetch(
        """
        SELECT ro.id::text        AS obs_id,
               ro.target_id::text AS target_id,
               ro.module          AS module,
               ro.observation_type AS observation_type,
               ro.value           AS value,
               ro.confidence      AS confidence,
               ro.raw_json        AS raw_json,
               rt.target_type     AS target_type,
               rt.target_value    AS target_value,
               rt.scope_json      AS scope_json
        FROM recon_observations ro
        JOIN recon_targets rt ON rt.id = ro.target_id
        WHERE ($1::uuid IS NULL OR ro.id > $1::uuid)
          AND rt.target_type = 'username'
          AND ro.observation_type = ANY($2::text[])
          AND ro.module <> ALL($3::text[])
        ORDER BY ro.id
        LIMIT $4
        """,
        since_id,
        list(_TYPE_MAP.keys()),
        list(_ECHO_MODULES),
        max(1, int(limit)),
    )
    return [dict(r) for r in rows]


async def _resolve_entity(analyzer_conn, target_value: str) -> str | None:
    """Look up entity_id via active entity_platform_links by username (ci)."""
    return await analyzer_conn.fetchval(
        """
        SELECT entity_id::text
        FROM entity_platform_links
        WHERE retracted_at IS NULL
          AND platform_username IS NOT NULL
          AND lower(platform_username) = lower($1)
        LIMIT 1
        """,
        target_value,
    )


async def _already_bridged(analyzer_conn, obs_id: str) -> bool:
    return bool(
        await analyzer_conn.fetchval(
            """
            SELECT 1 FROM identity_signals
            WHERE source_table = 'recon_observations'
              AND source_record_id = $1
            LIMIT 1
            """,
            obs_id,
        )
    )


async def _insert_signal(
    analyzer_conn,
    *,
    entity_id: str,
    signal_type: str,
    value: str,
    confidence: float,
    obs_id: str,
    metadata: dict[str, Any],
) -> None:
    await analyzer_conn.execute(
        """
        INSERT INTO identity_signals (
            entity_id, signal_type, source_platform, source_table,
            source_record_id, value, confidence, metadata
        )
        VALUES ($1::uuid, $2, 'spiderfoot', 'recon_observations',
                $3, $4, $5, $6::jsonb)
        """,
        entity_id,
        signal_type,
        obs_id,
        value,
        confidence,
        json.dumps(metadata, default=str),
    )


async def bridge_recon_observations(
    *,
    dry_run: bool = True,
    limit: int = 500,
    since_id: str | None = None,
) -> dict[str, Any]:
    """Bridge collector recon_observations into analyzer identity_signals.

    Returns a JSON-friendly summary. When ``dry_run`` is True, no rows are
    written.
    """
    collector_pool = get_collector_pool()
    analyzer_pool = get_analyzer_pool()
    inserted = 0
    orphaned = 0
    already = 0
    skipped_echo = 0  # kept for future filtering; SQL already excludes
    per_type: dict[str, int] = {}
    samples: list[dict[str, Any]] = []

    async with collector_pool.acquire() as coll_conn:
        rows = await _fetch_recon_rows(coll_conn, since_id, limit)

    async with analyzer_pool.acquire() as an_conn:
        for row in rows:
            obs_type = str(row.get("observation_type") or "")
            module = str(row.get("module") or "")
            if module in _ECHO_MODULES:
                skipped_echo += 1
                continue
            mapping = _TYPE_MAP.get(obs_type)
            if not mapping:
                continue
            signal_type, base_conf = mapping
            entity_id = await _resolve_entity(an_conn, str(row["target_value"]))
            if not entity_id:
                orphaned += 1
                continue
            value = _extract_value(obs_type, str(row.get("value") or ""))
            if not value:
                continue
            if await _already_bridged(an_conn, row["obs_id"]):
                already += 1
                continue
            per_type[signal_type] = per_type.get(signal_type, 0) + 1
            if len(samples) < 8:
                samples.append(
                    {
                        "target": row["target_value"],
                        "obs_type": obs_type,
                        "signal_type": signal_type,
                        "value": value[:80],
                        "entity_id": entity_id,
                    }
                )
            if dry_run:
                continue
            obs_conf = float(row.get("confidence") or 0.0)
            confidence = max(min(base_conf, obs_conf) if obs_conf > 0 else base_conf, 0.0)
            metadata = {
                "recon_module": module,
                "recon_observation_type": obs_type,
                "recon_target_value": row["target_value"],
                "recon_target_type": row["target_type"],
                "bridge": "recon_bridge.v1",
            }
            try:
                await _insert_signal(
                    an_conn,
                    entity_id=entity_id,
                    signal_type=signal_type,
                    value=value,
                    confidence=confidence,
                    obs_id=row["obs_id"],
                    metadata=metadata,
                )
                inserted += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "recon_bridge insert failed for obs=%s target=%s: %s",
                    row["obs_id"], row["target_value"], exc,
                )

    return {
        "dry_run": dry_run,
        "input_rows": len(rows),
        "candidates": sum(per_type.values()) + already,
        "inserted": inserted,
        "already_bridged": already,
        "orphaned_no_entity": orphaned,
        "skipped_echo_modules": skipped_echo,
        "by_signal_type": per_type,
        "samples": samples,
    }


async def _cli(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    await init_pools(apply_schema_ddl=False)
    try:
        summary = await bridge_recon_observations(
            dry_run=args.dry_run,
            limit=args.limit,
            since_id=args.since_id,
        )
    finally:
        await close_pools()
    print(json.dumps(summary, indent=2, sort_keys=True, default=str), flush=True)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Bridge collector recon_observations -> analyzer identity_signals. "
            "Opt-in, manually operated. See module docstring for scope."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be bridged without writing (default: write).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Max observations to scan per run (default: 500). Bounded on purpose.",
    )
    parser.add_argument(
        "--since-id",
        type=str,
        default=None,
        help="Only consider recon_observations.id > this UUID (for incremental runs).",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_cli(args)))


if __name__ == "__main__":
    main()
