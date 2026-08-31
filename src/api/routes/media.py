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
import html
import io
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse

from src.api.face_lookup import face_crop_url
from src.db.connection import get_analyzer_pool, get_collector_pool
from src.pipeline.media_common import resolve_media_path

logger = logging.getLogger(__name__)

router = APIRouter(tags=["media"])

# Cap thumbnail dimension. 256px keeps the gallery light over the wire while
# staying crisp on hi-dpi grid tiles.
_THUMB_MAX = 256


def _thumbnail_placeholder(label: str, detail: str = "") -> Response:
    safe_label = html.escape((label or "media").upper()[:24])
    safe_detail = html.escape((detail or "preview unavailable")[:64])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256" role="img" aria-label="{safe_label}">
<rect width="256" height="256" fill="#111111"/>
<rect x="16" y="16" width="224" height="224" rx="8" fill="#1f2937" stroke="#374151" stroke-width="2"/>
<circle cx="128" cy="104" r="28" fill="#4b5563"/>
<path d="M122 88 L146 104 L122 120 Z" fill="#e5e7eb"/>
<text x="128" y="164" text-anchor="middle" fill="#f9fafb" font-family="Arial, sans-serif" font-size="20" font-weight="700">{safe_label}</text>
<text x="128" y="190" text-anchor="middle" fill="#9ca3af" font-family="Arial, sans-serif" font-size="12">{safe_detail}</text>
</svg>"""
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=300"},
    )


def _parse_pg_array_text(raw: str | None) -> list[str]:
    if not raw or raw == "{}":
        return []
    body = raw.strip("{}")
    if not body:
        return []
    return [part.strip().strip('"') for part in body.split(",")]


def _estimated_rollup(rows_total: int, vals: str | None, freqs: str | None, key: str) -> list[dict]:
    names = _parse_pg_array_text(vals)
    weights = []
    for value in _parse_pg_array_text(freqs):
        try:
            weights.append(float(value))
        except ValueError:
            weights.append(0.0)
    rows = [
        {key: name, "n": int(round(rows_total * weight))}
        for name, weight in zip(names, weights)
    ]
    return sorted(rows, key=lambda r: r["n"], reverse=True)


async def _estimated_media_totals(conn) -> dict:
    row = await conn.fetchrow(
        """
        SELECT
            (SELECT GREATEST(c.reltuples, 0)::bigint
             FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relname = 'media_analysis') AS rows_total,
            (SELECT GREATEST(
                CASE
                    WHEN s.n_distinct < 0 THEN (-s.n_distinct * c.reltuples)
                    ELSE s.n_distinct
                END,
                0
             )::bigint
             FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
             LEFT JOIN pg_stats s
               ON s.schemaname = n.nspname
              AND s.tablename = c.relname
              AND s.attname = 'media_item_id'
             WHERE n.nspname = 'public' AND c.relname = 'media_analysis') AS items_total,
            (SELECT GREATEST(reltuples, 0)::bigint FROM pg_class
             WHERE relname = 'idx_media_analysis_has_gps') AS with_gps,
            (SELECT GREATEST(reltuples, 0)::bigint FROM pg_class
             WHERE relname = 'idx_media_analysis_has_text') AS with_text,
            (SELECT GREATEST(reltuples, 0)::bigint FROM pg_class
             WHERE relname = 'idx_media_analysis_has_face') AS with_face,
            (SELECT GREATEST(reltuples, 0)::bigint FROM pg_class
             WHERE relname = 'idx_media_analysis_is_derived') AS derived,
            (SELECT GREATEST(reltuples, 0)::bigint FROM pg_class
             WHERE relname = 'idx_media_analysis_has_phash') AS with_phash
        """
    )
    return {k: int(row[k] or 0) for k in row.keys()}


async def _estimated_analysis_type_counts(conn, rows_total: int) -> dict[str, int]:
    row = await conn.fetchrow(
        """
        SELECT most_common_vals::text AS vals, most_common_freqs::text AS freqs
        FROM pg_stats
        WHERE schemaname = 'public'
          AND tablename = 'media_analysis'
          AND attname = 'analysis_type'
        """
    )
    if not row:
        return {}
    estimates = _estimated_rollup(rows_total, row["vals"], row["freqs"], "analysis_type")
    return {str(item["analysis_type"]): int(item["n"] or 0) for item in estimates}


def _int_row(row: dict, key: str) -> int:
    return int(row.get(key) or 0)


def _coverage_item(
    key: str,
    label: str,
    count: int,
    *,
    processed: int | None = None,
    basis: str,
) -> dict:
    return {
        "key": key,
        "label": label,
        "status": "covered" if count > 0 else "missing",
        "count": int(count or 0),
        "processed": int(processed if processed is not None else count or 0),
        "basis": basis,
    }


async def _media_coverage_counts_exact(conn) -> dict:
    row = await conn.fetchrow(
        """
        SELECT
            COUNT(*)::bigint AS rows_total,
            COUNT(DISTINCT media_item_id)::bigint AS items_total,
            COUNT(*) FILTER (WHERE analysis_type = 'pdf_text')::bigint AS pdf_text_rows,
            COUNT(*) FILTER (
                WHERE analysis_type = 'pdf_text'
                  AND extracted_text IS NOT NULL
                  AND extracted_text <> ''
            )::bigint AS pdf_text_with_text,
            COUNT(*) FILTER (WHERE analysis_type = 'pdf_embedded_image')::bigint AS pdf_image_markers,
            COUNT(*) FILTER (WHERE analysis_type = 'pdf_image')::bigint AS pdf_image_rows,
            COUNT(*) FILTER (WHERE analysis_type = 'ocr_text')::bigint AS ocr_rows,
            COUNT(*) FILTER (
                WHERE analysis_type = 'ocr_text'
                  AND extracted_text IS NOT NULL
                  AND extracted_text <> ''
            )::bigint AS ocr_with_text,
            COUNT(*) FILTER (WHERE analysis_type = 'video_frames')::bigint AS video_frame_markers,
            COUNT(*) FILTER (WHERE analysis_type = 'video_frame')::bigint AS video_frame_rows,
            COUNT(*) FILTER (WHERE analysis_type = 'face_embedding' OR face_embedding IS NOT NULL)::bigint AS face_rows,
            COUNT(*) FILTER (WHERE analysis_type = 'exif_gps')::bigint AS exif_rows,
            COUNT(*) FILTER (
                WHERE analysis_type = 'exif_gps'
                  AND gps_lat IS NOT NULL
                  AND gps_lon IS NOT NULL
            )::bigint AS exif_with_gps,
            COUNT(*) FILTER (WHERE analysis_type = 'phash' OR perceptual_hash IS NOT NULL)::bigint AS phash_rows,
            COUNT(*) FILTER (
                WHERE parent_media_item_id IS NOT NULL
                   OR analysis_type IN ('pdf_image', 'video_frame')
            )::bigint AS derived_rows
        FROM media_analysis
        """
    )
    return dict(row or {})


async def _media_coverage_counts(conn, *, exact: bool = False) -> dict:
    if exact:
        return await _media_coverage_counts_exact(conn)

    totals = await _estimated_media_totals(conn)
    by_type = await _estimated_analysis_type_counts(conn, totals["rows_total"])
    pdf_text_rows = by_type.get("pdf_text", 0)
    ocr_rows = by_type.get("ocr_text", 0)
    exif_rows = by_type.get("exif_gps", 0)
    face_rows = max(by_type.get("face_embedding", 0), totals["with_face"])
    return {
        "rows_total": totals["rows_total"],
        "items_total": totals["items_total"],
        "pdf_text_rows": pdf_text_rows,
        "pdf_text_with_text": pdf_text_rows,
        "pdf_image_markers": by_type.get("pdf_embedded_image", 0),
        "pdf_image_rows": by_type.get("pdf_image", 0),
        "ocr_rows": ocr_rows,
        "ocr_with_text": ocr_rows,
        "video_frame_markers": by_type.get("video_frames", 0),
        "video_frame_rows": by_type.get("video_frame", 0),
        "face_rows": face_rows,
        "exif_rows": exif_rows,
        "exif_with_gps": max(totals["with_gps"], min(exif_rows, totals["with_gps"])),
        "phash_rows": max(by_type.get("phash", 0), totals["with_phash"]),
        "derived_rows": totals["derived"],
    }


async def _media_contact_signal_counts(conn) -> tuple[int, list[dict]]:
    rows = await conn.fetch(
        """
        SELECT COALESCE(source_column, 'unknown') AS source_column,
               signal_type,
               COUNT(*)::bigint AS n
        FROM identity_signals
        WHERE source_table = 'media_items'
          AND source_column = ANY($1::text[])
          AND signal_type = ANY($2::text[])
        GROUP BY source_column, signal_type
        ORDER BY n DESC, source_column, signal_type
        """,
        ["pdf_text", "ocr_text"],
        ["email_match", "cross_platform_link", "phone_match", "shared_website"],
    )
    by_source_column = [
        {
            "source_column": r["source_column"],
            "signal_type": r["signal_type"],
            "n": int(r["n"] or 0),
        }
        for r in rows
    ]
    return sum(r["n"] for r in by_source_column), by_source_column


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


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _analysis_preview(row: dict | None) -> dict | None:
    if not row:
        return None
    text = row.get("extracted_text")
    return {
        "analysis_id": row.get("analysis_id"),
        "analysis_type": row.get("analysis_type"),
        "content_type": row.get("content_type"),
        "source": row.get("source"),
        "text_preview": (text[:180] + "...") if text and len(text) > 180 else text,
        "has_text": bool(text),
        "has_gps": row.get("gps_lat") is not None and row.get("gps_lon") is not None,
        "gps_lat": row.get("gps_lat"),
        "gps_lon": row.get("gps_lon"),
        "taken_at": _iso(row.get("taken_at")),
        "processed_at": _iso(row.get("processed_at")),
        "thumbnail_url": f"/api/media/{row['analysis_id']}/thumbnail" if row.get("analysis_id") else None,
    }


async def _analysis_by_media_id(conn, media_ids: set[str]) -> dict[str, dict]:
    if not media_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (media_item_id)
               media_item_id,
               id::text AS analysis_id,
               analysis_type,
               content_type,
               source,
               extracted_text,
               gps_lat,
               gps_lon,
               taken_at,
               processed_at
        FROM media_analysis
        WHERE media_item_id = ANY($1::text[])
        ORDER BY media_item_id, processed_at DESC NULLS LAST
        """,
        sorted(media_ids),
    )
    return {str(r["media_item_id"]): dict(r) for r in rows}


