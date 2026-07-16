"""Mutual social-graph overlap (association relationship).

Two entities whose interaction neighborhoods overlap heavily — they share many
of the same group-mates / DM partners, from the collector `graph_edges` table —
are tightly linked in the real-world social graph.

DESIGN CHOICE (precision-first): this is emitted as an **entity_relationship**
(`social_graph_overlap`), NOT as a same-person `identity_signal`. Shared
neighborhoods are a strong *association* signal but a weak *same-person* one —
close friends share group-mates just like a person's two sock-puppet accounts
do. Routing it into identity_scorer (which computes same-person probability)
would inflate false merges between friends. Kept as an association edge it still
powers the relationship graph / community view without that risk.

EFFICIENCY: instead of O(entities^2) set intersections, we invert
neighbor -> entities and accumulate shared-neighbor counts per pair, SKIPPING
oversized shared neighbors (a 500-member group hub carries almost no identity
information — the reviewer's "weight by group size" taken to its limit).
"""
import json
import logging
import os
from collections import defaultdict
from itertools import combinations

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

_MIN_NEIGHBORS = int(os.getenv("GRAPH_OVERLAP_MIN_NEIGHBORS", "5"))   # entity needs >= this many neighbors
_MIN_SHARED = int(os.getenv("GRAPH_OVERLAP_MIN_SHARED", "3"))         # min shared neighbors to consider
_JACCARD_THRESHOLD = float(os.getenv("GRAPH_OVERLAP_JACCARD", "0.30"))
_MAX_FANOUT = int(os.getenv("GRAPH_OVERLAP_MAX_FANOUT", "50"))        # skip neighbors shared by > this many entities


def _norm(s: str | None) -> str | None:
    return s.strip().lower() if s else None


async def compute_graph_overlap() -> dict:
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()

    # (source, normalized account key) -> entity_id, from both username and id.
    async with analyzer.acquire() as conn:
        links = await conn.fetch("""
            SELECT entity_id::text AS eid, source, platform_id, platform_username
            FROM entity_platform_links
            WHERE retracted_at IS NULL
        """)
    account_to_entity: dict[tuple[str, str], str] = {}
    for r in links:
        for key in (_norm(r["platform_username"]), _norm(r["platform_id"])):
            if key:
                account_to_entity.setdefault((r["source"], key), r["eid"])

    if not account_to_entity:
        return {"entities": 0, "pairs": 0, "skipped": "no_links"}

    # Build each entity's neighborhood: namespaced neighbor nodes reached from
    # any of its accounts via graph_edges.
    async with collector.acquire() as conn:
        edges = await conn.fetch(
            "SELECT source, source_user, target_user FROM graph_edges WHERE target_user IS NOT NULL"
        )

    entity_neighbors: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        eid = account_to_entity.get((e["source"], _norm(e["source_user"])))
        if eid is None:
            continue
        # Namespace the neighbor by platform so the same handle on two platforms
        # isn't conflated.
        entity_neighbors[eid].add(f"{e['source']}:{_norm(e['target_user'])}")

    entity_neighbors = {k: v for k, v in entity_neighbors.items() if len(v) >= _MIN_NEIGHBORS}
    if len(entity_neighbors) < 2:
        return {"entities": len(entity_neighbors), "pairs": 0}

    # Invert neighbor -> entities, accumulate shared counts per pair (bounded).
    neighbor_entities: dict[str, list[str]] = defaultdict(list)
    for eid, nbrs in entity_neighbors.items():
        for n in nbrs:
            neighbor_entities[n].append(eid)

    shared: dict[tuple[str, str], int] = defaultdict(int)
    for nbr, ents in neighbor_entities.items():
        if len(ents) < 2 or len(ents) > _MAX_FANOUT:
            continue  # unique, or an uninformative giant hub
        for a, b in combinations(sorted(set(ents)), 2):
            shared[(a, b)] += 1

    rels: list[tuple] = []
    for (a, b), inter in shared.items():
        if inter < _MIN_SHARED:
            continue
        union = len(entity_neighbors[a]) + len(entity_neighbors[b]) - inter
        if union <= 0:
            continue
        jaccard = inter / union
        if jaccard < _JACCARD_THRESHOLD:
            continue
        rels.append((a, b, round(jaccard * 100), json.dumps({"jaccard": round(jaccard, 4), "shared": inter})))

    async with analyzer.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM entity_relationships WHERE relationship_type = 'social_graph_overlap'"
            )
            for a, b, weight, meta in rels:
                await conn.execute("""
                    INSERT INTO entity_relationships
                        (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                    VALUES ($1::uuid, $2::uuid, 'social_graph_overlap', $3, false, $4::jsonb)
                    ON CONFLICT (entity_a_id, entity_b_id, relationship_type)
                    DO UPDATE SET
                        weight = EXCLUDED.weight,
                        cross_platform = EXCLUDED.cross_platform,
                        sources = EXCLUDED.sources,
                        last_seen_at = EXCLUDED.last_seen_at,
                        updated_at = NOW()
                """, a, b, weight, meta)

    stats = {"entities": len(entity_neighbors), "overlap_pairs": len(rels)}
    logger.info("Graph overlap: %s", stats)
    return stats
