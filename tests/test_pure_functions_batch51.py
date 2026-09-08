"""
Pure-function tests — batch 51.

Covers previously untested pure functions:
- api.routes.collector_health: collector_health URL helpers (_collector_dashboard_url,
  _collector_cookie_vault_url already covered batch19) — adding _collector_from_live_row
  and _collectors_from_live edge cases not covered in batch50
- api.routes.readiness: USER_STORIES completeness, all additional story keys
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# api.routes.readiness: USER_STORIES completeness
# ---------------------------------------------------------------------------

class TestReadinessUserStoriesCompleteness:
    def test_all_stories_have_actor(self):
        from src.api.routes.readiness import USER_STORIES
        for key, story in USER_STORIES.items():
            assert "actor" in story, f"{key} missing actor"

    def test_all_stories_have_value(self):
        from src.api.routes.readiness import USER_STORIES
        for key, story in USER_STORIES.items():
            assert "value" in story, f"{key} missing value"

    def test_all_stories_have_proves(self):
        from src.api.routes.readiness import USER_STORIES
        for key, story in USER_STORIES.items():
            assert "proves" in story, f"{key} missing proves"

    def test_known_stories_present(self):
        from src.api.routes.readiness import USER_STORIES
        expected = {
            "databases_connected",
            "scheduler_self_healing",
            "supabase_populated",
            "backup_restorable",
            "decision_log_durable",
            "face_identity_safety",
            "face_processing_fresh",
            "collector_production_surfaces",
        }
        for key in expected:
            assert key in USER_STORIES, f"{key} missing from USER_STORIES"

    def test_analyst_workflows_present(self):
        from src.api.routes.readiness import USER_STORIES
        assert "analyst_workflows_available" in USER_STORIES

    def test_data_quality_ledger_present(self):
        from src.api.routes.readiness import USER_STORIES
        assert "data_quality_ledger" in USER_STORIES

    def test_all_actors_valid(self):
        from src.api.routes.readiness import USER_STORIES
        valid_actors = {"operator", "analyst"}
        for key, story in USER_STORIES.items():
            actor = story.get("actor", "")
            assert actor in valid_actors, f"{key} has unknown actor: {actor!r}"


# ---------------------------------------------------------------------------
# api.routes.collector_health: _collector_from_live_row edge cases
# ---------------------------------------------------------------------------

class TestCollectorFromLiveRowEdgeCases:
    def _c(self, row, targets=None):
        from src.api.routes.collector_health import _collector_from_live_row
        return _collector_from_live_row(row, targets or [])

    def test_status_falls_back_to_bridge_status(self):
        row = {
            "source": "instagram",
            "status": "degraded",
            "collection_mode": "continuous",
            "source_health_last_success_at": None,
            "source_health_updated_at": None,
            "browser_heartbeat_at": None,
            "bridge_status": "auth_error",
            "bridge_detail": "token expired",
            "detail": None,
        }
        result = self._c(row)
        assert result["status"] == "degraded"
        assert result["blocker"]["kind"] == "auth_error"

    def test_latest_status_matches_status(self):
        row = {
            "source": "telegram",
            "status": "active",
            "collection_mode": "continuous",
            "source_health_last_success_at": None,
            "source_health_updated_at": None,
            "browser_heartbeat_at": None,
            "bridge_status": None,
            "bridge_detail": None,
            "detail": None,
        }
        result = self._c(row)
        assert result["latest_status"] == "active"

    def test_browser_heartbeat_used_for_last_completed(self):
        row = {
            "source": "instagram",
            "status": "active",
            "collection_mode": "continuous",
            "source_health_last_success_at": None,
            "source_health_updated_at": None,
            "browser_heartbeat_at": "2026-03-15T10:00:00Z",
            "bridge_status": None,
            "bridge_detail": None,
            "detail": None,
        }
        result = self._c(row)
        assert result["last_completed"] is not None
        assert "2026-03-15" in result["last_completed"]


# ---------------------------------------------------------------------------
# api.routes.collector_health: _collector_production_summary input validation
# (test only the pure structural aspects — no DB calls)
# ---------------------------------------------------------------------------

class TestCollectorProductionSummaryStructure:
    def _c(self, surfaces=None):
        from src.api.routes.collector_health import _collector_production_summary
        return _collector_production_summary(surfaces or {})

    def test_empty_surfaces_returns_dict(self):
        result = self._c({})
        assert isinstance(result, dict)

    def test_required_keys_present(self):
        result = self._c({})
        for key in ("hard_source_issues", "realtime_failed_sources",
                    "quota_paused", "source_issues"):
            assert key in result, f"Missing key: {key}"

    def test_zero_hard_issues_on_empty(self):
        result = self._c({})
        assert result.get("hard_source_issues") == 0

    def test_empty_lists_on_empty_surfaces(self):
        result = self._c({})
        assert result.get("realtime_failed_sources") == []
        # source_issues is a count, not a list
        assert result.get("source_issues", 0) == 0

    def test_source_matrix_surfaces_parsed(self):
        surfaces = {
            "source_matrix": {
                "reachable": True,
                "payload": {
                    "sources": [
                        {"source": "instagram", "status": "active",
                         "last_24h": {}, "current_hour": {},
                         "blocker": {}, "rate_limit": {}},
                    ]
                }
            }
        }
        result = self._c(surfaces)
        # Verify it ran without error and returned a dict
        assert isinstance(result, dict)
