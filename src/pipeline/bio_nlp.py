import re
import json
import hashlib
import logging
from collections import Counter

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)


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

STOPWORDS = frozenset({
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your",
    "yours", "yourself", "yourselves", "he", "him", "his", "himself", "she",
    "her", "hers", "herself", "it", "its", "itself", "they", "them", "their",
    "theirs", "themselves", "what", "which", "who", "whom", "this", "that",
    "these", "those", "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing", "a", "an",
    "the", "and", "but", "if", "or", "because", "as", "until", "while", "of",
    "at", "by", "for", "with", "about", "against", "between", "through",
    "during", "before", "after", "above", "below", "to", "from", "up", "down",
    "in", "out", "on", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "both",
    "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not",
    "only", "own", "same", "so", "than", "too", "very", "s", "t", "can", "will",
    "just", "don", "should", "now", "d", "ll", "m", "o", "re", "ve", "y",
    "ain", "aren", "couldn", "didn", "doesn", "hadn", "hasn", "haven", "isn",
    "ma", "mightn", "mustn", "needn", "shan", "shouldn", "wasn", "weren",
    "won", "wouldn", "im", "ive", "id", "dont", "cant", "wont",
    "also", "like", "get", "go", "make", "one", "two", "de", "la", "el",
    "en", "que", "es", "un", "los", "las", "dan", "di", "yang", "dan",
})

EMOJI_RE = re.compile(
    "[\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff"
    "\U0001f1e0-\U0001f1ff\U00002702-\U000027b0\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001fa6f\U0001fa70-\U0001faff\U00002600-\U000026ff]+",
    flags=re.UNICODE,
)

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
MENTION_RE = re.compile(r"@\w+")
HASHTAG_RE = re.compile(r"#(\w+)")


def extract_tokens(text: str) -> list[str]:
    text = URL_RE.sub("", text)
    text = MENTION_RE.sub("", text)
    text = EMOJI_RE.sub("", text)
    text = text.lower()
    tokens = re.findall(r"\b[a-z]{2,20}\b", text)
    return [t for t in tokens if t not in STOPWORDS]


def extract_hashtags(text: str) -> list[str]:
    return [h.lower() for h in HASHTAG_RE.findall(text)]


def extract_emojis(text: str) -> list[str]:
    return EMOJI_RE.findall(text)


def detect_language_hint(text: str) -> str | None:
    cjk = len(re.findall(r"[一-鿿぀-ゟ゠-ヿ가-힯]", text))
    latin = len(re.findall(r"[a-zA-Z]", text))
    if cjk > latin:
        return "cjk"
    if latin > 0:
        return "latin"
    return None


CATEGORY_KEYWORDS = {
    "tech": {"developer", "engineer", "software", "code", "coding", "tech",
             "programming", "python", "javascript", "data", "ai", "ml",
             "devops", "backend", "frontend", "fullstack", "web", "app",
             "startup", "founder", "cto", "swe", "cs", "computer"},
    "fitness": {"fitness", "gym", "workout", "training", "runner", "running",
                "cycling", "cyclist", "triathlon", "marathon", "crossfit",
                "yoga", "health", "athlete", "sport", "sports", "strava",
                "swim", "hiking", "climbing"},
    "creative": {"artist", "designer", "photographer", "photography", "music",
                 "musician", "writer", "filmmaker", "creative", "art", "design",
                 "illustration", "painting", "drawing", "videographer", "dj"},
    "business": {"ceo", "founder", "entrepreneur", "business", "marketing",
                 "sales", "consultant", "manager", "director", "advisor",
                 "investor", "venture", "growth", "strategy", "brand"},
    "student": {"student", "university", "college", "studying", "learning",
                "school", "grad", "undergraduate", "phd", "masters", "degree"},
    "travel": {"travel", "traveler", "wanderlust", "nomad", "exploring",
               "adventure", "backpacker", "expat", "world", "countries"},
    "food": {"food", "foodie", "chef", "cooking", "cook", "recipe", "kitchen",
             "baking", "restaurant", "culinary", "eat", "eating"},
    "gaming": {"gamer", "gaming", "esports", "twitch", "streamer", "game",
               "games", "playstation", "xbox", "nintendo", "pc"},
}


def categorize(tokens: list[str]) -> dict[str, int]:
    token_set = set(tokens)
    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        overlap = token_set & keywords
        if overlap:
            scores[category] = len(overlap)
    return scores


