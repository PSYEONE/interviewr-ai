"""Retriever: the one object the engine uses for RAG.

Ties an Embedder to a DocumentStore. Ingest = extract -> chunk -> embed -> persist.
Retrieve = embed the live question -> exact cosine search. Retrieval is best-effort: it
never raises into the answer path (a down embedder or a model mismatch yields no context,
not a failed answer).
"""

from __future__ import annotations

import logging

from .base import DocKind, Embedder, Retrieved
from .chunk import chunk_text, extract_text
from .store import DocumentRecord, DocumentStore

_log = logging.getLogger(__name__)


class Retriever:
    def __init__(
        self,
        embedder: Embedder,
        store: DocumentStore,
        *,
        top_k: int,
        min_score: float,
        chunk_chars: int,
        chunk_overlap: int,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._top_k = top_k
        self._min_score = min_score
        self._chunk_chars = chunk_chars
        self._chunk_overlap = chunk_overlap

    @property
    def store(self) -> DocumentStore:
        return self._store

    def set_top_k(self, top_k: int) -> None:
        """Live-update the retrieval depth (applied by POST /settings/reload, no restart)."""
        self._top_k = top_k

    async def check_health(self) -> bool:
        return await self._embedder.check_health()

    async def ingest_file(self, name: str, kind: DocKind, data: bytes) -> DocumentRecord:
        return await self._ingest(name, kind, extract_text(name, data))

    async def ingest_text(self, name: str, kind: DocKind, text: str) -> DocumentRecord:
        return await self._ingest(name, kind, text)

    async def _ingest(self, name: str, kind: DocKind, text: str) -> DocumentRecord:
        chunks = chunk_text(text, self._chunk_chars, self._chunk_overlap)
        if not chunks:
            raise ValueError("document has no extractable text")
        vectors = await self._embedder.embed([c.text for c in chunks])
        return self._store.add_document(
            name, kind, [c.text for c in chunks], vectors, self._embedder.model_id
        )

    async def retrieve(self, query: str) -> list[Retrieved]:
        query = query.strip()
        if not query:
            return []
        stored = self._store.model_id()
        if stored is None:
            return []  # nothing ingested yet
        if stored != self._embedder.model_id:
            _log.warning(
                "RAG disabled for this query: store embedded with '%s' but active embedder "
                "is '%s'. Re-ingest documents or clear the store.",
                stored,
                self._embedder.model_id,
            )
            return []
        try:
            vectors = await self._embedder.embed([query])
        except Exception as exc:  # noqa: BLE001 - retrieval must never break the answer
            _log.warning("embedding the query failed, answering without context: %s", exc)
            return []
        if not vectors:
            return []
        return self._store.search(vectors[0], self._top_k, self._min_score)

    def list_documents(self) -> list[DocumentRecord]:
        return self._store.list_documents()

    def delete_document(self, doc_id: int) -> bool:
        return self._store.delete_document(doc_id)

    def clear(self) -> None:
        self._store.clear()

    async def close(self) -> None:
        await self._embedder.close()
        self._store.close()


__all__ = ["Retriever"]
