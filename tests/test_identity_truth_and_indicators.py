from src.pipeline.identity_truth import build_truth_assertion, corroborated_auto_truth
from src.pipeline.indicator_export import extract_indicators_from_text, normalize_indicator, resolve_domain_to_ips


def _signal(**overrides):
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "signal_type": "recon_observation",
        "source_platform": "spiderfoot",
        "source_table": "recon_observations",
        "value": "alice@example.com",
        "confidence": 0.6,
        "metadata": {},
    }
    row.update(overrides)
    return row


def test_spiderfoot_alone_is_not_auto_truth():
    ok, confidence, summary = corroborated_auto_truth([_signal()])

    assert ok is False
    assert confidence == 0
    assert summary["reason"] == "requires_spiderfoot_and_independent_hard_signal"


def test_spiderfoot_promotes_after_independent_hard_signal():
    assertion = build_truth_assertion(
        "11111111-1111-1111-1111-111111111111",
        "alice@example.com",
        [
            _signal(),
            _signal(
                id="00000000-0000-0000-0000-000000000002",
                signal_type="email_match",
                source_platform="telegram",
                source_table="telegram_users",
                confidence=0.95,
            ),
        ],
    )

    assert assertion is not None
    assert assertion["truth_state"] == "auto_truth"
    assert assertion["confidence"] >= 0.85
    assert assertion["evidence_count"] == 2


def test_extract_indicators_from_text_normalizes_core_types():
    text = 'Reach "Alice Example" at Alice@Example.COM, +1 (415) 555-0123, @Alice, example.org, 203.0.113.5'

    indicators = extract_indicators_from_text(text, default_region="US")
    values = {(item.indicator_type, item.normalized_value) for item in indicators}

    assert ("email", "alice@example.com") in values
    assert ("phone_e164", "+14155550123") in values
    assert ("username", "alice") in values
    assert ("domain", "example.org") in values
    assert ("ipv4", "203.0.113.5") in values
    assert ("full_name", "Alice Example") in values


def test_normalize_indicator_rejects_invalid_ipv4():
    assert normalize_indicator("ipv4", "999.1.1.1") is None


def test_resolve_domain_to_ips_uses_injected_resolver():
    def fake_resolver(*args):
        return [
            (None, None, None, "", ("203.0.113.8", 0)),
            (None, None, None, "", ("203.0.113.7", 0)),
        ]

    assert resolve_domain_to_ips("example.com", resolver=fake_resolver) == ["203.0.113.7", "203.0.113.8"]


def test_export_router_exposes_identity_and_indicator_status_routes():
    from src.api.routes.export import router

    paths = {getattr(route, "path", "") for route in router.routes}

    assert "/identity/truth/status" in paths
    assert "/indicators/export/supabase/status" in paths
    assert "/indicators/export/supabase/preview" in paths