@router.get("/entities/{entity_id}/media-faces")
async def entity_media_faces(entity_id: str, limit: int = Query(40, ge=1, le=120)):
    """Entity-scoped media/faces for the person page.

    Returns collector media linked to this entity, faces already linked to this
    entity, and other faces found in this entity's media. Corrections are recorded by
    /entities/{entity_id}/media-person-decision.
    """
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM entities WHERE id = $1::uuid", entity_id)
        if not exists:
            raise HTTPException(404, "Entity not found")

        link_rows = await conn.fetch(
            """
            SELECT source, platform_id, platform_username
            FROM entity_platform_links
            WHERE entity_id = $1::uuid
            """,
            entity_id,
        )
        known_face_rows = await conn.fetch(
            """
            SELECT face_id, media_item_id, confidence, method, created_at
            FROM entity_faces
            WHERE entity_id = $1::uuid
            ORDER BY confidence DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT $2
            """,
            entity_id,
            limit,
        )
        association_rows = await conn.fetch(
            """
            SELECT fa.associated_face_id,
                   fa.media_item_id,
                   fa.source_platform,
                   fa.quality_score,
                   fa.first_seen_at,
                   match.entity_id::text AS matched_entity_id,
                   match.canonical_name AS matched_entity_name,
                   match.confidence AS matched_confidence
            FROM face_associations fa
            LEFT JOIN LATERAL (
                SELECT ef.entity_id,
                       e.canonical_name,
                       ef.confidence
                FROM entity_faces ef
                JOIN entities e ON e.id = ef.entity_id
                WHERE ef.face_id = fa.associated_face_id
                  AND ef.entity_id <> fa.entity_id
                ORDER BY ef.confidence DESC NULLS LAST, e.canonical_name NULLS LAST
                LIMIT 1
            ) AS match ON TRUE
            WHERE fa.entity_id = $1::uuid
            ORDER BY
                match.confidence DESC NULLS LAST,
                fa.quality_score DESC NULLS LAST,
                fa.first_seen_at DESC NULLS LAST
            LIMIT $2
            """,
            entity_id,
            limit,
        )

    account_pairs: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for link in link_rows:
        source = str(link["source"] or "").strip()
        for value in (link["platform_id"], link["platform_username"]):
            account = str(value or "").strip()
            if not source or not account:
                continue
            key = (source, account.lower())
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            account_pairs.append((source, account))

    linked_rows = []
    collector_skipped = False
    collector_error = None
    if account_pairs:
        try:
            collector = get_collector_pool()
            sources = [source for source, _account in account_pairs]
            accounts = [account for _source, account in account_pairs]
            async with collector.acquire() as conn:
                linked_rows = await conn.fetch(
                    """
                    WITH keys AS (
                        SELECT *
                        FROM unnest($1::text[], $2::text[]) AS t(source, account)
                    ),
                    matched AS (
                        SELECT DISTINCT ON (m.id)
                               m.id::text AS media_item_id,
                               m.source,
                               m.entity_id,
                               m.entity_name,
                               m.content_type,
                               m.content_id,
                               m.filename,
                               m.file_size,
                               m.width,
                               m.height,
                               m.sha256,
                               m.kind,
                               m.collected_at
                        FROM media_items m
                        JOIN keys k
                          ON m.source = k.source
                         AND lower(m.entity_id) = lower(k.account)
                        ORDER BY m.id
                    )
                    SELECT *
                    FROM matched
                    ORDER BY collected_at DESC NULLS LAST
                    LIMIT $3
                    """,
                    sources,
                    accounts,
                    limit,
                )
        except Exception as exc:  # noqa: BLE001 - collector media should degrade, not break person pages
            collector_skipped = True
            collector_error = f"{exc.__class__.__name__}: {exc}"

    media_ids = {
        str(row["media_item_id"])
        for row in [*linked_rows, *known_face_rows, *association_rows]
        if row["media_item_id"]
    }
    async with analyzer.acquire() as conn:
        analysis = await _analysis_by_media_id(conn, media_ids)

    return {
        "entity_id": entity_id,
        "collector_skipped": collector_skipped,
        "collector_error": collector_error,
        "linked_media": [
            {
                "media_item_id": r["media_item_id"],
                "source": r["source"],
                "entity_id": r["entity_id"],
                "entity_name": r["entity_name"],
                "content_type": r["content_type"],
                "content_id": r["content_id"],
                "filename": r["filename"],
                "file_size": r["file_size"],
                "width": r["width"],
                "height": r["height"],
                "sha256": r["sha256"],
                "kind": r["kind"],
                "collected_at": _iso(r["collected_at"]),
                "analysis": _analysis_preview(analysis.get(str(r["media_item_id"]))),
            }
            for r in linked_rows
        ],
        "known_faces": [
            {
                "face_id": r["face_id"],
                "face_crop_url": face_crop_url(r["face_id"]),
                "media_item_id": r["media_item_id"],
                "confidence": r["confidence"],
                "method": r["method"],
                "created_at": _iso(r["created_at"]),
                "analysis": _analysis_preview(analysis.get(str(r["media_item_id"]))),
            }
            for r in known_face_rows
        ],
        "associated_faces": [
            {
                "associated_face_id": r["associated_face_id"],
                "face_crop_url": face_crop_url(r["associated_face_id"]),
                "media_item_id": r["media_item_id"],
                "source_platform": r["source_platform"],
                "quality_score": r["quality_score"],
                "first_seen_at": _iso(r["first_seen_at"]),
                "matched_entity_id": r["matched_entity_id"],
                "matched_entity_name": r["matched_entity_name"],
                "matched_confidence": r["matched_confidence"],
                "analysis": _analysis_preview(analysis.get(str(r["media_item_id"]))),
            }
            for r in association_rows
        ],
    }


