"""Deterministic Instagram<->Threads auto-merge (Phase 1.5).

Meta guarantee: a Threads handle IS the account's Instagram handle, and
Instagram handles are globally unique. So an IG profile and a Threads profile
that share the SAME strict-normalized handle are the same real person, EVEN
WHEN Phase 1 skipped their loose group because the handle is "common" across
many unrelated platforms (Telegram/X/etc.).

These tests lock the bypass to IG<->Threads ONLY — no other platform pairing
gets its gates weakened.
"""
from __future__ import annotations

import pytest

from src.pipeline.entity_resolver import (
    COMMON_USERNAME_ACCOUNTS,
    EntityCandidate,
    PlatformProfile,
    SignalMatch,
    STRONG_SIGNAL_TYPES,
    VERIFIED_SIGNAL_TYPES,
    _CROSS_ENTITY_SIGNAL_CONFIDENCE,
    _apply_no_auto_merge_policy,
    _phase15_merge_instagram_threads,
    compute_confidence,
    normalize_username,
)


# ---------- helpers ----------

def _build_profiles_by_username(profiles: list[PlatformProfile]) -> dict[str, list[PlatformProfile]]:
    """Mirror the LOOSE-normalized bucketing that load_platform_profiles produces.

    Phase 1.5 must iterate this same structure — loose to catch the common-handle
    groups Phase 1 SKIPS, then match strictly inside to honor the Meta guarantee.
    """
    by_username: dict[str, list[PlatformProfile]] = {}
    for p in profiles:
        norm = normalize_username(p.username)
        if norm:
            by_username.setdefault(norm, []).append(p)
    return by_username


def _find_candidate_containing(entities: list[EntityCandidate], src: str, pid: str) -> EntityCandidate | None:
    for e in entities:
        for p in e.profiles:
            if (p.source, p.platform_id) == (src, pid):
                return e
    return None


# ---------- registration ----------

def test_instagram_threads_linked_is_registered_as_strong_verified_signal():
    """The new signal type must be classified as STRONG (drives is_confirmed) and
    VERIFIED (deterministic same-person on its own — Meta guarantee)."""
    assert "instagram_threads_linked" in STRONG_SIGNAL_TYPES, \
        "instagram_threads_linked must be STRONG so compute_confidence auto-confirms it"
    assert "instagram_threads_linked" in VERIFIED_SIGNAL_TYPES, \
        "instagram_threads_linked is deterministic — near-certain same-person on its own"
    assert _CROSS_ENTITY_SIGNAL_CONFIDENCE.get("instagram_threads_linked") == 0.99, \
        "instagram_threads_linked cross-entity confidence must be 0.99 (deterministic)"


# ---------- primary correctness (the bug the spec targets) ----------

def test_common_handle_ig_threads_still_merges_and_confirms():
    """CRITICAL: IG 'john' + Threads 'john' where 'john' is COMMON on many other
    platforms (> COMMON_USERNAME_ACCOUNTS accounts, so Phase 1 SKIPS the loose
    group) MUST still auto-merge via Phase 1.5.

    Expected outcome: one EntityCandidate contains both the IG and Threads
    profiles, carries an instagram_threads_linked STRONG signal, and
    compute_confidence(...) reports is_confirmed=True.
    """
    ig = PlatformProfile(source="instagram", platform_id="ig-john-uid", username="john")
    threads = PlatformProfile(source="threads", platform_id="john", username="john")
    # Push the loose group above COMMON_USERNAME_ACCOUNTS so Phase 1 skips it.
    common_others = [
        PlatformProfile(source="telegram", platform_id=f"tg-john-{i}", username=f"john{i}")
        for i in range(COMMON_USERNAME_ACCOUNTS + 2)
    ]
    profiles = [ig, threads] + common_others
    by_username = _build_profiles_by_username(profiles)
    # Sanity: the loose group is indeed too large for Phase 1 to cluster.
    assert len(by_username["john"]) > COMMON_USERNAME_ACCOUNTS

    # Simulate Phase 1's outcome for a common-handle group: nothing clustered,
    # nothing assigned.
    entities: list[EntityCandidate] = []
    assigned: set[tuple[str, str]] = set()

    _phase15_merge_instagram_threads(by_username, entities, assigned)

    cand = _find_candidate_containing(entities, "instagram", "ig-john-uid")
    assert cand is not None, "Phase 1.5 must create/pick a candidate for the IG profile"
    threads_cand = _find_candidate_containing(entities, "threads", "john")
    assert threads_cand is cand, "IG and Threads for the same handle must land in the SAME candidate"

    sources = {p.source for p in cand.profiles}
    assert sources == {"instagram", "threads"}, \
        f"Phase 1.5 must not drag unrelated platforms in; got sources={sources}"

    signal_types = {s.signal_type for s in cand.signals}
    assert "instagram_threads_linked" in signal_types

    # STRONG signal ⇒ compute_confidence auto-confirms (drives is_confirmed=TRUE
    # at persistence, which is what makes the pair absent from the review queue).
    _, strong_count, is_confirmed = compute_confidence(cand.signals)
    assert strong_count >= 1
    assert is_confirmed is True

    # assigned must include both profiles so downstream phases don't
    # accidentally re-cluster or re-add them.
    assert ("instagram", "ig-john-uid") in assigned
    assert ("threads", "john") in assigned


