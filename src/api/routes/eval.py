from __future__ import annotations

from fastapi import APIRouter, Query

from src.db.connection import get_analyzer_pool

router = APIRouter(tags=["eval"])


@router.get("/eval/runs")
async def list_eval_runs(task: str | None = None, limit: int = Query(50, ge=1, le=200)):
    params: list = []
    where = ""
    if task:
        params.append(task)
        where = "WHERE s.task_type = $1"
    params.append(limit)
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT r.id::text, s.name, s.task_type, r.model_or_rule_version,
                   r.status, r.metrics_json, r.started_at, r.finished_at
            FROM eval_runs r
            JOIN eval_sets s ON s.id = r.set_id
            {where}
            ORDER BY r.started_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return {"data": [_row(row) for row in rows], "total": len(rows)}


@router.get("/eval/latest")
async def latest_eval_runs():
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (s.task_type)
                   r.id::text, s.name, s.task_type, r.model_or_rule_version,
                   r.status, r.metrics_json, r.started_at, r.finished_at
            FROM eval_runs r
            JOIN eval_sets s ON s.id = r.set_id
            ORDER BY s.task_type, r.started_at DESC
            """
        )
    return {"data": [_row(row) for row in rows], "total": len(rows)}


@router.get("/eval/{task}/regressions")
async def eval_regressions(task: str):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id::text, s.name, s.task_type, r.model_or_rule_version,
                   r.status, r.metrics_json, r.started_at, r.finished_at
            FROM eval_runs r
            JOIN eval_sets s ON s.id = r.set_id
            WHERE s.task_type = $1
            ORDER BY r.started_at DESC
            LIMIT 2
            """,
            task,
        )
    runs = [_row(row) for row in rows]
    delta = {}
    if len(runs) == 2:
        latest = runs[0]["metrics"] or {}
        previous = runs[1]["metrics"] or {}
        for key, value in latest.items():
            if isinstance(value, (int, float)) and isinstance(previous.get(key), (int, float)):
                delta[key] = round(float(value) - float(previous[key]), 4)
    return {"task": task, "runs": runs, "delta": delta}


def _row(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "task_type": row["task_type"],
        "model_or_rule_version": row["model_or_rule_version"],
        "status": row["status"],
        "metrics": row["metrics_json"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
    }
