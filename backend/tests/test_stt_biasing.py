"""STT context-biasing prompt construction from resume/JD vocab."""

from __future__ import annotations

from app.stt.biasing import build_biasing_prompt


def test_extracts_distinctive_terms_and_drops_common_words() -> None:
    text = "I worked at Hetzner building Kubernetes pipelines with PostgreSQL and AWS."
    prompt = build_biasing_prompt([text])
    terms = prompt.split(", ")
    assert "Hetzner" in terms
    assert "Kubernetes" in terms
    assert "PostgreSQL" in terms
    assert "AWS" in terms
    # Sentence-start "I" and lowercase words are not biased on.
    assert "I" not in terms
    assert "worked" not in terms
    assert "with" not in terms


def test_dedupes_case_insensitively_preserving_first_seen_order() -> None:
    prompt = build_biasing_prompt(["Tauri and Tauri", "Vulkan then Tauri"])
    assert prompt == "Tauri, Vulkan"


def test_extra_terms_come_first() -> None:
    prompt = build_biasing_prompt(["Postgres"], extra="Acme Corp, Jane Doe")
    assert prompt.startswith("Acme Corp, Jane Doe")


def test_respects_char_cap() -> None:
    text = " ".join(f"Term{i}" for i in range(200))
    prompt = build_biasing_prompt([text], max_chars=40)
    assert len(prompt) <= 40
    assert prompt  # still produced something


def test_empty_input_is_empty() -> None:
    assert build_biasing_prompt([]) == ""
    assert build_biasing_prompt(["just lowercase words here"]) == ""
