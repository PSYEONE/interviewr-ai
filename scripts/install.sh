#!/usr/bin/env bash
# Set up the interviewr engine, including the GPU STT build that pip cannot do on its own.
#
# Three things go wrong if you just `pip install pywhispercpp`, all documented in
# docs/linux-vulkan-build.md and all automated here:
#
#   1. pip fetches a prebuilt CPU-only wheel and silently ignores GGML_VULKAN=1, so STT runs on
#      the CPU while appearing to work.
#   2. A source build's .so files carry no $ORIGIN RPATH (auditwheel adds it for the prebuilt
#      wheel), so importing fails with "libwhisper.so.1: cannot open shared object file".
#   3. whisper.cpp uses Vulkan device 0, which on a laptop is usually the weak iGPU rather than
#      the discrete card.
#
# Idempotent: re-run safely. An already-working GPU build is detected and the multi-minute
# compile is skipped.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
# Overridable so the .deb build can point the install at its bundled interpreter.
VENV="${INTERVIEWR_VENV:-$BACKEND/.venv}"
PY="$VENV/bin/python"

WANT_GPU=1
ASSUME_YES=0
BUILD_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --cpu) WANT_GPU=0 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --build-only) BUILD_ONLY=1; ASSUME_YES=1 ;;
    --help|-h)
      echo "usage: scripts/install.sh [--cpu] [--yes] [--build-only]"
      echo "  --cpu         skip the Vulkan build and use the CPU wheel"
      echo "  --yes         unattended: install build dependencies and pick the GPU without asking"
      echo "  --build-only  compile only; skip every check that needs a GPU present (for packaging)"
      exit 0
      ;;
    *) echo "error: unknown option '$arg' (try --help)" >&2; exit 2 ;;
  esac
done

# Colour only for a human at a terminal. Piping to a file or a log then stays readable instead of
# filling with escape sequences, and NO_COLOR is the conventional way to ask for none.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != dumb ]; then
  BOLD=$'\033[1m'; CYAN=$'\033[36m'; GREEN=$'\033[32m'; RED=$'\033[31m'
  DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=''; CYAN=''; GREEN=''; RED=''; DIM=''; RESET=''
fi

