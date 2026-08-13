from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.eval.metrics import (
    classification_metrics,
    duplicate_count,
    evaluate_metric_gates,
    mrr_at_k,
    recall_at_k,
)


SUPPORTED_TASKS = {"search", "identity", "sentiment", "face", "location", "alerts"}
DEFAULT_SEED_PATH = Path(__file__).with_name("seed_sets.json")
DEFAULT_PRODUCTION_LABEL_LIMIT = 200


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _factory_items(item_set: dict[str, Any]) -> list[dict[str, Any]]:
    factory = item_set.get("factory")
    if not factory:
        return []
    name = factory.get("name")
    if name == "sentiment_examples":
        positive = ["I love this update", "great work today", "excellent safe win", "happy progress", "this is good"]
        negative = ["I hate this mess", "terrible broken result", "angry and worried", "this is awful", "bad sad outcome"]
        neutral = ["meeting at noon", "uploaded the file", "status unchanged", "route recorded", "message received"]
        unsupported = ["非常生气", "我很开心", "saya sangat marah", "यह बहुत अच्छा है", "நான் மகிழ்ச்சி"]
        rows = []
        for idx in range(int(factory.get("count", 100))):
            bucket = idx % 20
            if bucket < 6:
                label, text = "positive", positive[idx % len(positive)]
            elif bucket < 12:
                label, text = "negative", negative[idx % len(negative)]
            elif bucket < 16:
                label, text = "neutral", neutral[idx % len(neutral)]
            else:
                label, text = "unsupported", unsupported[idx % len(unsupported)]
            rows.append({
                "input_json": {"text": text, "prediction": label},
                "expected_json": {"label": label},
                "source_ref": f"seed:sentiment:factory:{idx:03d}",
                "label_source": "synthetic_factory",
            })
        return rows
    if name == "search_queries":
        count = int(factory.get("count", 40))
        return [
            {
                "input_json": {
                    "query": f"golden query {idx:02d}",
                    "ranked_event_ids": [f"event-{idx:02d}", f"event-{idx:02d}-alt", "noise"],
                },
                "expected_json": {"event_ids": [f"event-{idx:02d}"]},
                "source_ref": f"seed:search:factory:{idx:03d}",
                "label_source": "synthetic_factory",
            }
            for idx in range(count)
        ]
    if name == "alert_fixtures":
        count = int(factory.get("count", 12))
        return [
            {
                "input_json": {"fingerprint": f"stream-alert-{idx:02d}", "prediction": "fired"},
                "expected_json": {"label": "fired"},
                "source_ref": f"seed:alerts:factory:{idx:03d}",
                "label_source": "synthetic_factory",
            }
            for idx in range(count)
        ]
    return []


def materialize_seed_items(item_set: dict[str, Any]) -> list[dict[str, Any]]:
    return [*(item_set.get("items") or []), *_factory_items(item_set)]


async def _table_exists(conn, table: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table))
    except Exception:
        return False


async def _ensure_eval_set(conn, *, name: str, task_type: str, description: str, dry_run: bool) -> str | None:
    if dry_run:
        return None
    return await conn.fetchval(
        """
        INSERT INTO eval_sets (name, task_type, description)
        VALUES ($1, $2, $3)
        ON CONFLICT (name) DO UPDATE SET
            task_type = EXCLUDED.task_type,
            description = EXCLUDED.description
        RETURNING id
        """,
        name,
        task_type,
        description,
    )


async def _insert_eval_items(conn, set_id: str | None, items: list[dict[str, Any]], *, dry_run: bool) -> int:
    if dry_run:
        return len(items)
    inserted = 0
    for eval_item in items:
        result = await conn.execute(
            """
            INSERT INTO eval_items (
                set_id, input_json, expected_json, source_ref, label_source
            )
            SELECT $1::uuid, $2::jsonb, $3::jsonb, $4, $5
            WHERE NOT EXISTS (
                SELECT 1
                FROM eval_items
                WHERE set_id = $1::uuid
                  AND COALESCE(source_ref, '') = COALESCE($4, '')
            )
            """,
            set_id,
            json.dumps(eval_item["input_json"]),
            json.dumps(eval_item["expected_json"]),
            eval_item.get("source_ref"),
            eval_item.get("label_source"),
        )
        if result == "INSERT 0 1":
            inserted += 1
    return inserted


