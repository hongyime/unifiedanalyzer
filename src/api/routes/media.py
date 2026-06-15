"""Media analysis browser API.

Phase 6 (docs/media_analysis_plan.md) populated the `media_analysis` table —
OCR/PDF text, EXIF GPS, perceptual hashes, face embeddings — but there was no
way to look at any of it. These endpoints back the dashboard's Media page:

  GET /api/media                 alias for /media/browse (recent first)
  GET /api/media/stats           rollups by analysis_type / source / content_type
  GET /api/media/filters         distinct values for the filter dropdowns
  GET /api/media/browse          paginated + filtered grid of analysis rows
  GET /api/media/{id}/thumbnail  JPEG thumbnail for one analysis row

`id` everywhere is the analyzer-side media_analysis.id (UUID), which is what
/browse returns as `id`. Thumbnails resolve to either a collector media file
(real media_items.file_path, cross-DB) or a derived artifact this pipeline
wrote (result_json->>'derived_path'); resolve_media_path() enforces the same
path-confinement safety the collector dashboard uses.
"""
import asyncio
import io
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response

from src.db.connection import get_analyzer_pool, get_collector_pool
from src.pipeline.media_common import resolve_media_path

logger = logging.getLogger(__name__)

router = APIRouter(tags=["media"])

# Cap thumbnail dimension. 256px keeps the gallery light over the wire while
# staying crisp on hi-dpi grid tiles.
_THUMB_MAX = 256


def _row_to_dict(r) -> dict:
    rj = r["result_json"]
    if isinstance(rj, str):
        try:
            rj = json.loads(rj)
        except json.JSONDecodeError:
            rj = None
    text = r["extracted_text"]
    return {
        "id": str(r["id"]),
        "media_item_id": r["media_item_id"],
        "parent_media_item_id": r["parent_media_item_id"],
        "source": r["source"],
        "content_type": r["content_type"],
        "analysis_type": r["analysis_type"],
        # Trim text for the list payload; the grid only needs a preview.
        "text_preview": (text[:280] + "…") if text and len(text) > 280 else text,
        "has_text": bool(text),
        "gps_lat": r["gps_lat"],
        "gps_lon": r["gps_lon"],
        "has_gps": r["gps_lat"] is not None and r["gps_lon"] is not None,
        "taken_at": r["taken_at"].isoformat() if r["taken_at"] else None,
        "perceptual_hash": r["perceptual_hash"],
        "has_face": bool(r["face_embedding"]),
        "is_derived": r["parent_media_item_id"] is not None,
        "model_version": r["model_version"],
        "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
        "result_json": rj,
        # Thumbnails are only meaningful for image-bearing rows (real images +
        # derived pdf images / video frames). Let the client decide whether to
        # request one; the endpoint 404s gracefully otherwise.
        "thumbnail_url": f"/api/media/{r['id']}/thumbnail",
    }


