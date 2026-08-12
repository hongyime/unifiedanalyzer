import asyncio
import asyncio
from datetime import datetime, timezone

from src.api.routes import graph
from src.api.routes.graph import _relationship_row, confidence_bucket


def test_confidence_bucket_is_deterministic():
    assert confidence_bucket("same_person_probability", 90) == "hard"
    assert confidence_bucket("temporal_copost", 10) == "context-only"
    assert confidence_bucket("interaction", 3) == "weak"
    assert confidence_bucket("interaction", 1) == "context-only"


def test_relationship_row_decodes_sources_json_string():
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "entity_a_id": "00000000-0000-0000-0000-000000000002",
        "entity_b_id": "00000000-0000-0000-0000-000000000003",
        "relationship_type": "interaction",
        "weight": 3,
        "cross_platform": False,
        "sources": '{"evidence_refs":["event:1"],"by_type":{"reply":2}}',
        "last_seen_at": None,
    }

    result = _relationship_row(row)

    assert result["evidence_refs"] == ["event:1"]
    assert result["why"].startswith("Directed interactions")


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def test_graph_path_route_applies_filters_and_returns_evidence(monkeypatch):
    edge_id = "00000000-0000-0000-0000-000000000010"

    class Conn:
        def __init__(self):
            self.fetchrow_args = None

        async def fetchrow(self, _sql, *args):
            self.fetchrow_args = args
            return {"edge_ids": [edge_id]}

        async def fetch(self, _sql, *args):
            return [{
                "id": edge_id,
                "entity_a_id": "00000000-0000-0000-0000-000000000001",
                "entity_b_id": "00000000-0000-0000-0000-000000000002",
                "relationship_type": "interaction",
                "weight": 3,
                "cross_platform": False,
                "sources": {"evidence_refs": ["event:1"], "source": "telegram"},
                "last_seen_at": datetime(2026, 8, 12, tzinfo=timezone.utc),
            }]

    conn = Conn()
    monkeypatch.setattr(graph, "get_analyzer_pool", lambda: _Pool(conn))

    result = asyncio.run(graph.graph_path(
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        include_context_only=False,
        relationship_type="interaction",
        source="telegram",
    ))

    assert conn.fetchrow_args[-2:] == ("interaction", "telegram")
    assert result["found"] is True
    assert result["path"][0]["evidence_refs"] == ["event:1"]


def test_graph_pivots_route_filters_confidence(monkeypatch):
    class Conn:
        async def fetch(self, _sql, *args):
            return [
                {
                    "id": "00000000-0000-0000-0000-000000000011",
                    "entity_a_id": "00000000-0000-0000-0000-000000000001",
                    "entity_b_id": "00000000-0000-0000-0000-000000000002",
                    "relationship_type": "interaction",
                    "weight": 3,
                    "cross_platform": False,
                    "sources": {"evidence_refs": ["event:2"]},
                    "last_seen_at": None,
                },
                {
                    "id": "00000000-0000-0000-0000-000000000012",
                    "entity_a_id": "00000000-0000-0000-0000-000000000001",
                    "entity_b_id": "00000000-0000-0000-0000-000000000003",
                    "relationship_type": "temporal_copost",
                    "weight": 1,
                    "cross_platform": False,
                    "sources": {"evidence_refs": ["event:3"]},
                    "last_seen_at": None,
                },
            ]

    monkeypatch.setattr(graph, "get_analyzer_pool", lambda: _Pool(Conn()))

    result = asyncio.run(graph.graph_pivots(
        "00000000-0000-0000-0000-000000000001",
        include_context_only=True,
        confidence_bucket_filter="weak",
    ))

    assert result["total"] == 1
    assert result["groups"]["interaction_edges"][0]["confidence_bucket"] == "weak"
