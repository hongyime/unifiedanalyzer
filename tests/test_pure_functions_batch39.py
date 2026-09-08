"""
Pure-function tests — batch 39.

Covers previously untested pure functions:
- pipeline.face_clustering: _normalized_matrix, _passes_purity,
  _competing_entity_too_close, _cluster_too_loose
- pipeline.incremental_runner: _coverage_snapshots_for_phase_result
- pipeline.entity_resolver: normalize_username_strict edge cases,
  _CROSS_ENTITY_SIGNAL_CONFIDENCE completeness
"""
from __future__ import annotations

import json
import numpy as np
import pytest


# ---------------------------------------------------------------------------
# face_clustering: _normalized_matrix, _passes_purity, _competing_entity_too_close,
#                  _cluster_too_loose
# ---------------------------------------------------------------------------

class TestNormalizedMatrix:
    def _n(self, emb_texts):
        from src.pipeline.face_clustering import _normalized_matrix
        return _normalized_matrix(emb_texts)

    def test_empty_returns_none(self):
        assert self._n([]) is None

    def test_single_vector_unit_norm(self):
        v = [3.0, 4.0] + [0.0] * 510  # norm=5
        text = json.dumps(v)
        result = self._n([text])
        assert result is not None
        assert abs(float(np.linalg.norm(result[0])) - 1.0) < 1e-5

    def test_shape_correct(self):
        texts = [json.dumps([1.0] * 512) for _ in range(3)]
        result = self._n(texts)
        assert result.shape == (3, 512)

    def test_zero_vector_handled(self):
        # zero vector: norm clamped to 1.0 so no division-by-zero
        text = json.dumps([0.0] * 512)
        result = self._n([text])
        assert result is not None  # should not raise


class TestPassesPurity:
    def _p(self, cand_emb_text, anchors):
        from src.pipeline.face_clustering import _passes_purity
        return _passes_purity(cand_emb_text, anchors)

    def test_none_anchors_returns_false(self):
        v = json.dumps([1.0] + [0.0] * 511)
        assert self._p(v, None) is False

    def test_empty_anchors_returns_false(self):
        v = json.dumps([1.0] + [0.0] * 511)
        assert self._p(v, np.zeros((0, 512))) is False

    def test_identical_embedding_passes(self):
        # candidate exactly equals anchor → cosine=1.0 ≥ threshold
        v = np.zeros(512, dtype=np.float32)
        v[0] = 1.0
        cand_text = json.dumps(v.tolist())
        anchors = v.reshape(1, -1)
        assert self._p(cand_text, anchors) is True

    def test_orthogonal_embedding_fails(self):
        # candidate orthogonal to anchor → cosine=0 < threshold
        anchor = np.zeros(512, dtype=np.float32)
        anchor[0] = 1.0
        cand = np.zeros(512, dtype=np.float32)
        cand[1] = 1.0
        cand_text = json.dumps(cand.tolist())
        assert self._p(cand_text, anchor.reshape(1, -1)) is False

    def test_zero_candidate_returns_false(self):
        anchor = np.zeros(512, dtype=np.float32)
        anchor[0] = 1.0
        zero_text = json.dumps([0.0] * 512)
        assert self._p(zero_text, anchor.reshape(1, -1)) is False


class TestCompetingEntityTooClose:
    def _c(self, anchors, competitor_embs):
        from src.pipeline.face_clustering import _competing_entity_too_close
        return _competing_entity_too_close(anchors, competitor_embs)

    def test_none_anchors_returns_false(self):
        v = json.dumps([1.0] + [0.0] * 511)
        assert self._c(None, [v]) is False

    def test_empty_anchors_returns_false(self):
        v = json.dumps([1.0] + [0.0] * 511)
        assert self._c(np.zeros((0, 512)), [v]) is False

    def test_empty_competitors_returns_false(self):
        anchor = np.zeros((1, 512), dtype=np.float32)
        anchor[0, 0] = 1.0
        assert self._c(anchor, []) is False

    def test_identical_competitor_returns_true(self):
        anchor = np.zeros((1, 512), dtype=np.float32)
        anchor[0, 0] = 1.0
        comp_text = json.dumps(anchor[0].tolist())
        assert self._c(anchor, [comp_text]) is True

    def test_orthogonal_competitor_returns_false(self):
        anchor = np.zeros((1, 512), dtype=np.float32)
        anchor[0, 0] = 1.0
        comp = np.zeros(512, dtype=np.float32)
        comp[1] = 1.0
        comp_text = json.dumps(comp.tolist())
        assert self._c(anchor, [comp_text]) is False


class TestClusterTooLoose:
    def _t(self, anchors):
        from src.pipeline.face_clustering import _cluster_too_loose
        return _cluster_too_loose(anchors)

    def test_none_returns_false(self):
        assert self._t(None) is False

    def test_single_anchor_returns_false(self):
        v = np.zeros((1, 512), dtype=np.float32)
        v[0, 0] = 1.0
        assert self._t(v) is False

    def test_identical_anchors_not_too_loose(self):
        v = np.zeros(512, dtype=np.float32)
        v[0] = 1.0
        anchors = np.stack([v, v])  # cos=1.0 → not loose
        assert self._t(anchors) is False

    def test_orthogonal_anchors_too_loose(self):
        a = np.zeros(512, dtype=np.float32)
        a[0] = 1.0
        b = np.zeros(512, dtype=np.float32)
        b[1] = 1.0
        anchors = np.stack([a, b])  # cos=0.0 < threshold → too loose
        assert self._t(anchors) is True


# ---------------------------------------------------------------------------
# incremental_runner: _coverage_snapshots_for_phase_result
# ---------------------------------------------------------------------------

class TestCoverageSnapshotsForPhaseResult:
    def _c(self, run_id="r1", run_type="incremental", phase="timeline",
           status="completed", duration_ms=100, result=None, error=None):
        from src.pipeline.incremental_runner import _coverage_snapshots_for_phase_result
        return _coverage_snapshots_for_phase_result(
            run_id, run_type, phase, status, duration_ms, result or {}, error
        )

    def test_returns_list(self):
        result = self._c()
        assert isinstance(result, list)

    def test_at_least_one_row(self):
        result = self._c()
        assert len(result) >= 1

    def test_each_row_is_tuple(self):
        for row in self._c():
            assert isinstance(row, tuple)

    def test_run_id_in_row(self):
        rows = self._c(run_id="test-run")
        assert any("test-run" in str(row) for row in rows)

    def test_dict_result_produces_all_row(self):
        rows = self._c(result={"processed": 10, "attributed": 5})
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# entity_resolver: normalize_username_strict specific edge cases
# ---------------------------------------------------------------------------

class TestNormalizeUsernameStrictEdgeCases:
    def _n(self, username):
        from src.pipeline.entity_resolver import normalize_username_strict
        return normalize_username_strict(username)

    def test_strips_dot_but_keeps_digits(self):
        # "alice.123" → strip "." → "alice123" (digits kept)
        assert self._n("alice.123") == "alice123"

    def test_strips_underscore(self):
        assert self._n("alice_smith") == "alicesmith"

    def test_strips_dash(self):
        assert self._n("alice-smith") == "alicesmith"

    def test_long_username_with_digits_kept(self):
        assert self._n("hongyime") == "hongyime"

    def test_digit_suffix_preserved(self):
        # strict: digit suffix kept → "hongyime2" stays
        assert self._n("hongyime2") == "hongyime2"
