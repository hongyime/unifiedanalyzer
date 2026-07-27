from src.api.routes.entities import router


def test_legacy_merge_candidates_route_precedes_dynamic_entity_route():
    paths = [getattr(route, "path", "") for route in router.routes]

    assert "/entities/merge-candidates" in paths
    assert paths.index("/entities/merge-candidates") < paths.index("/entities/{entity_id}")
