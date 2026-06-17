"""Text extraction and chunking behaviour."""

from __future__ import annotations

import pytest

from app.rag.chunk import chunk_text, extract_text


def test_extract_text_plain() -> None:
    assert extract_text("notes.txt", b"hello   world") == "hello world"


def test_extract_text_normalizes_blank_lines_and_keeps_paragraphs() -> None:
    out = extract_text("r.md", b"a\n\n\n\nb")
    assert out == "a\n\nb"


def test_extract_text_unsupported_raises() -> None:
    with pytest.raises(ValueError, match="unsupported document type"):
        extract_text("resume.rtf", b"x")


def test_chunk_packs_paragraphs_until_budget() -> None:
    text = "para one\n\npara two\n\npara three"
    chunks = chunk_text(text, chunk_chars=20, overlap=0)
    # "para one\n\npara two" is 18 chars and fits; "para three" starts a new chunk.
    assert [c.text for c in chunks] == ["para one\n\npara two", "para three"]
    assert [c.ordinal for c in chunks] == [0, 1]


def test_chunk_overlap_carries_tail() -> None:
    text = "aaaa\n\nbbbb\n\ncccc"
    chunks = chunk_text(text, chunk_chars=10, overlap=4)
    assert len(chunks) >= 2
    # The second chunk is seeded with the tail of the first.
    assert chunks[1].text.startswith("bbbb")
    assert "bbbb" in chunks[0].text


def test_chunk_hard_splits_oversized_paragraph() -> None:
    text = "word " * 50  # one long paragraph, no blank-line breaks
    chunks = chunk_text(text.strip(), chunk_chars=40, overlap=0)
    assert len(chunks) > 1
    assert all(len(c.text) <= 40 for c in chunks)


def test_chunk_empty_text_yields_nothing() -> None:
    assert chunk_text("   \n\n  ", chunk_chars=100, overlap=10) == []
