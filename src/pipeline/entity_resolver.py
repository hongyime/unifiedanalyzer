import re
import uuid as uuid_module
import logging
from dataclasses import dataclass, field
from rapidfuzz import fuzz

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

USERNAME_STRIP_CHARS = "._-"
NAME_FUZZY_MIN_SCORE = 85
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
            "SELECT platform_user_id, username, first_name, last_name FROM telegram_users"
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


def compute_confidence(signals: list[SignalMatch]) -> tuple[float, int]:
    """Compute confidence score and independent signal count.

    Deduplicates by (signal_type, source_platform) to count truly independent signals.
    """
    score = 0.0
    seen_types: set[str] = set()

    for s in signals:
        score += s.confidence

    independent = set()
    for s in signals:
        independent.add((s.signal_type, s.source_platform))

    max_possible = 195.0
    normalized = min(score / max_possible, 1.0) if max_possible > 0 else 0.0
    return normalized, len(independent)


async def resolve_entities() -> dict:
    """Main identity resolution pipeline. Returns run stats."""
    targets = await load_collection_targets()
    profiles_by_username, no_username_profiles = await load_platform_profiles()
    commit_emails = await load_commit_emails()
    photo_hashes = await load_profile_photo_hashes()

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

    # Phase 1: Username exact match clustering
    for norm_user, group in profiles_by_username.items():
        relevant = [p for p in group if (p.source, p.platform_id) not in assigned]
        if len(relevant) < 2:
            continue

        has_target = any((p.source, p.platform_id) in target_sources for p in relevant)
        has_target_name = norm_user in target_names
        if not has_target and not has_target_name:
            continue

        candidate = EntityCandidate(profiles=relevant)
        platforms_seen = set()
        for p in relevant:
            platforms_seen.add(p.source)

        if len(platforms_seen) >= 2:
            for i, p1 in enumerate(relevant):
                for p2 in relevant[i + 1:]:
                    if p1.source != p2.source:
                        candidate.signals.append(SignalMatch(
                            signal_type="username_exact",
                            source_platform=p1.source,
                            target_platform=p2.source,
                            source_record_id=p1.platform_id,
                            target_record_id=p2.platform_id,
                            value=norm_user,
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

        # Strava real name signal
        strava_profiles = [p for p in candidate.profiles if p.source == "strava" and p.name]
        for sp in strava_profiles:
            for other in candidate.profiles:
                if other.source != "strava" and other.name:
                    score = fuzz.token_sort_ratio(sp.name, other.name)
                    if score >= NAME_FUZZY_MIN_SCORE:
                        candidate.signals.append(SignalMatch(
                            signal_type="real_name_fuzzy",
                            source_platform="strava",
                            target_platform=other.source,
                            source_record_id=sp.platform_id,
                            target_record_id=other.platform_id,
                            value=f"{sp.name} ~ {other.name} ({score}%)",
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

    matched_count = 0
    for wp in unassigned_wp:
        if wp.name:
            best_match: EntityCandidate | None = None
            best_profile: PlatformProfile | None = None
            best_score = 0
            for ename, candidate, eprofile in entity_names:
                score = fuzz.token_sort_ratio(wp.name, ename)
                if score >= NAME_FUZZY_MIN_SCORE and score > best_score:
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

        # Pre-compute all entity attributes and assign IDs in Python
        resolved: list[tuple] = []
        seen_entity_ids: set = set()
        for candidate in entities:
            confidence, independent_count = compute_confidence(candidate.signals)
            is_confirmed = confidence >= CONFIDENCE_THRESHOLD and independent_count >= MIN_SIGNALS

            canonical = None
            for p in candidate.profiles:
                if p.source == "strava" and p.name:
                    canonical = p.name
                    break
            if not canonical:
                for p in candidate.profiles:
                    if p.name:
                        canonical = p.name
                        break
            if not canonical:
                for p in candidate.profiles:
                    if p.username:
                        canonical = p.username
                        break

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

        # Batch UPSERT all links in a single UNNEST query
        eids, sources, pids, usernames, names, confs, confirmed = [], [], [], [], [], [], []
        for candidate, eid, _, confidence, _, _, _, is_confirmed in resolved:
            for p in candidate.profiles:
                eids.append(eid)
                sources.append(p.source)
                pids.append(p.platform_id)
                usernames.append(p.username)
                names.append(p.name)
                confs.append(confidence)
                confirmed.append(is_confirmed)
        if eids:
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
                    entity_id = EXCLUDED.entity_id,
                    platform_username = EXCLUDED.platform_username,
                    platform_name = EXCLUDED.platform_name,
                    confidence = EXCLUDED.confidence,
                    is_confirmed = EXCLUDED.is_confirmed,
                    updated_at = NOW()
            """, eids, sources, pids, usernames, names, confs, confirmed)
            stats["links"] = len(eids)

        # Single DELETE for all signals, then batch INSERT
        all_eids = [eid for _, eid, *_ in resolved]
        await conn.execute(
            "DELETE FROM identity_signals WHERE entity_id = ANY($1::uuid[])", all_eids
        )
        signal_rows = []
        for candidate, eid, *_ in resolved:
            for s in candidate.signals:
                signal_rows.append((
                    eid, s.signal_type, s.source_platform, s.source_record_id,
                    s.target_platform, s.target_record_id, s.value, s.confidence,
                ))
        if signal_rows:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_record_id,
                     target_platform, target_record_id, value, confidence)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """, signal_rows)
            stats["signals"] = len(signal_rows)

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
