"""DocumentStore persistence, exact cosine search, and the embed-model guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.store import DocumentStore

_MODEL = "ollama:nomic-embed-text"
# Three orthonormal vectors so similarity ranking is unambiguous.
_CHUNKS = ["x-axis", "y-axis", "z-axis"]
_VECS = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def _store(tmp_path: Path) -> DocumentStore:
    store = DocumentStore(tmp_path / "rag.sqlite3")
    store.add_document("resume.txt", "resume", _CHUNKS, _VECS, _MODEL)
    return store


def test_search_ranks_by_cosine(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hits = store.search([0.9, 0.1, 0.0], top_k=2)
    assert [h.text for h in hits] == ["x-axis", "y-axis"]
    assert hits[0].score > hits[1].score
    assert hits[0].doc_name == "resume.txt"
    assert hits[0].kind == "resume"


def test_min_score_filters(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Query aligned with x only; y/z have cosine 0 and are dropped above the floor.
    hits = store.search([1.0, 0.0, 0.0], top_k=3, min_score=0.5)
    assert [h.text for h in hits] == ["x-axis"]


def test_model_mismatch_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="Clear documents"):
        store.add_document("jd.txt", "job_description", ["a"], [[1.0, 0.0, 0.0]], "openai:other")


def test_clear_allows_model_switch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.clear()
    assert store.model_id() is None
    assert store.list_documents() == []
    store.add_document("jd.txt", "job_description", ["a"], [[1.0, 0.0]], "openai:other")
    assert store.model_id() == "openai:other"


def test_delete_invalidates_search_cache(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.search([1.0, 0.0, 0.0], top_k=1)  # warm the cached matrix
    doc_id = store.list_documents()[0]["id"]
    assert store.delete_document(doc_id) is True
    assert store.search([1.0, 0.0, 0.0], top_k=3) == []
    assert store.delete_document(doc_id) is False


def test_chunk_texts_resume_first(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "rag.sqlite3")
    # Add the job description first to prove ordering is by kind, not insertion order.
    store.add_document("jd.txt", "job_description", ["jd-a", "jd-b"], _VECS[:2], _MODEL)
    store.add_document("resume.txt", "resume", ["cv-a", "cv-b"], _VECS[:2], _MODEL)
    assert store.chunk_texts() == ["cv-a", "cv-b", "jd-a", "jd-b"]


def test_persistence_across_reopen(tmp_path: Path) -> None:
    _store(tmp_path).close()
    reopened = DocumentStore(tmp_path / "rag.sqlite3")
    assert reopened.model_id() == _MODEL
    assert [h.text for h in reopened.search([1.0, 0.0, 0.0], top_k=1)] == ["x-axis"]
