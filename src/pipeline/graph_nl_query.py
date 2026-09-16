"""Track-Explore #9: local NL query over graph + timeline.

Given an entity_id, load a bounded slice of that entity's graph edges +
timeline events + platform links and ask a local LLM (default Ollama HTTP,
no paid API per v7 plan) to produce a natural-language summary or answer a
specific question.

The heavy work is context extraction, not the LLM call — the prompt has to
fit inside a modest local model's context window, so we cap by count and by
recency.  All LLM output is treated as untrusted and returned as-is with a
disclaimer; nothing is auto-promoted into the analyst graph.

Design contract:

* Env-gated (``GRAPH_NL_ENABLED``, default 0).  When off, callers get
  ``{"skipped": "disabled", ...}`` — never an exception.
* Backend-pluggable via ``GRAPH_NL_BACKEND``:
    - ``ollama``  → POST ``$OLLAMA_URL/api/generate`` (default
       ``http://host.docker.internal:11434``), model ``$GRAPH_NL_MODEL``
       (default ``llama3.2:3b``).
    - ``openai``  → POST ``$GRAPH_NL_OPENAI_URL`` (OpenAI-compatible;
       OpenRouter, LM Studio, vLLM all speak this) with bearer
       ``$GRAPH_NL_API_KEY``.
    - ``off``     → return context + a note, never call out.
* Backend reachability is probed once per call.  If unreachable → return
  ``{"skipped": "backend_unreachable", ...}`` with context still attached
  so the caller can render the raw evidence.
* No auto-write back into the DB.  This is a read-only inference tool.

Env vars:

    GRAPH_NL_ENABLED            "1" enables the endpoint (default 0)
    GRAPH_NL_BACKEND            ollama|openai|off (default ollama)
    GRAPH_NL_MODEL              model id (default llama3.2:3b for ollama)
    GRAPH_NL_OLLAMA_URL         (default http://host.docker.internal:11434)
    GRAPH_NL_OPENAI_URL         (default https://openrouter.ai/api/v1/chat/completions)
    GRAPH_NL_API_KEY            bearer for openai-compatible endpoints
    GRAPH_NL_MAX_EDGES          cap on graph edges fed to the model (default 30)
    GRAPH_NL_MAX_TIMELINE       cap on timeline events fed to the model (default 30)
    GRAPH_NL_MAX_LINKS          cap on platform links fed to the model (default 15)
    GRAPH_NL_TIMEOUT_S          LLM HTTP timeout (default 60)
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)


def _cfg(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _cfg_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _is_enabled() -> bool:
    return os.getenv("GRAPH_NL_ENABLED", "0") == "1"


# ---------------------------------------------------------------------------
# Context extraction — bounded reads from analyzer DB
# ---------------------------------------------------------------------------


async def _extract_context(entity_id: str) -> dict[str, Any]:
    """Load a bounded slice of the entity's neighborhood for the prompt.

    Returns a dict with:
      * ``entity``      — canonical_name / created_at
      * ``platforms``   — most recent N platform link handles
      * ``edges``       — most recent N graph edges (both directions)
      * ``timeline``    — most recent N timeline events

    Every list is capped so the composed prompt stays under a modest local
    model's context window.  Empty on any DB error — the caller decides how
    to handle a data-less response.
    """
    max_edges = _cfg_int("GRAPH_NL_MAX_EDGES", 30)
    max_timeline = _cfg_int("GRAPH_NL_MAX_TIMELINE", 30)
    max_links = _cfg_int("GRAPH_NL_MAX_LINKS", 15)

    pool = get_analyzer_pool()
    ctx: dict[str, Any] = {
        "entity": None,
        "platforms": [],
        "edges": [],
        "timeline": [],
    }

    async with pool.acquire() as conn:
        ent = await conn.fetchrow(
            "SELECT id, canonical_name, created_at, last_seen_at "
            "FROM entities WHERE id = $1::uuid",
            entity_id,
        )
        if ent is None:
            return ctx
        ctx["entity"] = {
            "id": str(ent["id"]),
            "canonical_name": ent["canonical_name"],
            "created_at": ent["created_at"].isoformat() if ent["created_at"] else None,
            "last_seen_at": ent["last_seen_at"].isoformat() if ent["last_seen_at"] else None,
        }

        links = await conn.fetch(
            """
            SELECT platform, platform_username, platform_user_id, last_seen_at
            FROM entity_platform_links
            WHERE entity_id = $1::uuid
            ORDER BY last_seen_at DESC NULLS LAST
            LIMIT $2
            """,
            entity_id, max_links,
        )
        ctx["platforms"] = [
            {
                "platform": r["platform"],
                "username": r["platform_username"],
                "platform_user_id": r["platform_user_id"],
                "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
            }
            for r in links
        ]

        edges = await conn.fetch(
            """
            SELECT from_entity_id, to_entity_id, relationship_type,
                   confidence_bucket, weight, source, last_seen_at, why
            FROM graph_edges
            WHERE from_entity_id = $1::uuid OR to_entity_id = $1::uuid
            ORDER BY last_seen_at DESC NULLS LAST
            LIMIT $2
            """,
            entity_id, max_edges,
        )
        ctx["edges"] = [
            {
                "from_id": str(r["from_entity_id"]),
                "to_id": str(r["to_entity_id"]),
                "relationship_type": r["relationship_type"],
                "confidence": r["confidence_bucket"],
                "weight": r["weight"],
                "source": r["source"],
                "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
                "why": r["why"],
            }
            for r in edges
        ]

        events = await conn.fetch(
            """
            SELECT source, event_type, occurred_at, LEFT(COALESCE(title, ''), 200) AS title
            FROM timeline_events
            WHERE entity_id = $1::uuid
            ORDER BY occurred_at DESC
            LIMIT $2
            """,
            entity_id, max_timeline,
        )
        ctx["timeline"] = [
            {
                "source": r["source"],
                "event_type": r["event_type"],
                "occurred_at": r["occurred_at"].isoformat() if r["occurred_at"] else None,
                "title": r["title"],
            }
            for r in events
        ]

    return ctx


# ---------------------------------------------------------------------------
# Prompt composition
# ---------------------------------------------------------------------------


_SYSTEM = (
    "You are a factual research summariser working for an OSINT analyst.  "
    "You are given a JSON dossier describing one target entity: their aliases "
    "across platforms, the graph edges linking them to other entities, and "
    "a recent activity timeline.  Answer the operator's question strictly "
    "using facts present in the dossier.  Never invent identifiers, dates, "
    "or relationships.  If the dossier lacks the information needed, say so."
)


def _compose_prompt(context: dict[str, Any], question: str) -> str:
    """Compose the full prompt (system + dossier + question).

    Ollama's /api/generate takes a single ``prompt`` string; OpenAI-compatible
    /chat/completions takes a messages list — the caller adapts as needed.
    """
    import json as _json

    lines = [
        _SYSTEM,
        "",
        "=== dossier ===",
        _json.dumps(context, ensure_ascii=False, indent=2),
        "=== /dossier ===",
        "",
        f"Question: {question.strip() or 'Summarise this entity in 3-5 sentences.'}",
        "",
        "Answer:",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backend adapters
# ---------------------------------------------------------------------------


async def _call_ollama(prompt: str) -> str:
    base = _cfg("GRAPH_NL_OLLAMA_URL", "http://host.docker.internal:11434")
    model = _cfg("GRAPH_NL_MODEL", "llama3.2:3b")
    timeout = _cfg_float("GRAPH_NL_TIMEOUT_S", 60.0)
    payload = {"model": model, "prompt": prompt, "stream": False}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base.rstrip('/')}/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return (data.get("response") or "").strip()


async def _call_openai_compatible(prompt: str) -> str:
    """OpenAI-compatible /v1/chat/completions endpoint (OpenRouter, LM Studio,
    vLLM, TGI).  Bearer via GRAPH_NL_API_KEY."""
    url = _cfg("GRAPH_NL_OPENAI_URL",
               "https://openrouter.ai/api/v1/chat/completions")
    model = _cfg("GRAPH_NL_MODEL", "meta-llama/llama-3.2-3b-instruct:free")
    key = os.getenv("GRAPH_NL_API_KEY", "")
    timeout = _cfg_float("GRAPH_NL_TIMEOUT_S", 60.0)
    if not key:
        raise RuntimeError("GRAPH_NL_API_KEY is empty; cannot call openai-compatible backend")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as exc:  # pragma: no cover — defensive
        raise RuntimeError(f"unexpected openai-compatible response shape: {exc}")


async def _call_backend(prompt: str) -> str:
    backend = _cfg("GRAPH_NL_BACKEND", "ollama").lower()
    if backend == "ollama":
        return await _call_ollama(prompt)
    if backend == "openai":
        return await _call_openai_compatible(prompt)
    if backend == "off":
        return ""
    raise RuntimeError(f"unknown GRAPH_NL_BACKEND={backend!r}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def summarise_entity(entity_id: str, question: str = "") -> dict[str, Any]:
    """Return ``{"context": ..., "answer": str, "backend": str}`` on success or
    ``{"skipped": <reason>, ...}`` if disabled / unreachable / no entity.

    Never raises — every failure mode maps to a ``skipped`` result so the
    caller can render the raw context even when the LLM is offline.
    """
    if not _is_enabled():
        return {
            "skipped": "disabled",
            "hint": "set GRAPH_NL_ENABLED=1 in .env to enable graph_nl_query",
        }

    context = await _extract_context(entity_id)
    if context["entity"] is None:
        return {"skipped": "entity_not_found", "entity_id": entity_id}

    backend = _cfg("GRAPH_NL_BACKEND", "ollama").lower()
    if backend == "off":
        return {
            "skipped": "backend_off",
            "context": context,
            "hint": "set GRAPH_NL_BACKEND=ollama|openai",
        }

    prompt = _compose_prompt(context, question)
    try:
        answer = await _call_backend(prompt)
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning(
            "graph_nl_query backend=%s unreachable: %s: %s",
            backend, exc.__class__.__name__, exc,
        )
        return {
            "skipped": "backend_unreachable",
            "context": context,
            "backend": backend,
            "error": f"{exc.__class__.__name__}: {exc}",
        }

    return {
        "context": context,
        "answer": answer,
        "backend": backend,
    }


__all__ = ["summarise_entity"]
