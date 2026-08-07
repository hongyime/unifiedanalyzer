import re
import json
import uuid as uuid_module
import logging
from dataclasses import dataclass, field
from rapidfuzz import fuzz

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

USERNAME_STRIP_CHARS = "._-"
NAME_FUZZY_MIN_SCORE = 85
# P1-1 (identity_system_review_plan.md): name-only linking (no corroborating
# signal — Phase 4.5 WhatsApp attach) is the single biggest false-merge source
# (real_name_fuzzy is ~93% of all identity_signals). Demote it: require a
# distinctive full name (2+ tokens, not a lone common first name) AND a stricter
# fuzzy threshold before matching two people on name alone.
NAME_FUZZY_MIN_SCORE_NAME_ONLY = 90
MIN_NAME_TOKENS = 2
MIN_NAME_LENGTH = 5
# A normalized username shared by more than this many accounts is treated as a
# COMMON handle (likely different people) — those accounts are NOT clustered.
COMMON_USERNAME_ACCOUNTS = 10
# If a (digit-stripped) handle maps to more than this many distinct entities, it's
# too common to be useful cross-entity evidence — skip the username_similar links.
SIMILAR_USERNAME_MAX_ENTITIES = 5
CONFIDENCE_THRESHOLD = 0.85
MIN_SIGNALS = 2


@dataclass
class PlatformProfile:
    source: str
    platform_id: str
    username: str | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None


@dataclass
class SignalMatch:
    signal_type: str
    source_platform: str
    target_platform: str
    source_record_id: str
    target_record_id: str
    value: str
    confidence: float


@dataclass
class EntityCandidate:
    profiles: list[PlatformProfile] = field(default_factory=list)
    signals: list[SignalMatch] = field(default_factory=list)
    merge_policy_key: tuple[str, ...] | None = None


DEFAULT_USERNAME_RE = re.compile(r"^user\d*$", re.IGNORECASE)
MIN_NORMALIZED_LENGTH = 3


def normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    if DEFAULT_USERNAME_RE.match(username):
        return None
    if " " in username.strip():
        return None
    u = username.lower().strip()
    for ch in USERNAME_STRIP_CHARS:
        u = u.replace(ch, "")
    u = re.sub(r"\d+$", "", u)
    if not u or len(u) < MIN_NORMALIZED_LENGTH:
        return None
    return u


def normalize_username_strict(username: str | None) -> str | None:
    """Like normalize_username but KEEPS trailing digits — only case + punctuation
    (._-) are normalized. So "john_smith" == "johnsmith" (punctuation variant, same
    person) but "hongyime" != "bryanseah" (digit-suffix variant, treated as
    potentially DIFFERENT people). Phase 1 clusters on this strict key so only
    genuinely-same handles auto-merge; digit-variants are surfaced as cross-entity
    candidates instead (see username_similar)."""
    if not username:
        return None
    if DEFAULT_USERNAME_RE.match(username):
        return None
    if " " in username.strip():
        return None
    u = username.lower().strip()
    for ch in USERNAME_STRIP_CHARS:
        u = u.replace(ch, "")
    if not u or len(u) < MIN_NORMALIZED_LENGTH:
        return None
    return u


def name_is_distinctive(name: str | None) -> bool:
    """P1-1: gate name-ONLY identity linking on a distinctive full name. A lone
    common first name ("Mike") matching another "Mike" is not evidence two
    accounts are the same person; a full "Jane Halloran" match is much stronger.
    Require >= MIN_NAME_TOKENS whitespace-separated tokens and a reasonable
    length. Used only where a name match is the sole linking signal (Phase 4.5);
    paths that already require a second corroborating signal (Phase 4) don't need
    this gate but pass through it cheaply as extra safety."""
    if not name:
        return False
    stripped = name.strip()
    if len(stripped) < MIN_NAME_LENGTH:
        return False
    tokens = [t for t in stripped.split() if t]
    return len(tokens) >= MIN_NAME_TOKENS


def name_block_keys(name: str) -> set[str]:
    """P2-2 (identity_system_review_plan.md): candidate-generation block keys for
    name matching — the first 3 chars of each length>=3 token, lowercased. Two
    names with token_sort_ratio >= NAME_FUZZY_MIN_SCORE_NAME_ONLY must share
    near-identical tokens, hence at least one block key, so restricting fuzzy
    comparison to entities sharing a key prunes the O(n*m) all-pairs scan (28k+
    WhatsApp profiles x all entities) without dropping real matches."""
    keys: set[str] = set()
    for tok in name.lower().split():
        if len(tok) >= 3:
            keys.add(tok[:3])
    return keys


def parse_whatsapp_phone(jid: str) -> str | None:
    if not jid or "@lid" in jid or jid.startswith("status@"):
        return None
    phone_part = jid.split("@")[0]
    if re.match(r"^\d{7,15}$", phone_part):
        return phone_part
    return None


async def load_collection_targets() -> list[dict]:
    pool = get_collector_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT source, target_id, target_name FROM collection_targets WHERE status != 'disabled'"
        )
    return [dict(r) for r in rows]


