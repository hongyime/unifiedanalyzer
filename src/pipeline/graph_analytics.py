import json
import logging
from collections import defaultdict

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)


def _decode_meta(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


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

    # --- Label propagation community detection ---
    # Initialize each node's label to its own entity_id.
    labels: dict[str, str] = {node: node for node in nodes}
    sorted_nodes = sorted(nodes)

    for _ in range(20):
        changed = False
        for node in sorted_nodes:
            neighbors = adj[node]
            if not neighbors:
                continue
            label_weights: dict[str, int] = defaultdict(int)
            for neighbor, w in neighbors:
                label_weights[labels[neighbor]] += w

            max_weight = max(label_weights.values())
            best_labels = [lbl for lbl, w in label_weights.items() if w == max_weight]
            new_label = min(best_labels)

            if new_label != labels[node]:
                labels[node] = new_label
                changed = True

        if not changed:
            break

    # Group nodes by final label; communities are groups with >= 2 members.
    label_groups: dict[str, list[str]] = defaultdict(list)
    for node, label in labels.items():
        label_groups[label].append(node)

    communities = {
        label: members for label, members in label_groups.items() if len(members) >= 2
    }
    community_id_for: dict[str, str] = {}
    for label, members in communities.items():
        for node in members:
            community_id_for[node] = label

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

            community_id = community_id_for.get(node)

            existing = await conn.fetchrow(
                "SELECT id, metadata FROM behavioral_profiles WHERE entity_id = $1::uuid",
                node,
            )
            if existing:
                meta = _decode_meta(existing["metadata"])
                meta["graph_analytics"] = analytics
                if community_id is not None:
                    meta["community_id"] = community_id
                else:
                    meta.pop("community_id", None)
                await conn.execute("""
                    UPDATE behavioral_profiles
                    SET metadata = $1::jsonb, updated_at = NOW()
                    WHERE entity_id = $2::uuid
                """, json.dumps(meta, default=str), node)
            else:
                meta = {"graph_analytics": analytics}
                if community_id is not None:
                    meta["community_id"] = community_id
                await conn.execute("""
                    INSERT INTO behavioral_profiles (entity_id, metadata)
                    VALUES ($1::uuid, $2::jsonb)
                    ON CONFLICT (entity_id) DO UPDATE SET
                        metadata = $2::jsonb, updated_at = NOW()
                """, node, json.dumps(meta, default=str))

    stats = {
        "nodes": n,
        "edges": len(rels),
        "components": len(components),
        "largest_component": max(len(c) for c in components) if components else 0,
        "avg_clustering": round(sum(clustering.values()) / n, 4) if n else 0,
        "communities_found": len(communities),
        "largest_community_size": max((len(m) for m in communities.values()), default=0),
    }
    logger.info("Graph analytics: %s", stats)
    return stats
