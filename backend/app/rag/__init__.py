"""RAG package: resume + job-description retrieval, injected into the answer turn.

``build_retriever`` is the single entry point; it returns None when RAG is disabled so the
engine can skip it without special-casing each call site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Retrieved, format_context
from .embed import build_embedder
from .retriever import Retriever
from .store import DocumentStore

if TYPE_CHECKING:
    from ..config import Settings


def build_retriever(settings: Settings) -> Retriever | None:
    if not settings.rag_enabled:
        return None
    embedder = build_embedder(settings)
    store = DocumentStore(settings.resolved_rag_db_path())
    return Retriever(
        embedder,
        store,
        top_k=settings.rag_top_k,
        min_score=settings.rag_min_score,
        chunk_chars=settings.rag_chunk_chars,
        chunk_overlap=settings.rag_chunk_overlap,
    )


__all__ = ["Retrieved", "Retriever", "build_retriever", "format_context"]
