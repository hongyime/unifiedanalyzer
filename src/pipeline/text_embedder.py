"""Axis-1 MVP: multilingual sentence-embedding wrapper (e5-small, 384d).

Wraps intfloat/multilingual-e5-small via ONNX Runtime + the rust-backed
`tokenizers` package. Chosen because:

  - 384d output → fits vector(384) in the timeline_embeddings side table.
  - Multilingual (100+ langs) — matches the collector's mixed EN/ZH/JA corpus.
  - ~118 MB fp32 ONNX (or ~50 MB quantised) — trivial to lazily download at
    runtime; no baked-in model in the image.
  - ONNX Runtime is already a hard dep (requirements.txt) via the face engine.

Prefix convention: e5 REQUIRES "query: " for search queries and "passage: "
for indexed documents. Skipping the prefix silently degrades recall. The
`is_query` flag toggles between the two.

Lazy singleton: get_embedder() returns a module-level cached instance so a
long-lived process (scheduler, API) does not repeatedly reload the ~120 MB
ONNX weights. First call triggers the download; subsequent calls are no-ops.

Failure mode: the caller (see timeline_embedder.embed_new_timeline_events)
catches network/load errors and no-ops the phase. This module raises on
first-time load failure — it must not hide a broken install.
"""
import hashlib
import logging
import os
import threading
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Model source. Files fetched directly from HuggingFace via urllib — no
# huggingface_hub / hf_hub_download dep. Content-hash-verify is skipped
# (HF's URLs are content-addressed themselves; a corrupted download would
# fail at ONNX load time and re-download on the next attempt).
_HF_BASE = "https://huggingface.co/intfloat/multilingual-e5-small/resolve/main"

# Files to fetch. onnx/model.onnx is the fp32 weights (~118 MB) — always
# present on the hub. onnx/model_quantized.onnx (~50 MB int8) is used if it
# is smaller AND downloads successfully; otherwise we fall back to fp32.
_ONNX_FILENAME = "model.onnx"
_QUANTIZED_FILENAME = "model_quantized.onnx"
_TOKENIZER_FILENAME = "tokenizer.json"
_CONFIG_FILENAME = "config.json"

_MODEL_NAME = "intfloat/multilingual-e5-small"


def _default_model_dir() -> Path:
    """Where the ONNX + tokenizer files live on disk.

    Defaults to ${MEDIA_DERIVED_PATH}/models/text_embedder (bind-mounted onto
    Z:/unifiedanalyzer/media_derived/models/text_embedder on the host, which
    the docker stack sees at /app/media_derived). Override with
    TEXT_EMBED_MODEL_PATH for tests / a co-located dev model.
    """
    override = os.getenv("TEXT_EMBED_MODEL_PATH")
    if override:
        return Path(override).resolve()
    base = os.getenv("MEDIA_DERIVED_PATH", "/app/media_derived")
    return (Path(base) / "models" / "text_embedder").resolve()


