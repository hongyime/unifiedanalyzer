"""
Phase 4B: Cross-platform content fingerprinting.

Aggregates text content from all platforms per entity, extracts style
features, and detects entities with suspiciously similar writing patterns
(potential same-person signals).

Sources: TikTok descriptions, YouTube descriptions, Telegram messages,
         WhatsApp messages, Lemon8 descriptions.
"""
import re
import json
import logging
from collections import Counter
from math import sqrt

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

_MIN_TOKENS = 50     # skip entities with too little text
_SIM_THRESHOLD = 0.80  # cosine similarity for same-person signal
_MIN_SHARED_VOCAB = 8  # minimum shared vocab words for meaningful comparison

STOPWORDS = frozenset({
    "i", "me", "my", "we", "our", "you", "your", "he", "him", "his", "she",
    "her", "it", "its", "they", "them", "their", "what", "which", "who",
    "this", "that", "these", "those", "am", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "a", "an", "the", "and", "but", "if", "or", "of", "at", "by", "for",
    "with", "to", "from", "in", "out", "on", "up", "so", "as", "into",
    "not", "no", "can", "will", "just", "more", "also", "get", "go",
    "one", "all", "new", "like", "about", "than", "then", "some", "would",
    "there", "when", "where", "how", "much", "well", "now", "only", "even",
    "very", "over", "such", "here", "too", "any", "each", "other",
    "la", "de", "el", "en", "que", "es", "un", "los", "dan", "di", "yang",
    "im", "dont", "cant", "its", "ive", "id", "s", "t", "re", "ve", "ll",
})

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
MENTION_RE = re.compile(r"@\w+")
HASHTAG_RE = re.compile(r"#\w+")
EMOJI_RE = re.compile(
    "[\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff"
    "\U0001f1e0-\U0001f1ff\U00002702-\U000027b0\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001fa6f\U0001fa70-\U0001faff\U00002600-\U000026ff]+",
    flags=re.UNICODE,
)


