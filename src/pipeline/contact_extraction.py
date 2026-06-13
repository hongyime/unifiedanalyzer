"""
Phase 5E/5F: Contact/link extraction — high-confidence cross-platform
identity signals derived from emails, phone numbers, and links found
in bios and post/video/message text.

New signal types (fed into identity_scorer.compute_identity_scores):

  - email_match: two different entities' bios/content reference the
    same email address. Near-certain — personal emails are rarely
    shared between unrelated people. Generic addresses (info@, pr@,
    etc.) and emails shared by 3+ entities (agency/shared inboxes) are
    excluded.

  - cross_platform_link: a bio/content contains a URL pointing to a
    known platform profile (instagram.com/x, tiktok.com/@x, etc.) that
    resolves via entity_platform_links to a DIFFERENT entity. This is
    a self-disclosed cross-platform identity — stronger evidence than
    a bare @handle mention (bio_mention), since it's an explicit link
    rather than a name-drop.

  - phone_match: a bio/content contains a phone number that exactly
    matches another entity's WhatsApp phone JID. Near-certain, same
    tier as email_match — personal phone numbers are rarely shared.

  - shared_website: two different entities' bios/content link to the
    same non-platform personal website/domain (link aggregators like
    linktr.ee and common platform/email domains are excluded). Weaker
    than email/phone — a shared site can indicate a brand/team rather
    than the same person, so this is weighted lower.

Scans the same bio sources as bio_mention.py plus the same content
sources as content_fingerprint.py (post descriptions/captions/messages),
since contact info often appears in post text rather than the profile
bio itself (e.g. a YouTube video description with a contact email).

target_record_id convention: all new types store the *other entity's
UUID as text* directly (same convention as content_similarity /
temporal_copost / group_cooccurrence) — no extra resolution needed in
identity_scorer.
"""
import re
import logging
from collections import defaultdict

from src.db.connection import get_analyzer_pool, get_collector_pool
from src.pipeline.bio_mention import BIO_SOURCES, SOURCE_TABLE, _normalize_mention
from src.pipeline.entity_resolver import load_commit_emails

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Local-part prefixes that indicate a shared/agency/business inbox rather
# than a personal identifier — skip these even if they only appear twice.
_GENERIC_EMAIL_PREFIXES = frozenset({
    "info", "contact", "support", "hello", "hi", "business", "enquiry",
    "enquiries", "admin", "booking", "bookings", "pr", "press", "marketing",
    "sales", "team", "management", "collab", "collabs", "collaboration",
    "collaborations", "partnership", "partnerships", "sponsor",
    "sponsorship", "noreply", "no-reply",
})

# (target_platform, regex capturing the profile handle from a URL)
PLATFORM_LINK_PATTERNS: dict[str, re.Pattern] = {
    "instagram": re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})", re.IGNORECASE),
    "tiktok": re.compile(r"tiktok\.com/@([A-Za-z0-9_.]{2,30})", re.IGNORECASE),
    "youtube": re.compile(r"youtube\.com/(?:@|c/|channel/|user/)([A-Za-z0-9_.-]{2,40})", re.IGNORECASE),
    "telegram": re.compile(r"t\.me/([A-Za-z0-9_]{2,32})", re.IGNORECASE),
    "github": re.compile(r"github\.com/([A-Za-z0-9_-]{2,39})", re.IGNORECASE),
}

# URL path segments that look like a handle but are actually reserved
# routes (post/video pages, app sections, etc.) — skip these.
_RESERVED_PATH_SEGMENTS = frozenset({
    "p", "reel", "reels", "explore", "stories", "tv", "watch", "shorts",
    "playlist", "hashtag", "tag", "channel", "video", "videos", "results",
    "search", "orgs", "marketplace", "sponsors", "settings", "notifications",
    "feed", "share", "embed", "live", "about",
})

# Candidate phone numbers: optional + prefix, digit, then 7-16 digit/
# separator chars, ending in a digit. Exact-match against WhatsApp phone
# JIDs after stripping separators provides the precision (a coincidental
# 9-15 digit collision with a real phone number is effectively impossible).
PHONE_CANDIDATE_RE = re.compile(r"[+]?[\d][\d\s\-().]{7,16}\d")

