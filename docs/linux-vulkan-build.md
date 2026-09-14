# Linux Vulkan build notes (pywhispercpp)

Captured while bringing up Phase 0 on the Razer Blade laptop (Intel UHD + NVIDIA RTX
3070, Wayland). These are the non-obvious gotchas a future Linux installer must automate
so the user never has to think about them.

## The goal

`pywhispercpp` running on the **Vulkan** ggml backend (cross-vendor GPU: AMD/NVIDIA/Intel),
not the CPU backend. Verified working when `Model.system_info()` prints a line like:

```
ggml_vulkan: 1 = NVIDIA GeForce RTX 3070 Laptop GPU (NVIDIA) | uma: 0 | fp16: 1 | ...
```

## Gotcha 1: pip installs a CPU-only prebuilt wheel and silently ignores GGML_VULKAN

`pip install pywhispercpp` (even with `GGML_VULKAN=1` in the env) pulls the prebuilt
`manylinux` wheel, which is **CPU-only**. Build-time env vars are only honored when pip
actually compiles from source. Symptom: `system_info()` shows `CPU : AVX2 = 1 ...` with
**no `ggml_vulkan` line**.

Fix: force a from-source build.

```bash
pip install 'pip<26'   # see gotcha 1b
GGML_VULKAN=1 CMAKE_ARGS="-DGGML_VULKAN=ON" \
  PIP_CONSTRAINT=scripts/build-constraints.txt \
  pip install --force-reinstall --no-binary pywhispercpp --no-deps pywhispercpp
```

Name the package in `--no-binary`, not `:all:`. `:all:` also applies to the isolated build
environment, so pip compiles cmake, ninja and setuptools from source first, single-threaded,
before it reaches whisper at all. Measured on this machine: 42 minutes spent entirely inside
"Installing build dependencies", versus 16 seconds to reach the real build with the package
named.

### Gotcha 1b: pip's build environment picks cmake 4, which whisper.cpp cannot configure

pywhispercpp declares `cmake>=3.12` in `build-system.requires`, so the isolated build environment
gets the newest cmake on PyPI, currently 4.x, whatever apt installed. CMake 4 dropped the pre-3.10
compatibility whisper.cpp's bundled CMakeLists relies on, and configure fails:

```
CMake Error at whisper.cpp/ggml/src/ggml-cpu/CMakeLists.txt:57 (target_compile_features)
Could NOT find Vulkan (missing: Vulkan_LIBRARY) (found version "1.3.275")
```

The Vulkan line is a **red herring**: `libvulkan-dev` is installed and `libvulkan.so` is present.
It is CMake 4's rewritten `FindVulkan`, not a missing package.

The fix is `PIP_CONSTRAINT` with `cmake<4` (`scripts/build-constraints.txt`), and it only works
on **pip below 26**. pip 24 applies constraints to the build environment; pip 26 does not.
Measured on 26.2.1: the constraint is accepted without complaint and `cmake-4.4.3` installed
regardless. That is why `scripts/install.sh` pins `pip<26` instead of upgrading.

Builds from before roughly mid-2025 got cmake 3.x by luck of timing, which is why an older venv
keeps working while a fresh one fails.

### Build prerequisites (system packages, need sudo)

- `cmake`, `g++`/`cc` (build toolchain)
- `libvulkan-dev` (provides `/usr/include/vulkan/vulkan.h`, pkg-config `vulkan`)
- `glslc` (shader compiler; ships in `shaderc` / glslang tooling)
- `glslang-tools` (provides `glslangValidator`) and `spirv-headers` (provides the
  `SPIRV-Headers` cmake package), both required from pywhispercpp **1.5.1** onward. 1.5.0
  built without them, so a machine with a working older build can still fail a fresh one with
  `Could not find a package configuration file provided by "SPIRV-Headers"`.

The Vulkan **runtime** (loader + ICD) is usually already present from the GPU driver
(`vulkaninfo --summary` lists devices). Only the **dev** headers + `glslc` are missing on
a fresh box. These are additive and reversible; they do not touch GPU drivers or install
CUDA.

## Gotcha 2: the source build's .so files have no RPATH, so they can't find each other

The prebuilt wheel is processed by `auditwheel`, which sets `$ORIGIN` RPATH so the bundled
shared libs resolve. The raw `--no-binary` source build does **not** do this. Symptom at
import time:

```
ImportError: libwhisper.so.1: cannot open shared object file: No such file or directory
```

(and after fixing that one, `libggml.so.0`, then `libggml-base.so.0`, ... a dependency
chain). The libs ARE present in `site-packages/`, the loader just can't find siblings.

Fix: set `$ORIGIN` RPATH on every real (non-symlink) ELF. `patchelf` is available as a
pip wheel that bundles the binary, so this needs **no sudo**:

```bash
pip install patchelf
SP="$(python -c 'import site; print(site.getsitepackages()[0])')"
for f in "$SP"/libwhisper.so.* "$SP"/libggml*.so.* "$SP"/_pywhispercpp*.so; do
  [ -L "$f" ] && continue   # skip symlinks
  patchelf --set-rpath '$ORIGIN' "$f"
done
```

Verify with no `LD_LIBRARY_PATH` set:

```bash
python -c "from pywhispercpp.model import Model; print(Model.system_info())"
# must show the ggml_vulkan line
```

## This is automated

`scripts/install.sh` does everything above: detects Vulkan, installs the missing system build
packages behind one sudo prompt, source-builds with `GGML_VULKAN=ON`, patches the RPATH, and
refuses to exit successfully if `ggml_vulkan` is absent. It skips the build when one already
works, so it is safe to re-run. The steps below remain as the explanation of what it does and
why, and as the manual path if you would rather do it yourself.

Still open: hosting a prebuilt Vulkan wheel per Python ABI, so a fresh install skips the
four-minute compile entirely. That is packaging work rather than installer work.

## Installer TODO (automate all of the above)

1. Detect Vulkan runtime (`vulkaninfo`); if absent, fall back to CPU build and warn.
2. Ensure build deps (`libvulkan-dev`, `glslc`, `cmake`, `g++`) via the distro package
   manager, prompting for sudo once.
3. Source-build `pywhispercpp` with `GGML_VULKAN=ON`.
4. RPATH-patch the resulting `.so` files with `$ORIGIN` (bundled `patchelf` pip wheel).
5. Smoke-test `system_info()` for the `ggml_vulkan` line; if missing, report clearly and
   offer the CPU wheel as fallback (`pip install --force-reinstall pywhispercpp`).
6. Consider caching/shipping a prebuilt **Vulkan** wheel per Python ABI to skip the
   multi-minute compile entirely (the upstream prebuilt wheel is CPU-only, so we'd have to
   build and host our own).
