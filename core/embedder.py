"""The ``Embedder`` seam — the only way the core turns text into vectors (v0.16).

Mirrors the :class:`~core.llm.LLMClient` seam: the core depends on the
:class:`Embedder` Protocol, **never** on an embedding SDK. The default backend is a
**local multilingual** model (Ukrainian-capable; private — text never leaves the
machine, no per-call cost); a **cloud** API (Voyage / OpenAI) is opt-in via config
(it sends text out, so it's off unless configured). :class:`MockEmbedder` returns
deterministic fake vectors so cosine ranking is testable with **no paid APIs** in CI.

Semantic recall (SEMANTIC_RECALL.md) embeds every message into a per-user vector
store (the :class:`~core.repository.VectorStore` seam) and retrieves by meaning.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable

# A Ukrainian-capable multilingual **retrieval** model fastembed ships (dim 1024, ~2.24 GB).
# e5 is asymmetric — it needs "query: " / "passage: " prefixes (handled below); without them a
# keyword query matches by shape, not meaning. Overridable via LUMI_EMBED_MODEL (a fastembed model).
DEFAULT_LOCAL_MODEL = "intfloat/multilingual-e5-large"
# The mock's vector size — small but enough for stable cosine ranking in tests.
DEFAULT_MOCK_DIM = 64


class EmbedderError(RuntimeError):
    """Building or calling an embedder failed (e.g. a missing optional dependency)."""


@runtime_checkable
class Embedder(Protocol):
    """The seam the core depends on. Backends turn text into vectors."""

    @property
    def dim(self) -> int:
        """The vector dimensionality this embedder produces."""
        ...

    def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        """Return one vector per input text (batch; same order).

        ``is_query=True`` embeds a **search query**, ``False`` a stored **passage** — asymmetric
        retrieval models (e5) prefix the two differently, which is essential for good ranking.
        """
        ...


def _unit(vec: list[float]) -> list[float]:
    """Scale ``vec`` to unit length (a zero vector is returned unchanged)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class LocalEmbedder:
    """A local multilingual model via ``fastembed`` (lazy-imported, an optional dep).

    Text **never leaves the machine**. The model is downloaded/loaded on first use, so
    importing this module — and using :class:`MockEmbedder` — needs nothing installed.
    """

    def __init__(self, model: str = DEFAULT_LOCAL_MODEL) -> None:
        self._model_name = model
        # e5 models are asymmetric: they REQUIRE "query: " / "passage: " prefixes to rank well.
        self._e5 = "e5" in model.lower()
        self._model: object | None = None
        self._dim: int | None = None

    def _ensure(self) -> object:
        if self._model is None:
            try:
                from fastembed import TextEmbedding  # optional 'embed' extra, imported only here
            except ImportError as exc:  # pragma: no cover — only hit without the extra installed
                raise EmbedderError(
                    "The local embedder needs the 'embed' extra: pip install 'lumi[embed]' "
                    "(or set LUMI_EMBED_PROVIDER=voyage|openai)."
                ) from exc
            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        model = self._ensure()
        items = list(texts)
        if self._e5:
            prefix = "query: " if is_query else "passage: "
            items = [prefix + t for t in items]
        vectors = [_unit([float(x) for x in v]) for v in model.embed(items)]  # type: ignore[attr-defined]
        if vectors:
            self._dim = len(vectors[0])
        return vectors

    @property
    def dim(self) -> int:
        if self._dim is None:
            self.embed(["."])  # one-time probe to learn the dimensionality
        assert self._dim is not None
        return self._dim