async def load_platform_profiles() -> tuple[dict[str, list[PlatformProfile]], list[PlatformProfile]]:
    """Load all profiles from collector DB, grouped by normalized username.
    Returns (by_username, no_username) — the second list holds profiles
    without usernames (WhatsApp) that need special handling."""
    pool = get_collector_pool()
    profiles: list[PlatformProfile] = []

    async with pool.acquire() as conn:
        for row in await conn.fetch(
            "SELECT platform_user_id::text, login, name, email FROM github_users"
        ):
            profiles.append(PlatformProfile(
                source="github",
                platform_id=str(row["platform_user_id"]),
                username=row["login"],
                name=row["name"],
                email=row["email"],
            ))

        for row in await conn.fetch(
            "SELECT platform_user_id, username, full_name FROM instagram_profiles"
        ):
            profiles.append(PlatformProfile(
                source="instagram",
                platform_id=row["platform_user_id"],
                username=row["username"],
                name=row["full_name"],
            ))

        for row in await conn.fetch(
            # SYNC #40: exclude bots — a shared bot contact is not identity
            # evidence and can falsely merge unrelated people.
            "SELECT platform_user_id, username, first_name, last_name FROM telegram_users "
            "WHERE NOT COALESCE(is_bot, false)"
        ):
            name_parts = [row["first_name"] or "", row["last_name"] or ""]
            name = " ".join(p for p in name_parts if p).strip() or None
            profiles.append(PlatformProfile(
                source="telegram",
                platform_id=row["platform_user_id"],
                username=row["username"],
                name=name,
            ))

        for row in await conn.fetch(
            "SELECT platform_athlete_id::text, username, firstname, lastname FROM strava_athletes"
        ):
            name_parts = [row["firstname"] or "", row["lastname"] or ""]
            name = " ".join(p for p in name_parts if p).strip() or None
            profiles.append(PlatformProfile(
                source="strava",
                platform_id=str(row["platform_athlete_id"]),
                username=row["username"],
                name=name,
            ))

        for row in await conn.fetch(
            "SELECT platform_channel_id, title, custom_url FROM youtube_channels"
        ):
            profiles.append(PlatformProfile(
                source="youtube",
                platform_id=row["platform_channel_id"],
                username=row["custom_url"],
                name=row["title"],
            ))

        for row in await conn.fetch(
            "SELECT platform_user_id, username, nickname FROM tiktok_profiles"
        ):
            profiles.append(PlatformProfile(
                source="tiktok",
                platform_id=row["platform_user_id"],
                username=row["username"],
                name=row["nickname"],
            ))

        for row in await conn.fetch(
            "SELECT platform_user_id, username, nickname FROM lemon8_profiles"
        ):
            profiles.append(PlatformProfile(
                source="lemon8",
                platform_id=row["platform_user_id"],
                username=row["username"],
                name=row["nickname"],
            ))

        # threads: a threads handle IS the same Meta account's instagram handle,
        # so keying threads authors by username lets them cluster with instagram
        # (guaranteed same-person). No numeric id -> platform_id = username.
        # (SYNC #30/#34)
        for row in await conn.fetch(
            "SELECT DISTINCT author_username FROM threads_posts "
            "WHERE author_username IS NOT NULL AND author_username <> ''"
        ):
            profiles.append(PlatformProfile(
                source="threads", platform_id=row["author_username"],
                username=row["author_username"], name=None,
            ))
        try:
            x_rows = await conn.fetch(
                """
                SELECT platform_user_id, username, display_name
                FROM x_profiles
                WHERE platform_user_id IS NOT NULL AND platform_user_id <> ''
                """
            )
        except Exception:
            logger.debug("x_profiles unavailable; falling back to x_posts authors", exc_info=True)
            x_rows = await conn.fetch(
                "SELECT DISTINCT author_username AS platform_user_id, author_username AS username, NULL::text AS display_name "
                "FROM x_posts WHERE author_username IS NOT NULL AND author_username <> ''"
            )
        for row in x_rows:
            profiles.append(PlatformProfile(
                source="x", platform_id=row["platform_user_id"],
                username=row["username"], name=row["display_name"],
            ))

        try:
            fb_rows = await conn.fetch(
                """
                SELECT platform_user_id, username, display_name
                FROM facebook_profiles
                WHERE platform_user_id IS NOT NULL AND platform_user_id <> ''
                """
            )
        except Exception:
            logger.debug("facebook_profiles unavailable; skipping facebook profile load", exc_info=True)
            fb_rows = []
        for row in fb_rows:
            profiles.append(PlatformProfile(
                source="facebook", platform_id=row["platform_user_id"],
                username=row["username"], name=row["display_name"],
            ))

        for row in await conn.fetch(
            "SELECT platform_user_id, name, pushname FROM whatsapp_users"
        ):
            puid = row["platform_user_id"]
            if not puid or "@lid" in puid or puid.startswith("status@"):
                continue
            if "@newsletter" in puid or "@g.us" in puid or "@broadcast" in puid:
                continue
            display = row["name"] or row["pushname"] or ""
            phone = parse_whatsapp_phone(puid)
            profiles.append(PlatformProfile(
                source="whatsapp",
                platform_id=puid,
                username=None,
                name=display if display.strip() else None,
                phone=phone,
            ))

        # social_users is the broad cross-platform user index (usernames seen in
        # comments / mentions / discovery, far beyond the primary profile tables).
        # Feed only usernames that appear on >=2 DISTINCT platforms — that
        # co-occurrence of the SAME handle is exactly the corroboration the
        # resolver clusters on, extending SYNC #30 to the wider corpus (SYNC #33).
        # platform_id follows each source's native key: threads/x key on the
        # handle (like SYNC #30), everyone else on platform_user_id. Dedupes
        # harmlessly with the primary loaders via clustering + link upsert.
        _SU_SOURCES = {"instagram", "telegram", "youtube", "strava",
                       "threads", "lemon8", "tiktok", "x"}
        for row in await conn.fetch(
            """
            SELECT platform, platform_user_id, username, display_name
            FROM social_users
            WHERE username IS NOT NULL AND username <> ''
              AND lower(username) IN (
                SELECT lower(username) FROM social_users
                WHERE username IS NOT NULL AND username <> ''
                GROUP BY lower(username) HAVING count(DISTINCT platform) >= 2
              )
            """
        ):
            src = row["platform"]
            if src not in _SU_SOURCES:
                continue
            pid = row["username"] if src in ("threads", "x") else row["platform_user_id"]
            if not pid:
                continue
            profiles.append(PlatformProfile(
                source=src, platform_id=str(pid),
                username=row["username"], name=row["display_name"],
            ))

    by_username: dict[str, list[PlatformProfile]] = {}
    no_username: list[PlatformProfile] = []
    for p in profiles:
        norm = normalize_username(p.username)
        if norm:
            by_username.setdefault(norm, []).append(p)
        else:
            no_username.append(p)

    return by_username, no_username