def _decode_meta(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _tokenize(text: str) -> list[str]:
    text = URL_RE.sub("", text)
    text = MENTION_RE.sub("", text)
    text = HASHTAG_RE.sub("", text)
    text = EMOJI_RE.sub("", text)
    text = text.lower()
    return [t for t in re.findall(r"\b[a-z]{2,20}\b", text) if t not in STOPWORDS]


def _compute_fingerprint(texts: list[str]) -> dict | None:
    all_tokens: list[str] = []
    total_chars = 0
    total_posts = len(texts)
    exclamations = 0
    questions = 0
    caps_chars = 0
    total_alpha = 0
    emoji_count = 0
    hashtag_count = 0

    for text in texts:
        all_tokens.extend(_tokenize(text))
        total_chars += len(text)
        exclamations += text.count("!")
        questions += text.count("?")
        alpha = re.findall(r"[a-zA-Z]", text)
        caps_chars += sum(1 for c in alpha if c.isupper())
        total_alpha += len(alpha)
        emoji_count += len(EMOJI_RE.findall(text))
        hashtag_count += len(HASHTAG_RE.findall(text))

    if len(all_tokens) < _MIN_TOKENS:
        return None

    token_count = len(all_tokens)
    vocab = Counter(all_tokens)
    top_words = [w for w, _ in vocab.most_common(30)]

    return {
        "token_count": token_count,
        "vocab_size": len(vocab),
        "vocab_richness": round(len(vocab) / token_count, 3),
        "avg_post_length": round(total_chars / total_posts) if total_posts else 0,
        "avg_words_per_post": round(token_count / total_posts) if total_posts else 0,
        "exclamation_per_100": round(exclamations / token_count * 100, 2),
        "question_per_100": round(questions / token_count * 100, 2),
        "caps_ratio": round(caps_chars / total_alpha, 3) if total_alpha else 0,
        "emoji_per_post": round(emoji_count / total_posts, 2) if total_posts else 0,
        "hashtag_per_post": round(hashtag_count / total_posts, 2) if total_posts else 0,
        "top_words": top_words,
        "post_count": total_posts,
        "_vocab_counter": dict(vocab.most_common(50)),
    }


def _cosine_sim(a: dict, b: dict) -> float:
    """Cosine similarity of two word-count dicts."""
    all_words = set(a) | set(b)
    dot = sum(a.get(w, 0) * b.get(w, 0) for w in all_words)
    mag_a = sqrt(sum(v * v for v in a.values()))
    mag_b = sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


async def fingerprint_content() -> dict:
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    # --- Build entity lookup ---
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, source, platform_id FROM entity_platform_links"
        )
        pid_to_entity: dict[tuple[str, str], str] = {
            (l["source"], l["platform_id"]): l["entity_id"] for l in links
        }

        existing_rows = await conn.fetch(
            "SELECT entity_id::text, metadata FROM behavioral_profiles"
        )
        existing_meta: dict[str, dict] = {
            row["entity_id"]: _decode_meta(row["metadata"]) for row in existing_rows
        }

    # --- Collect text per entity ---
    entity_texts: dict[str, list[str]] = {}

    def _add(eid: str | None, text: str | None) -> None:
        if eid and text and text.strip():
            entity_texts.setdefault(eid, []).append(text.strip())

    async with collector.acquire() as conn:
        # TikTok
        try:
            rows = await conn.fetch("""
                SELECT tp.platform_user_id AS pid, p.description AS text
                FROM tiktok_posts p
                JOIN tiktok_profiles tp ON p.profile_id = tp.id
                WHERE p.description IS NOT NULL AND p.description != ''
            """)
            for r in rows:
                _add(pid_to_entity.get(("tiktok", r["pid"])), r["text"])
        except Exception:
            logger.debug("TikTok content load failed", exc_info=True)

        # YouTube
        try:
            rows = await conn.fetch("""
                SELECT ch.platform_channel_id AS pid, v.description AS text
                FROM youtube_videos v
                JOIN youtube_channels ch ON v.channel_id = ch.id
                WHERE v.description IS NOT NULL AND length(v.description) > 20
            """)
            for r in rows:
                _add(pid_to_entity.get(("youtube", r["pid"])), r["text"])
        except Exception:
            logger.debug("YouTube content load failed", exc_info=True)

        # Telegram
        try:
            rows = await conn.fetch("""
                SELECT u.platform_user_id AS pid, m.text
                FROM telegram_messages m
                JOIN telegram_users u ON m.sender_id = u.id
                WHERE m.text IS NOT NULL AND length(m.text) > 5
            """)
            for r in rows:
                _add(pid_to_entity.get(("telegram", r["pid"])), r["text"])
        except Exception:
            logger.debug("Telegram content load failed", exc_info=True)

        # WhatsApp (resolve @lid via lid_map)
        try:
            lid_rows = await conn.fetch("SELECT lid, phone_jid FROM whatsapp_lid_map")
            lid_map = {r["lid"]: r["phone_jid"] for r in lid_rows}
            rows = await conn.fetch("""
                SELECT u.platform_user_id AS raw_pid, m.text
                FROM whatsapp_messages m
                JOIN whatsapp_users u ON m.sender_id = u.id
                WHERE m.text IS NOT NULL AND length(m.text) > 5
                  AND m.from_me = false
            """)
            for r in rows:
                raw_pid = r["raw_pid"] or ""
                pid = lid_map.get(raw_pid, raw_pid) if "@lid" in raw_pid else raw_pid
                _add(pid_to_entity.get(("whatsapp", pid)), r["text"])
        except Exception:
            logger.debug("WhatsApp content load failed", exc_info=True)

        # Lemon8
        try:
            rows = await conn.fetch("""
                SELECT lp.platform_user_id AS pid, p.description AS text
                FROM lemon8_posts p
                JOIN lemon8_profiles lp ON p.profile_id = lp.id
                WHERE p.description IS NOT NULL AND p.description != ''
            """)
            for r in rows:
                _add(pid_to_entity.get(("lemon8", r["pid"])), r["text"])
        except Exception:
            logger.debug("Lemon8 content load failed", exc_info=True)

        # Instagram (once posts flow in)
        try:
            rows = await conn.fetch("""
                SELECT ip.platform_user_id AS pid, p.caption AS text
                FROM instagram_posts p
                JOIN instagram_profiles ip ON p.profile_id = ip.id
                WHERE p.caption IS NOT NULL AND p.caption != ''
            """)
            for r in rows:
                _add(pid_to_entity.get(("instagram", r["pid"])), r["text"])
        except Exception:
            logger.debug("Instagram content load failed", exc_info=True)

    # --- Compute fingerprints ---
    fingerprints: dict[str, dict] = {}
    for entity_id, texts in entity_texts.items():
        fp = _compute_fingerprint(texts)
        if fp:
            fingerprints[entity_id] = fp

    stats = {"entities_fingerprinted": len(fingerprints), "similarity_signals": 0}

    # --- Pairwise similarity comparison ---
    entity_ids = list(fingerprints)
    new_signals: list[tuple] = []
    for i, eid_a in enumerate(entity_ids):
        for eid_b in entity_ids[i + 1:]:
            vocab_a = fingerprints[eid_a]["_vocab_counter"]
            vocab_b = fingerprints[eid_b]["_vocab_counter"]
            shared = set(vocab_a) & set(vocab_b)
            if len(shared) < _MIN_SHARED_VOCAB:
                continue
            sim = _cosine_sim(vocab_a, vocab_b)
            if sim >= _SIM_THRESHOLD:
                new_signals.append((
                    eid_a,
                    "content_similarity",
                    "multi",
                    None, None, None,
                    "multi",
                    eid_b,
                    f"cosine:{sim:.3f}",
                    round(sim * 0.9, 3),
                ))
                stats["similarity_signals"] += 1

    # --- Persist fingerprints and signals ---
    async with analyzer.acquire() as conn:
        for entity_id, fp in fingerprints.items():
            stored_fp = {k: v for k, v in fp.items() if k != "_vocab_counter"}
            existing = await conn.fetchrow(
                "SELECT id, metadata FROM behavioral_profiles WHERE entity_id = $1::uuid",
                entity_id,
            )
            if existing:
                meta = _decode_meta(existing["metadata"])
                meta["content_fingerprint"] = stored_fp
                await conn.execute("""
                    UPDATE behavioral_profiles
                    SET metadata = $1::jsonb, updated_at = NOW()
                    WHERE entity_id = $2::uuid
                """, json.dumps(meta, default=str), entity_id)
            else:
                await conn.execute("""
                    INSERT INTO behavioral_profiles (entity_id, metadata)
                    VALUES ($1::uuid, $2::jsonb)
                    ON CONFLICT (entity_id) DO UPDATE SET
                        metadata = $2::jsonb, updated_at = NOW()
                """, entity_id, json.dumps({"content_fingerprint": stored_fp}, default=str))

        # Clear old content_similarity signals and re-insert
        await conn.execute(
            "DELETE FROM identity_signals WHERE signal_type = 'content_similarity'"
        )
        if new_signals:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, new_signals)

    logger.info("Content fingerprint: %s", stats)
    return stats