async def harvest_production_eval_items(
    conn,
    *,
    limit_per_task: int = DEFAULT_PRODUCTION_LABEL_LIMIT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Seed bounded eval sets from real Analyzer labels when those tables exist."""
    limit = max(1, min(int(limit_per_task or DEFAULT_PRODUCTION_LABEL_LIMIT), 1000))
    sets = 0
    items = 0
    by_task: dict[str, int] = {}

    async def seed(name: str, task_type: str, description: str, rows: list[dict[str, Any]]) -> None:
        nonlocal sets, items
        if not rows:
            return
        set_id = await _ensure_eval_set(conn, name=name, task_type=task_type, description=description, dry_run=dry_run)
        written = await _insert_eval_items(conn, set_id, rows, dry_run=dry_run)
        sets += 1
        items += written
        by_task[task_type] = by_task.get(task_type, 0) + written

    if await _table_exists(conn, "timeline_translation_search"):
        rows = await conn.fetch(
            """
            SELECT event_id::text, canonical_text, source
            FROM timeline_translation_search
            WHERE canonical_text IS NOT NULL
              AND canonical_text <> ''
            ORDER BY occurred_at DESC NULLS LAST
            LIMIT $1
            """,
            limit,
        )
        await seed(
            "production_search_labels_v1",
            "search",
            "Search labels harvested from timeline text rows.",
            [
                {
                    "input_json": {"query": str(row["canonical_text"])[:120], "ranked_event_ids": [row["event_id"]]},
                    "expected_json": {"event_ids": [row["event_id"]]},
                    "source_ref": f"timeline_translation_search:{row['event_id']}",
                    "label_source": "production_search_timeline",
                }
                for row in rows
            ],
        )

    if await _table_exists(conn, "identity_labels"):
        rows = await conn.fetch(
            """
            SELECT entity_a::text, entity_b::text, label, source, created_at
            FROM identity_labels
            ORDER BY created_at DESC NULLS LAST
            LIMIT $1
            """,
            limit,
        )
        await seed(
            "production_identity_labels_v1",
            "identity",
            "Identity eval labels harvested from dashboard/replay identity_labels.",
            [
                {
                    "input_json": {"prediction": "same" if int(row["label"] or 0) == 1 else "different"},
                    "expected_json": {"label": "same" if int(row["label"] or 0) == 1 else "different"},
                    "source_ref": f"identity_labels:{row['entity_a']}:{row['entity_b']}",
                    "label_source": f"production_identity:{row['source'] or 'unknown'}",
                }
                for row in rows
            ],
        )

    if await _table_exists(conn, "timeline_text_features"):
        rows = await conn.fetch(
            """
            SELECT event_id::text, sentiment_label
            FROM timeline_text_features
            WHERE sentiment_label IS NOT NULL
              AND sentiment_label <> ''
            ORDER BY processed_at DESC NULLS LAST
            LIMIT $1
            """,
            limit,
        )
        await seed(
            "production_sentiment_labels_v1",
            "sentiment",
            "Sentiment eval labels harvested from processed timeline text features.",
            [
                {
                    "input_json": {"prediction": row["sentiment_label"]},
                    "expected_json": {"label": row["sentiment_label"]},
                    "source_ref": f"timeline_text_features:{row['event_id']}",
                    "label_source": "production_sentiment_pipeline",
                }
                for row in rows
            ],
        )

    if await _table_exists(conn, "entity_faces"):
        rows = await conn.fetch(
            """
            SELECT face_id, entity_id::text, media_item_id, created_at
            FROM entity_faces
            ORDER BY created_at DESC NULLS LAST
            LIMIT $1
            """,
            limit,
        )
        await seed(
            "production_face_labels_v1",
            "face",
            "Face eval labels harvested from accepted entity_faces links.",
            [
                {
                    "input_json": {"prediction": "match", "face_id": row["face_id"]},
                    "expected_json": {"label": "match"},
                    "source_ref": f"entity_faces:{row['entity_id']}:{row['face_id']}",
                    "label_source": "production_face_entity_links",
                }
                for row in rows
            ],
        )

    if await _table_exists(conn, "location_evidence"):
        rows = await conn.fetch(
            """
            SELECT entity_id::text, evidence_key, status, source, updated_at
            FROM location_evidence
            WHERE status IN ('confirmed', 'rejected')
            ORDER BY updated_at DESC NULLS LAST
            LIMIT $1
            """,
            limit,
        )
        await seed(
            "production_location_labels_v1",
            "location",
            "Location eval labels harvested from confirmed/rejected location evidence.",
            [
                {
                    "input_json": {"prediction": "confirmed" if row["status"] == "confirmed" else "rejected"},
                    "expected_json": {"label": "confirmed" if row["status"] == "confirmed" else "rejected"},
                    "source_ref": f"location_evidence:{row['entity_id']}:{row['evidence_key']}",
                    "label_source": f"production_location:{row['source'] or 'unknown'}",
                }
                for row in rows
            ],
        )

    if await _table_exists(conn, "alert_fingerprints"):
        rows = await conn.fetch(
            """
            SELECT fingerprint, status, alert_type, updated_at
            FROM alert_fingerprints
            ORDER BY updated_at DESC NULLS LAST
            LIMIT $1
            """,
            limit,
        )
        await seed(
            "production_alert_fixtures_v1",
            "alerts",
            "Alert eval fixtures harvested from stream alert fingerprints.",
            [
                {
                    "input_json": {
                        "fingerprint": row["fingerprint"],
                        "prediction": "suppressed" if row["status"] == "suppressed" else "fired",
                    },
                    "expected_json": {"label": "suppressed" if row["status"] == "suppressed" else "fired"},
                    "source_ref": f"alert_fingerprints:{row['fingerprint']}",
                    "label_source": f"production_alert:{row['alert_type'] or 'unknown'}",
                }
                for row in rows
            ],
        )

    return {"sets": sets, "items": items, "by_task": by_task, "dry_run": dry_run}


def _prediction_for_row(task: str, row) -> tuple[dict[str, Any], dict[str, Any], bool | None, str | None]:
    input_json = _json_obj(row["input_json"])
    expected_json = _json_obj(row["expected_json"])
    try:
        if task == "search":
            expected = expected_json.get("event_ids", [])
            ranked = input_json.get("ranked_event_ids", [])
            item_recall = recall_at_k(expected, ranked, 20)
            item_mrr = mrr_at_k(expected, ranked, 20)
            return (
                {"ranked_event_ids": ranked},
                {"recall_at_20": item_recall, "mrr_at_20": item_mrr},
                item_recall > 0,
                None,
            )
        prediction_label = str(input_json.get("prediction", ""))
        expected_label = str(expected_json.get("label", ""))
        prediction_json = {"label": prediction_label}
        if task == "alerts":
            prediction_json["fingerprint"] = str(input_json.get("fingerprint") or row["item_id"])
        return (
            prediction_json,
            {},
            prediction_label == expected_label if expected_label else None,
            None,
        )
    except Exception as exc:  # noqa: BLE001 - an eval item error should not abort the run.
        return ({}, {}, None, str(exc)[:500])


async def run_eval(conn, *, task: str, model_or_rule_version: str = "manual", dry_run: bool = False) -> dict[str, Any]:
    if task not in SUPPORTED_TASKS:
        raise ValueError(f"unsupported eval task: {task}")

    rows = await conn.fetch(
        """
        SELECT i.id::text AS item_id,
               s.id::text AS set_id,
               i.input_json,
               i.expected_json
        FROM eval_sets s
        JOIN eval_items i ON i.set_id = s.id
        WHERE s.task_type = $1
        ORDER BY i.created_at, i.id
        """,
        task,
    )
    if task == "alerts":
        fingerprints = [
            str(_json_obj(row["input_json"]).get("fingerprint") or row["item_id"])
            for row in rows
        ]
        metrics = {"duplicate_count": duplicate_count(fingerprints), "support": len(rows)}
    elif task == "search":
        recalls = []
        mrrs = []
        for row in rows:
            expected = _json_obj(row["expected_json"]).get("event_ids", [])
            ranked = _json_obj(row["input_json"]).get("ranked_event_ids", [])
            recalls.append(recall_at_k(expected, ranked, 20))
            mrrs.append(mrr_at_k(expected, ranked, 20))
        metrics = {
            "recall_at_20": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
            "mrr_at_20": round(sum(mrrs) / len(mrrs), 4) if mrrs else 0.0,
            "support": len(rows),
        }
    else:
        truth = [str(_json_obj(row["expected_json"]).get("label", "")) for row in rows]
        pred = [str(_json_obj(row["input_json"]).get("prediction", "")) for row in rows]
        metrics = classification_metrics(truth, pred)

    previous_row = await conn.fetchrow(
        """
        SELECT r.metrics_json
        FROM eval_runs r
        JOIN eval_sets s ON s.id = r.set_id
        WHERE s.task_type = $1
        ORDER BY r.started_at DESC
        LIMIT 1
        """,
        task,
    )
    previous_metrics = _json_obj(previous_row["metrics_json"]) if previous_row else None
    gate = evaluate_metric_gates(task, metrics, previous_metrics)
    metrics = {**metrics, **gate}

    run_id = None
    if rows and not dry_run:
        run_id = await conn.fetchval(
            """
            INSERT INTO eval_runs (set_id, model_or_rule_version, status, metrics_json, finished_at)
            VALUES ($1::uuid, $2, 'completed', $3::jsonb, NOW())
            RETURNING id::text
            """,
            rows[0]["set_id"],
            model_or_rule_version,
            json.dumps(metrics),
        )
        prediction_rows = []
        for row in rows:
            prediction_json, score_json, correct, error = _prediction_for_row(task, row)
            prediction_rows.append((
                run_id,
                row["item_id"],
                json.dumps(prediction_json),
                json.dumps(score_json),
                correct,
                error,
            ))
        await conn.executemany(
            """
            INSERT INTO eval_predictions (
                run_id, item_id, prediction_json, score_json, correct, error
            )
            VALUES ($1::uuid, $2::uuid, $3::jsonb, $4::jsonb, $5, $6)
            ON CONFLICT (run_id, item_id) DO UPDATE SET
                prediction_json = EXCLUDED.prediction_json,
                score_json = EXCLUDED.score_json,
                correct = EXCLUDED.correct,
                error = EXCLUDED.error
            """,
            prediction_rows,
        )

    return {
        "task": task,
        "dry_run": dry_run,
        "run_id": run_id,
        "items": len(rows),
        "metrics": metrics,
        "gate_status": gate["gate_status"],
        "gate_failures": gate["gate_failures"],
        "gate_warnings": gate["gate_warnings"],
    }


async def seed_eval_sets(
    conn,
    *,
    seed_path: str | Path | None = None,
    dry_run: bool = False,
    include_production_labels: bool = True,
    production_limit_per_task: int = DEFAULT_PRODUCTION_LABEL_LIMIT,
) -> dict[str, Any]:
    path = Path(seed_path) if seed_path else DEFAULT_SEED_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    sets = payload if isinstance(payload, list) else payload.get("sets", [])
    inserted_sets = 0
    inserted_items = 0
    for item_set in sets:
        items = materialize_seed_items(item_set)
        if dry_run:
            inserted_sets += 1
            inserted_items += len(items)
            continue
        set_id = await _ensure_eval_set(
            conn,
            name=item_set["name"],
            task_type=item_set["task_type"],
            description=item_set.get("description"),
            dry_run=False,
        )
        inserted_sets += 1
        inserted_items += await _insert_eval_items(conn, set_id, items, dry_run=False)

    production = {"sets": 0, "items": 0, "by_task": {}, "dry_run": dry_run}
    if include_production_labels:
        production = await harvest_production_eval_items(
            conn,
            limit_per_task=production_limit_per_task,
            dry_run=dry_run,
        )
    return {
        "seed_path": str(path),
        "dry_run": dry_run,
        "sets": inserted_sets,
        "items": inserted_items,
        "production_sets": production["sets"],
        "production_items": production["items"],
        "production_by_task": production["by_task"],
    }
