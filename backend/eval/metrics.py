"""Normalized Word Error Rate via jiwer.

Both reference and hypothesis are normalized the same way (lowercase, strip punctuation,
collapse whitespace) so we measure real word errors, not whisper's casing/punctuation against
LibriSpeech's uppercase-no-punctuation transcripts.
"""

from __future__ import annotations

import jiwer

NORMALIZE = jiwer.Compose(
    [
        jiwer.ToLowerCase(),
        jiwer.RemovePunctuation(),  # type: ignore[no-untyped-call]
        jiwer.RemoveMultipleSpaces(),
        jiwer.Strip(),
        jiwer.ReduceToListOfListOfWords(),
    ]
)


def score(reference: list[str], hypothesis: list[str]) -> jiwer.WordOutput:
    """WER stats for parallel reference/hypothesis lists. Pass single-element lists for one
    file, or the full corpus for a true (total-errors / total-words) aggregate WER."""
    return jiwer.process_words(
        reference, hypothesis, reference_transform=NORMALIZE, hypothesis_transform=NORMALIZE
    )


def diff(output: jiwer.WordOutput) -> str:
    """Aligned ref/hyp view marking substitutions, deletions and insertions."""
    return jiwer.visualize_alignment(output, show_measures=False).strip()
