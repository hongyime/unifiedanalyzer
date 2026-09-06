"""
QA-lane tests for pure row-mapper functions in:
- src/api/routes/alerts.py: _stream_alert_row, _alert_window_row,
  _decode_alert_suppression
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.api.routes.alerts import (
    _alert_window_row,
    _decode_alert_suppression,
    _stream_alert_row,
)

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _stream_row(**overrides):
    base = {
        "fingerprint": "abc123",
        "alert_type": "SILENCE_GAP",
        "entity_id": "eid-1",
        "source": "telegram",
        "window_start": _NOW,
        "window_end": _NOW,
        "last_sent_at": _NOW,
        "count": 3,
        "status": "sent",
        "detail": {"key": "val"},
        "updated_at": _NOW,
    }
    base.update(overrides)
    return base


def _window_row(**overrides):
    base = {
        "bucket_type": "hour",
        "bucket_key": "2024-06-01T12",
        "source": "telegram",
        "window_start": _NOW,
        "window_end": _NOW,
        "count": 10,
        "baseline": 5.0,
        "detail": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return base


def _suppression_row(**overrides):
    base = {
        "id": "sup-1",
        "scope": "entity",
        "alert_type": "SILENCE_GAP",
        "entity_id": "eid-1",
        "source": "telegram",
        "reason": "maintenance",
        "starts_at": _NOW,
        "ends_at": _NOW,
        "created_at": _NOW,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _stream_alert_row
# ---------------------------------------------------------------------------

class TestStreamAlertRow:
    def test_basic_fields_mapped(self):
        result = _stream_alert_row(_stream_row())
        assert result["fingerprint"] == "abc123"
        assert result["alert_type"] == "SILENCE_GAP"
        assert result["count"] == 3
        assert result["status"] == "sent"

    def test_datetime_fields_isoformatted(self):
        result = _stream_alert_row(_stream_row())
        assert "2024-06-01" in result["window_start"]
        assert "2024-06-01" in result["last_sent_at"]

    def test_none_datetimes_become_none(self):
        result = _stream_alert_row(_stream_row(window_start=None, last_sent_at=None, updated_at=None))
        assert result["window_start"] is None
        assert result["last_sent_at"] is None

    def test_detail_decoded(self):
        result = _stream_alert_row(_stream_row(detail='{"x": 1}'))
        assert result["detail"] == {"x": 1}

    def test_none_detail_becomes_empty_dict(self):
        result = _stream_alert_row(_stream_row(detail=None))
        assert result["detail"] == {}


# ---------------------------------------------------------------------------
# _alert_window_row
# ---------------------------------------------------------------------------

class TestAlertWindowRow:
    def test_basic_fields_mapped(self):
        result = _alert_window_row(_window_row())
        assert result["bucket_type"] == "hour"
        assert result["count"] == 10
        assert abs(result["baseline"] - 5.0) < 1e-9

    def test_none_baseline_becomes_none(self):
        result = _alert_window_row(_window_row(baseline=None))
        assert result["baseline"] is None

    def test_datetime_fields_isoformatted(self):
        result = _alert_window_row(_window_row())
        assert "2024-06-01" in result["window_start"]
        assert "2024-06-01" in result["created_at"]

    def test_none_datetimes_become_none(self):
        result = _alert_window_row(_window_row(window_start=None, created_at=None))
        assert result["window_start"] is None
        assert result["created_at"] is None

    def test_detail_decoded(self):
        result = _alert_window_row(_window_row(detail='{"count": 5}'))
        assert result["detail"] == {"count": 5}


# ---------------------------------------------------------------------------
# _decode_alert_suppression
# ---------------------------------------------------------------------------

class TestDecodeAlertSuppression:
    def test_basic_fields_mapped(self):
        result = _decode_alert_suppression(_suppression_row())
        assert result["id"] == "sup-1"
        assert result["scope"] == "entity"
        assert result["alert_type"] == "SILENCE_GAP"
        assert result["reason"] == "maintenance"

    def test_datetime_fields_isoformatted(self):
        result = _decode_alert_suppression(_suppression_row())
        assert "2024-06-01" in result["starts_at"]
        assert "2024-06-01" in result["ends_at"]
        assert "2024-06-01" in result["created_at"]

    def test_none_datetimes_become_none(self):
        result = _decode_alert_suppression(_suppression_row(starts_at=None, ends_at=None, created_at=None))
        assert result["starts_at"] is None
        assert result["ends_at"] is None
        assert result["created_at"] is None
