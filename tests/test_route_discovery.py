from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.api.routes import readiness


def application(paths: tuple[str, ...]) -> FastAPI:
    app = FastAPI()
    router = APIRouter()
    for path in paths:
        async def endpoint() -> dict[str, str]:
            return {"source": "synthetic-api"}

        router.add_api_route(path, endpoint, methods=["GET"])
    app.include_router(router, prefix="/api")

    @app.get("/{full_path:path}")
    async def fallback(full_path: str) -> dict[str, str]:
        return {"source": "synthetic-spa"}

    return app


@pytest.mark.asyncio
async def test_included_routes_are_proven_without_http_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    app = application(("/entities", "/review/candidates", "/triage", "/cases"))
    probe = AsyncMock(return_value={"ok": False})
    monkeypatch.setattr(readiness, "_analyst_workflow_http_probe", probe)

    result = await readiness._analyst_workflow_status(app)

    assert result["ok"] is True
    assert result["missing"] == []
    assert set(result["mounted"]) == {"/api/entities", "/api/review/candidates", "/api/triage", "/api/cases"}
    probe.assert_not_awaited()
    with TestClient(app) as client:
        for path in result["mounted"]:
            assert client.get(path).json() == {"source": "synthetic-api"}


@pytest.mark.asyncio
async def test_missing_routes_are_not_proven_by_module_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    app = application(("/entities",))
    probe = AsyncMock(return_value={"ok": False})
    monkeypatch.setattr(readiness, "_analyst_workflow_http_probe", probe)

    result = await readiness._analyst_workflow_status(app)

    assert result["ok"] is False
    assert set(result["missing"]) == {"/api/review/candidates", "/api/triage", "/api/cases"}
    assert result["mounted"] == ["/api/entities"]
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_prefixed_nested_routers_use_effective_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    outer = APIRouter(prefix="/api")
    inner = APIRouter()
    for path in ("/entities", "/review/candidates", "/triage", "/cases"):
        async def endpoint() -> dict[str, bool]:
            return {"fixture": True}

        inner.add_api_route(path, endpoint, methods=["GET"])
    outer.include_router(inner)
    app.include_router(outer)
    probe = AsyncMock(return_value={"ok": False})
    monkeypatch.setattr(readiness, "_analyst_workflow_http_probe", probe)

    result = await readiness._analyst_workflow_status(app)

    assert result["ok"] is True
    assert result["missing"] == []
    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_application_does_not_borrow_global_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    probe = AsyncMock(return_value={"ok": False})
    monkeypatch.setattr(readiness, "_analyst_workflow_http_probe", probe)

    result = await readiness._analyst_workflow_status(app)

    assert result["ok"] is False
    assert result["mounted"] == []
    assert set(result["missing"]) == set(result["required"])
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_http_fallback_retains_its_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = {"ok": True, "probe": "http", "missing": [], "results": {"fixture": "proven"}}
    probe = AsyncMock(return_value=evidence)
    monkeypatch.setattr(readiness, "_analyst_workflow_http_probe", probe)

    assert await readiness._analyst_workflow_status(FastAPI()) == evidence
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_unreadable_route_tree_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class InvalidApplication:
        @property
        def routes(self) -> list:
            raise RuntimeError("fixture route tree unavailable")

    probe = AsyncMock()
    monkeypatch.setattr(readiness, "_analyst_workflow_http_probe", probe)

    result = await readiness._analyst_workflow_status(InvalidApplication())

    assert result["ok"] is False
    assert result["mounted"] == []
    assert set(result["missing"]) == set(result["required"])
    assert result["error"] == "RuntimeError: fixture route tree unavailable"
    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_application_uses_its_mounted_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api import app as api_app

    fixture_app = application(("/entities", "/review/candidates", "/triage", "/cases"))
    monkeypatch.setattr(api_app, "app", fixture_app)
    probe = AsyncMock()
    monkeypatch.setattr(readiness, "_analyst_workflow_http_probe", probe)

    result = await readiness._analyst_workflow_status()

    assert result["ok"] is True
    assert result["missing"] == []
    probe.assert_not_awaited()
