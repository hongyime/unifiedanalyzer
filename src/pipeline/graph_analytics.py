import json
import logging
from collections import defaultdict

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)


async def compute_graph_analytics() -> dict:
    pool = get_analyzer_pool()

    async with pool.acquire() as conn:
        rels = await conn.fetch("""
            SELECT entity_a_id::text, entity_b_id::text, weight, relationship_type
            FROM entity_relationships
        """)

    if not rels:
        logger.info("Graph analytics: no relationships to analyze")
        return {"nodes": 0, "edges": 0}

    adj: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for r in rels:
        a, b, w = r["entity_a_id"], r["entity_b_id"], r["weight"]
        adj[a].append((b, w))
        adj[b].append((a, w))

    nodes = set(adj.keys())
    n = len(nodes)

    # Degree centrality
    degree: dict[str, int] = {node: len(neighbors) for node, neighbors in adj.items()}

    # Weighted degree (strength)
    strength: dict[str, int] = {}
    for node, neighbors in adj.items():
        strength[node] = sum(w for _, w in neighbors)

    # Betweenness centrality (BFS-based, unweighted)
    betweenness: dict[str, float] = {node: 0.0 for node in nodes}
    if n <= 500:
        for s in nodes:
            stack: list[str] = []
            pred: dict[str, list[str]] = {node: [] for node in nodes}
            sigma: dict[str, int] = {node: 0 for node in nodes}
            sigma[s] = 1
            dist: dict[str, int] = {node: -1 for node in nodes}
            dist[s] = 0
            queue = [s]
            qi = 0

            while qi < len(queue):
                v = queue[qi]
                qi += 1
                stack.append(v)
                for w, _ in adj[v]:
                    if dist[w] < 0:
                        dist[w] = dist[v] + 1
                        queue.append(w)
                    if dist[w] == dist[v] + 1:
                        sigma[w] += sigma[v]
                        pred[w].append(v)

            delta: dict[str, float] = {node: 0.0 for node in nodes}
            while stack:
                w = stack.pop()
                for v in pred[w]:
                    delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])
                if w != s:
                    betweenness[w] += delta[w]

        # Normalize
        if n > 2:
            norm = 2.0 / ((n - 1) * (n - 2))
            for node in betweenness:
                betweenness[node] *= norm

    # Connected components
    visited: set[str] = set()
    components: list[set[str]] = []
    for node in nodes:
        if node in visited:
            continue
        component: set[str] = set()
        stack = [node]
        while stack:
            v = stack.pop()
            if v in visited:
                continue
            visited.add(v)
            component.add(v)
            for neighbor, _ in adj[v]:
                if neighbor not in visited:
                    stack.append(neighbor)
        components.append(component)

    # Clustering coefficient
    clustering: dict[str, float] = {}
    for node in nodes:
        neighbors = {n for n, _ in adj[node]}
        k = len(neighbors)
        if k < 2:
            clustering[node] = 0.0
            continue
        triangles = 0
        neighbor_list = list(neighbors)
        for i, ni in enumerate(neighbor_list):
            ni_neighbors = {n for n, _ in adj[ni]}
            for nj in neighbor_list[i + 1:]:
                if nj in ni_neighbors:
                    triangles += 1
        clustering[node] = (2.0 * triangles) / (k * (k - 1))

    # Store per-entity analytics in behavioral_profiles.metadata
    async with pool.acquire() as conn:
        for node in nodes:
            analytics = {
                "degree": degree.get(node, 0),
                "strength": strength.get(node, 0),
                "betweenness": round(betweenness.get(node, 0), 6),
                "clustering": round(clustering.get(node, 0), 4),
                "component_size": next(
                    (len(c) for c in components if node in c), 0
                ),
            }

            existing = await conn.fetchrow(
                "SELECT id, metadata FROM behavioral_profiles WHERE entity_id = $1::uuid",
                node,
            )
            if existing:
                meta = existing["metadata"] if isinstance(existing["metadata"], dict) else {}
                meta["graph_analytics"] = analytics
                await conn.execute("""
                    UPDATE behavioral_profiles
                    SET metadata = $1::jsonb, updated_at = NOW()
                    WHERE entity_id = $2::uuid
                """, json.dumps(meta, default=str), node)
            else:
                await conn.execute("""
                    INSERT INTO behavioral_profiles (entity_id, metadata)
                    VALUES ($1::uuid, $2::jsonb)
                    ON CONFLICT (entity_id) DO UPDATE SET
                        metadata = $2::jsonb, updated_at = NOW()
                """, node, json.dumps({"graph_analytics": analytics}, default=str))

    stats = {
        "nodes": n,
        "edges": len(rels),
        "components": len(components),
        "largest_component": max(len(c) for c in components) if components else 0,
        "avg_clustering": round(sum(clustering.values()) / n, 4) if n else 0,
    }
    logger.info("Graph analytics: %s", stats)
    return stats
