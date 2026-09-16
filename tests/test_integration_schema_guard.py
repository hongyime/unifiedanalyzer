"""Reject production targets before opening any database connection."""
import pytest

from scripts.bootstrap_integration_db import validate_test_urls


def fixture_environment() -> dict[str, str]:
    return {
        "ANALYZER_CI_SCHEMA_SETUP": "1",
        "ANALYZER_DATABASE_URL": "postgres://fixture@127.0.0.1:5432/analyzer_ci",
        "COLLECTOR_DATABASE_URL": "postgres://fixture@127.0.0.1:5432/collector_ci",
    }


def test_explicit_local_disposable_pair_is_accepted() -> None:
    environ = fixture_environment()
    assert validate_test_urls(environ) == (
        environ["ANALYZER_DATABASE_URL"], environ["COLLECTOR_DATABASE_URL"]
    )


@pytest.mark.parametrize("variable,value", [
    ("ANALYZER_CI_SCHEMA_SETUP", ""),
    ("ANALYZER_DATABASE_URL", "postgres://fixture@db.example.com/analyzer_ci"),
    ("ANALYZER_DATABASE_URL", "postgres://fixture@127.0.0.1/unifiedanalyzer"),
    ("COLLECTOR_DATABASE_URL", "postgres://fixture@127.0.0.1/analyzer_ci"),
    ("COLLECTOR_DATABASE_URL", "postgres://fixture@127.0.0.1:6432/collector_ci"),
    ("COLLECTOR_DATABASE_URL", "postgres://fixture@127.0.0.1/collector_ci?host=db.example.com"),
    ("COLLECTOR_DATABASE_URL", "postgres://fixture@127.0.0.1/collector_ci#alternate"),
    ("COLLECTOR_DATABASE_URL", ""),
])
def test_unsafe_or_ambiguous_targets_are_rejected(variable: str, value: str) -> None:
    environ = fixture_environment()
    environ[variable] = value
    with pytest.raises(ValueError) as error:
        validate_test_urls(environ)
    assert "postgres://" not in str(error.value)
