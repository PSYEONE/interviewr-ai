# Contributing

Bug reports and ideas are welcome as [issues](https://github.com/PSYEONE/interviewr-ai/issues).
For code, read this first.

## The licence terms, up front

The project is AGPL-3.0, and contributions need a signed
[Contributor License Agreement](./CLA.md). You keep the copyright in your work; the agreement lets
the project be relicensed or offered commercially later without having to track down every
contributor for permission.

That is a real trade-off and worth understanding before you spend time on a change. If it is not
acceptable to you, an issue describing the problem is still genuinely useful, and someone else can
write the patch.

Accept it by commenting on your pull request:

```
I have read CLA.md and I accept it. Signed: <your full name> <your@email>
```

Putting that line in the pull request description works too. A check called `cla` watches for it and
turns green once it is there, so there is nothing to wait on from a maintainer.

## Before you open a pull request

Run what CI would run if it existed:

```bash
cd backend && pytest && mypy --strict . && ruff check .
cd overlay && npm run check && npm test
cd overlay/src-tauri && cargo test && cargo clippy --all-targets
```

A change to the engine's events, config or system report usually needs the matching type updated by
hand in `overlay/src/lib/types.ts`, which is kept in sync manually.

## What makes a change easy to accept

- **One thing per pull request.** Separate the fix from the refactor.
- **A test that fails without it**, for anything behavioural. The existing tests are grouped by
  behaviour rather than by file, and `backend/tests/test_capture_recovery.py` is a good example of
  the style.
- **Platform honesty.** This is Linux-first and the compositor decides a lot. If a window-management
  or capture feature only works on KDE, or only on X11, say so in the code comment and in any
  user-facing copy rather than implying it works everywhere.
- **Comments that say what the code does and why it has to be that way.** No em dashes, no banner
  separators.

## Architecture rule worth knowing

The Python engine is the product; every UI is a client of it. The engine broadcasts events to any
number of subscribers over an authenticated WebSocket and must not assume a single client. Keep
overlay-specific or transport-specific logic out of `backend/app/`.
