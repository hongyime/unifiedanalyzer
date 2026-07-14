import json
import logging
from collections import defaultdict

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)


def _sorted_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _jsonb_param(raw) -> str:
    return json.dumps(raw, default=str)


async def refresh_relationship_intelligence() -> dict:
    analyzer = get_analyzer_pool()
    stats = {
        "self_declared_link_rows": 0,
        "content_reuse_rows": 0,
        "shared_home_or_gym_rows": 0,
    }

    async with analyzer.acquire() as conn:
        raw_rows = await conn.fetch(
            """
            SELECT sig.entity_id::text AS entity_id,
                   sig.signal_type,
                   sig.source_platform,
                   sig.source_table,
                   sig.target_platform,
                   sig.target_record_id,
                   sig.value,
                   sig.confidence,
                   sig.metadata,
                   epl.entity_id::text AS bio_target_entity_id
            FROM identity_signals sig
            LEFT JOIN entity_platform_links epl
              ON sig.signal_type = 'bio_mention'
             AND epl.source = sig.target_platform
             AND epl.platform_id = sig.target_record_id
             AND epl.retracted_at IS NULL
            WHERE sig.signal_type IN (
                'bio_mention',
                'cross_platform_link',
                'shared_website',
                'content_similarity',
                'shared_route_origin'
            )
            """
        )

        by_pair: dict[str, dict[tuple[str, str], list[dict]]] = {
            "self_declared_link": defaultdict(list),
            "content_reuse": defaultdict(list),
            "shared_home_or_gym": defaultdict(list),
        }

        for row in raw_rows:
            entity_id = row["entity_id"]
            signal_type = row["signal_type"]
            target_entity_id = None

            if signal_type == "bio_mention":
                target_entity_id = row["bio_target_entity_id"]
            elif signal_type in {"cross_platform_link", "shared_website", "content_similarity", "shared_route_origin"}:
                target = row["target_record_id"]
                if target and len(target) == 36:
                    target_entity_id = target

            if not target_entity_id or target_entity_id == entity_id:
                continue

            if signal_type in {"bio_mention", "cross_platform_link", "shared_website"}:
                rel_type = "self_declared_link"
            elif signal_type == "content_similarity":
                rel_type = "content_reuse"
            else:
                rel_type = "shared_home_or_gym"

            by_pair[rel_type][_sorted_pair(entity_id, target_entity_id)].append({
                "signal_type": signal_type,
                "source_platform": row["source_platform"],
                "source_table": row["source_table"],
                "target_platform": row["target_platform"],
                "value": row["value"],
                "confidence": float(row["confidence"] or 0.0),
                "metadata": row["metadata"],
            })

        await conn.execute(
            "DELETE FROM entity_relationships WHERE relationship_type = ANY($1::text[])",
            ["self_declared_link", "content_reuse", "shared_home_or_gym"],
        )

        payloads: list[tuple] = []
        for pair, rows in by_pair["self_declared_link"].items():
            counts: dict[str, int] = defaultdict(int)
            examples: list[str] = []
            for row in rows:
                counts[row["signal_type"]] += 1
                if row["signal_type"] == "shared_website":
                    examples.append(f"shared website {row['value']}")
                elif row["signal_type"] == "cross_platform_link":
                    examples.append(f"linked profile {row['value']}")
                elif row["signal_type"] == "bio_mention":
                    examples.append(f"bio mention @{row['value']}")
            examples = list(dict.fromkeys(examples))[:5]
            weight = min(
                100,
                counts.get("cross_platform_link", 0) * 35
                + counts.get("shared_website", 0) * 30
                + counts.get("bio_mention", 0) * 20,
            )
            payloads.append((
                pair[0],
                pair[1],
                "self_declared_link",
                weight or 1,
                True,
                _jsonb_param({
                    "why": "Human-authored cross-reference in a bio, link, or personal website.",
                    "signal_counts": counts,
                    "examples": examples,
                }),
                None,
            ))

        stats["self_declared_link_rows"] = len(by_pair["self_declared_link"])

        for pair, rows in by_pair["content_reuse"].items():
            examples = [row["value"] for row in rows if row["value"]]
            avg_conf = sum(row["confidence"] for row in rows) / max(1, len(rows))
            weight = min(100, round(avg_conf * 100) + max(0, len(rows) - 1) * 5)
            payloads.append((
                pair[0],
                pair[1],
                "content_reuse",
                weight or 1,
                True,
                _jsonb_param({
                    "why": "Their content fingerprints align across posts, suggesting reused or coordinated content.",
                    "signal_count": len(rows),
                    "examples": list(dict.fromkeys(examples))[:5],
                    "avg_confidence": round(avg_conf, 3),
                }),
                None,
            ))

        stats["content_reuse_rows"] = len(by_pair["content_reuse"])

        for pair, rows in by_pair["shared_home_or_gym"].items():
            examples = [row["value"] for row in rows if row["value"]]
            avg_conf = sum(row["confidence"] for row in rows) / max(1, len(rows))
            weight = min(100, round(avg_conf * 100) + max(0, len(rows) - 1) * 10)
            payloads.append((
                pair[0],
                pair[1],
                "shared_home_or_gym",
                weight or 1,
                False,
                _jsonb_param({
                    "why": "They repeatedly start Strava activities from the same recurring origin point.",
                    "signal_count": len(rows),
                    "examples": list(dict.fromkeys(examples))[:5],
                    "avg_confidence": round(avg_conf, 3),
                }),
                None,
            ))

        stats["shared_home_or_gym_rows"] = len(by_pair["shared_home_or_gym"])

        if payloads:
            await conn.executemany(
                """
                INSERT INTO entity_relationships
                    (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources, last_seen_at)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb, $7::timestamptz)
                """,
                payloads,
            )

    logger.info("Relationship intelligence refreshed: %s", stats)
    return stats
