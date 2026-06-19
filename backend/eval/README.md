# STT accuracy evaluation

Measures transcription accuracy so model and threshold choices are numbers, not vibes.

## Run

From the `backend/` dir (with the venv active or via `.venv/bin/python`):

```bash
# download LibriSpeech test-clean and score 100 files with the current default model
python -m eval.run --fetch test-clean --n 100

# compare a bigger model (auto-downloads)
python -m eval.run --fetch test-clean --n 100 --model small.en

# score your own clips: a folder of <stem>.wav (or .mp3/.flac/.m4a) + <stem>.txt
python -m eval.run --data path/to/clips --n 0
```

Other flags: `--mode {model,stream,both}`, `--seed`, `--worst N` (offenders to print),
and knob overrides `--max-utterance / --silence-hold / --silence-rms / --partial-interval`
(default to the values in `app/config.py`, so a default run reflects production).

Requires `ffmpeg` on PATH and the `jiwer` dev dep (`pip install -e ".[dev]"`).

## What the numbers mean

Two passes, scored as **normalized WER** (lowercased, punctuation stripped):

- **model**, the whole file transcribed once via `WhisperTranscriber.transcribe_raw`. Pure
  model quality. Comparable to published whisper WER.
- **stream**, the file fed through the real `transcribe_stream` in 0.3s chunks, scoring the
  committed text. This is what users actually see.
- **pipeline-induced delta** = stream_wer − model_wer. Errors our streaming/VAD/LocalAgreement
  logic adds on top of the model (boundary drops, premature commits). Watch this when tuning the
  VAD/streaming knobs.
- **RTF** (real-time factor) = whisper inference seconds / audio seconds. Stream RTF includes
  every partial re-transcription, so it is the true live cost; it must stay comfortably < 1.

Worst offenders print an aligned ref/hyp diff so you can see *what kind* of error it is.
Full per-file results are written to `eval/results/<timestamp>_<model>.json` (gitignored).

## Important caveat

LibriSpeech is clean, read audiobook speech by native speakers. It is a **calibrated floor and
a regression set**, not your real-world number. Live interview/Zoom audio (accents, spontaneous
speech, crosstalk, mic quality, system-capture artifacts) will score worse. Gather 10-20 real
clips and run them via `--data` for the number that actually predicts product quality. That
domain set is also where a context-biasing `initial_prompt` (seeded from resume + job-description
terms) would help with jargon and proper nouns, which LibriSpeech cannot show.

## Suggested workflow

1. Baseline: `--fetch test-clean --n 100` with the default model.
2. Model lever (usually the biggest): re-run with `--model small.en` / `medium.en`, compare WER
   and RTF, decide whether to raise the default `whisper_model`.
3. Hard set: `--fetch test-other` for accented/noisier read speech.
4. Domain set: `--data <your clips>` once you have real recordings.
