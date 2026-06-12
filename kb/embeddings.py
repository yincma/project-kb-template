from __future__ import annotations

from collections.abc import Sequence
import warnings


class BGEEmbedder:
    """Lazy local embedding wrapper.

    BGE-M3 uses FlagEmbedding for dense vectors. Lightweight profiles can use
    SentenceTransformers models such as all-MiniLM-L6-v2.
    """

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
        self._backend: str | None = None

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
            if self._backend == "sentence-transformers":
                encoded = model.encode(batch, batch_size=self.batch_size, normalize_embeddings=True)
            else:
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

        if _is_sentence_transformers_model(self.model_name):
            try:
                from sentence_transformers import SentenceTransformer
            except Exception as exc:
                raise RuntimeError(
                    "sentence-transformers is required for lightweight embeddings. "
                    "Install dependencies with `uv sync`."
                ) from exc
            kwargs = {}
            if self.device:
                kwargs["device"] = self.device
            self._model = SentenceTransformer(self.model_name, **kwargs)
            self._backend = "sentence-transformers"
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
        self._backend = "bge-m3"
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


def _is_sentence_transformers_model(model_name: str) -> bool:
    normalized = model_name.lower()
    return "all-minilm" in normalized or normalized.startswith("sentence-transformers/")
