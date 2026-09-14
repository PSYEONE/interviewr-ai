# .deb packaging: where it stands

Shelved on this branch, unfinished. The whole stack is written; the container build does not yet
produce a package.

## What works

- The relocatability the package depends on is on `main` (XDG config and state dirs, the overlay
  finding an installed engine and its bundled interpreter, the overlay reading the same `.env` as
  the engine).
- The image builds up to the pywhispercpp compile: apt on `ubuntu:24.04`, the bundled
  python-build-standalone interpreter, rust, node, and `pip install -e backend`.
- cmake is correctly held at 3.31 in pip's build environment by `pip<26` plus
  `scripts/build-constraints.txt` (also on `main`).

## Why the base is 24.04 and Python is bundled

Everything compiled on the 26.04 dev machine requires `GLIBC_2.43` (one symbol, `sqrtf`), so a
package built there installs on 26.04 only. 26.04 also ships only `python3.14` with no
`python3.12`, so there is no single system interpreter to depend on across releases.

## The blocker

Inside `packaging/Dockerfile`, the pywhispercpp source build fails at configure:

```
CMake Error at whisper.cpp/ggml/src/CMakeLists.txt:47 (ggml_get_flags):
  ggml_get_flags Function invoked with incorrect arguments
CMake Error at .../cmake-3.31/Modules/FindOpenMP.cmake:644 (if):
  if given arguments: "CMAKE_C_COMPILER_ID" "STREQUAL" "Fujitsu" "OR" "STREQUAL" "IntelLLVM"
```

Line 47 is `ggml_get_flags(${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION})`, so a compiler
ID or version is expanding empty.

## Disproven, do not retry

- **CMake 4.** Real, and fixed separately, but this error also occurs on cmake 3.31.
- **`--no-build-isolation`.** Same error with build isolation on.
- **Compiler detection.** Passing `-DCMAKE_C_COMPILER=/usr/bin/gcc -DCMAKE_CXX_COMPILER=/usr/bin/g++`
  through `CMAKE_ARGS` changed nothing. g++ is present and identifies as GNU 13.3.0 in a bare
  project under the same image and Ninja generator.

## The one lead

The same build **succeeded** (`Successfully installed pywhispercpp-1.5.1`) in a minimal
`ubuntu:24.04` container with only `build-essential cmake glslc glslang-tools libvulkan-dev
spirv-headers`, bundled pip 24.3.1 and `PIP_CONSTRAINT=cmake<4`. The Dockerfile differs in:

1. ~10 extra apt packages (the webkit/gtk/soup `-dev` set, `dpkg-dev`, `fakeroot`, `file`,
   `patchelf`, `pkg-config`).
2. `ENV`s: `PREFIX`, `CMAKE_BUILD_PARALLEL_LEVEL`, `MAKEFLAGS`, plus the ARGs.
3. Running through `install.sh`, which installs the backend's dependencies first.

This matters because pywhispercpp's `setup.py` passes **every environment variable to cmake as a
`-D` define** (build logs show `-DHOME=/root`, `-DPATH=...`, `-DPREFIX=...`). Next step is to
bisect those three differences, cheapest first: the ENVs, then the apt set. The failure shows at
about 60s into the install step, so each bisect run is short once apt is cached.

## Also untested

- Whether `bundle.linux.deb.files` preserves the ggml symlinks the Dockerfile creates.
- `scripts/build-deb.sh`, `.github/workflows/release.yml` and the bundle config end to end.
- Memory: the tmpfs `/tmp` on the dev machine filled with apport cores once and OOM-killed a
  build; `BUILD_JOBS` (default 8) caps the shader compile.