async def load_commit_emails() -> dict[str, list[str]]:
    """Returns {email: [github_platform_user_id, ...]}."""
    pool = get_collector_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT c.author_email, u.platform_user_id::text
            FROM github_commits c
            JOIN github_users u ON c.author_login = u.login
            WHERE c.author_email IS NOT NULL
              AND c.author_email NOT LIKE '%@users.noreply.github.com'
              AND c.author_email != ''
        """)
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["author_email"].lower(), []).append(r["platform_user_id"])
    return result


async def load_profile_photo_hashes() -> dict[str, list[tuple[str, str]]]:
    """Returns {sha256: [(source, entity_id), ...]} for profile photos."""
    pool = get_collector_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT sha256, source, entity_id
            FROM media_items
            WHERE sha256 IS NOT NULL
              AND content_type IN ('profile_photo', 'avatar', 'profile_pic')
        """)
    result: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        if r["sha256"]:
            result.setdefault(r["sha256"], []).append((r["source"], r["entity_id"]))
    return result


# Linking-rework (identity link quality): classify the resolver's own signals by
# strength. STRONG signals are hard identifiers; VERIFIED ones are near-certain
# same-person on their own (a shared verified phone / email / commit email /
# identical profile photo). real_name_fuzzy is intentionally NOT strong — a shared
# name is weak and was the source of the "b"-telegram over-linking.
STRONG_SIGNAL_TYPES = {
    "username_exact", "whatsapp_phone", "commit_email",
    "email_match", "phone_match", "profile_photo_sha256", "media_face_match",
}
VERIFIED_SIGNAL_TYPES = {
    "whatsapp_phone", "commit_email", "email_match", "phone_match", "profile_photo_sha256",
}

_CROSS_ENTITY_SIGNAL_CONFIDENCE = {
    "username_exact": 0.95,
    "whatsapp_phone": 0.98,
    "commit_email": 0.98,
    "email_match": 0.98,
    "phone_match": 0.98,
    "profile_photo_sha256": 0.95,
    "media_face_match": 0.90,
    "real_name_fuzzy": 0.65,
}


def compute_confidence(signals: list[SignalMatch]) -> tuple[float, int, bool]:
    """Return (normalized_confidence, independent_strong_count, is_confirmed).

    A link is auto-CONFIRMED when it rests on at least one genuinely STRONG
    signal — an EXACT match on a rare username, or a VERIFIED identifier (shared
    phone / email / commit email / identical profile photo). These are now gated
    to actually be strong: username_exact is only emitted for identical, non-common
    handles; a match that survives only after digit/punctuation stripping, a common
    handle, or a fuzzy name is WEAK (username_similar / real_name_fuzzy) and is NOT
    counted here — so those rest as *candidates* for human review rather than
    silently auto-confirming (e.g. "b", or "mike" vs "mike123"). Requiring TWO
    strong signals was too strict in practice (a rare exact handle across platforms
    is already trustworthy and is most entities' only evidence).
    """
    score = sum(s.confidence for s in signals)
    strong_types = {s.signal_type for s in signals if s.signal_type in STRONG_SIGNAL_TYPES}

    max_possible = 195.0
    normalized = min(score / max_possible, 1.0) if max_possible > 0 else 0.0
    is_confirmed = len(strong_types) >= 1
    return normalized, len(strong_types), is_confirmed


def _policy_group_key(
    profile: PlatformProfile,
    existing_links: dict[tuple[str, str], object],
) -> tuple[str, ...]:
    entity_id = existing_links.get((profile.source, profile.platform_id))
    if entity_id:
        return ("existing", str(entity_id))
    return ("new", profile.source, profile.platform_id)


def _cross_entity_confidence(signal: SignalMatch) -> float:
    if signal.signal_type in _CROSS_ENTITY_SIGNAL_CONFIDENCE:
        return _CROSS_ENTITY_SIGNAL_CONFIDENCE[signal.signal_type]
    try:
        raw = float(signal.confidence)
    except (TypeError, ValueError):
        return 0.55
    if raw > 1:
        raw = raw / 100.0
    return min(max(raw, 0.0), 1.0)