# Personal-website links: require an explicit scheme or "www." prefix so
# we don't match incidental "word.word" patterns in prose.
WEBSITE_RE = re.compile(
    r"(?:https?://)?www\.([a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z]{2,24})+)"
    r"|https?://([a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z]{2,24})+)",
    re.IGNORECASE,
)

# Domains that are platforms, link aggregators, email providers, or other
# infrastructure shared by unrelated people — not evidence of identity.
_EXCLUDED_WEBSITE_DOMAINS = frozenset({
    "instagram.com", "tiktok.com", "youtube.com", "youtu.be", "t.me",
    "telegram.me", "telegram.org", "github.com", "twitter.com", "x.com",
    "facebook.com", "fb.com", "fb.watch", "linkedin.com", "lemon8-app.com",
    "whatsapp.com", "wa.me", "snapchat.com", "pinterest.com", "reddit.com",
    "linktr.ee", "linktree.com", "beacons.ai", "bio.link", "linkin.bio",
    "campsite.bio", "msha.ke", "bio.site", "carrd.co", "lnk.bio", "tap.bio",
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "live.com", "googlemail.com", "google.com", "apple.com", "spotify.com",
    "amazon.com", "amzn.to", "bit.ly", "tinyurl.com", "shopify.com",
    "discord.gg", "discord.com", "patreon.com", "onlyfans.com",
    "venmo.com", "paypal.com", "paypal.me", "cash.app", "apps.apple.com",
    "play.google.com", "forms.gle", "docs.google.com", "drive.google.com",
})

# Content/post sources mirroring content_fingerprint.py's coverage
# (excluding WhatsApp, handled separately below for @lid resolution).
CONTENT_SOURCES = [
    ("tiktok", "tiktok_posts", """
        SELECT tp.platform_user_id AS pid, p.description AS text
        FROM tiktok_posts p
        JOIN tiktok_profiles tp ON p.profile_id = tp.id
        WHERE p.description IS NOT NULL AND p.description != ''
    """),
    ("youtube", "youtube_videos", """
        SELECT ch.platform_channel_id AS pid, v.description AS text
        FROM youtube_videos v
        JOIN youtube_channels ch ON v.channel_id = ch.id
        WHERE v.description IS NOT NULL AND length(v.description) > 20
    """),
    ("telegram", "telegram_messages", """
        SELECT u.platform_user_id AS pid, m.text AS text
        FROM telegram_messages m
        JOIN telegram_users u ON m.sender_id = u.id
        WHERE m.text IS NOT NULL AND length(m.text) > 5
    """),
    ("lemon8", "lemon8_posts", """
        SELECT lp.platform_user_id AS pid, p.description AS text
        FROM lemon8_posts p
        JOIN lemon8_profiles lp ON p.profile_id = lp.id
        WHERE p.description IS NOT NULL AND p.description != ''
    """),
    ("instagram", "instagram_posts", """
        SELECT ip.platform_user_id AS pid, p.caption AS text
        FROM instagram_posts p
        JOIN instagram_profiles ip ON p.profile_id = ip.id
        WHERE p.caption IS NOT NULL AND p.caption != ''
    """),
]


def _extract_emails(text: str) -> list[str]:
    return [m.group(0).lower() for m in EMAIL_RE.finditer(text)]


def _extract_platform_links(text: str) -> list[tuple[str, str]]:
    """Return [(target_platform, normalized_handle), ...] for profile URLs in text."""
    results = []
    for platform, pattern in PLATFORM_LINK_PATTERNS.items():
        for m in pattern.finditer(text):
            raw_handle = m.group(1)
            if raw_handle.lower() in _RESERVED_PATH_SEGMENTS:
                continue
            norm = _normalize_mention(raw_handle)
            if norm:
                results.append((platform, norm))
    return results


