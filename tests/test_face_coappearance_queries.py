from datetime import datetime, timezone

from src.pipeline import interaction_graph, timeline_builder


def test_direct_face_association_interaction_query_registered():
    specs = [
        spec for spec in interaction_graph.SOURCE_QUERIES
        if spec["source"] == "facetracker" and spec["interaction_type"] == "face_coappear"
    ]

    assert specs
    query = specs[0]["query"]
    assert "FROM face_associations fa" in query
    assert "JOIN public.entity_faces ef" in query
    assert "WHERE ef.entity_id <> fa.entity_id" in query
    assert "GROUP BY fa.entity_id, ef.entity_id, fa.media_item_id" in query
    assert "face_association_entity_face" in query
    assert query.count("UNION ALL") >= 2
    assert specs[0]["time_filters"] == {
        "pairs_where_clause": "occurred_at",
        "relationship_where_clause": "COALESCE(er.last_seen_at, er.created_at)",
    }


def test_direct_face_association_timeline_query_registered():
    specs = [
        spec for spec in timeline_builder.PLATFORM_QUERIES
        if spec["source"] == "facetracker" and spec["event_type"] == "PHOTO_COAPPEARANCE"
    ]

    assert specs
    query = specs[0]["query"]
    assert "FROM face_associations fa" in query
    assert "JOIN public.entity_faces ef" in query
    assert "WHERE ef.entity_id <> fa.entity_id" in query
    assert "GROUP BY fa.entity_id, ef.entity_id, fa.media_item_id" in query
    assert "face_association_entity_face" in query
    assert ":owner" in query
    assert ":associated" in query
    assert specs[0]["time_filters"] == {
        "pairs_where_clause": "occurred_at",
        "relationship_where_clause": "COALESCE(er.last_seen_at, er.created_at)",
    }


def test_face_coappearance_interaction_since_query_filters_each_branch():
    spec = next(
        spec for spec in interaction_graph.SOURCE_QUERIES
        if spec["source"] == "facetracker" and spec["interaction_type"] == "face_coappear"
    )

    query, params = interaction_graph._format_source_query(
        spec, datetime(2026, 7, 28, tzinfo=timezone.utc)
    )

    assert len(params) == 1
    assert query.count("WHERE occurred_at IS NOT NULL AND occurred_at > $1") == 2
    assert query.count(
        "AND COALESCE(er.last_seen_at, er.created_at) IS NOT NULL "
        "AND COALESCE(er.last_seen_at, er.created_at) > $1"
    ) == 2


def test_face_coappearance_timeline_since_query_filters_each_branch():
    spec = next(
        spec for spec in timeline_builder.PLATFORM_QUERIES
        if spec["source"] == "facetracker" and spec["event_type"] == "PHOTO_COAPPEARANCE"
    )

    query, params = timeline_builder._format_platform_query(
        spec, datetime(2026, 7, 28, tzinfo=timezone.utc)
    )

    assert len(params) == 1
    assert query.count("WHERE occurred_at IS NOT NULL AND occurred_at > $1") == 2
    assert query.count(
        "AND COALESCE(er.last_seen_at, er.created_at) IS NOT NULL "
        "AND COALESCE(er.last_seen_at, er.created_at) > $1"
    ) == 2
