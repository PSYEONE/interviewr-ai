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

- **Library copies:** the bundler copied every library alias as a full file (about 98 MB). The
  Dockerfile now keeps only the names the engine loads: five files, and the .deb went from 213 MB to 175 MB.
- **Dev desktop entry:** a checkout build's `~/.local` entry hid the packaged one. The script is gone;
  the app now writes that entry only when running from source and removes it once the package is installed.
- App bugs found while testing (wizard pin, model row flashing Download, transcription stalling)
  are fixed, and the rebuilt package was retested.

## Ruled out

- CMake 4: a separate problem, fixed on `main` with `pip<26` and `scripts/build-constraints.txt`.
- `--no-build-isolation`, and passing compiler paths explicitly.

## Why Ubuntu 24.04 and a bundled Python

- Binaries built on 26.04 need glibc 2.43, so they won't run on 24.04.
- 26.04 only ships Python 3.14, so there's no common system Python to depend on.

## Still to verify

- Install on a clean machine with no dev checkout, config or KWin rule.
- Releases are built locally with `./scripts/build-deb.sh` for now. The GitHub release workflow is
  parked on the `packaging-deb` branch and has never run (needs build caching and more disk space).