@router.get("/media/stats")
async def media_stats():
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        # Dashboard rollups must stay responsive while full-resolution is busy.
        # Exact COUNT(*) / GROUP BY over this wide table can block the UI for
        # seconds under IO pressure, so use planner/index stats for dashboard
        # tiles. The media grid itself still returns exact page rows.
        totals = await _estimated_media_totals(conn)
        rollup_rows = await conn.fetch(
            """
            SELECT attname, most_common_vals::text AS vals, most_common_freqs::text AS freqs
            FROM pg_stats
            WHERE schemaname = 'public'
              AND tablename = 'media_analysis'
              AND attname = ANY($1::text[])
            """
            , ["analysis_type", "source", "content_type"]
        )
        by_att = {r["attname"]: dict(r) for r in rollup_rows}
        totals["estimated"] = True
        rows_total = totals["rows_total"]
    return {
        "totals": totals,
        "by_analysis_type": _estimated_rollup(
            rows_total,
            by_att.get("analysis_type", {}).get("vals"),
            by_att.get("analysis_type", {}).get("freqs"),
            "analysis_type",
        ),
        "by_source": _estimated_rollup(
            rows_total,
            by_att.get("source", {}).get("vals"),
            by_att.get("source", {}).get("freqs"),
            "source",
        ),
        "by_content_type": _estimated_rollup(
            rows_total,
            by_att.get("content_type", {}).get("vals"),
            by_att.get("content_type", {}).get("freqs"),
            "content_type",
        ),
    }