async def analyze_bios() -> dict:
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    bio_sources = [
        ("github", "SELECT platform_user_id::text AS pid, bio FROM github_users WHERE bio IS NOT NULL AND bio != ''"),
        ("instagram", "SELECT platform_user_id AS pid, bio FROM instagram_profiles WHERE bio IS NOT NULL AND bio != ''"),
        ("telegram", "SELECT platform_user_id AS pid, bio FROM telegram_users WHERE bio IS NOT NULL AND bio != ''"),
        ("tiktok", "SELECT platform_user_id AS pid, bio FROM tiktok_profiles WHERE bio IS NOT NULL AND bio != ''"),
        ("lemon8", "SELECT platform_user_id AS pid, bio FROM lemon8_profiles WHERE bio IS NOT NULL AND bio != ''"),
        ("youtube", "SELECT platform_channel_id AS pid, description AS bio FROM youtube_channels WHERE description IS NOT NULL AND description != ''"),
        ("whatsapp", "SELECT platform_user_id AS pid, COALESCE(about, status) AS bio FROM whatsapp_users WHERE (about IS NOT NULL AND about != '') OR (status IS NOT NULL AND status != '')"),
    ]

    platform_bios: dict[str, dict[str, str]] = {}

    async with collector.acquire() as conn:
        for source, query in bio_sources:
            try:
                rows = await conn.fetch(query)
                for r in rows:
                    if r["bio"]:
                        platform_bios.setdefault(source, {})[r["pid"]] = r["bio"]
            except Exception:
                logger.debug("Skipping bio source %s", source, exc_info=True)

    entity_lookup: dict[tuple[str, str], str] = {}
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, source, platform_id FROM entity_platform_links"
        )
        for l in links:
            entity_lookup[(l["source"], l["platform_id"])] = l["entity_id"]

    entity_bios: dict[str, list[tuple[str, str]]] = {}
    for source, bios in platform_bios.items():
        for pid, bio_text in bios.items():
            entity_id = entity_lookup.get((source, pid))
            if entity_id:
                entity_bios.setdefault(entity_id, []).append((source, bio_text))

    stats = {"entities_analyzed": 0, "bios_processed": 0, "skipped_unchanged": 0}

    async with analyzer.acquire() as conn:
        existing_meta_rows = await conn.fetch(
            "SELECT entity_id::text, metadata FROM behavioral_profiles"
        )
        existing_meta: dict[str, dict] = {}
        for row in existing_meta_rows:
            meta = _decode_meta(row["metadata"])
            if meta:
                existing_meta[str(row["entity_id"])] = meta

    async with analyzer.acquire() as conn:
        for entity_id, bio_list in entity_bios.items():
            combined = "|".join(f"{s}:{t}" for s, t in sorted(bio_list))
            bio_hash = hashlib.md5(combined.encode()).hexdigest()[:16]
            prev_meta = existing_meta.get(entity_id, {})
            if prev_meta.get("bio_nlp", {}).get("bio_hash") == bio_hash:
                stats["skipped_unchanged"] += 1
                continue

            all_tokens: list[str] = []
            all_hashtags: list[str] = []
            all_emojis: list[str] = []
            bio_texts: dict[str, str] = {}
            languages: list[str] = []

            for source, text in bio_list:
                tokens = extract_tokens(text)
                all_tokens.extend(tokens)
                all_hashtags.extend(extract_hashtags(text))
                all_emojis.extend(extract_emojis(text))
                bio_texts[source] = text[:500]
                lang = detect_language_hint(text)
                if lang:
                    languages.append(lang)

            if not all_tokens and not all_hashtags:
                continue

            token_freq = Counter(all_tokens).most_common(20)
            categories = categorize(all_tokens)

            nlp_data = {
                "keywords": [{"word": w, "count": c} for w, c in token_freq],
                "hashtags": [{"tag": t, "count": c} for t, c in Counter(all_hashtags).most_common(10)],
                "categories": categories,
                "top_emojis": [{"emoji": e, "count": c} for e, c in Counter(all_emojis).most_common(5)],
                "language_hints": list(set(languages)),
                "bio_sources": list(bio_texts.keys()),
                "bio_count": len(bio_list),
                "bio_hash": bio_hash,
            }

            existing = await conn.fetchrow(
                "SELECT id, metadata FROM behavioral_profiles WHERE entity_id = $1::uuid",
                entity_id,
            )
            if existing:
                meta = _decode_meta(existing["metadata"])
                meta["bio_nlp"] = nlp_data
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
                """, entity_id, json.dumps({"bio_nlp": nlp_data}, default=str))

            stats["entities_analyzed"] += 1
            stats["bios_processed"] += len(bio_list)

    logger.info("Bio NLP analysis: %s", stats)
    return stats