def _extract_phone_numbers(text: str) -> list[str]:
    """Return normalized (digits-only) phone number candidates from text.

    Also yields the variant with a leading "00" international-dialing
    prefix stripped, so e.g. "0044 7723 442078" can match WhatsApp's
    "447723442078" JID form.
    """
    results = []
    for m in PHONE_CANDIDATE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 9 <= len(digits) <= 15:
            results.append(digits)
        if digits.startswith("00") and 9 <= len(digits) - 2 <= 15:
            results.append(digits[2:])
    return results


def _extract_website_domains(text: str) -> list[str]:
    """Return lowercase personal-website domains (excluding platforms/aggregators)."""
    results = []
    for m in WEBSITE_RE.finditer(text):
        domain = (m.group(1) or m.group(2) or "").lower()
        if domain and domain not in _EXCLUDED_WEBSITE_DOMAINS:
            results.append(domain)
    return results


async def extract_contacts() -> dict:
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    # --- entity_platform_links lookups ---
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, source, platform_id, platform_username FROM entity_platform_links"
        )

    pid_to_entity: dict[tuple[str, str], str] = {
        (l["source"], l["platform_id"]): l["entity_id"] for l in links
    }
    # (target_platform, normalized_username) -> entity_id
    platform_username_to_entity: dict[tuple[str, str], str] = {}
    for l in links:
        if l["platform_username"]:
            norm = _normalize_mention(l["platform_username"])
            if norm:
                platform_username_to_entity[(l["source"], norm)] = l["entity_id"]

    # phone digits (from WhatsApp JID "<digits>@s.whatsapp.net") -> entity_id
    phone_to_entity: dict[str, str] = {}
    for l in links:
        if l["source"] == "whatsapp" and l["platform_id"] and l["platform_id"].endswith("@s.whatsapp.net"):
            digits = l["platform_id"].split("@", 1)[0]
            if digits.isdigit() and 9 <= len(digits) <= 15:
                phone_to_entity[digits] = l["entity_id"]

    # --- collect (source_platform, text) per entity from bios + content ---
    entity_texts: dict[str, list[tuple[str, str]]] = defaultdict(list)
    # Bio-only text, kept separate for shared_website: post/video descriptions
    # are full of sponsor/affiliate/credit links shared across unrelated
    # creators (royalty-free music sites, gear affiliates, etc.), whereas a
    # profile bio linking to a personal site is a deliberate self-disclosure.
    entity_bio_texts: dict[str, list[str]] = defaultdict(list)

    async with collector.acquire() as conn:
        # Bios (profile-level)
        for source, query in BIO_SOURCES:
            try:
                rows = await conn.fetch(query)
                for r in rows:
                    eid = pid_to_entity.get((source, str(r["pid"])))
                    if eid and r["bio"]:
                        entity_texts[eid].append((source, r["bio"]))
                        entity_bio_texts[eid].append(r["bio"])
            except Exception:
                logger.debug("Contact extraction: skipping bio source %s", source, exc_info=True)

        # Content/post text
        for source, _table, query in CONTENT_SOURCES:
            try:
                rows = await conn.fetch(query)
                for r in rows:
                    eid = pid_to_entity.get((source, str(r["pid"])))
                    if eid and r["text"]:
                        entity_texts[eid].append((source, r["text"]))
            except Exception:
                logger.debug("Contact extraction: skipping content source %s", source, exc_info=True)

        # Note: WhatsApp chat messages are deliberately NOT scanned here.
        # Unlike bios/posts (content an entity publishes about themselves),
        # WhatsApp messages are private correspondence that routinely
        # contains OTHER people's contact info (shared contacts, forwarded
        # messages) — too noisy for "near-certain self-disclosure" signals.

    # --- extract emails, phone numbers, links, and websites per entity ---
    email_to_entities: dict[str, set[str]] = defaultdict(set)
    domain_to_entities: dict[str, set[str]] = defaultdict(set)
    # eid -> set of (source_platform_of_text, target_platform, normalized_handle, target_eid)
    entity_links_found: dict[str, set[tuple[str, str, str, str]]] = defaultdict(set)
    # eid -> set of (source_platform_of_text, phone_digits, target_eid)
    entity_phones_found: dict[str, set[tuple[str, str, str]]] = defaultdict(set)

    for eid, texts in entity_texts.items():
        for source_platform, text in texts:
            if "unifiedcollector" in text.lower():
                continue  # collector setup/test artifact, not user content
            for email in _extract_emails(text):
                email_to_entities[email].add(eid)
            for target_platform, handle in _extract_platform_links(text):
                target_eid = platform_username_to_entity.get((target_platform, handle))
                if target_eid and target_eid != eid:
                    entity_links_found[eid].add((source_platform, target_platform, handle, target_eid))
            for phone in _extract_phone_numbers(text):
                target_eid = phone_to_entity.get(phone)
                if target_eid and target_eid != eid:
                    entity_phones_found[eid].add((source_platform, phone, target_eid))

    for eid, bios in entity_bio_texts.items():
        for bio in bios:
            for domain in _extract_website_domains(bio):
                domain_to_entities[domain].add(eid)

    # --- merge in GitHub commit emails (resolved to entity_ids) ---
    commit_emails = await load_commit_emails()
    for email, github_pids in commit_emails.items():
        for pid in github_pids:
            eid = pid_to_entity.get(("github", pid))
            if eid:
                email_to_entities[email].add(eid)

    # --- build email_match signals: exactly 2 distinct entities, non-generic ---
    new_signals: list[tuple] = []
    email_match_pairs = 0
    for email, eids in email_to_entities.items():
        local_part = email.split("@", 1)[0]
        if local_part in _GENERIC_EMAIL_PREFIXES:
            continue
        if len(eids) != 2:
            continue
        a, b = sorted(eids)
        new_signals.append((a, "email_match", "multi", None, None, None, "multi", b, email, 0.90))
        email_match_pairs += 1

    # --- build cross_platform_link signals ---
    # Skip source entities that link to many distinct targets — that pattern
    # indicates a podcast/curator crediting multiple guests or sponsors
    # rather than disclosing their own other accounts (same false-positive
    # class as bio_mention: a link to someone else's profile isn't evidence
    # that you ARE that person).
    link_count = 0
    for eid, found in entity_links_found.items():
        distinct_targets = {target_eid for _, _, _, target_eid in found}
        if len(distinct_targets) > 2:
            continue
        for source_platform, target_platform, handle, target_eid in found:
            new_signals.append((
                eid, "cross_platform_link", source_platform, None, None, None,
                target_platform, target_eid, f"{target_platform}:{handle}", 0.75,
            ))
            link_count += 1

    # --- build phone_match signals: bio/content phone number matches another
    # entity's WhatsApp JID exactly ---
    phone_match_count = 0
    for eid, found in entity_phones_found.items():
        for source_platform, phone, target_eid in found:
            new_signals.append((
                eid, "phone_match", source_platform, None, None, None,
                "whatsapp", target_eid, f"phone:{phone}", 0.90,
            ))
            phone_match_count += 1

    # --- build shared_website signals: exactly 2 distinct entities link to
    # the same non-platform personal domain ---
    shared_website_count = 0
    for domain, eids in domain_to_entities.items():
        if len(eids) != 2:
            continue
        a, b = sorted(eids)
        new_signals.append((a, "shared_website", "multi", None, None, None, "multi", b, domain, 0.65))
        shared_website_count += 1

    # --- persist: delete-then-executemany (same pattern as other signal types) ---
    # source_table IS NULL scopes this delete to contact_extraction's own
    # rows — media_analysis.py / media_analysis_tier1.py emit the same
    # signal_types with source_table='media_items' and manage their own
    # scoped deletes (see media_common.emit_media_contact_signals).
    async with analyzer.acquire() as conn:
        await conn.execute(
            "DELETE FROM identity_signals WHERE signal_type = ANY($1::text[]) AND source_table IS NULL",
            ["email_match", "cross_platform_link", "phone_match", "shared_website"],
        )
        if new_signals:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, new_signals)

    stats = {
        "entities_scanned": len(entity_texts),
        "email_match_signals": email_match_pairs,
        "cross_platform_link_signals": link_count,
        "phone_match_signals": phone_match_count,
        "shared_website_signals": shared_website_count,
    }
    logger.info("Contact extraction: %s", stats)
    return stats
