#!/usr/bin/env bash
# Sets up the engine and the Vulkan GPU build of pywhispercpp (details in docs/linux-vulkan-build.md); safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
# The .deb build sets INTERVIEWR_VENV to install into its bundled Python instead.
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

# Colour output only in an interactive terminal, and not when NO_COLOR is set.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != dumb ]; then
  BOLD=$'\033[1m'; CYAN=$'\033[36m'; GREEN=$'\033[32m'; RED=$'\033[31m'
  DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=''; CYAN=''; GREEN=''; RED=''; DIM=''; RESET=''
fi

say() { printf '\n%s==%s %s%s%s\n' "$CYAN" "$RESET" "$BOLD" "$1" "$RESET"; }
have() { command -v "$1" >/dev/null 2>&1; }
die() { printf '%serror:%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }

# 1. Preflight: check the basics before installing anything.
say "Checking prerequisites"
[ "$(uname -s)" = "Linux" ] || die "this installer is Linux-only (the engine captures via PipeWire)"
have python3 || die "python3 not found"
python3 - <<'EOF' || die "python 3.12+ required (found $(python3 -V 2>&1))"
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
EOF
# PipeWire is only needed to run the engine, so a packaging build skips the check.
if [ "$BUILD_ONLY" -eq 0 ]; then
  for tool in pw-record pactl; do
    have "$tool" || die "$tool not found; the engine needs PipeWire (package: pipewire-utils / pulseaudio-utils)"
  done
  echo "  python $(python3 -V 2>&1 | cut -d' ' -f2), PipeWire tools present"
else
  echo "  python $(python3 -V 2>&1 | cut -d' ' -f2), build-only (skipping runtime checks)"
fi

# 2. Venv: installs the engine without the [local] extra, which would overwrite a GPU build with the CPU-only wheel.
say "Installing the engine into $VENV"
[ -d "$VENV" ] || python3 -m venv "$VENV"
# Keep pip below 26: newer pip ignores PIP_CONSTRAINT for build tools, which step 5 relies on.
"$PY" -m pip install --quiet 'pip<26'
# Not --quiet, so a first install shows download progress instead of looking stuck.
"$PY" -m pip install -e "$BACKEND"

# Checks for the ggml_vulkan line, which ggml prints to stderr (system_info()'s return value never contains it).
gpu_backend_line() {
  "$PY" -c 'from pywhispercpp.model import Model; Model.system_info()' 2>&1 | grep -m1 ggml_vulkan
}

gpu_ready() {
  gpu_backend_line >/dev/null 2>&1
}

if [ "$WANT_GPU" -eq 0 ]; then
  say "Skipping the GPU build (--cpu)"
  # --no-deps avoids reinstalling every dependency; the second install fills in anything missing.
  "$PY" -m pip install --force-reinstall --no-deps pywhispercpp
  "$PY" -m pip install pywhispercpp
  echo "  CPU build installed. Transcription will be slower but works on any machine."
  exit 0
fi

# 3. Vulkan detection: stop here rather than building a CPU-only version the user thinks is using the GPU.
# Skipped for --build-only, since a build container has no GPU and doesn't need one to compile.
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
  # Debian/Ubuntu package names; glslang-tools and spirv-headers are required from pywhispercpp 1.5.1 onward.
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

  # 5. Build from source: --no-binary stops pip using the CPU-only wheel (name the package, not :all:, or pip compiles cmake too).
  say "Building pywhispercpp with Vulkan"
  echo "  Compiling the Vulkan shaders takes a few minutes. It runs once."
  # --no-deps avoids reinstalling every dependency; PIP_CONSTRAINT keeps the build on cmake 3 (see build-constraints.txt).
  GGML_VULKAN=1 CMAKE_ARGS="-DGGML_VULKAN=ON" PIP_CONSTRAINT="$ROOT/scripts/build-constraints.txt" \
    "$PY" -m pip install --force-reinstall --no-binary pywhispercpp --no-deps pywhispercpp

  # Installs pywhispercpp's own dependencies without touching the source build.
  "$PY" -m pip install pywhispercpp
fi

# 6. Sets an $ORIGIN RPATH so the compiled libraries find each other in site-packages (libvulkan.so.1 is left alone).
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

# 6b. Packaging build stops here: checks the Vulkan library was built, since the smoke test below needs a GPU.
if [ "$BUILD_ONLY" -eq 1 ]; then
  say "Checking the Vulkan backend compiled"
  compgen -G "$SITE/libggml-vulkan.so*" >/dev/null ||
    die "no libggml-vulkan in $SITE; the build fell back to the CPU wheel"
  echo "  Device selection is left to first run, which is where the GPU is visible."
  exit 0
fi

# 7. Smoke test: fail rather than silently falling back to the CPU.
say "Verifying the GPU backend"
if ! gpu_ready; then
  echo "  The build finished but Model.system_info() shows no ggml_vulkan line, so STT would" >&2
  echo "  silently run on the CPU. See docs/linux-vulkan-build.md, or run with --cpu to accept" >&2
  echo "  the CPU build deliberately." >&2
  exit 1
fi
# Uses ggml's device list rather than vulkaninfo's, because that's the numbering whisper uses.
devices=$("$PY" -c 'from pywhispercpp.model import Model; Model.system_info()' 2>&1 |
  grep -E '^ggml_vulkan: [0-9]+ = ' || true)
printf '%s%s%s\n' "$GREEN" "$(echo "$devices" | sed 's/^/  /')" "$RESET"

# 8. Pick the GPU for transcription; "uma: 1" marks an integrated GPU and "uma: 0" a discrete one.
names=(); integrated=()
while IFS= read -r line; do
  [ -n "$line" ] || continue
  idx="${line#ggml_vulkan: }"; idx="${idx%% =*}"
  name="${line#*= }"; name="${name%% (*}"
  names[idx]="$name"
  case "$line" in *"uma: 1"*) integrated[idx]=1 ;; *) integrated[idx]=0 ;; esac
done <<< "$devices"

# Sets INTERVIEWR_WHISPER_GPU_DEVICE in .env, updating the existing line if there is one.
set_gpu_device() {
  local value="$1" key="INTERVIEWR_WHISPER_GPU_DEVICE"
  [ -f "$ROOT/.env" ] || cp "$ROOT/.env.example" "$ROOT/.env"
  if grep -qE "^${key}=" "$ROOT/.env"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ROOT/.env"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ROOT/.env"
  fi
}

# Default choice: the only discrete GPU if there is exactly one, otherwise device 0.
default=0
discrete=()
for i in "${!names[@]}"; do
  [ "${integrated[i]}" -eq 0 ] && discrete+=("$i")
done
[ "${#discrete[@]}" -eq 1 ] && default="${discrete[0]}"

if [ "${#names[@]}" -le 1 ]; then
  echo "  Transcription will use ${names[0]:-the only device}."
elif [ ! -t 0 ] || [ "$ASSUME_YES" -eq 1 ]; then
  # No terminal, or --yes: pick the discrete GPU automatically instead of prompting.
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
    # Digits only: bash would read names[abc] as names[0] and names[-1] as the last entry.
    if [[ "$choice" =~ ^[0-9]+$ ]] && [ -n "${names[$choice]+x}" ]; then break; fi
    echo "  Pick one of the numbers listed above."
  done
  set_gpu_device "$choice"
  echo "  Using ${names[$choice]} (device $choice)."
fi
say "Next steps"
# No ollama pull here: the app recommends a model that fits the GPU's VRAM on first run.
echo "  Run the overlay:  cd overlay && npm install && npm run tauri dev"
