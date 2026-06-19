"""Shared RAG types and the provider-agnostic Embedder protocol.

The retriever talks to one Embedder. Backends (Ollama local, OpenAI/Gemini cloud) are
adapters behind this protocol, exactly like the LLM layer, so the user can choose
offline-first or a cloud key without the rest of the engine learning which is in use.

One hard rule lives here: query vectors must come from the same model as the stored
vectors. ``model_id`` is the identity used to detect and reject a mismatch.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

DocKind = Literal["resume", "job_description", "other"]


@dataclass(frozen=True)
class Chunk:
    """A piece of a source document, ready to embed and store."""

    text: str
    ordinal: int


@dataclass(frozen=True)
class Retrieved:
    """A stored chunk returned by a similarity search."""

    text: str
    doc_name: str
    kind: DocKind
    score: float


@runtime_checkable
class Embedder(Protocol):
    """Batch text embedder. ``model_id`` namespaces the vector space (``provider:model``)."""

    @property
    def model_id(self) -> str: ...

    async def check_health(self) -> bool: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def close(self) -> None: ...


_KIND_LABEL: dict[DocKind, str] = {
    "resume": "resume",
    "job_description": "job description",
    "other": "background",
}


def format_context(chunks: Sequence[Retrieved]) -> str:
    """Render retrieved chunks into a preamble for the answer turn.

    Returns "" when there is nothing to inject, so the caller can fall back to the
    bare transcript. The preamble is prepended to the *current* user message only and
    never committed to history, so each turn retrieves fresh against the live question.
    """
    if not chunks:
        return ""
    lines = [
        "Relevant background about the candidate and the role "
        "(retrieved from their resume and the job description):",
        "",
    ]
    for c in chunks:
        lines.append(f"[{_KIND_LABEL[c.kind]} - {c.doc_name}] {c.text}")
    lines += [
        "",
        "Use the background above where it is relevant. Then respond to the latest "
        "question in the meeting transcript:",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "Chunk",
    "DocKind",
    "Embedder",
    "Retrieved",
    "format_context",
]
