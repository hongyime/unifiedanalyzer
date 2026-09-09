"""
Pure-function tests — batch 102.

Covers:
- pipeline.indicator_export: extract_indicators_from_text
- pipeline.account_proximity: _owner_entity_label, _peer_links,
  _owner_for_peer_link
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.indicator_export: extract_indicators_from_text
# ---------------------------------------------------------------------------

class TestExtractIndicatorsFromText:
    def _e(self, text, region=None):
        from src.pipeline.indicator_export import extract_indicators_from_text
        return extract_indicators_from_text(text, default_region=region)

    def test_none_returns_empty(self):
        assert self._e(None) == []

    def test_empty_string_returns_empty(self):
        assert self._e("") == []

    def test_extracts_email(self):
        result = self._e("contact alice@example.com")
        types = [i.indicator_type for i in result]
        assert "email" in types

    def test_extracts_domain(self):
        result = self._e("visit my site at example.com")
        types = [i.indicator_type for i in result]
        assert "domain" in types

    def test_email_domain_not_duplicated_as_domain(self):
        # example.com in alice@example.com should not yield a separate domain
        result = self._e("email alice@example.com")
        domains = [i.normalized_value for i in result if i.indicator_type == "domain"]
        assert "example.com" not in domains

    def test_returns_list_of_normalized_indicators(self):
        result = self._e("alice@example.com")
        assert isinstance(result, list)
        for item in result:
            assert hasattr(item, "indicator_type")
            assert hasattr(item, "normalized_value")

    def test_deduplicates(self):
        result = self._e("alice@example.com alice@example.com")
        emails = [i for i in result if i.indicator_type == "email"]
        assert len(emails) == 1


# ---------------------------------------------------------------------------
# pipeline.account_proximity: _owner_entity_label
# ---------------------------------------------------------------------------

class TestOwnerEntityLabel:
    def _l(self, entity_id, links_by_entity, preferred_source):
        from src.pipeline.account_proximity import _owner_entity_label
        return _owner_entity_label(entity_id, links_by_entity, preferred_source)

    def test_preferred_source_returned(self):
        links = {"eid-1": [{"source": "instagram", "platform_id": "alice"}]}
        result = self._l("eid-1", links, "instagram")
        assert result == "alice"

    def test_fallback_to_any_platform_id(self):
        links = {"eid-1": [{"source": "telegram", "platform_id": "123456"}]}
        result = self._l("eid-1", links, "instagram")
        assert result == "123456"

    def test_no_links_returns_entity_id(self):
        result = self._l("eid-1", {}, "instagram")
        assert result == "eid-1"

    def test_preferred_missing_falls_back(self):
        links = {"eid-1": [
            {"source": "telegram", "platform_id": "tg123"},
            {"source": "instagram", "platform_id": None},
        ]}
        result = self._l("eid-1", links, "instagram")
        # instagram has no platform_id, falls back to telegram
        assert result == "tg123"


# ---------------------------------------------------------------------------
# pipeline.account_proximity: _peer_links
# ---------------------------------------------------------------------------

class TestPeerLinks:
    def _p(self, entity_id, links_by_entity, preferred_source=None):
        from src.pipeline.account_proximity import _peer_links
        return _peer_links(entity_id, links_by_entity, preferred_source)

    def test_no_links_returns_empty(self):
        assert self._p("eid-1", {}) == []

    def test_preferred_source_filtered(self):
        links = {"eid-1": [
            {"source": "instagram", "platform_id": "alice"},
            {"source": "telegram", "platform_id": "123"},
        ]}
        result = self._p("eid-1", links, "instagram")
        assert all(l["source"] == "instagram" for l in result)

    def test_preferred_not_found_falls_back_to_target_platforms(self):
        from src.pipeline.account_proximity import TARGET_PLATFORMS
        links = {"eid-1": [
            {"source": "telegram", "platform_id": "123"},
        ]}
        result = self._p("eid-1", links, "instagram")
        if "telegram" in TARGET_PLATFORMS:
            assert len(result) == 1
        else:
            assert result == []

    def test_none_platform_id_excluded(self):
        links = {"eid-1": [
            {"source": "instagram", "platform_id": None},
            {"source": "telegram", "platform_id": "123"},
        ]}
        result = self._p("eid-1", links, "instagram")
        # instagram has no platform_id, not returned
        assert all(l.get("platform_id") for l in result)


# ---------------------------------------------------------------------------
# pipeline.account_proximity: _owner_for_peer_link
# ---------------------------------------------------------------------------

class TestOwnerForPeerLink:
    def _o(self, owner_by_platform, link):
        from src.pipeline.account_proximity import _owner_for_peer_link
        return _owner_for_peer_link(owner_by_platform, link)

    def test_matching_platform_returns_owner(self):
        result = self._o({"instagram": "owner-eid"}, {"source": "instagram"})
        assert result == "owner-eid"

    def test_missing_platform_returns_empty(self):
        result = self._o({}, {"source": "telegram"})
        assert result == ""

    def test_source_normalized_via_platform(self):
        # "ig" normalizes to "instagram"
        result = self._o({"instagram": "owner-eid"}, {"source": "ig"})
        assert result == "owner-eid"
