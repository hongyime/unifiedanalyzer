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


async def seed_eval_sets(conn, *, seed_path: str | Path | None = None, dry_run: bool = False) -> dict[str, Any]:
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
        set_id = await conn.fetchval(
            """
            INSERT INTO eval_sets (name, task_type, description)
            VALUES ($1, $2, $3)
            ON CONFLICT (name) DO UPDATE SET
                task_type = EXCLUDED.task_type,
                description = EXCLUDED.description
            RETURNING id
            """,
            item_set["name"],
            item_set["task_type"],
            item_set.get("description"),
        )
        inserted_sets += 1
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
                inserted_items += 1
    return {
        "seed_path": str(path),
        "dry_run": dry_run,
        "sets": inserted_sets,
        "items": inserted_items,
    }
