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


def test_a_pdf_bullet_glyph_becomes_a_markdown_bullet() -> None:
    # Straight from a real CV: pypdf hands back the Symbol font's bullet, which means nothing
    # outside that font, and the list structure it stood for is worth keeping.
    out = extract_text("cv.txt", "Technical Skills \n\uf0b7 Languages: \nPython".encode())
    assert out == "Technical Skills\n- Languages:\nPython"


def test_real_bullet_characters_are_normalized_too() -> None:
    assert extract_text("a.md", "\u2022 one\n\u00b7 two".encode()) == "- one\n- two"


def test_a_private_use_glyph_mid_line_is_dropped_not_bulleted() -> None:
    # Only a leading marker is a list; one in the middle of a sentence is just noise.
    assert extract_text("a.md", "Python \uf0b7 Java".encode()) == "Python Java"


def test_ligatures_are_folded_so_words_stay_searchable() -> None:
    # "\ufb01rst" would never match a search for "first".
    assert extract_text("a.md", "\ufb01rst".encode()) == "first"


def test_invisible_characters_are_removed() -> None:
    # A soft hyphen or zero-width space inside a word stops it matching the plain spelling.
    assert extract_text("a.md", "devel\u00adopment\u200b.".encode()) == "development."


def test_odd_spaces_collapse_like_ordinary_ones() -> None:
    assert extract_text("a.md", "a\u00a0\u2009b".encode()) == "a b"


def test_real_typography_is_left_alone() -> None:
    # En dashes and curly quotes are the author's words, not extraction artifacts.
    out = extract_text("a.md", "2019\u20132021 isn\u2019t mine".encode())
    assert out == "2019\u20132021 isn\u2019t mine"


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