say() { printf '\n%s==%s %s%s%s\n' "$CYAN" "$RESET" "$BOLD" "$1" "$RESET"; }
have() { command -v "$1" >/dev/null 2>&1; }
die() { printf '%serror:%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }

# 1. Preflight. Fail here with something actionable rather than deep inside pip.
say "Checking prerequisites"
[ "$(uname -s)" = "Linux" ] || die "this installer is Linux-only (the engine captures via PipeWire)"
have python3 || die "python3 not found"
python3 - <<'EOF' || die "python 3.12+ required (found $(python3 -V 2>&1))"
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
EOF
# PipeWire is a runtime dependency, not a build one; the .deb declares it instead.
if [ "$BUILD_ONLY" -eq 0 ]; then
  for tool in pw-record pactl; do
    have "$tool" || die "$tool not found; the engine needs PipeWire (package: pipewire-utils / pulseaudio-utils)"
  done
  echo "  python $(python3 -V 2>&1 | cut -d' ' -f2), PipeWire tools present"
else
  echo "  python $(python3 -V 2>&1 | cut -d' ' -f2), build-only (skipping runtime checks)"
fi

# 2. Venv. Reused when it already exists; pip decides if anything needs installing.
#
# Deliberately without the [local] extra, which is just pywhispercpp: installing it here would let
# pip drop the prebuilt CPU wheel on top of an existing source build, silently undoing the GPU
# backend and costing a half-hour rebuild. Whichever path below runs owns that package instead.
say "Installing the engine into backend/.venv"
[ -d "$VENV" ] || python3 -m venv "$VENV"
# Pinned below 26 rather than upgraded. pip 26 stopped applying PIP_CONSTRAINT to build
# dependencies, which is the only lever over the cmake that compiles pywhispercpp (see step 5 and
# scripts/build-constraints.txt). Measured on 26.2.1: a `cmake<4` constraint is accepted in silence
# and cmake 4.4.3 installed regardless. Both the pin and the constraint can go once pywhispercpp's
# bundled whisper.cpp configures under CMake 4.
"$PY" -m pip install --quiet 'pip<26'
# Not --quiet: on a fresh clone this downloads numpy and the rest, and pip's own collecting and
# download output is the only sign the script has not hung.
"$PY" -m pip install -e "$BACKEND"

# Whether the GPU build is already in place. Used before building (to skip a long compile) and
# after (as the thing that decides success), so it lives in one function.
#
# It has to read the process output, not system_info()'s return value: that returns whisper's CPU
# feature string, while ggml prints the "ggml_vulkan: ..." line to stderr when it registers the
# backend. Checking the return value looks right and is always false, which rebuilds every run and
# then reports the finished build as broken.
gpu_backend_line() {
  "$PY" -c 'from pywhispercpp.model import Model; Model.system_info()' 2>&1 | grep -m1 ggml_vulkan
}

gpu_ready() {
  gpu_backend_line >/dev/null 2>&1
}

if [ "$WANT_GPU" -eq 0 ]; then
  say "Skipping the GPU build (--cpu)"
  # --no-deps for the same reason as the GPU path: force-reinstall would otherwise rebuild the
  # whole dependency tree. The plain install after it supplies anything missing.
  "$PY" -m pip install --force-reinstall --no-deps pywhispercpp
  "$PY" -m pip install pywhispercpp
  echo "  CPU build installed. Transcription will be slower but works on any machine."
  exit 0
fi

# 3. Vulkan detection. No Vulkan means no GPU path at all, and saying so is better than
#    producing a CPU build the user believes is GPU-accelerated.
#
#    Skipped for --build-only: compiling against the Vulkan headers needs no GPU, and a build
#    container has no /dev/dri, so this gate and the smoke test at the end would both refuse a good
#    build. Which device to use is a per-machine choice the app makes on first run.
if [ "$BUILD_ONLY" -eq 0 ]; then
  say "Looking for Vulkan"
  if ! have vulkaninfo || ! vulkaninfo --summary >/dev/null 2>&1; then
    echo "  No working Vulkan runtime found."
    echo "  Install your GPU's Vulkan driver (mesa-vulkan-drivers, or the NVIDIA driver), then"
    echo "  re-run. To proceed on the CPU instead: scripts/install.sh --cpu"
    exit 1
  fi
  vulkaninfo --summary 2>/dev/null | sed -n 's/^\s*deviceName\s*=\s*/  GPU: /p'
fi

if [ "$BUILD_ONLY" -eq 0 ] && gpu_ready; then
  say "GPU build already working, nothing to build"
else
  # 4. Build deps, one sudo prompt, only for what is actually missing.
  say "Checking build dependencies"
  # Named for the Debian/Ubuntu packages, each checked by the artefact cmake actually looks for.
  # SPIRV-Headers and glslangValidator are needed from pywhispercpp 1.5.1 onward; 1.5.0 built
  # without them, so a machine with a working older build can still fail a fresh one.
  pkgs=()
  have cmake || pkgs+=(cmake)
  have g++ || pkgs+=(g++)
  have glslc || pkgs+=(glslc)
  have glslangValidator || pkgs+=(glslang-tools)
  [ -e /usr/include/vulkan/vulkan.h ] || pkgs+=(libvulkan-dev)
  compgen -G "/usr/lib/*/cmake/SPIRV-Headers" >/dev/null 2>&1 ||
    [ -d /usr/share/cmake/SPIRV-Headers ] || pkgs+=(spirv-headers)
  if [ ${#pkgs[@]} -gt 0 ]; then
    if ! have apt-get; then
      die "install these with your package manager, then re-run: ${pkgs[*]}"
    fi
    echo "  Missing: ${pkgs[*]}"
    if [ "$ASSUME_YES" -eq 0 ]; then
      read -r -p "  Install them with sudo apt-get? [y/N] " reply
      [[ "$reply" =~ ^[Yy]$ ]] || die "build dependencies are required for the GPU build (or use --cpu)"
    fi
    sudo apt-get install -y "${pkgs[@]}"
  else
    echo "  all present"
  fi

  # 5. Source build. --no-binary is what stops pip substituting the prebuilt CPU wheel.
  #
  #    Named for the one package, NOT `:all:`. `:all:` also applies to the build dependencies, so
  #    pip compiles cmake, ninja and setuptools from source first, single-threaded. Measured: 42
  #    minutes spent entirely inside "Installing build dependencies", never reaching whisper.
  say "Building pywhispercpp with Vulkan"
  echo "  Compiling the Vulkan shaders takes a few minutes. It runs once."
  #    --no-deps keeps --force-reinstall from tearing down and rebuilding the whole dependency
  #    tree: without it pip uninstalls and reinstalls identical versions of numpy, certifi and
  #    idna, which is 40-odd MB of churn and leaves a window where numpy is missing if the build
  #    fails. The follow-up install below is what actually supplies the dependencies.
  #
  #    PIP_CONSTRAINT keeps cmake 4 out of pip's isolated build environment, where apt's cmake is
  #    never used. It only takes effect on the pip pinned in step 2.
  GGML_VULKAN=1 CMAKE_ARGS="-DGGML_VULKAN=ON" PIP_CONSTRAINT="$ROOT/scripts/build-constraints.txt" \
    "$PY" -m pip install --force-reinstall --no-binary pywhispercpp --no-deps pywhispercpp

  # pywhispercpp needs platformdirs, requests and tqdm, and step 2 deliberately skips the [local]
  # extra, so nothing else brings them in. With the package already installed pip reports
  # "Requirement already satisfied" and fills in only what is missing, leaving the source build
  # untouched.
  "$PY" -m pip install pywhispercpp
fi

# 6. RPATH. The libraries sit beside the extension in site-packages, which the loader does not
#    search; $ORIGIN means "look next to me". Never touch libvulkan.so.1: that has to keep
#    resolving to the system ICD loader or it cannot reach the driver.
say "Pointing the extension at its own libraries"
"$PY" -m pip install --quiet patchelf
SITE="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
patched=0
while IFS= read -r -d '' so; do
  [ -L "$so" ] && continue
  "$VENV/bin/patchelf" --set-rpath '$ORIGIN' "$so"
  patched=$((patched + 1))
done < <(find "$SITE" -maxdepth 1 \( -name 'libwhisper*.so*' -o -name 'libggml*.so*' \
  -o -name '_pywhispercpp*.so' \) -print0)
echo "  set \$ORIGIN on $patched libraries"

# 6b. For a packaging build, stop here. The runtime smoke test below needs a driver, so the check
#     that the Vulkan backend really compiled is that its library exists: pip falling back to the
#     prebuilt CPU wheel produces no libggml-vulkan at all.
if [ "$BUILD_ONLY" -eq 1 ]; then
  say "Checking the Vulkan backend compiled"
  compgen -G "$SITE/libggml-vulkan.so*" >/dev/null ||
    die "no libggml-vulkan in $SITE; the build fell back to the CPU wheel"
  echo "  Device selection is left to first run, which is where the GPU is visible."
  exit 0
fi

# 7. Smoke test. The whole point: never report success on a silent CPU fallback.
say "Verifying the GPU backend"
if ! gpu_ready; then
  echo "  The build finished but Model.system_info() shows no ggml_vulkan line, so STT would" >&2
  echo "  silently run on the CPU. See docs/linux-vulkan-build.md, or run with --cpu to accept" >&2
  echo "  the CPU build deliberately." >&2
  exit 1
fi
# ggml's own enumeration, which is the one whisper indexes into. Deliberately not vulkaninfo's:
# ggml drops software rasterisers like llvmpipe, so the two lists disagree and telling the user
# to pick from the wrong one is how you end up on the wrong GPU.
devices=$("$PY" -c 'from pywhispercpp.model import Model; Model.system_info()' 2>&1 |
  grep -E '^ggml_vulkan: [0-9]+ = ' || true)
printf '%s%s%s\n' "$GREEN" "$(echo "$devices" | sed 's/^/  /')" "$RESET"

# 8. Pick the device. Choosing wrong is the third gotcha and it does not look like an error: STT
#    just runs on the iGPU, taking seconds per segment instead of milliseconds.
#
#    "uma" is unified memory, so uma:1 is an integrated GPU sharing system RAM and uma:0 is a
#    discrete card. That labels each entry without guessing from the product name.
names=(); integrated=()
while IFS= read -r line; do
  [ -n "$line" ] || continue
  idx="${line#ggml_vulkan: }"; idx="${idx%% =*}"
  name="${line#*= }"; name="${name%% (*}"
  names[idx]="$name"
  case "$line" in *"uma: 1"*) integrated[idx]=1 ;; *) integrated[idx]=0 ;; esac
done <<< "$devices"

# Update the key in place, or append it, leaving every other line alone. Plain KEY=value, which is
# what config_store._write_env produces and what the engine reads back.
set_gpu_device() {
  local value="$1" key="INTERVIEWR_WHISPER_GPU_DEVICE"
  [ -f "$ROOT/.env" ] || cp "$ROOT/.env.example" "$ROOT/.env"
  if grep -qE "^${key}=" "$ROOT/.env"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ROOT/.env"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ROOT/.env"
  fi
}

# What to suggest, or to choose outright when there is nobody to ask: the sole discrete card if
# there is exactly one, otherwise whisper's own default of device 0.
default=0
discrete=()
for i in "${!names[@]}"; do
  [ "${integrated[i]}" -eq 0 ] && discrete+=("$i")
done
[ "${#discrete[@]}" -eq 1 ] && default="${discrete[0]}"

if [ "${#names[@]}" -le 1 ]; then
  echo "  Transcription will use ${names[0]:-the only device}."
elif [ ! -t 0 ] || [ "$ASSUME_YES" -eq 1 ]; then
  # No terminal, or the user asked not to be asked. Never block on input nobody will give, but
  # do not hand back a config file to edit either: picking the discrete card is what the prompt
  # would have suggested, and leaving whisper on device 0 is the documented way to end up
  # transcribing on the iGPU at seconds per segment instead of milliseconds.
  if [ "${#discrete[@]}" -eq 1 ]; then
    set_gpu_device "$default"
    echo "  Picked ${names[$default]} (device $default) without asking. Change it in Settings."
  else
    echo "  Several GPUs and none is clearly the discrete one, so whisper's device 0 stands."
    echo "  Pick another in Settings if that is the integrated one."
  fi
else
  say "Choose the GPU for transcription"
  for i in "${!names[@]}"; do
    printf '  %s) %-44s %s%s%s\n' "$i" "${names[i]}" "$DIM" \
      "$([ "${integrated[i]}" -eq 1 ] && echo integrated || echo discrete)" "$RESET"
  done
  echo
  while true; do
    read -r -p "  Which should transcription use? [$default] " choice
    choice="${choice:-$default}"
    # The digits test is load-bearing, not belt-and-braces: bash evaluates an array subscript
    # arithmetically, so names[abc] resolves to names[0] and names[-1] to the last element. Both
    # would sail through a bare existence check and write nonsense the engine cannot parse.
    if [[ "$choice" =~ ^[0-9]+$ ]] && [ -n "${names[$choice]+x}" ]; then break; fi
    echo "  Pick one of the numbers listed above."
  done
  set_gpu_device "$choice"
  echo "  Using ${names[$choice]} (device $choice)."
fi
say "Next steps"
# Deliberately no `ollama pull` here. The app ranks the model catalog against the VRAM it detects,
# so naming one up front risks a multi-gigabyte download of the wrong size. It also owns the Ollama
# install command, which lives in ollama_admin.INSTALL_COMMAND and should not be copied.
echo "  Run the overlay:  cd overlay && npm install && npm run tauri dev"
