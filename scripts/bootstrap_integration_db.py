"""Build disposable CI databases from the actual Analyzer/Collector schemas.

Run with ``python -m scripts.bootstrap_integration_db``. This is a test helper,
not a production migration command. It rejects non-loopback and non-CI URLs.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_ROOT = ROOT / ".test-upstream" / "collector-reviewed"


def validate_test_urls(environ: dict[str, str]) -> tuple[str, str]:
    """Require explicit opt-in and two isolated, local fixture databases."""
    if environ.get("ANALYZER_CI_SCHEMA_SETUP") != "1":
        raise ValueError("Disposable schema setup requires ANALYZER_CI_SCHEMA_SETUP=1")
    urls = []
    endpoints = []
    for variable, database in (
        ("ANALYZER_DATABASE_URL", "analyzer_ci"),
        ("COLLECTOR_DATABASE_URL", "collector_ci"),
    ):
        value = environ.get(variable, "")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.path != f"/{database}"
            or parsed.query
            or parsed.fragment
        ):
            # Do not include connection strings or credentials in errors.
            raise ValueError(f"{variable} must select local disposable {database}")
        endpoints.append((parsed.hostname, parsed.port or 5432))
        urls.append(value)
    if endpoints[0] != endpoints[1]:
        raise ValueError("Both fixture databases must use the same local server")
    return urls[0], urls[1]


async def apply_schemas(analyzer_url: str, collector_url: str) -> dict:
    spec = importlib.util.spec_from_file_location(
        "collector_fixture_migrations", COLLECTOR_ROOT / "src/db/migrate.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Pinned Collector schema checkout is missing")
    migrations = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrations)

    connection = await asyncpg.connect(analyzer_url, timeout=5, command_timeout=60)
    try:
        if not await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'collector_ci')"
        ):
            await connection.execute("CREATE DATABASE collector_ci")
        await connection.execute((ROOT / "src/db/schema.sql").read_text(encoding="utf-8"))
    finally:
        await connection.close()

    pool = await asyncpg.create_pool(collector_url, min_size=1, max_size=2, timeout=5, command_timeout=60)
    try:
        summary = await migrations.apply_all(pool)
        if summary["deferred"]:
            raise RuntimeError("Collector fixture schema setup was deferred")
        return summary
    finally:
        await pool.close()


def apply_face_schema(analyzer_url: str) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from src.face.storage.database import Base

    engine = create_engine(
        make_url(analyzer_url).set(drivername="postgresql+psycopg2"),
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=60000"},
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS facetracker"))
            Base.metadata.create_all(
                connection.execution_options(schema_translate_map={None: "facetracker"})
            )
    finally:
        engine.dispose()


def main() -> None:
    analyzer_url, collector_url = validate_test_urls(dict(os.environ))
    summary = asyncio.run(asyncio.wait_for(apply_schemas(analyzer_url, collector_url), timeout=120))
    apply_face_schema(analyzer_url)
    print(json.dumps({"collector_schema": summary, "analyzer_schema": True, "face_schema": True}))


if __name__ == "__main__":
    main()
