"""
QA-lane tests for src/pipeline/run_reporting.py.

All four functions are pure (no DB). Covers:
- production_run_types: returns list containing 'incremental' and 'full_resolution'
- probe_phase_names: returns list containing 'forced_failure'
- is_production_run_type: True for production types, False otherwise
- is_probe_phase_name: True for probe phases, False otherwise
"""
from __future__ import annotations

import pytest

from src.pipeline.run_reporting import (
    PRODUCTION_RUN_TYPES,
    PROBE_PHASE_NAMES,
    is_probe_phase_name,
    is_production_run_type,
    probe_phase_names,
    production_run_types,
)


class TestProductionRunTypes:
    def test_returns_list(self):
        assert isinstance(production_run_types(), list)

    def test_contains_incremental(self):
        assert "incremental" in production_run_types()

    def test_contains_full_resolution(self):
        assert "full_resolution" in production_run_types()

    def test_returns_copy_not_original(self):
        # Mutations must not affect the module constant
        result = production_run_types()
        result.append("rogue")
        assert "rogue" not in production_run_types()

    def test_matches_constant(self):
        assert set(production_run_types()) == set(PRODUCTION_RUN_TYPES)


class TestProbePhaseNames:
    def test_returns_list(self):
        assert isinstance(probe_phase_names(), list)

    def test_contains_forced_failure(self):
        assert "forced_failure" in probe_phase_names()

    def test_returns_copy_not_original(self):
        result = probe_phase_names()
        result.append("rogue")
        assert "rogue" not in probe_phase_names()

    def test_matches_constant(self):
        assert set(probe_phase_names()) == set(PROBE_PHASE_NAMES)


class TestIsProductionRunType:
    def test_incremental_is_production(self):
        assert is_production_run_type("incremental") is True

    def test_full_resolution_is_production(self):
        assert is_production_run_type("full_resolution") is True

    def test_none_is_not_production(self):
        assert is_production_run_type(None) is False

    def test_arbitrary_string_is_not_production(self):
        assert is_production_run_type("test_run") is False

    def test_empty_string_is_not_production(self):
        assert is_production_run_type("") is False

    def test_probe_phase_is_not_production(self):
        assert is_production_run_type("forced_failure") is False


class TestIsProbePhase:
    def test_forced_failure_is_probe(self):
        assert is_probe_phase_name("forced_failure") is True

    def test_none_is_not_probe(self):
        assert is_probe_phase_name(None) is False

    def test_production_run_type_is_not_probe(self):
        assert is_probe_phase_name("incremental") is False

    def test_arbitrary_string_is_not_probe(self):
        assert is_probe_phase_name("some_phase") is False

    def test_empty_string_is_not_probe(self):
        assert is_probe_phase_name("") is False
