"""Extracts text from txt, md, pdf and docx files and splits it into overlapping chunks."""

from __future__ import annotations

import io
import re
import unicodedata
from pathlib import Path

from .base import Chunk

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}

# A PDF's list markers arrive as Symbol/Wingdings glyphs in the private use area, which mean nothing
# outside their font, so a line starting with one becomes a markdown bullet and the rest are dropped.
_PRIVATE_USE = "\ue000-\uf8ff"
_BULLETS = f"\u2022\u00b7\u25aa\u25cf\u25e6{_PRIVATE_USE}"
_LEADING_BULLET = re.compile(rf"^[ \t]*[{_BULLETS}]+[ \t]*", re.MULTILINE)
_STRAY_PRIVATE_USE = re.compile(f"[{_PRIVATE_USE}]")
# Invisible characters that silently stop a word matching the same word typed normally.
_INVISIBLE = re.compile("[\u00ad\u200b-\u200d\ufeff]")
_ODD_SPACES = re.compile("[\u00a0\u2000-\u200a\u202f\u205f\u3000]")


def extract_text(filename: str, data: bytes) -> str:
    """Turns an uploaded document into plain text based on its extension; raises ValueError for unsupported types."""
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
    """Strip extraction artifacts, then collapse whitespace while keeping paragraph breaks."""
    # NFKC folds the ligatures PDFs use, so "ﬁrst" can still be found by searching for "first".
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    text = _ODD_SPACES.sub(" ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _LEADING_BULLET.sub("- ", text)
    text = _STRAY_PRIVATE_USE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    # PDF extraction leaves a space at the end of nearly every line.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, chunk_chars: int, overlap: int) -> list[Chunk]:
    """Packs paragraphs into chunks of up to ``chunk_chars``, repeating the last ``overlap`` chars at the start of the next.

    Paragraphs longer than ``chunk_chars`` are first split on word boundaries.
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