@router.get("/media/stats")
async def media_stats():
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        by_type = await conn.fetch(
            "SELECT analysis_type, COUNT(*) AS n FROM media_analysis GROUP BY analysis_type ORDER BY n DESC"
        )
        by_source = await conn.fetch(
            "SELECT source, COUNT(*) AS n FROM media_analysis GROUP BY source ORDER BY n DESC"
        )
        by_content = await conn.fetch(
            "SELECT content_type, COUNT(*) AS n FROM media_analysis GROUP BY content_type ORDER BY n DESC"
        )
        totals = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS rows_total,
                COUNT(DISTINCT media_item_id) AS items_total,
                COUNT(*) FILTER (WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL) AS with_gps,
                COUNT(*) FILTER (WHERE extracted_text IS NOT NULL AND extracted_text <> '') AS with_text,
                COUNT(*) FILTER (WHERE face_embedding IS NOT NULL) AS with_face,
                COUNT(*) FILTER (WHERE parent_media_item_id IS NOT NULL) AS derived,
                COUNT(*) FILTER (WHERE perceptual_hash IS NOT NULL) AS with_phash
            FROM media_analysis
            """
        )
    return {
        "totals": dict(totals),
        "by_analysis_type": [dict(r) for r in by_type],
        "by_source": [dict(r) for r in by_source],
        "by_content_type": [dict(r) for r in by_content],
    }


@router.get("/media/filters")
async def media_filters():
    """Distinct facet values for the dashboard filter dropdowns."""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        types = await conn.fetch("SELECT DISTINCT analysis_type FROM media_analysis ORDER BY analysis_type")
        sources = await conn.fetch("SELECT DISTINCT source FROM media_analysis ORDER BY source")
        contents = await conn.fetch("SELECT DISTINCT content_type FROM media_analysis ORDER BY content_type")
    return {
        "analysis_types": [r["analysis_type"] for r in types],
        "sources": [r["source"] for r in sources],
        "content_types": [r["content_type"] for r in contents],
    }


@router.get("/media/browse")
async def media_browse(
    page: int = Query(1, ge=1),
    per_page: int = Query(48, ge=1, le=200),
    analysis_type: str | None = None,
    source: str | None = None,
    content_type: str | None = None,
    has_gps: bool = False,
    has_text: bool = False,
    has_face: bool = False,
    derived: bool | None = None,
    q: str | None = None,
):
    pool = get_analyzer_pool()
    offset = (page - 1) * per_page

    conditions: list[str] = []
    params: list = []
    idx = 1

    def add(cond: str, value):
        nonlocal idx
        conditions.append(cond.replace("$$", f"${idx}"))
        params.append(value)
        idx += 1

    if analysis_type:
        add("analysis_type = $$", analysis_type)
    if source:
        add("source = $$", source)
    if content_type:
        add("content_type = $$", content_type)
    if has_gps:
        conditions.append("gps_lat IS NOT NULL AND gps_lon IS NOT NULL")
    if has_text:
        conditions.append("extracted_text IS NOT NULL AND extracted_text <> ''")
    if has_face:
        conditions.append("face_embedding IS NOT NULL")
    if derived is True:
        conditions.append("parent_media_item_id IS NOT NULL")
    elif derived is False:
        conditions.append("parent_media_item_id IS NULL")
    if q:
        add("extracted_text ILIKE $$", f"%{q}%")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM media_analysis {where}", *params)
        rows = await conn.fetch(
            f"""
            SELECT id, media_item_id, parent_media_item_id, source, content_type,
                   analysis_type, extracted_text, result_json, gps_lat, gps_lon,
                   taken_at, perceptual_hash, face_embedding, model_version, processed_at
            FROM media_analysis
            {where}
            ORDER BY processed_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params, per_page, offset,
        )

    return {
        "data": [_row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/media")
async def media_index(
    page: int = Query(1, ge=1),
    per_page: int = Query(48, ge=1, le=200),
):
    """Alias for /media/browse with defaults — recent analysis rows."""
    return await media_browse(page=page, per_page=per_page)


def _is_real_media_uuid(media_item_id: str) -> bool:
    """A real collector media_items.id is a plain UUID. Derived/synthetic ids
    embed a marker, e.g. "{uuid}:pdf_img:1:12" or "{uuid}:frame:0", and must
    never be fed to a `::uuid` query (asyncpg raises, crashing the request)."""
    return ":" not in media_item_id and len(media_item_id) in (32, 36)


def _derived_path_from_json(rj) -> str | None:
    if isinstance(rj, str):
        try:
            rj = json.loads(rj)
        except json.JSONDecodeError:
            return None
    return (rj or {}).get("derived_path")


async def _resolve_thumbnail_path(analysis_id: str):
    """Resolve a media_analysis row to a renderable image Path, or None.

    Three cases, in order:
      1. This row carries result_json->>'derived_path' (pdf_image/video_frame
         rows written by 6C.2 / 6H) — use it directly.
      2. The row's media_item_id is synthetic (e.g. a phash/face row computed
         ON a derived frame: parent is NULL but media_item_id is "{uuid}:frame:0")
         — find the sibling derived row with the same media_item_id that DOES
         carry a derived_path.
      3. media_item_id is a real collector UUID — read media_items.file_path
         cross-DB.
    All paths go through resolve_media_path()'s confinement check.
    """
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT media_item_id, parent_media_item_id, result_json "
            "FROM media_analysis WHERE id = $1::uuid",
            analysis_id,
        )
        if not row:
            return None

        # Case 1: own derived_path.
        derived = _derived_path_from_json(row["result_json"])
        if derived:
            return resolve_media_path(derived)

        media_item_id = row["media_item_id"]

        # Case 2: synthetic id without its own derived_path — borrow the
        # sibling row's (the pdf_image/video_frame that produced the artifact).
        if not _is_real_media_uuid(media_item_id):
            sibling = await conn.fetchval(
                "SELECT result_json->>'derived_path' FROM media_analysis "
                "WHERE media_item_id = $1 AND result_json ? 'derived_path' LIMIT 1",
                media_item_id,
            )
            return resolve_media_path(sibling)

    # Case 3: real media item — look up its file_path in the collector DB.
    collector = get_collector_pool()
    async with collector.acquire() as conn:
        file_path = await conn.fetchval(
            "SELECT file_path FROM media_items WHERE id = $1::uuid",
            media_item_id,
        )
    return resolve_media_path(file_path)


def _render_thumbnail(path: Path) -> bytes | None:
    """Open `path`, downscale, return JPEG bytes. Synchronous (Pillow blocks);
    callers run it in a worker thread. Returns None for unreadable/non-image
    files. Pillow imported lazily so processes that never serve a thumbnail
    don't pay the import cost."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        logger.warning("Pillow not installed — thumbnails unavailable")
        return None
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((_THUMB_MAX, _THUMB_MAX))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=82)
            return buf.getvalue()
    except Exception as e:  # noqa: BLE001 — unreadable / non-image file
        logger.debug("thumbnail render failed for %s: %s", path, e)
        return None


@router.get("/media/{analysis_id}/thumbnail")
async def media_thumbnail(analysis_id: str):
    path = await _resolve_thumbnail_path(analysis_id)
    if path is None:
        raise HTTPException(404, "No thumbnail available")

    # Pillow decode/encode is CPU-bound and blocking; offload to a thread so a
    # large image (or a slow network-mounted collector file) doesn't stall the
    # event loop — which also serves /ws/health and every other request.
    data = await asyncio.to_thread(_render_thumbnail, path)
    if data is None:
        raise HTTPException(404, "Cannot render thumbnail")

    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )
