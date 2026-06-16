"""STT accuracy eval: model-level WER vs streaming-pipeline WER on a labelled test set.

Run from the backend dir as a module:
    python -m eval.run --fetch test-clean --n 100
    python -m eval.run --data path/to/clips --model small.en
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from app.audio.capture import CHUNK_SAMPLES
from app.config import Settings, get_settings
from app.stt.whisper import WhisperTranscriber, transcribe_stream
from eval import metrics
from eval.audio import load_audio
from eval.dataset import Sample, fetch_librispeech, load_folder, load_librispeech

SAMPLE_RATE = 16000


class TimedTranscriber(WhisperTranscriber):
    """WhisperTranscriber that accumulates whisper inference time, for real-time factor."""

    def __init__(self, model_name: str, use_gpu: bool, gpu_device: int) -> None:
        super().__init__(model_name, use_gpu, gpu_device)
        self.infer_s = 0.0

    def reset_meter(self) -> None:
        self.infer_s = 0.0

    def transcribe_raw(self, audio: np.ndarray) -> str:
        start = time.perf_counter()
        out = super().transcribe_raw(audio)
        self.infer_s += time.perf_counter() - start
        return out


async def stream_text(
    audio: np.ndarray,
    transcriber: WhisperTranscriber,
    *,
    silence_rms: float,
    silence_hold_s: float,
    max_utterance_s: float,
    partial_interval_s: float,
    commit_margin_words: int,
) -> str:
    """Feed the file through the real streaming pipeline in 0.3s chunks and return the
    concatenated committed text. Trailing silence is appended so the final utterance flushes
    (a file may end mid-speech, where production audio would keep flowing into silence)."""
    pad = np.zeros(int((silence_hold_s + 0.5) * SAMPLE_RATE), dtype=np.float32)
    padded = np.concatenate([audio, pad])

    async def chunks() -> AsyncIterator[np.ndarray]:
        for i in range(0, len(padded), CHUNK_SAMPLES):
            yield padded[i : i + CHUNK_SAMPLES]

    finals: list[str] = []
    async for item in transcribe_stream(
        chunks(),
        transcriber,
        sample_rate=SAMPLE_RATE,
        silence_rms=silence_rms,
        silence_hold_s=silence_hold_s,
        max_utterance_s=max_utterance_s,
        partial_interval_s=partial_interval_s,
        commit_margin_words=commit_margin_words,
        emit_partials=True,
    ):
        if item.final and item.text:
            finals.append(item.text)
    return " ".join(finals)


def parse_args(settings: Settings) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="STT accuracy eval (model vs streaming pipeline)")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--fetch", choices=["test-clean", "test-other", "both"])
    src.add_argument("--data", type=Path, help="LibriSpeech root, or flat <stem>.wav + <stem>.txt")
    p.add_argument("--n", type=int, default=100, help="files to sample (0 = all)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--model", default=settings.whisper_model)
    p.add_argument("--mode", choices=["model", "stream", "both"], default="both")
    p.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache" / "interviewr-eval")
    p.add_argument("--out", type=Path, default=Path(__file__).parent / "results")
    p.add_argument("--worst", type=int, default=10, help="worst offenders to print with diffs")
    p.add_argument("--max-utterance", type=float, default=settings.vad_max_utterance_s)
    p.add_argument("--silence-hold", type=float, default=settings.vad_silence_hold_s)
    p.add_argument("--silence-rms", type=float, default=settings.vad_silence_rms)
    p.add_argument("--partial-interval", type=float, default=settings.stt_partial_interval_s)
    p.add_argument("--commit-margin", type=int, default=settings.stt_commit_margin_words)
    return p.parse_args()


def resolve_samples(args: argparse.Namespace) -> list[Sample]:
    if args.data is not None:
        if any(args.data.rglob("*.trans.txt")):
            pool = load_librispeech(args.data, 0, args.seed)
        else:
            pool = load_folder(args.data, 0, args.seed)
    elif args.fetch is not None:
        subsets = ["test-clean", "test-other"] if args.fetch == "both" else [args.fetch]
        pool = []
        for sub in subsets:
            pool += load_librispeech(fetch_librispeech(sub, args.cache_dir), 0, args.seed)
    else:
        sys.exit("Specify a source: --fetch {test-clean,test-other,both} or --data <dir>")
    random.Random(args.seed).shuffle(pool)
    return pool[: args.n] if args.n > 0 else pool


def main() -> None:
    settings = get_settings()
    args = parse_args(settings)
    samples = resolve_samples(args)
    if not samples:
        sys.exit("No samples found.")

    do_model = args.mode in ("model", "both")
    do_stream = args.mode in ("stream", "both")
    print(f"Evaluating {len(samples)} files | model={args.model} | mode={args.mode}")

    tr = TimedTranscriber(args.model, settings.whisper_use_gpu, settings.whisper_gpu_device)
    tr.warmup()

    rows: list[dict[str, object]] = []
    refs: list[str] = []
    model_hyps: list[str] = []
    stream_hyps: list[str] = []
    total_audio_s = model_infer_s = stream_infer_s = 0.0

    for i, s in enumerate(samples, 1):
        audio = load_audio(s.audio_path)
        dur = len(audio) / SAMPLE_RATE
        total_audio_s += dur
        refs.append(s.reference)
        row: dict[str, object] = {"id": s.id, "audio_s": round(dur, 2), "reference": s.reference}

        if do_model:
            tr.reset_meter()
            hyp = tr.transcribe_raw(audio)
            model_infer_s += tr.infer_s
            model_hyps.append(hyp)
            row["model_hyp"] = hyp
            row["model_wer"] = round(metrics.score([s.reference], [hyp]).wer, 4)
        if do_stream:
            tr.reset_meter()
            hyp = asyncio.run(
                stream_text(
                    audio,
                    tr,
                    silence_rms=args.silence_rms,
                    silence_hold_s=args.silence_hold,
                    max_utterance_s=args.max_utterance,
                    partial_interval_s=args.partial_interval,
                    commit_margin_words=args.commit_margin,
                )
            )
            stream_infer_s += tr.infer_s
            stream_hyps.append(hyp)
            row["stream_hyp"] = hyp
            row["stream_wer"] = round(metrics.score([s.reference], [hyp]).wer, 4)
        rows.append(row)
        print(f"\r  {i}/{len(samples)}", end="", flush=True)
    print()

    aggregate: dict[str, float] = {}
    if do_model:
        aggregate["model_wer"] = round(metrics.score(refs, model_hyps).wer, 4)
        aggregate["model_rtf"] = round(model_infer_s / total_audio_s, 3)
    if do_stream:
        aggregate["stream_wer"] = round(metrics.score(refs, stream_hyps).wer, 4)
        aggregate["stream_rtf"] = round(stream_infer_s / total_audio_s, 3)
    if do_model and do_stream:
        aggregate["pipeline_delta_wer"] = round(
            aggregate["stream_wer"] - aggregate["model_wer"], 4
        )

    _print_report(args, len(samples), total_audio_s, aggregate, rows, do_model, do_stream)
    _write_results(args, aggregate, rows)


def _print_report(
    args: argparse.Namespace,
    n: int,
    audio_s: float,
    aggregate: dict[str, float],
    rows: list[dict[str, object]],
    do_model: bool,
    do_stream: bool,
) -> None:
    print("\n" + "=" * 64)
    print(f"RESULTS  model={args.model}  files={n}  audio={audio_s / 60:.1f} min")
    print(
        f"knobs: max_utterance={args.max_utterance}s silence_hold={args.silence_hold}s "
        f"silence_rms={args.silence_rms} partial_interval={args.partial_interval}s "
        f"commit_margin={args.commit_margin}"
    )
    print("-" * 64)
    if do_model:
        print(f"  model  WER {aggregate['model_wer']:.2%}   RTF {aggregate['model_rtf']}")
    if do_stream:
        print(f"  stream WER {aggregate['stream_wer']:.2%}   RTF {aggregate['stream_rtf']}")
    if do_model and do_stream:
        print(f"  pipeline-induced delta: {aggregate['pipeline_delta_wer']:+.2%} WER")
    print("=" * 64)

    rank_key = "stream_wer" if do_stream else "model_wer"
    hyp_key = "stream_hyp" if do_stream else "model_hyp"
    worst = sorted(rows, key=lambda r: float(r[rank_key]), reverse=True)[: args.worst]  # type: ignore[arg-type]
    if worst and float(worst[0][rank_key]) > 0:  # type: ignore[arg-type]
        print(f"\nWorst {len(worst)} ({rank_key}):")
        for r in worst:
            out = metrics.score([str(r["reference"])], [str(r[hyp_key])])
            print(f"\n[{r['id']}] WER {float(r[rank_key]):.2%}")  # type: ignore[arg-type]
            print(metrics.diff(out))


def _write_results(
    args: argparse.Namespace, aggregate: dict[str, float], rows: list[dict[str, object]]
) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = args.out / f"{stamp}_{args.model}.json"
    payload = {
        "config": {
            "model": args.model,
            "mode": args.mode,
            "n": len(rows),
            "seed": args.seed,
            "max_utterance_s": args.max_utterance,
            "silence_hold_s": args.silence_hold,
            "silence_rms": args.silence_rms,
            "partial_interval_s": args.partial_interval,
            "commit_margin_words": args.commit_margin,
        },
        "aggregate": aggregate,
        "files": rows,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
