from pathlib import Path

from src.pipeline.entity_resolver import (
    EntityCandidate,
    PlatformProfile,
    SignalMatch,
    _apply_no_auto_merge_policy,
)


def test_entity_resolver_does_not_auto_reassign_existing_platform_links():
    source = Path("src/pipeline/entity_resolver.py").read_text(encoding="utf-8")

    assert "ON CONFLICT (source, platform_id) DO UPDATE SET" in source
    assert "entity_id = EXCLUDED.entity_id" not in source


def test_no_auto_merge_policy_splits_new_multi_platform_candidate():
    candidate = EntityCandidate(
        profiles=[
            PlatformProfile(source="instagram", platform_id="ig-1", username="alice"),
            PlatformProfile(source="tiktok", platform_id="tt-1", username="alice"),
        ],
        signals=[
            SignalMatch(
                signal_type="username_exact",
                source_platform="instagram",
                target_platform="tiktok",
                source_record_id="ig-1",
                target_record_id="tt-1",
                value="alice",
                confidence=20.0,
            )
        ],
    )

    split, cross_signals, stats = _apply_no_auto_merge_policy([candidate], {})

    assert len(split) == 2
    assert [len(c.profiles) for c in split] == [1, 1]
    assert all(c.signals == [] for c in split)
    assert len(cross_signals) == 1
    assert cross_signals[0][2].signal_type == "username_exact"
    assert stats == {"auto_merge_candidates_split": 1, "cross_entity_signals": 1}


def test_no_auto_merge_policy_preserves_existing_entity_group():
    existing_entity = "00000000-0000-0000-0000-000000000001"
    candidate = EntityCandidate(
        profiles=[
            PlatformProfile(source="instagram", platform_id="ig-1", username="alice"),
            PlatformProfile(source="tiktok", platform_id="tt-1", username="alice"),
        ],
        signals=[
            SignalMatch(
                signal_type="username_exact",
                source_platform="instagram",
                target_platform="tiktok",
                source_record_id="ig-1",
                target_record_id="tt-1",
                value="alice",
                confidence=20.0,
            )
        ],
    )

    split, cross_signals, stats = _apply_no_auto_merge_policy(
        [candidate],
        {
            ("instagram", "ig-1"): existing_entity,
            ("tiktok", "tt-1"): existing_entity,
        },
    )

    assert len(split) == 1
    assert len(split[0].profiles) == 2
    assert len(split[0].signals) == 1
    assert cross_signals == []
    assert stats == {"auto_merge_candidates_split": 0, "cross_entity_signals": 0}