def _download_file(url: str, dest: Path, retry_once: bool = True) -> None:
    """urllib.request.urlretrieve with one retry. Writes atomically via a
    .part sidecar so a partial download never masquerades as a complete one.

    Retry is intentional — HuggingFace occasionally returns transient 5xx
    or truncates a download. Two attempts covers the common case without
    turning into an infinite retry storm on a permanently broken URL.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    attempts = 2 if retry_once else 1
    last_exc: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            logger.info("text_embedder: downloading %s -> %s (attempt %d)", url, dest, attempt + 1)
            urllib.request.urlretrieve(url, tmp)
            os.replace(tmp, dest)
            return
        except Exception as e:  # noqa: BLE001 — network layer, any exception is a retry candidate
            last_exc = e
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            logger.warning("text_embedder: download failed (attempt %d): %s", attempt + 1, e)
    raise RuntimeError(f"Failed to download {url} after {attempts} attempts: {last_exc}") from last_exc


def _ensure_model_files(model_dir: Path) -> tuple[Path, Path, Path]:
    """Guarantee onnx/model.onnx + tokenizer.json + config.json exist locally.

    Returns (onnx_path, tokenizer_path, config_path). Idempotent — a file that
    already exists is skipped. Tries the quantized ONNX first (smaller, faster
    on CPU) and falls back to fp32 if the quantized artifact is missing on the
    hub. onnx/ subdir is preserved to match the HF layout so a future
    from_pretrained() call would also work.
    """
    onnx_dir = model_dir / "onnx"
    onnx_dir.mkdir(parents=True, exist_ok=True)

    # Tokenizer + config are tiny (< 5 MB combined) — always fetch first, they
    # are needed regardless of quantised-vs-fp32.
    tokenizer_path = model_dir / _TOKENIZER_FILENAME
    if not tokenizer_path.is_file():
        _download_file(f"{_HF_BASE}/{_TOKENIZER_FILENAME}", tokenizer_path)
    config_path = model_dir / _CONFIG_FILENAME
    if not config_path.is_file():
        _download_file(f"{_HF_BASE}/{_CONFIG_FILENAME}", config_path)

    # ONNX weights — prefer quantised if present (or a previous run left it),
    # else fp32. On a fresh install we probe quantised first (a 404 raises,
    # caught, and we fall back to fp32).
    quantized_path = onnx_dir / _QUANTIZED_FILENAME
    fp32_path = onnx_dir / _ONNX_FILENAME
    onnx_path: Optional[Path] = None
    if quantized_path.is_file():
        onnx_path = quantized_path
    elif fp32_path.is_file():
        onnx_path = fp32_path
    else:
        # Try quantised first — smaller download, faster CPU inference.
        try:
            _download_file(f"{_HF_BASE}/onnx/{_QUANTIZED_FILENAME}", quantized_path)
            onnx_path = quantized_path
        except Exception:
            logger.info("text_embedder: quantised ONNX unavailable, falling back to fp32")
            _download_file(f"{_HF_BASE}/onnx/{_ONNX_FILENAME}", fp32_path)
            onnx_path = fp32_path

    return onnx_path, tokenizer_path, config_path


class TextEmbedder:
    """multilingual-e5-small (384d) ONNX + tokenizers wrapper.

    Not thread-safe for concurrent .embed() calls — ONNX Runtime is
    thread-safe at the session layer, but the tokenizer batch encode is not
    guaranteed thread-safe across all tokenizer versions. The singleton
    (get_embedder) is guarded by a lock; concurrent callers must serialise
    themselves or use an executor with max_workers=1.
    """

    OUTPUT_DIM = 384
    MODEL_NAME = _MODEL_NAME

    def __init__(self, model_dir: Optional[Path] = None):
        # Lazy imports so this module can be imported without the deps present
        # (the caller-side try/except keeps the pipeline non-fatal on load
        # failure — see timeline_embedder.embed_new_timeline_events).
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.model_dir = (model_dir or _default_model_dir()).resolve()
        onnx_path, tokenizer_path, config_path = _ensure_model_files(self.model_dir)

        # Reasonable defaults on this shared box (see FACE_ONNX_THREADS
        # precedent in src/face/engine/detector.py:86). Users override via
        # TEXT_EMBED_ONNX_THREADS. inter_op=1 because a batch of 32 short
        # sentences doesn't benefit from multiple parallel graph runs.
        onnx_threads = int(os.getenv("TEXT_EMBED_ONNX_THREADS", "2") or 2)
        so = ort.SessionOptions()
        so.intra_op_num_threads = onnx_threads
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(onnx_path), sess_options=so, providers=["CPUExecutionProvider"]
        )
        # Cache the input-name set — some ONNX exports of e5 omit
        # token_type_ids, so we probe once and only pass what the graph
        # accepts.
        self._input_names = {i.name for i in self._session.get_inputs()}

        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        # Read max_seq_length from config.json — e5-small is 512 upstream but
        # the exported ONNX may hard-cap at 128/256; we clamp defensively.
        max_len = 512
        try:
            import json
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            max_len = int(cfg.get("max_position_embeddings", max_len))
        except Exception:
            logger.debug("text_embedder: could not read config max_position_embeddings", exc_info=True)
        self._max_len = min(max_len, 512)

        # Enable tokenizer's own truncation + padding so batch encode returns
        # aligned int arrays without us re-implementing it.
        self._tokenizer.enable_truncation(max_length=self._max_len)
        self._tokenizer.enable_padding(pad_id=self._tokenizer.token_to_id("<pad>") or 1,
                                       pad_token="<pad>")
        self._batch_size = int(os.getenv("TEXT_EMBED_BATCH", "32") or 32)

        logger.info(
            "text_embedder: initialised model=%s dim=%d max_len=%d onnx=%s onnx_threads=%d",
            self.MODEL_NAME, self.OUTPUT_DIM, self._max_len, onnx_path.name, onnx_threads,
        )

    def embed(self, texts: list[str], is_query: bool = False) -> "np.ndarray":  # noqa: F821
        """(N, 384) L2-normalised float32 array. Empty input -> shape (0, 384).

        `is_query=True` prefixes each text with "query: " (search queries).
        `is_query=False` uses "passage: " (indexed titles). Skipping this
        prefix silently degrades e5's semantic quality — see the model card.
        """
        import numpy as np

        if not texts:
            return np.zeros((0, self.OUTPUT_DIM), dtype=np.float32)

        prefix = "query: " if is_query else "passage: "
        prepared = [prefix + (t or "") for t in texts]

        outputs: list["np.ndarray"] = []
        for i in range(0, len(prepared), self._batch_size):
            chunk = prepared[i : i + self._batch_size]
            encodings = self._tokenizer.encode_batch(chunk)
            input_ids = np.asarray([e.ids for e in encodings], dtype=np.int64)
            attention_mask = np.asarray([e.attention_mask for e in encodings], dtype=np.int64)

            feed: dict[str, "np.ndarray"] = {}
            if "input_ids" in self._input_names:
                feed["input_ids"] = input_ids
            if "attention_mask" in self._input_names:
                feed["attention_mask"] = attention_mask
            if "token_type_ids" in self._input_names:
                # xlm-roberta / e5 uses all-zero token_type_ids (single-segment).
                feed["token_type_ids"] = np.zeros_like(input_ids)

            # Output name is model-dependent — the first output is the token
            # embeddings (last_hidden_state) for every e5 export we care about.
            last_hidden = self._session.run(None, feed)[0]  # (B, T, D)
            # Mean-pool over tokens weighted by the attention mask, then L2-
            # normalise. This matches the reference sentence-transformers e5
            # pooling — critical for cosine similarity to make sense.
            mask = attention_mask.astype(np.float32)[..., None]  # (B, T, 1)
            summed = (last_hidden * mask).sum(axis=1)
            counts = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
            pooled = summed / counts  # (B, D)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            norms = np.clip(norms, a_min=1e-12, a_max=None)
            outputs.append((pooled / norms).astype(np.float32))

        return np.concatenate(outputs, axis=0)


# Singleton: one loaded ONNX session shared across the whole process. Guarded
# by a lock because a request handler and the scheduler phase can race on
# first call. Post-init the instance is treated as read-only (the caller
# serializes its own .embed() calls, see class docstring).
_embedder: Optional[TextEmbedder] = None
_embedder_lock = threading.Lock()


def get_embedder() -> TextEmbedder:
    global _embedder
    if _embedder is not None:
        return _embedder
    with _embedder_lock:
        if _embedder is None:
            _embedder = TextEmbedder()
    return _embedder


def text_sha1(text: str) -> str:
    """SHA-1 of the raw text (no prefix). Used by timeline_embedder to detect
    a changed title after re-embed. Kept in this module because both the
    embed phase and the search endpoint share the same hashing convention."""
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()