class CloudEmbedder:
    """A cloud embedding API (Voyage or OpenAI), lazy-imported.

    **Off unless configured** — it sends private text to a third party. The API key is a
    **secret** (never logged/committed); construction fails without one.
    """

    def __init__(self, provider: str, model: str, api_key: str) -> None:
        if not api_key:
            raise EmbedderError(f"The {provider} embedder needs an API key (a secret in .env).")
        self._provider = provider
        self._model = model
        self._api_key = api_key
        self._client: object | None = None
        self._dim: int | None = None

    def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        texts = list(texts)
        if self._provider == "voyage":
            vectors = self._embed_voyage(texts, is_query)
        elif self._provider == "openai":
            vectors = self._embed_openai(texts)
        else:  # pragma: no cover — build_embedder gates the provider name
            raise EmbedderError(f"Unknown cloud embed provider: {self._provider!r}")
        vectors = [_unit(v) for v in vectors]
        if vectors:
            self._dim = len(vectors[0])
        return vectors

    def _embed_voyage(self, texts: list[str], is_query: bool = False) -> list[list[float]]:
        try:
            import voyageai  # optional; imported only when the Voyage provider is used
        except ImportError as exc:  # pragma: no cover
            raise EmbedderError("The Voyage embedder needs 'voyageai' installed.") from exc
        if self._client is None:
            self._client = voyageai.Client(api_key=self._api_key)
        # Voyage is asymmetric too — tell it whether this is a query or a document.
        input_type = "query" if is_query else "document"
        result = self._client.embed(texts, model=self._model, input_type=input_type)  # type: ignore[attr-defined]
        return [[float(x) for x in v] for v in result.embeddings]

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        try:
            import openai  # optional; imported only when the OpenAI provider is used
        except ImportError as exc:  # pragma: no cover
            raise EmbedderError("The OpenAI embedder needs 'openai' installed.") from exc
        if self._client is None:
            self._client = openai.OpenAI(api_key=self._api_key)
        result = self._client.embeddings.create(model=self._model, input=texts)  # type: ignore[attr-defined]
        return [[float(x) for x in d.embedding] for d in result.data]

    @property
    def dim(self) -> int:
        if self._dim is None:
            self.embed(["."])
        assert self._dim is not None
        return self._dim


class MockEmbedder:
    """A deterministic, network-free :class:`Embedder` for tests.

    Each text maps to a fixed **bag-of-words hashing** vector: tokens bucket into the
    ``dim`` dimensions by a stable (non-salted) hash, so identical text → identical vector
    and texts that **share words rank closer** under cosine — realistic top-K tests with
    **no network, no paid API**. Every call's inputs are recorded in :attr:`calls`.
    """

    def __init__(self, dim: int = DEFAULT_MOCK_DIM) -> None:
        self._dim = dim
        self.calls: list[list[str]] = []

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in text.lower().split():
            # blake2b is stable across runs (unlike the salted built-in hash()).
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            vec[int.from_bytes(digest, "big") % self._dim] += 1.0
        return _unit(vec)

    def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        # is_query is ignored — the mock is deterministic (same text → same vector) so query
        # and passage of identical text match exactly, which keeps the ranking tests stable.
        texts = list(texts)
        self.calls.append(list(texts))
        return [self._vector(t) for t in texts]


def build_embedder(
    provider: str = "local", model: str | None = None, *, api_key: str = ""
) -> Embedder:
    """Construct an :class:`Embedder` for ``provider``.

    ``local`` (default) → :class:`LocalEmbedder` (needs the ``embed`` extra);
    ``voyage`` / ``openai`` → :class:`CloudEmbedder` (needs ``api_key``);
    ``mock`` → :class:`MockEmbedder`. Raises :class:`EmbedderError` on an unknown provider.
    Constructing ``local`` does **not** load the model (lazy on first ``embed``).
    """
    provider = (provider or "local").strip().lower()
    if provider == "local":
        return LocalEmbedder(model or DEFAULT_LOCAL_MODEL)
    if provider in ("voyage", "openai"):
        if not model:
            model = "voyage-3" if provider == "voyage" else "text-embedding-3-small"
        return CloudEmbedder(provider, model, api_key)
    if provider == "mock":
        return MockEmbedder()
    raise EmbedderError(f"Unknown embed provider: {provider!r}")
