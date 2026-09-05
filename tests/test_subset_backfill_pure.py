"""
QA-lane tests for pure functions in:
- src/pipeline/run_interaction_subset.py: _csv_set
- src/pipeline/run_timeline_subset.py: _csv_set (same implementation)
- src/pipeline/watch_refill_backfill.py: _count_files
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.pipeline.run_interaction_subset import _csv_set as interaction_csv_set
from src.pipeline.run_timeline_subset import _csv_set as timeline_csv_set
from src.pipeline.watch_refill_backfill import _count_files


# ---------------------------------------------------------------------------
# run_interaction_subset._csv_set  (and timeline variant — same logic)
# ---------------------------------------------------------------------------

class TestCsvSetInteraction:
    def test_none_returns_none(self):
        assert interaction_csv_set(None) is None

    def test_empty_string_returns_none(self):
        assert interaction_csv_set("") is None

    def test_whitespace_only_returns_none(self):
        assert interaction_csv_set("   ,  ") is None

    def test_single_value(self):
        assert interaction_csv_set("telegram") == {"telegram"}

    def test_multiple_values(self):
        result = interaction_csv_set("telegram,whatsapp,instagram")
        assert result == {"telegram", "whatsapp", "instagram"}

    def test_strips_whitespace_around_values(self):
        result = interaction_csv_set("  telegram  ,  whatsapp  ")
        assert result == {"telegram", "whatsapp"}

    def test_deduplicates(self):
        result = interaction_csv_set("a,a,b")
        assert result == {"a", "b"}


class TestCsvSetTimeline:
    def test_none_returns_none(self):
        assert timeline_csv_set(None) is None

    def test_empty_string_returns_none(self):
        assert timeline_csv_set("") is None

    def test_single_value(self):
        assert timeline_csv_set("github") == {"github"}

    def test_multiple_values(self):
        result = timeline_csv_set("github,instagram,strava")
        assert result == {"github", "instagram", "strava"}


# ---------------------------------------------------------------------------
# watch_refill_backfill._count_files
# ---------------------------------------------------------------------------

class TestCountFiles:
    def test_missing_dir_returns_0(self, tmp_path):
        missing = tmp_path / "does_not_exist"
        assert _count_files(missing) == 0

    def test_empty_dir_returns_0(self, tmp_path):
        assert _count_files(tmp_path) == 0

    def test_single_file(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        assert _count_files(tmp_path) == 1

    def test_multiple_files(self, tmp_path):
        for i in range(5):
            (tmp_path / f"file{i}.txt").write_text("x")
        assert _count_files(tmp_path) == 5

    def test_recursive_count(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (tmp_path / "a.txt").write_text("x")
        (subdir / "b.txt").write_text("x")
        assert _count_files(tmp_path) == 2

    def test_nested_dirs(self, tmp_path):
        d1 = tmp_path / "d1"
        d2 = d1 / "d2"
        d2.mkdir(parents=True)
        (d2 / "deep.txt").write_text("x")
        assert _count_files(tmp_path) == 1