def test_normal_non_common_ig_threads_pair_still_merges_no_regression():
    """A plain (non-common) IG+Threads same-handle pair must continue to merge.

    Simulates the state Phase 1 leaves for a small loose group: both profiles
    already in a shared EntityCandidate with a username_exact signal. Phase 1.5
    must be idempotent — add the deterministic link signal exactly once, no
    duplicate profiles, no candidate proliferation.
    """
    ig = PlatformProfile(source="instagram", platform_id="ig-rare-uid", username="rareuser")
    threads = PlatformProfile(source="threads", platform_id="rareuser", username="rareuser")
    by_username = _build_profiles_by_username([ig, threads])

    prior = EntityCandidate(profiles=[ig, threads])
    prior.signals.append(SignalMatch(
        signal_type="username_exact",
        source_platform="instagram", target_platform="threads",
        source_record_id="ig-rare-uid", target_record_id="rareuser",
        value="rareuser", confidence=20.0,
    ))
    entities: list[EntityCandidate] = [prior]
    assigned: set[tuple[str, str]] = {("instagram", "ig-rare-uid"), ("threads", "rareuser")}

    _phase15_merge_instagram_threads(by_username, entities, assigned)

    # Still exactly one candidate carrying both profiles.
    ig_cands = [e for e in entities if any(p.source == "instagram" and p.platform_id == "ig-rare-uid" for p in e.profiles)]
    assert len(ig_cands) == 1
    cand = ig_cands[0]
    # No profile duplication.
    profile_keys = [(p.source, p.platform_id) for p in cand.profiles]
    assert len(profile_keys) == len(set(profile_keys)) == 2

    signal_types = [s.signal_type for s in cand.signals]
    assert "username_exact" in signal_types, "existing Phase 1 signal preserved"
    ig_th = [s for s in cand.signals if s.signal_type == "instagram_threads_linked"]
    assert len(ig_th) == 1, f"instagram_threads_linked must be deduped; got {len(ig_th)}"

    _, _, is_confirmed = compute_confidence(cand.signals)
    assert is_confirmed is True


def test_non_matching_ig_threads_handles_do_not_link():
    """IG 'alice' + Threads 'bob' must NOT link (different handles = different
    accounts; Meta guarantee does not apply)."""
    ig = PlatformProfile(source="instagram", platform_id="ig-a", username="alice")
    threads = PlatformProfile(source="threads", platform_id="bob", username="bob")
    by_username = _build_profiles_by_username([ig, threads])
    entities: list[EntityCandidate] = []
    assigned: set[tuple[str, str]] = set()

    _phase15_merge_instagram_threads(by_username, entities, assigned)

    for e in entities:
        sources = {p.source for p in e.profiles}
        if "instagram" in sources and "threads" in sources:
            pytest.fail(f"non-matching IG/Threads must not link: {[(p.source,p.platform_id) for p in e.profiles]}")
    # No spurious instagram_threads_linked signal anywhere.
    for e in entities:
        for s in e.signals:
            assert s.signal_type != "instagram_threads_linked"


def test_bypass_is_scoped_to_ig_threads_only_ig_plus_telegram_common_not_auto_merged():
    """Proves the bypass is IG<->Threads-scoped. IG 'john' + Telegram 'john' in
    a COMMON loose group must NOT get auto-merged by Phase 1.5 — the bypass
    weakens no gate for any pairing outside {instagram, threads}."""
    ig = PlatformProfile(source="instagram", platform_id="ig-john-uid", username="john")
    telegram = PlatformProfile(source="telegram", platform_id="tg-john-uid", username="john")
    common_others = [
        PlatformProfile(source="x", platform_id=f"x-{i}", username=f"john{i}")
        for i in range(COMMON_USERNAME_ACCOUNTS + 2)
    ]
    profiles = [ig, telegram] + common_others
    by_username = _build_profiles_by_username(profiles)
    assert len(by_username["john"]) > COMMON_USERNAME_ACCOUNTS

    entities: list[EntityCandidate] = []
    assigned: set[tuple[str, str]] = set()

    _phase15_merge_instagram_threads(by_username, entities, assigned)

    # No instagram_threads_linked signal must have been emitted (no Threads present).
    for e in entities:
        for s in e.signals:
            assert s.signal_type != "instagram_threads_linked"
    # No candidate must combine IG + Telegram via Phase 1.5.
    for e in entities:
        srcs = {p.source for p in e.profiles}
        if "instagram" in srcs and "telegram" in srcs:
            pytest.fail(f"Phase 1.5 must not merge IG+Telegram (bypass is IG<->Threads only): {[(p.source,p.platform_id) for p in e.profiles]}")
    # Telegram must not be marked assigned by Phase 1.5.
    assert ("telegram", "tg-john-uid") not in assigned


