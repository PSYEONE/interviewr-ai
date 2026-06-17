"""Document text extraction and chunking.

Extraction covers the formats resumes/JDs actually ship in: plain text, markdown, PDF,
DOCX. Chunking packs whole paragraphs up to a character budget and carries a short overlap
tail across the boundary, so a fact split across the cut is still retrievable from both
chunks (the same idea as Natively's overlap windows).
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from .base import Chunk

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}


def extract_text(filename: str, data: bytes) -> str:
    """Decode/parse an uploaded document into plain text.

    Dispatches on the filename suffix. Raises ValueError for unsupported types so the
    caller can return a clean 4xx instead of ingesting garbage.
    """
    suffix = Path(filename).suffix.lower()
    if suffix in _TEXT_SUFFIXES or suffix == "":
        return _normalize(data.decode("utf-8", errors="replace"))
    if suffix == ".pdf":
        return _normalize(_extract_pdf(data))
    if suffix == ".docx":
        return _normalize(_extract_docx(data))
    raise ValueError(f"unsupported document type '{suffix}' (use txt, md, pdf, or docx)")


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    return "\n\n".join(p.text for p in doc.paragraphs)


def _normalize(text: str) -> str:
    """Collapse intra-line whitespace and runs of blank lines, keeping paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, chunk_chars: int, overlap: int) -> list[Chunk]:
    """Split normalized text into overlapping chunks on paragraph boundaries.

    Paragraphs are packed greedily up to ``chunk_chars``; when a chunk closes, an overlap
    tail of up to ``overlap`` chars from its end seeds the next chunk. A single paragraph
    longer than ``chunk_chars`` is hard-split so no chunk exceeds the budget by much.
    """
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    overlap = max(0, min(overlap, chunk_chars - 1))

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    units: list[str] = []
    for para in paragraphs:
        if len(para) <= chunk_chars:
            units.append(para)
        else:
            units.extend(_hard_split(para, chunk_chars))

    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + 2 + len(unit) <= chunk_chars:
            current = f"{current}\n\n{unit}"
        else:
            chunks.append(current)
            tail = current[-overlap:].lstrip() if overlap else ""
            current = f"{tail}\n\n{unit}" if tail else unit
    if current:
        chunks.append(current)

    return [Chunk(text=c, ordinal=i) for i, c in enumerate(chunks)]


def _hard_split(text: str, size: int) -> list[str]:
    """Break an oversized paragraph on word boundaries into <= size pieces."""
    words = text.split(" ")
    pieces: list[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= size:
            current = f"{current} {word}"
        else:
            pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    return pieces


__all__ = ["chunk_text", "extract_text"]
