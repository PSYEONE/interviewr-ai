#!/usr/bin/env bash
# Build the interviewr .deb in a container and drop it in dist/.
#
# The container is not a convenience. glibc symbols are forward-compatible but not backward, so a
# package built on the maintainer's 26.04 machine installs on 26.04 and nowhere else: one symbol
# (`sqrtf`, re-versioned in glibc 2.43) is enough to pin it. packaging/Dockerfile builds on 24.04
# so a single artifact covers 24.04 through 26.04+, Debian 13, Mint 22+ and Pop.
#
# This script holds no build logic of its own. The workflow in .github/workflows/release.yml calls
# it, so CI and a local run produce the same thing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/dist"
IMAGE="interviewr-deb"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != dumb ]; then
  BOLD=$'\033[1m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
else
  BOLD=''; CYAN=''; RESET=''
fi
say() { printf '\n%s==%s %s%s%s\n' "$CYAN" "$RESET" "$BOLD" "$1" "$RESET"; }
die() { printf 'error: %s\n' "$1" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker not found; it is what pins the glibc floor (see the comment above)"
docker info >/dev/null 2>&1 || die "cannot talk to the docker daemon (is it running, and are you in the docker group?)"

say "Building the image"
# Plain progress so a CI log stays readable; the fancy renderer emits control codes.
docker build --progress plain -f "$ROOT/packaging/Dockerfile" -t "$IMAGE" "$ROOT"

say "Extracting the package"
mkdir -p "$OUT"
# A stopped container is just a filesystem to copy out of, which avoids mounting the host into the
# build and keeps the image the single source of the artifact.
container="$(docker create "$IMAGE")"
trap 'docker rm -f "$container" >/dev/null 2>&1 || true' EXIT
docker cp "$container:/out/." "$OUT/"

say "Result"
for deb in "$OUT"/*.deb; do
  [ -e "$deb" ] || die "no .deb was produced"
  printf '  %s (%s)\n' "$(basename "$deb")" "$(du -h "$deb" | cut -f1)"
  # The control file is worth reading: the bundler adds dependencies of its own on top of the ones
  # tauri.conf.json declares, and a missing one only shows up as a broken install.
  dpkg -I "$deb" 2>/dev/null | sed -n 's/^ Depends: /  depends: /p'
done
echo
echo "  Install with:  sudo apt install $OUT/$(basename "$(ls -t "$OUT"/*.deb | head -1)")"
