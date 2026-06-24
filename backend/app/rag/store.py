"""Persistent chunk store with exact (brute-force) cosine search.

A resume + a job description is tens-to-low-hundreds of chunks, so an ANN index is
premature; a single normalized matrix-vector product gives exact top-k in well under a
millisecond. We persist to plain sqlite (vectors as float32 BLOBs) and do the similarity in
numpy. Deliberately not sqlite-vec: this project's Python sqlite3 lacks enable_load_extension,
and brute-force is both simpler and exact at this scale.

The whole store shares one embedding model: ``meta.model_id`` records it, and writes from a
different model are rejected so query and stored vectors always live in the same space.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

import numpy as np
from numpy.typing import NDArray

from .base import DocKind, Retrieved

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    added_at    TEXT NOT NULL,
    chunk_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    text    TEXT NOT NULL,
    vector  BLOB NOT NULL
);
"""


class DocumentRecord(TypedDict):
    id: int
    name: str
    kind: DocKind
    added_at: str
    chunk_count: int


class DocumentStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Cached normalized matrix + parallel row metadata; invalidated on every write.
        self._matrix: NDArray[np.float32] | None = None
        self._rows: list[tuple[str, str, DocKind]] = []

    def model_id(self) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'model_id'").fetchone()
        return None if row is None else cast(str, row["value"])

    def add_document(
        self,
        name: str,
        kind: DocKind,
        chunks: Sequence[str],
        vectors: Sequence[Sequence[float]],
        model_id: str,
    ) -> DocumentRecord:
        """Persist a document's chunks + vectors. Rejects a different embedding model."""
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors length mismatch")
        if not chunks:
            raise ValueError("document produced no chunks")
        existing = self.model_id()
        if existing is not None and existing != model_id:
            raise ValueError(
                f"store holds vectors from '{existing}', cannot mix in '{model_id}'. "
                "Clear documents before switching the embedding model."
            )

        added_at = datetime.now(timezone.utc).isoformat()
        with self._conn:
            if existing is None:
                self._conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('model_id', ?)", (model_id,)
                )
            cur = self._conn.execute(
                "INSERT INTO documents (name, kind, added_at, chunk_count) VALUES (?, ?, ?, ?)",
                (name, kind, added_at, len(chunks)),
            )
            doc_id = int(cast(int, cur.lastrowid))
            self._conn.executemany(
                "INSERT INTO chunks (doc_id, ordinal, text, vector) VALUES (?, ?, ?, ?)",
                [
                    (doc_id, i, text, _to_blob(vec))
                    for i, (text, vec) in enumerate(zip(chunks, vectors, strict=True))
                ],
            )
        self._invalidate()
        return DocumentRecord(
            id=doc_id, name=name, kind=kind, added_at=added_at, chunk_count=len(chunks)
        )

    def search(
        self, query_vec: Sequence[float], top_k: int, min_score: float = 0.0
    ) -> list[Retrieved]:
        """Return the top_k chunks by cosine similarity to query_vec (descending)."""
        matrix = self._ensure_matrix()
        if matrix is None or top_k <= 0:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        norm = float(np.linalg.norm(q))
        if norm == 0.0:
            return []
        scores = matrix @ (q / norm)
        order = np.argsort(-scores)[:top_k]
        out: list[Retrieved] = []
        for idx in order:
            score = float(scores[idx])
            if score < min_score:
                break
            text, doc_name, kind = self._rows[int(idx)]
            out.append(Retrieved(text=text, doc_name=doc_name, kind=kind, score=score))
        return out

    def chunk_texts(self) -> list[str]:
        """All chunk text, resume documents first then the rest, in document/ordinal order.
        Used to derive the STT context-biasing vocab (resume-first front-loads the candidate's
        own name and skills)."""
        rows = self._conn.execute(
            "SELECT c.text FROM chunks c JOIN documents d ON c.doc_id = d.id "
            "ORDER BY (d.kind != 'resume'), d.id, c.ordinal"
        ).fetchall()
        return [cast(str, r["text"]) for r in rows]

    def list_documents(self) -> list[DocumentRecord]:
        rows = self._conn.execute(
            "SELECT id, name, kind, added_at, chunk_count FROM documents ORDER BY id"
        ).fetchall()
        return [
            DocumentRecord(
                id=int(r["id"]),
                name=cast(str, r["name"]),
                kind=cast(DocKind, r["kind"]),
                added_at=cast(str, r["added_at"]),
                chunk_count=int(r["chunk_count"]),
            )
            for r in rows
        ]

    def delete_document(self, doc_id: int) -> bool:
        with self._conn:
            cur = self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        self._invalidate()
        return cur.rowcount > 0

    def clear(self) -> None:
        """Drop all documents and the recorded model, freeing the store to switch models."""
        with self._conn:
            self._conn.execute("DELETE FROM documents")
            self._conn.execute("DELETE FROM meta WHERE key = 'model_id'")
        self._invalidate()

    def close(self) -> None:
        self._conn.close()

    def _invalidate(self) -> None:
        self._matrix = None
        self._rows = []

    def _ensure_matrix(self) -> NDArray[np.float32] | None:
        """Build (once) the row-normalized matrix + parallel metadata from sqlite."""
        if self._matrix is not None:
            return self._matrix
        rows = self._conn.execute(
            "SELECT c.text, c.vector, d.name, d.kind "
            "FROM chunks c JOIN documents d ON d.id = c.doc_id "
            "ORDER BY c.id"
        ).fetchall()
        if not rows:
            return None
        vectors = [np.frombuffer(r["vector"], dtype=np.float32) for r in rows]
        matrix = np.vstack(vectors)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        np.maximum(norms, 1e-12, out=norms)  # avoid divide-by-zero on a null vector
        self._matrix = (matrix / norms).astype(np.float32)
        self._rows = [
            (cast(str, r["text"]), cast(str, r["name"]), cast(DocKind, r["kind"])) for r in rows
        ]
        return self._matrix


def _to_blob(vec: Sequence[float]) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


__all__ = ["DocumentRecord", "DocumentStore"]
