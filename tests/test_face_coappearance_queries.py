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
