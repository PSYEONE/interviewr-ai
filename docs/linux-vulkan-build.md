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
GGML_VULKAN=1 CMAKE_ARGS="-DGGML_VULKAN=ON" \
  pip install --force-reinstall --no-binary :all: --no-deps pywhispercpp
```

### Build prerequisites (system packages, need sudo)

- `cmake`, `g++`/`cc` (build toolchain)
- `libvulkan-dev` (provides `/usr/include/vulkan/vulkan.h`, pkg-config `vulkan`)
- `glslc` (shader compiler; ships in `shaderc` / glslang tooling)

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
