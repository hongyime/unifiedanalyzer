from datetime import datetime, timezone

from src.pipeline.text_normalizer import (
    build_canonical_timeline_text,
    source_fingerprint,
)


def _event(metadata):
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "entity_id": "00000000-0000-0000-0000-000000000002",
        "occurred_at": datetime(2026, 8, 8, tzinfo=timezone.utc),
        "source": "instagram",
        "event_type": "CONTENT_PUBLISHED",
        "source_record_id": "post-1",
        "title": "Launch post from @bryan",
        "detail": "Extra timeline detail",
        "metadata": metadata,
    }


def test_canonical_text_preserves_osint_tokens_from_collector_metadata():
    normalized = build_canonical_timeline_text(
        _event({
            "caption": "Met #OSINT people at https://example.com/path",
            "target_preview": "reply preview",
            "location_name": "Marina Bay",
            "mentions": ["analyst_one"],
            "hashtags": ["investigation"],
            "ignored_blob": {"large": "not indexed"},
        })
    )

    assert "Launch post from @bryan" in normalized.canonical_text
    assert "Met #OSINT people" in normalized.canonical_text
    assert "reply preview" in normalized.canonical_text
    assert "Marina Bay" in normalized.canonical_text
    assert normalized.selected_metadata["caption"].startswith("Met #OSINT")
    assert "ignored_blob" not in normalized.selected_metadata
    assert normalized.mention_count == 1
    assert normalized.hashtag_count == 1
    assert normalized.url_count == 1
    assert normalized.domain_count == 1
    assert normalized.token_count > 0
    assert len(normalized.text_sha1) == 40


def test_source_fingerprint_changes_when_text_source_changes():
    first = _event({"caption": "first caption"})
    second = _event({"caption": "second caption"})

    assert source_fingerprint(first) != source_fingerprint(second)
    assert source_fingerprint(first) == source_fingerprint(_event({"caption": "first caption"}))


def test_canonical_text_truncates_with_flag():
    normalized = build_canonical_timeline_text(
        _event({"caption": "x" * 100}),
        max_chars=20,
    )

    assert len(normalized.canonical_text) <= 20
    assert normalized.flags["truncated"] is True
