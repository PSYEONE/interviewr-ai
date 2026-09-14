# .deb packaging status

The Docker build produces a working `.deb` (about 213 MB). It installed and ran on the maintainer's
laptop: the wizard, a local model download and a live interview all worked.

## What fixed the build

- **Renamed `ENV PREFIX` to `INTERVIEWR_PREFIX`.** pywhispercpp's `setup.py` passes every
  environment variable to cmake, so `PREFIX` clashed with a cmake variable and configure failed at
  `ggml_get_flags`. Keep Dockerfile `ENV` and `ARG` names namespaced.
- **Raised `RUST_VERSION` from 1.83.0 to 1.91.1** so the overlay compiles.

## Verified

- The `--locked` build passes.
- `sudo apt install ./dist/interviewr_*.deb` installs, and the app launches from the menu.
- GPU transcription works in the installed copy (`ggml_vulkan` is listed by
  `/usr/lib/interviewr/python/bin/python3 -c 'from pywhispercpp.model import Model; Model.system_info()'`).
- Settings are saved under `~/.config/interviewr`, not inside `/usr/lib`.

## Known issues

- **Library symlinks don't survive packaging.** `libggml-vulkan.so`, `.so.0` and `.so.0.13.1` install
  as three separate 46 MB files, about 93 MB wasted.
- **A dev desktop entry hides the packaged one.** `overlay/scripts/install-desktop-entry.sh` writes
  `~/.local/share/applications/interviewr-overlay.desktop`, which takes priority over
  `/usr/share/applications` and launches the old dev binary. Remove it and run `kbuildsycoca6`.
- App bugs found while testing (wizard pin, model row flashing Download, transcription stalling
  after Clear) are being fixed on `main`; rebase this branch afterwards.

## Ruled out

- CMake 4: a separate problem, fixed on `main` with `pip<26` and `scripts/build-constraints.txt`.
- `--no-build-isolation`, and passing compiler paths explicitly.

## Why Ubuntu 24.04 and a bundled Python

- Binaries built on 26.04 need glibc 2.43, so they won't run on 24.04.
- 26.04 only ships Python 3.14, so there's no common system Python to depend on.

## Still to verify

- Install on a clean machine with no dev checkout, config or KWin rule.
- `.github/workflows/release.yml` works end to end.
