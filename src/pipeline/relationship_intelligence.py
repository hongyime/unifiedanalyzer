import json
import logging
from collections import defaultdict
from math import sqrt

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)


def _sorted_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _jsonb_param(raw) -> str:
    return json.dumps(raw, default=str)


def _decode_meta(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _scaled_similarity(a, b, floor: float = 1.0) -> float | None:
    try:
        av = float(a)
        bv = float(b)
    except (TypeError, ValueError):
        return None
    denom = max(floor, abs(av), abs(bv))
    if denom == 0:
        return 1.0
    return max(0.0, 1.0 - abs(av - bv) / denom)


def _cosine_sim(a: dict[str, int], b: dict[str, int]) -> float:
    all_words = set(a) | set(b)
    if not all_words:
        return 0.0
    dot = sum(a.get(word, 0) * b.get(word, 0) for word in all_words)
    mag_a = sqrt(sum(v * v for v in a.values()))
    mag_b = sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


async def refresh_relationship_intelligence() -> dict:
    analyzer = get_analyzer_pool()
    stats = {
        "self_declared_link_rows": 0,
        "content_reuse_rows": 0,
        "shared_home_or_gym_rows": 0,
        "style_similarity_rows": 0,
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
        profile_rows = await conn.fetch(
            """
            SELECT entity_id::text AS entity_id, metadata
            FROM behavioral_profiles
            WHERE metadata ? 'content_fingerprint'
               OR metadata ? 'bio_nlp'
            """
        )

        by_pair: dict[str, dict[tuple[str, str], list[dict]]] = {
            "self_declared_link": defaultdict(list),
            "content_reuse": defaultdict(list),
            "shared_home_or_gym": defaultdict(list),
            "style_similarity": defaultdict(list),
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
            ["self_declared_link", "content_reuse", "shared_home_or_gym", "style_similarity"],
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

        style_profiles: dict[str, dict] = {}
        for row in profile_rows:
            meta = _decode_meta(row["metadata"])
            fingerprint = meta.get("content_fingerprint")
            bio_nlp = meta.get("bio_nlp")
            if not isinstance(fingerprint, dict):
                fingerprint = {}
            if not isinstance(bio_nlp, dict):
                bio_nlp = {}
            words = {
                str(word).lower()
                for word in fingerprint.get("top_words", [])[:15]
                if isinstance(word, str) and word.strip()
            }
            word_counter = {
                str(word).lower(): int(count)
                for word, count in (fingerprint.get("_vocab_counter") or {}).items()
                if str(word).strip()
            }
            if not word_counter:
                word_counter = {word: 1 for word in words}
            emojis = {
                str(item.get("emoji"))
                for item in bio_nlp.get("top_emojis", [])
                if isinstance(item, dict) and item.get("emoji")
            }
            languages = {
                str(lang).lower()
                for lang in bio_nlp.get("language_hints", [])
                if isinstance(lang, str) and lang.strip()
            }
            feature_scores = []
            for key, floor in (
                ("avg_post_length", 20.0),
                ("avg_words_per_post", 8.0),
                ("exclamation_per_100", 1.0),
                ("question_per_100", 1.0),
                ("caps_ratio", 0.05),
                ("emoji_per_post", 0.5),
                ("hashtag_per_post", 0.5),
                ("vocab_richness", 0.05),
            ):
                value = fingerprint.get(key)
                if value is not None:
                    feature_scores.append((key, value, floor))
            if len(feature_scores) < 4 and not emojis and not languages:
                continue
            style_profiles[row["entity_id"]] = {
                "features": {key: value for key, value, _floor in feature_scores},
                "feature_floors": {key: floor for key, _value, floor in feature_scores},
                "top_words": words,
                "word_counter": word_counter,
                "emojis": emojis,
                "languages": languages,
            }

        profile_ids = sorted(style_profiles)
        for idx, entity_a in enumerate(profile_ids):
            profile_a = style_profiles[entity_a]
            for entity_b in profile_ids[idx + 1:]:
                profile_b = style_profiles[entity_b]
                shared_feature_keys = sorted(set(profile_a["features"]) & set(profile_b["features"]))
                if len(shared_feature_keys) < 4:
                    continue
                sims = []
                for key in shared_feature_keys:
                    sim = _scaled_similarity(
                        profile_a["features"][key],
                        profile_b["features"][key],
                        profile_a["feature_floors"][key],
                    )
                    if sim is not None:
                        sims.append(sim)
                if len(sims) < 4:
                    continue
                numeric_similarity = sum(sims) / len(sims)
                word_similarity = _cosine_sim(profile_a["word_counter"], profile_b["word_counter"])
                shared_words = sorted(profile_a["top_words"] & profile_b["top_words"])
                shared_emojis = sorted(profile_a["emojis"] & profile_b["emojis"])
                shared_languages = sorted(profile_a["languages"] & profile_b["languages"])
                emoji_similarity = _jaccard(profile_a["emojis"], profile_b["emojis"])
                language_similarity = _jaccard(profile_a["languages"], profile_b["languages"])
                score = (
                    numeric_similarity * 0.6
                    + word_similarity * 0.2
                    + emoji_similarity * 0.1
                    + language_similarity * 0.1
                )
                if score < 0.76:
                    continue
                if not shared_words and not shared_emojis and not shared_languages:
                    continue
                reasons = []
                if shared_languages:
                    reasons.append(f"shared language {'/'.join(shared_languages[:3])}")
                if shared_emojis:
                    reasons.append(f"emoji overlap {' '.join(shared_emojis[:3])}")
                if shared_words:
                    reasons.append(f"{len(shared_words)} shared top words")
                by_pair["style_similarity"][_sorted_pair(entity_a, entity_b)].append({
                    "score": round(score, 3),
                    "numeric_similarity": round(numeric_similarity, 3),
                    "word_similarity": round(word_similarity, 3),
                    "shared_words": shared_words[:8],
                    "shared_emojis": shared_emojis[:5],
                    "shared_languages": shared_languages[:5],
                    "why": "Similar writing style, emoji use, and language hints suggest a soft affinity.",
                    "reason_bits": reasons,
                })

        for pair, rows in by_pair["style_similarity"].items():
            best = max(rows, key=lambda row: row["score"])
            weight = min(85, max(30, round(best["score"] * 100)))
            payloads.append((
                pair[0],
                pair[1],
                "style_similarity",
                weight,
                True,
                _jsonb_param({
                    "why": best["why"],
                    "score": best["score"],
                    "numeric_similarity": best["numeric_similarity"],
                    "word_similarity": best["word_similarity"],
                    "shared_words": best["shared_words"],
                    "shared_emojis": best["shared_emojis"],
                    "shared_languages": best["shared_languages"],
                    "reasons": best["reason_bits"],
                }),
                None,
            ))

        stats["style_similarity_rows"] = len(by_pair["style_similarity"])

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
