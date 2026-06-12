from __future__ import annotations

from collections.abc import Sequence
import warnings


class BGEEmbedder:
    """Lazy local BGE-M3 embedding wrapper."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        *,
        batch_size: int = 16,
        device: str | None = None,
        use_fp16: bool | None = None,
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.device = device
        self.use_fp16 = use_fp16
        self._model = None

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_texts([text])
        return vectors[0] if vectors else []

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load_model()
        all_vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            encoded = model.encode(
                batch,
                batch_size=self.batch_size,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False,
            )
            dense = encoded["dense_vecs"] if isinstance(encoded, dict) else encoded
            all_vectors.extend(_to_list_vectors(dense))
        return all_vectors

    def _load_model(self):
        if self._model is not None:
            return self._model

        try:
            from FlagEmbedding import BGEM3FlagModel
        except Exception as exc:
            raise RuntimeError(
                "FlagEmbedding is required for local BGE-M3 embeddings. "
                "Install dependencies with `uv sync`."
            ) from exc

        use_fp16 = self.use_fp16
        if use_fp16 is None:
            use_fp16 = _cuda_available()

        kwargs = {"use_fp16": bool(use_fp16)}
        if self.device:
            kwargs["devices"] = self.device

        try:
            self._model = BGEM3FlagModel(self.model_name, **kwargs)
        except TypeError:
            if self.device:
                warnings.warn("This FlagEmbedding version does not accept `devices`; retrying without it.")
                kwargs.pop("devices", None)
            self._model = BGEM3FlagModel(self.model_name, **kwargs)
        return self._model


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _to_list_vectors(vectors) -> list[list[float]]:
    if hasattr(vectors, "tolist"):
        vectors = vectors.tolist()
    return [[float(value) for value in vector] for vector in vectors]

