"""A/B test for STT context-biasing on real speech (LibriSpeech).

For each clip we transcribe once with no prompt, find the reference words the unbiased pass
missed, feed exactly those back as the `initial_prompt`, and transcribe again. This is an
oracle upper bound: it measures how much biasing recovers *when the spoken vocab is in your
resume/JD* (the case the feature targets), isolating the prompt's effect from vocab discovery.

Run: python -m eval.bias_ab [N] [subset]   (uses the eval cache; no network)
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.stt.whisper import WhisperTranscriber
from eval import metrics
from eval.audio import load_audio
from eval.dataset import load_librispeech

_CACHE = Path.home() / ".cache" / "interviewr-eval"


def _normset(text: str) -> set[str]:
    return {w for w in text.lower().split() if w.isalpha()}


def _missed(reference: str, hypothesis: str, min_len: int = 4) -> list[str]:
    """Reference words (length >= min_len) absent from the hypothesis: what biasing should fix."""
    hyp = _normset(hypothesis)
    out: list[str] = []
    seen: set[str] = set()
    for raw in reference.split():
        w = raw.lower()
        if w.isalpha() and len(w) >= min_len and w not in hyp and w not in seen:
            seen.add(w)
            out.append(raw.title())  # present as proper-noun-style vocab
    return out


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    subset = sys.argv[2] if len(sys.argv) > 2 else "test-other"
    root = _CACHE / "LibriSpeech" / subset
    if not root.is_dir():
        sys.exit(f"no cached LibriSpeech at {root}; run the WER eval first to populate it")

    samples = load_librispeech(root, n, seed=0)
    t = WhisperTranscriber(model_name="small.en", use_gpu=True)
    t.warmup()

    refs: list[str] = []
    off: list[str] = []
    on: list[str] = []
    fixed_examples: list[str] = []

    for s in samples:
        audio = load_audio(s.audio_path)
        t.set_prompt("")
        hyp_off = t.transcribe_raw(audio)
        missed = _missed(s.reference, hyp_off)
        t.set_prompt(", ".join(missed))
        hyp_on = t.transcribe_raw(audio)

        refs.append(s.reference)
        off.append(hyp_off)
        on.append(hyp_on)

        if missed:
            recovered = _normset(s.reference) - _normset(hyp_off)
            recovered &= _normset(hyp_on)
            recovered = {w for w in recovered if len(w) >= 4}
            if recovered and len(fixed_examples) < 5:
                fixed_examples.append(
                    f"  biased on {missed} -> recovered {sorted(recovered)}"
                )

    wer_off = metrics.score(refs, off).wer
    wer_on = metrics.score(refs, on).wer
    print(f"\nsamples: {len(samples)}  subset: {subset}")
    print(f"WER  biasing OFF: {wer_off:.4f}")
    print(f"WER  biasing ON : {wer_on:.4f}")
    delta = wer_off - wer_on
    print(f"delta (off-on)  : {delta:+.4f}  ({delta / wer_off * 100:+.1f}%)" if wer_off else "")
    if fixed_examples:
        print("\nrecovered-word examples:")
        print("\n".join(fixed_examples))


if __name__ == "__main__":
    main()