def _apply_no_auto_merge_policy(
    candidates: list[EntityCandidate],
    existing_links: dict[tuple[str, str], object],
) -> tuple[list[EntityCandidate], list[tuple[tuple[str, ...], tuple[str, ...], SignalMatch]], dict[str, int]]:
    """Split resolver clusters into persistence units without creating merges.

    Existing entity groups stay intact, because they reflect prior persisted
    state or human decisions. Newly discovered accounts become singleton
    entities. Evidence that would have joined two groups is emitted later as a
    cross-entity signal so Review can rank it without silently merging people.
    """
    split_candidates: list[EntityCandidate] = []
    cross_signals: list[tuple[tuple[str, ...], tuple[str, ...], SignalMatch]] = []
    stats = {"auto_merge_candidates_split": 0, "cross_entity_signals": 0}

    for candidate in candidates:
        if not candidate.profiles:
            continue
        groups: dict[tuple[str, ...], list[PlatformProfile]] = {}
        profile_groups: dict[tuple[str, str], tuple[str, ...]] = {}
        for profile in candidate.profiles:
            key = _policy_group_key(profile, existing_links)
            groups.setdefault(key, []).append(profile)
            profile_groups[(profile.source, profile.platform_id)] = key

        if len(groups) > 1:
            stats["auto_merge_candidates_split"] += 1

        intra_signals: dict[tuple[str, ...], list[SignalMatch]] = {
            key: [] for key in groups
        }
        for signal in candidate.signals:
            source_key = profile_groups.get((signal.source_platform, signal.source_record_id))
            target_key = profile_groups.get((signal.target_platform, signal.target_record_id))
            if source_key and target_key and source_key == target_key:
                intra_signals[source_key].append(signal)
            elif source_key and target_key:
                cross_signals.append((source_key, target_key, signal))

        for key, profiles in groups.items():
            split_candidates.append(EntityCandidate(
                profiles=profiles,
                signals=intra_signals.get(key, []),
                merge_policy_key=key,
            ))

    stats["cross_entity_signals"] = len(cross_signals)
    return split_candidates, cross_signals, stats


