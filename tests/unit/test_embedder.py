"""Unit tests for the Embedder seam (LUMI-061).

The seam mirrors the LLMClient seam: the core depends on the ``Embedder`` Protocol,
never an SDK. ``import core.embedder`` must work without the local model installed
(lazy), and ``MockEmbedder`` must be deterministic so cosine ranking is testable —
all with no network and no paid APIs.
"""

import math

import pytest

from core.embedder import (
    DEFAULT_LOCAL_MODEL,
    Embedder,
    EmbedderError,
    LocalEmbedder,
    MockEmbedder,
    _unit,
    build_embedder,
)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def test_import_works_without_the_model():
    # The module + MockEmbedder import with no embedding model installed (lazy backends).
    assert MockEmbedder().dim > 0


def test_mock_is_an_embedder():
    assert isinstance(MockEmbedder(), Embedder)


def test_mock_is_deterministic_same_text_same_vector():
    e = MockEmbedder()
    [v1] = e.embed(["Привіт, Лілі"])
    [v2] = e.embed(["Привіт, Лілі"])
    assert v1 == v2  # identical text → identical vector


def test_mock_records_calls_and_keeps_dim():
    e = MockEmbedder(dim=32)
    out = e.embed(["one", "two", "three"])
    assert len(out) == 3
    assert all(len(v) == 32 == e.dim for v in out)
    assert e.calls == [["one", "two", "three"]]


def test_mock_vectors_are_unit_length():
    [v] = MockEmbedder().embed(["кава молоко цукор"])
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)


def test_mock_shared_words_rank_closer():
    # Bag-of-words hashing: texts that share words are more similar under cosine —
    # so top-K ranking is meaningful in the index/recall tests.
    e = MockEmbedder()
    query, near, far = e.embed(["я люблю каву", "люблю каву вранці", "погода сьогодні похмура"])
    assert _cosine(query, near) > _cosine(query, far)


def test_empty_text_is_a_zero_vector_not_a_crash():
    [v] = MockEmbedder().embed([""])
    assert v == [0.0] * MockEmbedder().dim  # no division-by-zero on an empty string


def test_unit_normalizes_and_leaves_zero_alone():
    assert _unit([3.0, 4.0]) == [0.6, 0.8]
    assert _unit([0.0, 0.0]) == [0.0, 0.0]  # zero vector is returned unchanged


def test_build_embedder_mock_and_local():
    assert isinstance(build_embedder("mock"), MockEmbedder)
    # 'local' is the default and must NOT load the model on construction (lazy).
    local = build_embedder("local")
    assert isinstance(local, LocalEmbedder)
    assert local._model is None  # not loaded yet


def test_build_embedder_unknown_provider_raises():
    with pytest.raises(EmbedderError):
        build_embedder("nonsense")


def test_cloud_embedder_requires_a_key():
    # A cloud provider sends text out → it must refuse to build without a secret key.
    with pytest.raises(EmbedderError):
        build_embedder("voyage", api_key="")
    with pytest.raises(EmbedderError):
        build_embedder("openai", api_key="")


def test_default_local_model_is_multilingual():
    assert "multilingual" in DEFAULT_LOCAL_MODEL