# ---------- integration with _apply_no_auto_merge_policy ----------

def test_no_auto_merge_policy_keeps_ig_threads_deterministic_link_as_one_group():
    """The no-auto-merge policy normally SPLITS new multi-platform clusters
    into singletons and emits cross-entity signals — that's what prevents
    silent auto-merges elsewhere. But a candidate carrying an
    instagram_threads_linked STRONG signal represents a Meta-guaranteed
    same-person link, so its IG+Threads profiles MUST stay in ONE policy
    group and persist as ONE entity. This is the only documented bypass.
    """
    candidate = EntityCandidate(
        profiles=[
            PlatformProfile(source="instagram", platform_id="ig-1", username="john"),
            PlatformProfile(source="threads", platform_id="john", username="john"),
        ],
        signals=[
            SignalMatch(
                signal_type="instagram_threads_linked",
                source_platform="instagram", target_platform="threads",
                source_record_id="ig-1", target_record_id="john",
                value="john", confidence=50.0,
            )
        ],
    )

    split, cross_signals, stats = _apply_no_auto_merge_policy([candidate], {})

    assert len(split) == 1, "IG+Threads deterministic link must NOT be split"
    assert len(split[0].profiles) == 2
    assert {p.source for p in split[0].profiles} == {"instagram", "threads"}
    # The link signal survives as an intra-group signal, NOT as a cross-entity one.
    assert cross_signals == []
    assert stats.get("auto_merge_candidates_split", 0) == 0
    intra_types = {s.signal_type for s in split[0].signals}
    assert "instagram_threads_linked" in intra_types


def test_no_auto_merge_policy_bypass_only_covers_ig_threads_members_not_bystanders():
    """The bypass is scoped to the specific IG and Threads profiles named by an
    instagram_threads_linked signal. Any OTHER same-cluster profile
    (e.g. a Telegram user Phase 1 pulled in on username_exact) must still get
    the normal per-profile policy split — so a same-cluster Telegram bystander
    does NOT get free-ride-merged into the IG+Threads entity.
    """
    candidate = EntityCandidate(
        profiles=[
            PlatformProfile(source="instagram", platform_id="ig-1", username="john"),
            PlatformProfile(source="threads", platform_id="john", username="john"),
            PlatformProfile(source="telegram", platform_id="tg-99", username="john"),
        ],
        signals=[
            SignalMatch(
                signal_type="instagram_threads_linked",
                source_platform="instagram", target_platform="threads",
                source_record_id="ig-1", target_record_id="john",
                value="john", confidence=50.0,
            ),
            SignalMatch(
                signal_type="username_exact",
                source_platform="instagram", target_platform="telegram",
                source_record_id="ig-1", target_record_id="tg-99",
                value="john", confidence=20.0,
            ),
        ],
    )

    split, cross_signals, _stats = _apply_no_auto_merge_policy([candidate], {})

    # Exactly 2 groups: {IG, Threads} together (bypass), {Telegram} alone (normal split).
    assert len(split) == 2, f"expected 2 groups, got {len(split)}"
    group_sources = sorted([tuple(sorted({p.source for p in g.profiles})) for g in split])
    assert group_sources == [("instagram", "threads"), ("telegram",)]
    # Cross-signal must exist for the username_exact link between the IG+Threads
    # group and the Telegram singleton — Telegram was NOT auto-merged in.
    assert len(cross_signals) >= 1
    assert any(s.signal_type == "username_exact" for _, _, s in cross_signals)


def test_no_auto_merge_policy_bypass_uses_existing_entity_id_when_available():
    """If either IG or Threads already links to an existing entity in the DB,
    the bypass must reuse THAT entity_id (via the ('existing', <uuid>) policy
    key), so persistence merges onto the existing entity rather than minting
    a new one and orphaning the prior link."""
    existing_eid = "00000000-0000-0000-0000-0000000000aa"
    candidate = EntityCandidate(
        profiles=[
            PlatformProfile(source="instagram", platform_id="ig-1", username="john"),
            PlatformProfile(source="threads", platform_id="john", username="john"),
        ],
        signals=[
            SignalMatch(
                signal_type="instagram_threads_linked",
                source_platform="instagram", target_platform="threads",
                source_record_id="ig-1", target_record_id="john",
                value="john", confidence=50.0,
            )
        ],
    )

    split, _cross, _stats = _apply_no_auto_merge_policy(
        [candidate],
        {("instagram", "ig-1"): existing_eid},
    )

    assert len(split) == 1
    key = split[0].merge_policy_key
    assert key is not None and key[0] == "existing" and key[1] == existing_eid, \
        f"bypass must reuse existing entity_id; got merge_policy_key={key}"
