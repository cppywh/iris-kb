from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterable

import numpy as np


class EmbeddingUnavailable(RuntimeError):
    pass


DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
FALLBACK_MODEL = "moka-ai/m3e-base"
HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"


def _local_snapshot(repo_id: str) -> Path | None:
    base = HF_CACHE / ("models--" + repo_id.replace("/", "--"))
    snapshots = base / "snapshots"
    if not snapshots.exists():
        return None
    for candidate in sorted(snapshots.iterdir(), reverse=True):
        if (candidate / "config.json").exists():
            return candidate
    return None


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack_vector(vector: Iterable[float]) -> bytes:
    return np.asarray(list(vector), dtype=np.float32).tobytes()


def unpack_vector(blob: bytes, dim: int | None = None) -> np.ndarray:
    vector = np.frombuffer(blob, dtype=np.float32)
    if dim is not None and vector.shape[0] != dim:
        raise ValueError(f"embedding dim mismatch: expected {dim}, got {vector.shape[0]}")
    return vector


class Embedder:
    """Lazy local sentence-transformers wrapper.

    The model is downloaded from HF_ENDPOINT when set. HuggingFace is blocked in
    some networks; callers should set HF_ENDPOINT=https://hf-mirror.com.
    """

    def __init__(self, model_name: str | None = None, cache_dir: Path | None = None):
        requested = model_name or os.environ.get("VULN_MASTERHUB_EMBED_MODEL")
        self.cache_dir = cache_dir
        self._model = None

        if requested:
            self.model_id = requested
            self.model_path = requested
        else:
            bge_local = _local_snapshot(DEFAULT_MODEL)
            m3e_local = _local_snapshot(FALLBACK_MODEL)
            if bge_local is not None:
                self.model_id = DEFAULT_MODEL
                self.model_path = str(bge_local)
            elif m3e_local is not None:
                self.model_id = FALLBACK_MODEL
                self.model_path = str(m3e_local)
            else:
                self.model_id = DEFAULT_MODEL
                self.model_path = DEFAULT_MODEL

    @property
    def model_name(self) -> str:
        return self.model_id

    def _ensure_endpoint(self) -> None:
        if not os.environ.get("HF_ENDPOINT") and not os.environ.get("HF_HUB_OFFLINE"):
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

    def load(self):
        if self._model is not None:
            return self._model
        self._ensure_endpoint()
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:  # pragma: no cover - depends on environment
            raise EmbeddingUnavailable(f"sentence-transformers unavailable: {exc}") from exc
        kwargs = {}
        if os.environ.get('VULN_MASTERHUB_EMBED_DEVICE'):
            kwargs['device']=os.environ['VULN_MASTERHUB_EMBED_DEVICE']
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            kwargs["cache_folder"] = str(self.cache_dir)
        try:
            self._model = SentenceTransformer(self.model_path, **kwargs)
        except Exception as exc:  # pragma: no cover - download/network dependent
            raise EmbeddingUnavailable(
                f"failed to load embedding model {self.model_id}: {exc}. "
                "Set HF_ENDPOINT=https://hf-mirror.com, pre-download the model, "
                "or use VULN_MASTERHUB_EMBED_MODEL to point at a local model."
            ) from exc
        return self._model

    def encode(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        model = self.load()
        vectors = model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    @property
    def dim(self) -> int:
        model = self.load()
        return int(model.get_sentence_embedding_dimension())