async def resolve_entities() -> dict:
    """Main identity resolution pipeline. Returns run stats."""
    targets = await load_collection_targets()
    profiles_by_username, no_username_profiles = await load_platform_profiles()
    # commit_email / profile_photo signals are OPTIONAL enrichers. Their queries
    # scan large collector tables (github_commits is ~7.3M rows) and can time out
    # under heavy concurrent load — but that must NOT abort the whole resolution
    # run (which was crashing full_resolution with a TimeoutError). Degrade
    # gracefully to an empty map; the next quieter run picks them back up.
    try:
        commit_emails = await load_commit_emails()
    except Exception:
        logger.warning("load_commit_emails failed (non-fatal, skipping commit_email signal)", exc_info=True)
        commit_emails = {}
    try:
        photo_hashes = await load_profile_photo_hashes()
    except Exception:
        logger.warning("load_profile_photo_hashes failed (non-fatal, skipping profile_photo signal)", exc_info=True)
        photo_hashes = {}
    
    # 2026-07-09: Load NER persons_mentioned from analyzer DB to extend real_name_fuzzy.
    ner_persons: dict[tuple[str, str], list[str]] = {}
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            SELECT epl.source, epl.platform_id, e.metadata->'enrichment'->'persons_mentioned' AS persons
            FROM entity_platform_links epl
            JOIN entities e ON epl.entity_id = e.id
            WHERE e.metadata->'enrichment'->'persons_mentioned' IS NOT NULL
        """)
        for r in rows:
            persons_json = r["persons"]
            if persons_json:
                # asyncpg may return JSONB already-decoded (list/dict) or as a
                # raw string depending on codec config — handle both. (Missing
                # `import json` here previously crashed every full_resolution run
                # with NameError once an entity had persons_mentioned metadata.)
                persons = persons_json if isinstance(persons_json, (list, dict)) \
                    else json.loads(persons_json)
                if persons:
                    # persons_mentioned entries are {"text": <name>, "count": N}
                    # (spaCy NER output from entity_enrichment). Earlier code read
                    # a non-existent "item" key -> KeyError crashed full_resolution.
                    # Tolerate dicts (text/item/name keys) and bare strings.
                    names = []
                    for p in persons:
                        if isinstance(p, dict):
                            v = p.get("text") or p.get("item") or p.get("name")
                        else:
                            v = p
                        if v:
                            names.append(v)
                    if names:
                        ner_persons[(r["source"], r["platform_id"])] = names

    all_profiles: list[PlatformProfile] = []
    for group in profiles_by_username.values():
        all_profiles.extend(group)

    target_sources = {(t["source"], t["target_id"]) for t in targets}

    # Build entities from collection targets
    target_profiles: list[PlatformProfile] = []
    for p in all_profiles:
        if (p.source, p.platform_id) in target_sources:
            target_profiles.append(p)

    # Also match by target_name as username
    target_names = {}
    for t in targets:
        if t["target_name"]:
            target_names[normalize_username(t["target_name"])] = t

    for norm_user, group in profiles_by_username.items():
        if norm_user in target_names:
            for p in group:
                if (p.source, p.platform_id) not in target_sources:
                    target_profiles.append(p)

    # Group profiles into entity candidates by cross-platform matching
    entities: list[EntityCandidate] = []
    assigned: set[tuple[str, str]] = set()

    # Phase 1: cluster by STRICT username (case/punctuation-normalized, digits
    # KEPT) so only genuinely-same handles auto-merge. Within each loose (digit-
    # stripped) group we sub-group by the strict handle: "hongyime" clusters
    # with "hongyime", but "bryanseah" stays a SEPARATE person — later surfaced
    # as a cross-entity username_similar candidate rather than silently merged.
    for norm_user, group in profiles_by_username.items():
        has_target_name = norm_user in target_names
        # A loose handle shared by too many accounts is COMMON (different people) —
        # don't cluster it at all; those accounts stand alone.
        if len(group) > COMMON_USERNAME_ACCOUNTS:
            continue

        by_strict: dict[str, list[PlatformProfile]] = {}
        for p in group:
            strict = normalize_username_strict(p.username) or f"__raw__{(p.username or '').lower()}"
            by_strict.setdefault(strict, []).append(p)

        for strict_key, sub in by_strict.items():
            relevant = [p for p in sub if (p.source, p.platform_id) not in assigned]
            if len(relevant) < 2:
                continue
            has_target = any((p.source, p.platform_id) in target_sources for p in relevant)
            # Corroborated-scope (SYNC #30): keep a cluster if target-linked OR it
            # spans >=2 platforms with the SAME strict handle (cross-platform
            # same-person). Lone single-platform non-target clusters still skip,
            # so group-chat randoms don't become entities.
            n_platforms = len({p.source for p in relevant})
            if not has_target and not has_target_name and n_platforms < 2:
                continue

            candidate = EntityCandidate(profiles=relevant)
            platforms_seen = {p.source for p in relevant}
            if len(platforms_seen) >= 2:
                # Same strict handle across platforms is a strong exact match.
                for i, p1 in enumerate(relevant):
                    for p2 in relevant[i + 1:]:
                        if p1.source != p2.source:
                            candidate.signals.append(SignalMatch(
                                signal_type="username_exact",
                                source_platform=p1.source,
                                target_platform=p2.source,
                                source_record_id=p1.platform_id,
                                target_record_id=p2.platform_id,
                                value=strict_key,
                                confidence=20.0,
                            ))
            entities.append(candidate)
            for p in relevant:
                assigned.add((p.source, p.platform_id))

    # Phase 2: Add unmatched target profiles as single-profile entities
    for p in target_profiles:
        if (p.source, p.platform_id) not in assigned:
            entities.append(EntityCandidate(profiles=[p]))
            assigned.add((p.source, p.platform_id))

    # Phase 3: Enrich with additional signals
    for candidate in entities:
        # WhatsApp phone signals
        for p in candidate.profiles:
            if p.phone:
                for other in candidate.profiles:
                    if other.source != p.source:
                        candidate.signals.append(SignalMatch(
                            signal_type="whatsapp_phone",
                            source_platform="whatsapp",
                            target_platform=other.source,
                            source_record_id=p.platform_id,
                            target_record_id=other.platform_id,
                            value=p.phone,
                            confidence=40.0,
                        ))

        # GitHub commit email signals
        github_profiles = [p for p in candidate.profiles if p.source == "github"]
        for gp in github_profiles:
            for email, user_ids in commit_emails.items():
                if gp.platform_id in user_ids:
                    candidate.signals.append(SignalMatch(
                        signal_type="commit_email",
                        source_platform="github",
                        target_platform="github",
                        source_record_id=gp.platform_id,
                        target_record_id=gp.platform_id,
                        value=email,
                        confidence=35.0,
                    ))

        # Strava real name signal + NER extended (T2.3)
        # Any profile with a name OR NER persons can fuzzy match another profile's name or NER persons.
        # Previously only Strava names were checked against other names.
        for i, p1 in enumerate(candidate.profiles):
            names1 = []
            if p1.name:
                names1.append(p1.name)
            names1.extend(ner_persons.get((p1.source, p1.platform_id), []))
            if not names1:
                continue
                
            for p2 in candidate.profiles[i + 1:]:
                if p1.source == p2.source:
                    continue
                names2 = []
                if p2.name:
                    names2.append(p2.name)
                names2.extend(ner_persons.get((p2.source, p2.platform_id), []))
                if not names2:
                    continue
                
                best_score = 0
                best_pair = None
                for n1 in names1:
                    for n2 in names2:
                        score = fuzz.token_sort_ratio(n1, n2)
                        if score > best_score:
                            best_score = score
                            best_pair = (n1, n2)
                
                if best_score >= NAME_FUZZY_MIN_SCORE:
                    candidate.signals.append(SignalMatch(
                        signal_type="real_name_fuzzy",
                        source_platform=p1.source,
                        target_platform=p2.source,
                        source_record_id=p1.platform_id,
                        target_record_id=p2.platform_id,
                        value=f"{best_pair[0]} ~ {best_pair[1]} ({best_score}%)",
                        confidence=15.0,
                    ))

        # Profile photo SHA256 match
        profile_sources = {(p.source, p.platform_id) for p in candidate.profiles}
        for sha, entries in photo_hashes.items():
            matching = [(s, eid) for s, eid in entries if (s, eid) in profile_sources]
            if len(matching) >= 2:
                for i, (s1, e1) in enumerate(matching):
                    for s2, e2 in matching[i + 1:]:
                        candidate.signals.append(SignalMatch(
                            signal_type="profile_photo_sha256",
                            source_platform=s1,
                            target_platform=s2,
                            source_record_id=e1,
                            target_record_id=e2,
                            value=sha,
                            confidence=20.0,
                        ))

    # Phase 4: Try to merge single-profile entities via name fuzzy match
    # (only if a second independent signal exists)
    singles = [e for e in entities if len(e.profiles) == 1]
    for i, e1 in enumerate(singles):
        for e2 in singles[i + 1:]:
            p1, p2 = e1.profiles[0], e2.profiles[0]
            if p1.source == p2.source:
                continue
            if not p1.name or not p2.name:
                continue
            # A shared name only counts toward a merge if it's a distinctive full
            # name — never a generic "b"/"mike" (see name_is_distinctive).
            if not (name_is_distinctive(p1.name) and name_is_distinctive(p2.name)):
                continue
            score = fuzz.token_sort_ratio(p1.name, p2.name)
            if score >= NAME_FUZZY_MIN_SCORE:
                # Name alone is insufficient — check for a second signal type
                has_other = False
                combined = e1.signals + e2.signals
                for s in combined:
                    if s.signal_type != "real_name_fuzzy":
                        has_other = True
                        break
                if has_other:
                    e1.profiles.extend(e2.profiles)
                    e1.signals.extend(e2.signals)
                    e1.signals.append(SignalMatch(
                        signal_type="real_name_fuzzy",
                        source_platform=p1.source,
                        target_platform=p2.source,
                        source_record_id=p1.platform_id,
                        target_record_id=p2.platform_id,
                        value=f"{p1.name} ~ {p2.name} ({score}%)",
                        confidence=15.0,
                    ))
                    e2.profiles.clear()
                    e2.signals.clear()

    entities = [e for e in entities if e.profiles]

    # Phase 4.5: Link username-less profiles (WhatsApp) to entities.
    # Pre-build (name, candidate, profile) tuples for O(n) lookup per wp.
    unassigned_wp = [wp for wp in no_username_profiles if (wp.source, wp.platform_id) not in assigned]
    logger.info("Phase 4.5: %d username-less profiles to process", len(unassigned_wp))

    entity_names: list[tuple[str, EntityCandidate, PlatformProfile]] = []
    for candidate in entities:
        for p in candidate.profiles:
            if p.name:
                entity_names.append((p.name, candidate, p))
                break

    # P2-2: block index over entity names, so each WhatsApp profile only fuzzy-
    # matches entities that share a name-token prefix instead of all ~N entities.
    # Only distinctive names are match-eligible (P1-1), so only they are indexed.
    block_index: dict[str, list[int]] = {}
    for idx, (ename, _, _) in enumerate(entity_names):
        if not name_is_distinctive(ename):
            continue
        for key in name_block_keys(ename):
            block_index.setdefault(key, []).append(idx)

    matched_count = 0
    for wp in unassigned_wp:
        # P1-1: name is the ONLY linking signal here (WhatsApp profiles have no
        # username), so demote it — only attach on a distinctive full name and a
        # stricter fuzzy threshold. A lone/short/common name falls through to a
        # standalone secondary entity instead of being force-merged on a weak
        # coincidence (the dominant historical false-merge vector).
        if wp.name and name_is_distinctive(wp.name):
            best_match: EntityCandidate | None = None
            best_profile: PlatformProfile | None = None
            best_score = 0
            # P2-2: gather only entities sharing a name-token prefix (block) with
            # this profile; dedupe indices across the profile's block keys.
            cand_idxs: set[int] = set()
            for key in name_block_keys(wp.name):
                cand_idxs.update(block_index.get(key, ()))
            for idx in cand_idxs:
                ename, candidate, eprofile = entity_names[idx]
                score = fuzz.token_sort_ratio(wp.name, ename)
                if score >= NAME_FUZZY_MIN_SCORE_NAME_ONLY and score > best_score:
                    best_score = score
                    best_match = candidate
                    best_profile = eprofile

            if best_match and best_profile:
                best_match.profiles.append(wp)
                best_match.signals.append(SignalMatch(
                    signal_type="real_name_fuzzy",
                    source_platform=wp.source,
                    target_platform=best_profile.source,
                    source_record_id=wp.platform_id,
                    target_record_id=best_profile.platform_id,
                    value=f"{wp.name} ~ {best_profile.name} ({best_score}%)",
                    confidence=15.0,
                ))
                if wp.phone:
                    best_match.signals.append(SignalMatch(
                        signal_type="whatsapp_phone",
                        source_platform="whatsapp",
                        target_platform=best_profile.source,
                        source_record_id=wp.platform_id,
                        target_record_id=best_profile.platform_id,
                        value=wp.phone,
                        confidence=40.0,
                    ))
                assigned.add((wp.source, wp.platform_id))
                matched_count += 1
                continue

        if wp.source == "whatsapp":
            entities.append(EntityCandidate(profiles=[wp]))
            assigned.add((wp.source, wp.platform_id))

    secondary_count = sum(
        1 for e in entities
        if len(e.profiles) == 1 and e.profiles[0].source == "whatsapp" and not e.signals
    )
    logger.info("Phase 4.5: %d matched to existing entities, %d new WhatsApp secondary entities",
                matched_count, secondary_count)

    # Phase 5: Persist to analyzer DB — batched to avoid N×round-trip overhead
    pool = get_analyzer_pool()
    stats = {"entities_created": 0, "entities_updated": 0, "links": 0, "signals": 0}

    async with pool.acquire() as conn:
        existing_links = {}
        for row in await conn.fetch(
            "SELECT entity_id, source, platform_id FROM entity_platform_links"
        ):
            existing_links[(row["source"], row["platform_id"])] = row["entity_id"]

        entities, pending_cross_signals, no_auto_stats = _apply_no_auto_merge_policy(
            entities, existing_links,
        )
        stats.update({k: v for k, v in no_auto_stats.items() if v})
        if no_auto_stats["auto_merge_candidates_split"]:
            logger.info("No-auto-merge policy: %s", no_auto_stats)

        # Pre-compute all entity attributes and assign IDs in Python
        resolved: list[tuple] = []
        seen_entity_ids: set = set()
        for candidate in entities:
            confidence, independent_count, is_confirmed = compute_confidence(candidate.signals)
            # A single-account entity has no merge to doubt — the account is
            # trivially its own person, so it's confirmed. "Candidate" is reserved
            # for MULTI-account merges resting only on weak evidence
            # (username_similar / real_name_fuzzy), which is what a human reviews.
            if len(candidate.profiles) <= 1:
                is_confirmed = True

            # Canonical name, best-first: a proper Strava name, then a distinctive
            # full name, then a username handle (a real "hongyime" beats a
            # 1-char nickname like "b"), then any name, then a source:handle
            # fallback so an account with nothing still isn't "(unnamed)".
            canonical = None
            for p in candidate.profiles:
                if p.source == "strava" and p.name:
                    canonical = p.name
                    break
            if not canonical:
                for p in candidate.profiles:
                    if p.name and name_is_distinctive(p.name):
                        canonical = p.name
                        break
            if not canonical:
                for p in candidate.profiles:
                    if p.username:
                        canonical = p.username
                        break
            if not canonical:
                for p in candidate.profiles:
                    if p.name:
                        canonical = p.name
                        break
            if not canonical and candidate.profiles:
                p = candidate.profiles[0]
                canonical = f"{p.source}:{p.username or p.platform_id}"

            entity_id = None
            for p in candidate.profiles:
                eid = existing_links.get((p.source, p.platform_id))
                if eid and eid not in seen_entity_ids:
                    entity_id = eid
                    break

            is_secondary = (
                len(candidate.profiles) == 1
                and candidate.profiles[0].source == "whatsapp"
                and not candidate.signals
            )
            tier = "secondary" if is_secondary else "primary"
            is_new = entity_id is None
            if is_new:
                entity_id = str(uuid_module.uuid4())

            seen_entity_ids.add(entity_id)
            resolved.append((candidate, entity_id, canonical, confidence,
                              independent_count, tier, is_new, is_confirmed))

        # Batch UPDATE existing entities (one executemany instead of N awaits)
        update_rows = [
            (canonical, confidence, count, tier, eid)
            for _, eid, canonical, confidence, count, tier, is_new, _ in resolved
            if not is_new
        ]
        if update_rows:
            await conn.executemany("""
                UPDATE entities SET
                    canonical_name = $1, confidence_score = $2,
                    signal_count = $3, tier = $4, updated_at = NOW()
                WHERE id = $5
            """, update_rows)
            stats["entities_updated"] = len(update_rows)

        # Batch INSERT new entities with pre-assigned UUIDs
        insert_rows = [
            (eid, tier, canonical, confidence, count)
            for _, eid, canonical, confidence, count, tier, is_new, _ in resolved
            if is_new
        ]
        if insert_rows:
            await conn.executemany("""
                INSERT INTO entities (id, tier, canonical_name, confidence_score, signal_count)
                VALUES ($1::uuid, $2, $3, $4, $5)
            """, insert_rows)
            stats["entities_created"] = len(insert_rows)

        # Batch UPSERT all links in a single UNNEST query. Dedupe by
        # (source, platform_id): the same platform id can surface in >1 cluster
        # (e.g. a social_users username variant, SYNC #33), and Postgres refuses
        # an ON CONFLICT DO UPDATE that would hit the same target row twice in one
        # command (CardinalityViolationError). Keep the first occurrence
        # deterministically so a contested id lands on a single entity.
        eids, sources, pids, usernames, names, confs, confirmed = [], [], [], [], [], [], []
        seen_link_keys: set[tuple[str, str]] = set()
        for candidate, eid, _, confidence, _, _, _, is_confirmed in resolved:
            for p in candidate.profiles:
                key = (p.source, p.platform_id)
                if key in seen_link_keys:
                    continue
                seen_link_keys.add(key)
                eids.append(eid)
                sources.append(p.source)
                pids.append(p.platform_id)
                usernames.append(p.username)
                names.append(p.name)
                confs.append(confidence)
                confirmed.append(is_confirmed)
        if eids:
            # No-auto-merge policy: automatic resolution may refresh link metadata
            # but must not move an existing platform account to a different entity.
            await conn.execute("""
                INSERT INTO entity_platform_links
                    (entity_id, source, platform_id, platform_username, platform_name,
                     confidence, link_method, is_confirmed)
                SELECT entity_id, source, platform_id, platform_username, platform_name,
                       confidence, 'auto', is_confirmed
                FROM UNNEST(
                    $1::uuid[], $2::text[], $3::text[], $4::text[], $5::text[],
                    $6::float8[], $7::bool[]
                ) AS t(entity_id, source, platform_id, platform_username, platform_name,
                       confidence, is_confirmed)
                ON CONFLICT (source, platform_id) DO UPDATE SET
                    platform_username = EXCLUDED.platform_username,
                    platform_name = EXCLUDED.platform_name,
                    confidence = EXCLUDED.confidence,
                    is_confirmed = EXCLUDED.is_confirmed,
                    updated_at = NOW()
            """, eids, sources, pids, usernames, names, confs, confirmed)
            stats["links"] = len(eids)

        # Stale-link cleanup (linking-rework): remove AUTO links for accounts that
        # WERE loaded this run but are no longer clustered into any person — e.g.
        # the frozen "b" telegram accounts linked by old, looser name matching.
        # Guards:
        #   - only accounts present in THIS run's loaded profiles are eligible, so
        #     a source that failed to load (absent from loaded_keys) can never have
        #     its links wiped;
        #   - only link_method='auto' rows — manual splits/merges are user
        #     decisions and are never auto-deleted.
        loaded_keys = {(p.source, p.platform_id) for p in all_profiles}
        loaded_keys |= {(p.source, p.platform_id) for p in no_username_profiles}
        persisted_keys = set(zip(sources, pids))
        stale = [k for k in loaded_keys if k not in persisted_keys]
        if stale:
            del_sources = [s for s, _ in stale]
            del_pids = [pid for _, pid in stale]
            deleted = await conn.execute("""
                DELETE FROM entity_platform_links epl
                USING UNNEST($1::text[], $2::text[]) AS t(source, platform_id)
                WHERE epl.source = t.source AND epl.platform_id = t.platform_id
                  AND epl.link_method = 'auto'
            """, del_sources, del_pids)
            if deleted and deleted != "DELETE 0":
                logger.info("Stale-link cleanup: %s (accounts no longer clustered)", deleted)

        # Single DELETE for all signals, then batch INSERT
        all_eids = [eid for _, eid, *_ in resolved]
        await conn.execute(
            "DELETE FROM identity_signals WHERE entity_id = ANY($1::uuid[]) AND signal_type NOT IN ('bio_mention', 'content_similarity', 'group_cooccurrence', 'email_match', 'cross_platform_link', 'phone_match', 'shared_website', 'shared_route_origin')",
            all_eids,
        )
        signal_rows = []
        for candidate, eid, *_ in resolved:
            for s in candidate.signals:
                signal_rows.append((
                    eid, s.signal_type, s.source_platform, s.source_record_id,
                    s.target_platform, s.target_record_id, s.value, s.confidence,
                ))
        policy_entity_ids = {
            candidate.merge_policy_key: str(eid)
            for candidate, eid, *_ in resolved
            if candidate.merge_policy_key is not None
        }
        for source_key, target_key, signal in pending_cross_signals:
            source_entity_id = policy_entity_ids.get(source_key)
            target_entity_id = policy_entity_ids.get(target_key)
            if not source_entity_id or not target_entity_id or source_entity_id == target_entity_id:
                continue
            signal_rows.append((
                source_entity_id,
                signal.signal_type,
                signal.source_platform,
                signal.source_record_id,
                "entity",
                target_entity_id,
                signal.value,
                _cross_entity_confidence(signal),
            ))
        if signal_rows:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_record_id,
                     target_platform, target_record_id, value, confidence)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """, signal_rows)
            stats["signals"] = len(signal_rows)

        # Cross-entity "similar username" candidates: entities that share a
        # digit-stripped handle but were NOT merged (different strict handles —
        # e.g. bryanseah vs hongyime). Emit a weak CROSS-entity signal
        # (target_record_id = the other entity's UUID) so the scorer surfaces them
        # in Review to confirm/reject, instead of the resolver silently merging
        # possibly-different people. Skipped for handles shared by too many
        # entities (too common to be evidence).
        loose_norm_entities: dict[str, set] = {}
        # Build entity_id → set[platform] so we can filter same-platform pairs.
        entity_platforms: dict[str, set[str]] = {}
        for candidate, eid, *_ in resolved:
            platforms = {p.source for p in candidate.profiles}
            entity_platforms[str(eid)] = platforms
            for n in {normalize_username(p.username) for p in candidate.profiles}:
                if n:
                    # str() — recovered ids are asyncpg UUIDs, new ones are strings.
                    loose_norm_entities.setdefault(n, set()).add(str(eid))
        sim_rows = []
        for n, eid_set in loose_norm_entities.items():
            if not (2 <= len(eid_set) <= SIMILAR_USERNAME_MAX_ENTITIES):
                continue
            eid_list = sorted(eid_set)
            for i, a in enumerate(eid_list):
                for b in eid_list[i + 1:]:
                    # 2026-07-09: skip same-platform pairs — entities that only
                    # exist on the same platform(s) with different user_ids are
                    # definitively different people. username_similar should only
                    # fire cross-platform to be meaningful evidence.
                    if entity_platforms.get(a) == entity_platforms.get(b):
                        continue
                    sim_rows.append((a, "username_similar", "multi", n, "multi", b, n, 0.7))
        if sim_rows:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_record_id,
                     target_platform, target_record_id, value, confidence)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """, sim_rows)
            stats["username_similar_pairs"] = len(sim_rows)

        # Clean up orphaned entities
        orphaned = await conn.execute("""
            DELETE FROM entities
            WHERE id NOT IN (SELECT DISTINCT entity_id FROM entity_platform_links)
        """)
        if orphaned and orphaned != "DELETE 0":
            logger.info("Cleaned up orphaned entities: %s", orphaned)

    stats["entities"] = stats["entities_created"] + stats["entities_updated"]
    logger.info("Entity resolution complete: %s", stats)
    return stats