@router.get("/media/coverage")
async def media_coverage(exact: bool = Query(False)):
    """Production coverage for media/PDF analysis surfaces.

    The default path uses planner/index estimates so the dashboard stays
    responsive while full-resolution media jobs are active. `exact=true` keeps a
    bounded audit path for maintenance checks.
    """
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        counts = await _media_coverage_counts(conn, exact=exact)
        contact_total, contact_breakdown = await _media_contact_signal_counts(conn)

    coverage = [
        _coverage_item(
            "pdf_text",
            "PDF text",
            _int_row(counts, "pdf_text_with_text"),
            processed=_int_row(counts, "pdf_text_rows"),
            basis="media_analysis.analysis_type='pdf_text'",
        ),
        _coverage_item(
            "pdf_images",
            "PDF embedded images",
            _int_row(counts, "pdf_image_rows"),
            processed=_int_row(counts, "pdf_image_markers"),
            basis="media_analysis.analysis_type IN ('pdf_embedded_image','pdf_image')",
        ),
        _coverage_item(
            "ocr_text",
            "OCR text",
            _int_row(counts, "ocr_with_text"),
            processed=_int_row(counts, "ocr_rows"),
            basis="media_analysis.analysis_type='ocr_text'",
        ),
        _coverage_item(
            "video_frames",
            "Video frames",
            _int_row(counts, "video_frame_rows"),
            processed=_int_row(counts, "video_frame_markers"),
            basis="media_analysis.analysis_type IN ('video_frames','video_frame')",
        ),
        _coverage_item(
            "faces",
            "Faces",
            _int_row(counts, "face_rows"),
            basis="media_analysis.face_embedding IS NOT NULL",
        ),
        _coverage_item(
            "exif_gps",
            "EXIF/GPS",
            _int_row(counts, "exif_with_gps"),
            processed=_int_row(counts, "exif_rows"),
            basis="media_analysis.analysis_type='exif_gps'",
        ),
        _coverage_item(
            "contact_signals",
            "Contact signals",
            contact_total,
            basis="identity_signals.source_table='media_items'",
        ),
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "estimated": not exact,
        "rows_total": _int_row(counts, "rows_total"),
        "items_total": _int_row(counts, "items_total"),
        "derived_rows": _int_row(counts, "derived_rows"),
        "phash_rows": _int_row(counts, "phash_rows"),
        "coverage": coverage,
        "contact_signals": {
            "total": contact_total,
            "by_source_column": contact_breakdown,
        },
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
        if conditions:
            total = await conn.fetchval(f"SELECT COUNT(*) FROM media_analysis {where}", *params)
        else:
            total = (await _estimated_media_totals(conn))["rows_total"]
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
            "SELECT media_item_id, parent_media_item_id, content_type, result_json "
            "FROM media_analysis WHERE id = $1::uuid",
            analysis_id,
        )
        if not row:
            raise HTTPException(404, "Media analysis row not found")

        # Case 1: own derived_path.
        derived = _derived_path_from_json(row["result_json"])
        if derived:
            return resolve_media_path(derived)

        media_item_id = row["media_item_id"]

        # Case 1b: a pdf_embedded_image MARKER row (content_type='pdf') has no
        # image of its own — its media_item_id is the parent PDF, which is often
        # absent once collector source media is pruned. Prefer one of the PDF's
        # already-extracted embedded images (child pdf_image rows) that still
        # exists on disk; only fall back to rendering the PDF itself (Case 3) if
        # none survive.
        if row["content_type"] == "pdf":
            children = await conn.fetch(
                "SELECT result_json->>'derived_path' AS dp FROM media_analysis "
                "WHERE parent_media_item_id = $1 AND analysis_type = 'pdf_image' "
                "AND result_json ? 'derived_path' LIMIT 25",
                media_item_id,
            )
            for ch in children:
                p = resolve_media_path(ch["dp"])
                if p is not None:
                    return p

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
    try:
        collector = get_collector_pool()
        async with collector.acquire() as conn:
            file_path = await conn.fetchval(
                "SELECT file_path FROM media_items WHERE id = $1::uuid",
                media_item_id,
            )
    except Exception as e:  # noqa: BLE001 - thumbnails degrade if collector is offline
        logger.warning("collector media lookup skipped for %s: %s", media_item_id, e)
        return None
    return resolve_media_path(file_path)


