import asyncio
import json
from dotenv import load_dotenv
load_dotenv()
from src.db.connection import init_pools, close_pools, get_analyzer_pool


async def main():
    await init_pools()
    a = get_analyzer_pool()

    async with a.acquire() as conn:
        # Show raw type and value of metadata for a few rows
        rows = await conn.fetch("""
            SELECT entity_id::text, metadata,
                   metadata->'bio_nlp' AS bio_nlp_raw,
                   metadata->'bio_nlp'->>'bio_hash' AS bio_hash_raw
            FROM behavioral_profiles
            LIMIT 3
        """)
        for r in rows:
            meta = r["metadata"]
            print(f"entity={r['entity_id'][:8]}")
            print(f"  metadata type: {type(meta)}")
            print(f"  metadata keys: {list(meta.keys()) if isinstance(meta, dict) else 'NOT A DICT'}")
            print(f"  bio_nlp_raw type: {type(r['bio_nlp_raw'])}")
            print(f"  bio_hash_raw: {r['bio_hash_raw']}")
            if isinstance(meta, dict) and "bio_nlp" in meta:
                bio_nlp = meta["bio_nlp"]
                print(f"  bio_nlp type: {type(bio_nlp)}")
                if isinstance(bio_nlp, dict):
                    print(f"  bio_nlp keys: {list(bio_nlp.keys())}")
                    print(f"  bio_hash: {bio_nlp.get('bio_hash', 'MISSING')}")
                else:
                    print(f"  bio_nlp value (not dict): {repr(bio_nlp)[:100]}")
            print()

        # Check distribution: how many have bio_nlp but no bio_hash (old format)
        no_hash = await conn.fetchval("""
            SELECT COUNT(*) FROM behavioral_profiles
            WHERE metadata ? 'bio_nlp'
              AND (metadata->'bio_nlp'->>'bio_hash') IS NULL
        """)
        with_hash = await conn.fetchval("""
            SELECT COUNT(*) FROM behavioral_profiles
            WHERE (metadata->'bio_nlp'->>'bio_hash') IS NOT NULL
        """)
        print(f"bio_nlp without bio_hash: {no_hash}")
        print(f"bio_nlp with bio_hash: {with_hash}")

    await close_pools()


asyncio.run(main())
