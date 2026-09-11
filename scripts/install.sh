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
VENV="$BACKEND/.venv"
PY="$VENV/bin/python"

WANT_GPU=1
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --cpu) WANT_GPU=0 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --help|-h)
      echo "usage: scripts/install.sh [--cpu] [--yes]"
      echo "  --cpu   skip the Vulkan build and use the CPU wheel"
      echo "  --yes   don't prompt before installing system build dependencies"
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
for tool in pw-record pactl; do
  have "$tool" || die "$tool not found; the engine needs PipeWire (package: pipewire-utils / pulseaudio-utils)"
done
echo "  python $(python3 -V 2>&1 | cut -d' ' -f2), PipeWire tools present"

# 2. Venv. Reused when it already exists; pip decides if anything needs installing.
#
# Deliberately without the [local] extra, which is just pywhispercpp: installing it here would let
# pip drop the prebuilt CPU wheel on top of an existing source build, silently undoing the GPU
# backend and costing a half-hour rebuild. Whichever path below runs owns that package instead.
say "Installing the engine into backend/.venv"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$PY" -m pip install --quiet --upgrade pip
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
  "$PY" -m pip install --force-reinstall pywhispercpp
  echo "  CPU build installed. Transcription will be slower but works on any machine."
  exit 0
fi

# 3. Vulkan detection. No Vulkan means no GPU path at all, and saying so is better than
#    producing a CPU build the user believes is GPU-accelerated.
say "Looking for Vulkan"
if ! have vulkaninfo || ! vulkaninfo --summary >/dev/null 2>&1; then
  echo "  No working Vulkan runtime found."
  echo "  Install your GPU's Vulkan driver (mesa-vulkan-drivers, or the NVIDIA driver), then"
  echo "  re-run. To proceed on the CPU instead: scripts/install.sh --cpu"
  exit 1
fi
vulkaninfo --summary 2>/dev/null | sed -n 's/^\s*deviceName\s*=\s*/  GPU: /p'

if gpu_ready; then
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
  #    Not --no-deps: pywhispercpp pulls platformdirs, requests and tqdm, and step 2 deliberately
  #    does not install the [local] extra, so nothing else would bring them in. They come from
  #    wheels, since --no-binary names only pywhispercpp.
  GGML_VULKAN=1 CMAKE_ARGS="-DGGML_VULKAN=ON" \
    "$PY" -m pip install --force-reinstall --no-binary pywhispercpp pywhispercpp
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

if [ "${#names[@]}" -le 1 ]; then
  echo "  Transcription will use ${names[0]:-the only device}."
elif [ "$ASSUME_YES" -eq 1 ] || [ ! -t 0 ]; then
  # Never block a scripted run waiting for an answer nobody is there to give.
  echo
  echo "  More than one GPU. Set INTERVIEWR_WHISPER_GPU_DEVICE in .env to the number of the one"
  echo "  you want; whisper uses device 0 otherwise, which on a laptop is usually the integrated."
else
  say "Choose the GPU for transcription"
  default=0
  discrete=()
  for i in "${!names[@]}"; do
    [ "${integrated[i]}" -eq 0 ] && discrete+=("$i")
    printf '  %s) %-44s %s%s%s\n' "$i" "${names[i]}" "$DIM" \
      "$([ "${integrated[i]}" -eq 1 ] && echo integrated || echo discrete)" "$RESET"
  done
  [ "${#discrete[@]}" -eq 1 ] && default="${discrete[0]}"
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
  echo "  Set INTERVIEWR_WHISPER_GPU_DEVICE=$choice in .env (${names[$choice]})."
fi
say "Next steps"
echo "  1. Install Ollama and pull a model:  ollama pull qwen3:8b"
echo "  2. Build and run the overlay:        cd overlay && npm install && npm run tauri dev"
echo "  The setup wizard handles the rest, including downloading a whisper model."
