"""
Phase 6 small-batch validation (task 14, docs/media_analysis_plan.md).

Runs 6C -> 6C.2 -> 6H -> 6A -> 6B -> 6D -> 6F with small limits, then prints
media_analysis row counts by analysis_type and any new identity_signals rows
for the three Phase 6 signal types.
"""
import asyncio, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
os.chdir(r"C:\unifiedanalyzer")
sys.path.insert(0, r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool
from src.pipeline.media_analysis import (
    analyze_media_exif, analyze_media_phash, analyze_media_pdf_text, extract_pdf_images,
)
from src.pipeline.media_analysis_tier1 import analyze_media_ocr, analyze_media_faces, extract_video_frames


async def main():
    await init_pools(apply_schema_ddl=False)

    print("=== 6C analyze_media_pdf_text(limit=10) ===")
    print(await analyze_media_pdf_text(limit=10))

    print("\n=== 6C.2 extract_pdf_images(limit=10) ===")
    print(await extract_pdf_images(limit=10))

    print("\n=== 6H extract_video_frames(limit=2) ===")
    print(await extract_video_frames(limit=2))

    print("\n=== 6A analyze_media_exif(limit=20) ===")
    print(await analyze_media_exif(limit=20))

    print("\n=== 6B analyze_media_phash(limit=20) ===")
    print(await analyze_media_phash(limit=20))

    print("\n=== 6D analyze_media_ocr(limit=10) ===")
    print(await analyze_media_ocr(limit=10))

    print("\n=== 6F analyze_media_faces(limit=10) ===")
    print(await analyze_media_faces(limit=10))

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        print("\n=== media_analysis row counts by analysis_type ===")
        rows = await conn.fetch("""
            SELECT analysis_type, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE parent_media_item_id IS NOT NULL) AS derived
            FROM media_analysis GROUP BY analysis_type ORDER BY analysis_type
        """)
        for r in rows:
            print(f"  {r['analysis_type']:<20} {r['n']:>6}  (derived: {r['derived']})")

        print("\n=== identity_signals: Phase 6 signal types ===")
        rows = await conn.fetch("""
            SELECT signal_type, COUNT(*) AS n FROM identity_signals
            WHERE signal_type IN ('media_gps_colocation', 'media_perceptual_match', 'media_face_match')
            GROUP BY signal_type ORDER BY signal_type
        """)
        if rows:
            for r in rows:
                print(f"  {r['signal_type']:<25} {r['n']}")
        else:
            print("  (none yet)")

        print("\n=== identity_signals: media-derived contact signals (pdf_text/ocr_text) ===")
        rows = await conn.fetch("""
            SELECT signal_type, source_column, COUNT(*) AS n FROM identity_signals
            WHERE source_table = 'media_items'
            GROUP BY signal_type, source_column ORDER BY signal_type, source_column
        """)
        if rows:
            for r in rows:
                print(f"  {r['signal_type']:<20} {r['source_column']:<10} {r['n']}")
        else:
            print("  (none yet)")

    await close_pools()


asyncio.run(main())