def _render_pdf_first_page(path: Path) -> bytes | None:
    """Rasterize a PDF's first page to a JPEG thumbnail via PyMuPDF. Pillow can't
    open PDFs, so pdf_embedded_image marker rows (and any pdf content_type row)
    resolve to the parent PDF file and land here — previously they 404'd with no
    preview at all."""
    try:
        import fitz
        from PIL import Image
        with fitz.open(str(path)) as doc:
            if doc.page_count == 0:
                return None
            page = doc.load_page(0)
            longest = max(page.rect.width, page.rect.height) or 1.0
            scale = min(_THUMB_MAX / longest, 4.0)  # cap upscale of tiny pages
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        buf = io.BytesIO()
        im.thumbnail((_THUMB_MAX, _THUMB_MAX))
        im.save(buf, format="JPEG", quality=82)
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001 — unreadable/encrypted/corrupt PDF
        logger.debug("pdf first-page render failed for %s: %s", path, e)
        return None


def _render_thumbnail(path: Path) -> bytes | None:
    """Open `path`, downscale, return JPEG bytes. Synchronous (Pillow blocks);
    callers run it in a worker thread. Returns None for unreadable/non-image
    files. Pillow imported lazily so processes that never serve a thumbnail
    don't pay the import cost."""
    if path.suffix.lower() == ".pdf":
        return _render_pdf_first_page(path)
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
        return _thumbnail_placeholder("media", "no thumbnail")

    # Pillow decode/encode is CPU-bound and blocking; offload to a thread so a
    # large image (or a slow network-mounted collector file) doesn't stall the
    # event loop — which also serves /ws/health and every other request.
    data = await asyncio.to_thread(_render_thumbnail, path)
    if data is None:
        return _thumbnail_placeholder(path.suffix.lstrip(".") or "media", "cannot render")

    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/media/{analysis_id}/file")
