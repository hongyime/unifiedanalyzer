import html

from src.notifications import telegram


async def whois_lookup(query: str) -> str:
    from src.db.connection import get_analyzer_pool, get_collector_pool

    like = f"%{query.strip()}%"
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT e.id::text, e.canonical_name, e.tier, e.confidence_score,
                   e.signal_count, e.last_seen_at
            FROM entities e
            WHERE e.canonical_name ILIKE $1
               OR EXISTS (
                   SELECT 1 FROM entity_platform_links epl
                   WHERE epl.entity_id = e.id
                     AND epl.platform_username ILIKE $1
               )
            ORDER BY e.confidence_score DESC NULLS LAST
            LIMIT 1
            """,
            like,
        )
        if not row:
            return f"\u2753 No entity found for <code>{html.escape(query[:80])}</code>"
        eid = row["id"]
        links = await conn.fetch(
            """
            SELECT source, platform_id, platform_username,
                   COALESCE(NULLIF(platform_username,''),
                            NULLIF(platform_name,''),
                            platform_id) AS handle
            FROM entity_platform_links
            WHERE entity_id = $1::uuid
            ORDER BY confidence DESC
            """,
            eid,
        )
        sigs = await conn.fetch(
            """
            SELECT signal_type, confidence
            FROM identity_signals
            WHERE entity_id = $1::uuid
            ORDER BY confidence DESC NULLS LAST
            LIMIT 5
            """,
            eid,
        )

    wa_jids = [lnk["platform_id"] for lnk in links if lnk["source"] == "whatsapp" and lnk["platform_id"]]
    usernames = list({lnk["platform_username"] for lnk in links if lnk["platform_username"]})
    emails = [u for u in usernames if "@" in u]
    gaia_line = ""
    disc_count = 0
    phone_info = ""
    wa_device_count = 0
    try:
        cpool = get_collector_pool()
        async with cpool.acquire() as cconn:
            if emails:
                grow = await cconn.fetchrow(
                    "SELECT rt.target_value AS email, ro.value "
                    "FROM recon_observations ro "
                    "JOIN recon_targets rt ON rt.id = ro.target_id "
                    "WHERE ro.module = 'ghunt' AND ro.observation_type = 'GAIA_ID' "
                    "  AND rt.target_value = ANY($1::text[]) LIMIT 1",
                    emails,
                )
                if grow:
                    gaia_line = (
                        f"{html.escape(grow['email'])} "
                        f"GAIA:{html.escape(str(grow['value'] or ''))[:20]}"
                    )
            if usernames:
                disc_count = int(await cconn.fetchval(
                    "SELECT COUNT(DISTINCT ro.value) FROM recon_observations ro "
                    "JOIN recon_targets rt ON rt.id = ro.target_id "
                    "WHERE ro.module = 'maigret' "
                    "  AND ro.observation_type = 'ACCOUNT_EXTERNAL_OWNED' "
                    "  AND rt.target_value = ANY($1::text[])",
                    usernames,
                ) or 0)
            if wa_jids:
                prow = await cconn.fetchrow(
                    "SELECT carrier, region_name FROM wa_phone_intel "
                    "WHERE phone_jid = ANY($1::text[]) LIMIT 1",
                    wa_jids,
                )
                if prow:
                    phone_info = ", ".join(v for v in [prow["region_name"], prow["carrier"]] if v)
                drow = await cconn.fetchrow(
                    "SELECT SUM(COALESCE(device_count, 0)) AS total "
                    "FROM wa_device_observations "
                    "WHERE phone_jid = ANY($1::text[])",
                    wa_jids,
                )
                if drow and drow["total"]:
                    wa_device_count = int(drow["total"])
    except Exception:
        pass  # Existing optional enrichment: preserve lookup output if Collector is unavailable.

    base_url = telegram.get_dashboard_url()
    name = html.escape(row["canonical_name"] or "Unknown")
    tier = row["tier"] or "?"
    conf = f"{float(row['confidence_score']):.0%}" if row["confidence_score"] is not None else "?"
    handle_parts = [f"{html.escape(lnk['source'])}:{html.escape(lnk['handle'])}" for lnk in links[:8] if lnk["handle"]]
    sig_parts = [
        f"{s['signal_type'].replace('_', ' ')} ({float(s['confidence']):.0%})"
        if s["confidence"] is not None else s["signal_type"].replace("_", " ")
        for s in sigs
    ]
    lines = [f"\U0001f464 <b>{name}</b> [{tier}] conf {conf}"]
    if handle_parts:
        _sep = " \u00b7 "
        lines.append(f"Handles: {_sep.join(handle_parts)}")
    if sig_parts:
        lines.append(f"Signals: {'; '.join(sig_parts)}")
    if gaia_line:
        lines.append(f"Google: {gaia_line}")
    if disc_count:
        lines.append(f"Discovered accounts: {disc_count}")
    if phone_info:
        lines.append(f"Phone: {phone_info}")
    if wa_device_count:
        lines.append(f"WA devices: {wa_device_count}")
    lines.append(f"{base_url}/entities/{eid}")
    return "\n".join(lines)
