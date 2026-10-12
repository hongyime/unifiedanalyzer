from __future__ import annotations

import io
import json
import subprocess
import threading
from types import SimpleNamespace

import pytest


def test_detect_strict_raises_while_detect_keeps_swallow_contract():
    np = pytest.importorskip("numpy")
    pytest.importorskip("insightface")
    from src.face.engine.detector import FaceDetector, FaceInferenceError

    def fail(_image):
        raise RuntimeError("mock model failure")

    detector = FaceDetector.__new__(FaceDetector)
    detector.app = SimpleNamespace(get=fail)
    detector._detect_lock = threading.Lock()
    image = np.zeros((4, 4, 3), dtype=np.uint8)

    with pytest.raises(FaceInferenceError, match="mock model failure"):
        detector.detect_strict(image)
    assert detector.detect(image) == []
    detector.app.get = lambda _image: []
    assert detector.detect_strict(image) == []


def test_face_worker_detector_cache_is_lazy_and_keyed_by_model_config(monkeypatch):
    pytest.importorskip("insightface")
    import src.face.engine.detector as detector_module
    import src.face_worker as face_worker

    monkeypatch.setattr(face_worker, "_IMAGE_FACE_DETECTOR", None)
    monkeypatch.setattr(face_worker, "_IMAGE_FACE_DETECTOR_KEY", None)
    monkeypatch.setattr(face_worker, "_VIDEO_FACE_DETECTOR", None)
    monkeypatch.setattr(face_worker, "_VIDEO_FACE_DETECTOR_KEY", None)
    constructions = []

    class FakeDetector:
        def __init__(self, *, model_name, providers, root):
            constructions.append((model_name, tuple(providers), root))

    monkeypatch.setattr(detector_module, "FaceDetector", FakeDetector)
    assert constructions == []

    image_detector = face_worker._get_cached_face_detector()
    assert face_worker._get_cached_face_detector() is image_detector
    video_detector = face_worker._get_cached_face_detector(video=True)
    assert face_worker._get_cached_face_detector(video=True) is video_detector
    assert image_detector is not video_detector
    assert len(constructions) == 2

    monkeypatch.setenv("FACE_MODEL_ROOT", "/models/alternate")
    root_keyed_detector = face_worker._get_cached_face_detector()
    assert root_keyed_detector is not image_detector
    assert len(constructions) == 3

    monkeypatch.setenv("FACE_MIN_CONFIDENCE", "0.61")
    threshold_keyed_detector = face_worker._get_cached_face_detector()
    assert threshold_keyed_detector is not root_keyed_detector
    assert len(constructions) == 4

    model_keyed_detector = face_worker._get_cached_face_detector(model_name="buffalo_m")
    assert model_keyed_detector is not threshold_keyed_detector
    provider_keyed_detector = face_worker._get_cached_face_detector(
        model_name="buffalo_m", providers=("CUDAExecutionProvider",),
    )
    assert provider_keyed_detector is not model_keyed_detector
    assert len(constructions) == 6


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (subprocess.TimeoutExpired("ffmpeg", 180), True),
        (OSError("temporary file read failure"), True),
        (subprocess.CalledProcessError(1, ["ffmpeg"]), False),
        (RuntimeError("unexpected decode failure"), False),
    ],
)
def test_video_extraction_error_classification(error, expected):
    from src.face_worker import _is_transient_video_extraction_error

    assert _is_transient_video_extraction_error(error) is expected


def test_vectorized_topical_pairs_match_legacy_nested_loop_exactly():
    np = pytest.importorskip("numpy")
    from src.pipeline.topical_similarity import _collect_topical_similarity_pairs

    rng = np.random.default_rng(20261012)
    centroids = rng.normal(size=(12, 9)).astype(np.float32)
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)
    centroids[1] = centroids[0]
    sims = centroids @ centroids.T
    np.fill_diagonal(sims, -1.0)
    # Exercise a float32 value just below a Python-float threshold. Direct
    # float32 comparison can round 0.95 down and include this legacy-excluded
    # score on NumPy versions using weak scalar promotion.
    threshold = 0.95
    sims[0, 2] = np.nextafter(np.float32(threshold), np.float32(0.0))
    entity_ids = [f"entity-{i:02d}" for i in range(len(centroids))]
    n_events = {entity_id: i + 1 for i, entity_id in enumerate(entity_ids)}
    dismissed = {(entity_ids[0], entity_ids[1]), (entity_ids[4], entity_ids[8])}

    old_stats = {
        "pairs_scored": 0,
        "below_threshold_skipped": 0,
        "dismissed_skipped": 0,
    }
    old_signals = []
    for i in range(len(entity_ids)):
        a = entity_ids[i]
        for j in range(i + 1, len(entity_ids)):
            b = entity_ids[j]
            old_stats["pairs_scored"] += 1
            score = float(sims[i, j])
            if score < threshold:
                old_stats["below_threshold_skipped"] += 1
                continue
            key = (a, b) if a < b else (b, a)
            if key in dismissed:
                old_stats["dismissed_skipped"] += 1
                continue
            value_json = json.dumps({
                "n_events_a": n_events[a],
                "n_events_b": n_events[b],
                "model": "intfloat/multilingual-e5-small",
            })
            old_signals.append((
                key[0], "topical_similarity", "analyzer", "timeline_embeddings",
                None, None, None, key[1], value_json, round(score, 4),
            ))

    new_signals, new_stats = _collect_topical_similarity_pairs(
        entity_ids, n_events, sims, dismissed, threshold,
    )
    assert new_signals == old_signals
    assert new_stats == old_stats


def test_vectorized_topical_pairs_preserve_legacy_nan_comparison():
    np = pytest.importorskip("numpy")
    from src.pipeline.topical_similarity import _collect_topical_similarity_pairs

    sims = np.array([
        [-1.0, np.nan],
        [np.nan, -1.0],
    ], dtype=np.float32)
    signals, stats = _collect_topical_similarity_pairs(
        ["entity-a", "entity-b"], {"entity-a": 1, "entity-b": 2},
        sims, set(), 0.95,
    )

    assert len(signals) == 1
    assert signals[0][0] == "entity-a"
    assert signals[0][7] == "entity-b"
    assert np.isnan(signals[0][9])
    assert stats == {
        "pairs_scored": 1,
        "below_threshold_skipped": 0,
        "dismissed_skipped": 0,
    }


def test_telegram_plain_text_retry_omits_parse_mode(monkeypatch):
    from src.notifications import telegram

    payloads = []

    class Response:
        status = 200

        @staticmethod
        def read():
            return b'{"result":{"message_id":123}}'

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        if len(payloads) == 1:
            raise telegram.urllib.error.HTTPError(
                request.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=io.BytesIO(b"{\"description\":\"Bad Request: can't parse entities\"}"),
            )
        return Response()

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)
    assert telegram._send_sync("broken <b> message")[0] is True
    assert payloads[0]["parse_mode"] == "HTML"
    assert "parse_mode" not in payloads[1]
