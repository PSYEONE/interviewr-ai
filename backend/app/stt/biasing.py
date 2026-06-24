"""Build a whisper context-biasing prompt from resume/job-description vocab.

The whisper decoder takes an ``initial_prompt`` that seeds its prior without being
transcribed, nudging spelling toward supplied vocabulary. We feed it the distinctive
terms (names, companies, tech, acronyms) from the ingested docs so jargon and proper
nouns transcribe correctly instead of being guessed phonetically. Keep it to terms, not
prose: a long or sentence-like prompt triggers whisper's hallucination/repeat failure mode.
"""

from __future__ import annotations

import re

# Word-ish token: a letter followed by letters/digits and a few tech-name symbols (C++, Node.js,
# GPT-4). Distinctiveness is decided after, by the presence of an uppercase letter.
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]*")

# Capitalized words that are common sentence-starters or generic resume headings, not vocab.
_STOPWORDS = frozenset(
    w.lower()
    for w in (
        "The A An I We My Me He She They It Its This That These Those Their Our Your His Her "
        "In On At For To Of And Or But As With By From Was Were Is Are Be Been Being Has Have Had "
        "Experience Education Skills Summary Profile Projects Project Work Working Present Current "
        "Responsibilities Achievements Achievement References Contact Email Phone Address Mobile "
        "Languages Tools Technologies Technology Developed Designed Built Led Managed Created "
        "January February March April May June July August September October November December "
        "Monday Tuesday Wednesday Thursday Friday Saturday Sunday"
    ).split()
)


def _distinctive_terms(text: str) -> list[str]:
    """Tokens worth biasing on: anything containing an uppercase letter (proper nouns, acronyms,
    mixed-case tech), at least two chars, not a generic Capitalized stopword."""
    out: list[str] = []
    for tok in _TOKEN.findall(text):
        term = tok.strip(".-")
        if len(term) < 2 or not any(c.isupper() for c in term) or term.lower() in _STOPWORDS:
            continue
        out.append(term)
    return out


def build_biasing_prompt(texts: list[str], extra: str = "", max_chars: int = 600) -> str:
    """Comma-separated biasing vocab, manual ``extra`` terms first, then distinctive terms from
    ``texts`` in first-seen order (callers pass resume text first to front-load it). Deduped
    case-insensitively and capped to ``max_chars`` so it stays under whisper's prompt window."""
    candidates = [t.strip() for t in extra.split(",") if t.strip()]
    candidates.extend(_distinctive_terms("\n".join(texts)))

    seen: set[str] = set()
    terms: list[str] = []
    used = 0
    for term in candidates:
        key = term.lower()
        if key in seen:
            continue
        cost = len(term) + (2 if terms else 0)  # ", " separator
        if used + cost > max_chars:
            break
        seen.add(key)
        terms.append(term)
        used += cost
    return ", ".join(terms)
