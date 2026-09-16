"""Pure-function tests for src/pipeline/graph_nl_query.py.

Covers env parsing, prompt composition, backend dispatch, and the public
``summarise_entity`` entry point with backends mocked out (no live LLM
required).  Database access is stubbed via monkeypatching the pool
accessor.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.pipeline import graph_nl_query as nl


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


class TestEnvHelpers:
    def test_cfg_uses_default_when_missing(self, monkeypatch):
        monkeypatch.delenv("GRAPH_NL_MODEL", raising=False)
        assert nl._cfg("GRAPH_NL_MODEL", "fallback") == "fallback"

    def test_cfg_uses_default_when_empty_string(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_MODEL", "")
        assert nl._cfg("GRAPH_NL_MODEL", "fallback") == "fallback"

    def test_cfg_uses_env_when_present(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_MODEL", "custom-model")
        assert nl._cfg("GRAPH_NL_MODEL", "fallback") == "custom-model"

    def test_cfg_int_bad_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_MAX_EDGES", "not-a-number")
        assert nl._cfg_int("GRAPH_NL_MAX_EDGES", 42) == 42

    def test_cfg_int_good_value(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_MAX_EDGES", "7")
        assert nl._cfg_int("GRAPH_NL_MAX_EDGES", 42) == 7

    def test_cfg_float_bad_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_TIMEOUT_S", "x.y")
        assert nl._cfg_float("GRAPH_NL_TIMEOUT_S", 5.0) == 5.0

    def test_cfg_float_good_value(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_TIMEOUT_S", "2.5")
        assert nl._cfg_float("GRAPH_NL_TIMEOUT_S", 5.0) == 2.5

    def test_is_enabled_default_off(self, monkeypatch):
        monkeypatch.delenv("GRAPH_NL_ENABLED", raising=False)
        assert nl._is_enabled() is False

    def test_is_enabled_via_1(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_ENABLED", "1")
        assert nl._is_enabled() is True


# ---------------------------------------------------------------------------
# Prompt composition
# ---------------------------------------------------------------------------


class TestComposePrompt:
    def test_prompt_embeds_dossier(self):
        ctx = {"entity": {"id": "abc", "canonical_name": "Alice"}, "edges": [],
               "timeline": [], "platforms": []}
        p = nl._compose_prompt(ctx, "who is Alice?")
        assert "=== dossier ===" in p
        assert "Alice" in p
        assert "who is Alice?" in p
        assert nl._SYSTEM in p

    def test_prompt_falls_back_to_default_question(self):
        ctx = {"entity": {"id": "x"}, "edges": [], "timeline": [], "platforms": []}
        p = nl._compose_prompt(ctx, "")
        assert "Summarise this entity" in p


# ---------------------------------------------------------------------------
# Backend adapter — Ollama
# ---------------------------------------------------------------------------


class TestOllama:
    @pytest.mark.asyncio
    async def test_call_ollama_returns_response_field(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_OLLAMA_URL", "http://ollama.test:11434")
        monkeypatch.setenv("GRAPH_NL_MODEL", "llama3.2:3b")

        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json = MagicMock(return_value={"response": "Hello, world."})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=fake_resp)

        with patch.object(httpx, "AsyncClient", return_value=client):
            answer = await nl._call_ollama("prompt text")
        assert answer == "Hello, world."
        called_url = client.post.await_args.args[0]
        assert called_url == "http://ollama.test:11434/api/generate"

    @pytest.mark.asyncio
    async def test_call_ollama_strips_whitespace(self, monkeypatch):
        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json = MagicMock(return_value={"response": "  spaced\n"})
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=fake_resp)
        with patch.object(httpx, "AsyncClient", return_value=client):
            assert await nl._call_ollama("p") == "spaced"


# ---------------------------------------------------------------------------
# Backend adapter — OpenAI-compatible
# ---------------------------------------------------------------------------


class TestOpenAICompat:
    @pytest.mark.asyncio
    async def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("GRAPH_NL_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="GRAPH_NL_API_KEY"):
            await nl._call_openai_compatible("p")

    @pytest.mark.asyncio
    async def test_reads_choices_content(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_API_KEY", "sk-fake")
        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": "chat answer"}}],
        })
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=fake_resp)
        with patch.object(httpx, "AsyncClient", return_value=client):
            assert await nl._call_openai_compatible("p") == "chat answer"


# ---------------------------------------------------------------------------
# summarise_entity — end-to-end control flow with mocks
# ---------------------------------------------------------------------------


def _pool_stub_returning(entity_row=None, links=None, edges=None, events=None):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=entity_row)
    conn.fetch = AsyncMock(side_effect=[
        links or [], edges or [], events or [],
    ])
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool


class TestSummariseEntity:
    @pytest.mark.asyncio
    async def test_disabled_returns_skip(self, monkeypatch):
        monkeypatch.delenv("GRAPH_NL_ENABLED", raising=False)
        out = await nl.summarise_entity("abc")
        assert out["skipped"] == "disabled"

    @pytest.mark.asyncio
    async def test_entity_not_found(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_ENABLED", "1")
        pool = _pool_stub_returning(entity_row=None)
        monkeypatch.setattr(nl, "get_analyzer_pool", lambda: pool)
        out = await nl.summarise_entity("00000000-0000-0000-0000-000000000000")
        assert out["skipped"] == "entity_not_found"

    @pytest.mark.asyncio
    async def test_backend_off_returns_context(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_ENABLED", "1")
        monkeypatch.setenv("GRAPH_NL_BACKEND", "off")
        ent = {
            "id": "abc", "canonical_name": "Alice",
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "last_seen_at": None,
        }
        pool = _pool_stub_returning(entity_row=ent)
        monkeypatch.setattr(nl, "get_analyzer_pool", lambda: pool)
        out = await nl.summarise_entity("abc")
        assert out["skipped"] == "backend_off"
        assert out["context"]["entity"]["canonical_name"] == "Alice"

    @pytest.mark.asyncio
    async def test_backend_unreachable_still_returns_context(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_ENABLED", "1")
        monkeypatch.setenv("GRAPH_NL_BACKEND", "ollama")
        ent = {
            "id": "abc", "canonical_name": "Alice",
            "created_at": None, "last_seen_at": None,
        }
        pool = _pool_stub_returning(entity_row=ent)
        monkeypatch.setattr(nl, "get_analyzer_pool", lambda: pool)

        async def boom(_prompt: str) -> str:
            raise httpx.ConnectError("no ollama")

        monkeypatch.setattr(nl, "_call_ollama", boom)
        out = await nl.summarise_entity("abc")
        assert out["skipped"] == "backend_unreachable"
        assert out["backend"] == "ollama"
        assert out["context"]["entity"]["canonical_name"] == "Alice"

    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch):
        monkeypatch.setenv("GRAPH_NL_ENABLED", "1")
        monkeypatch.setenv("GRAPH_NL_BACKEND", "ollama")
        ent = {
            "id": "abc", "canonical_name": "Alice",
            "created_at": None, "last_seen_at": None,
        }
        pool = _pool_stub_returning(entity_row=ent)
        monkeypatch.setattr(nl, "get_analyzer_pool", lambda: pool)

        async def ok(_prompt: str) -> str:
            return "Alice is a person."

        monkeypatch.setattr(nl, "_call_ollama", ok)
        out = await nl.summarise_entity("abc", "who is alice?")
        assert out["answer"] == "Alice is a person."
        assert out["backend"] == "ollama"
