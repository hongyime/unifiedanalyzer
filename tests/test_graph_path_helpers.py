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
