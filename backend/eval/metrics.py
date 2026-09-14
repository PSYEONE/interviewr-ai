"""Word error rate via jiwer, after lowercasing and removing punctuation from both texts so only real word errors count."""

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