async def media_file(analysis_id: str):
    """Serve the ORIGINAL media file (video/image/pdf) for one analysis row so
    the dashboard can PLAY videos instead of only showing the thumbnail frame.
    Resolves the real collector media_items.file_path first (the actual .mp4),
    falling back to a derived artifact. FileResponse handles HTTP Range, so
    <video> seeking works. Same path-confinement as thumbnails."""
    try:
        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT media_item_id, result_json->>'derived_path' AS derived_path "
                "FROM media_analysis WHERE id = $1::uuid",
                analysis_id,
            )
    except Exception:
        raise HTTPException(status_code=400, detail="bad analysis id")
    if row is None:
        raise HTTPException(status_code=404, detail="not found")

    path = None
    media_item_id = row["media_item_id"]
    if media_item_id:
        try:
            collector = get_collector_pool()
            async with collector.acquire() as c:
                file_path = await c.fetchval(
                    "SELECT file_path FROM media_items WHERE id = $1::uuid",
                    media_item_id,
                )
            path = resolve_media_path(file_path)
        except Exception as e:  # noqa: BLE001 - degrade to derived path below
            logger.warning("collector media file lookup failed for %s: %s", media_item_id, e)
    if path is None and row["derived_path"]:
        path = resolve_media_path(row["derived_path"])
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="media file not available")
    return FileResponse(str(path), headers={"Cache-Control": "public, max-age=3600"})
