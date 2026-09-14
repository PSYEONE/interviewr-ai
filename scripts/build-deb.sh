#!/usr/bin/env bash
# Builds the interviewr .deb inside docker (packaging/Dockerfile) and copies it to dist/.
# Building on Ubuntu 24.04 lets one package install on 24.04 and every newer release.
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

command -v docker >/dev/null 2>&1 || die "docker not found"
docker info >/dev/null 2>&1 || die "cannot talk to the docker daemon (is it running, and are you in the docker group?)"

say "Building the image"
# Plain progress prints the full build log.
docker build --progress plain -f "$ROOT/packaging/Dockerfile" -t "$IMAGE" "$ROOT"

say "Extracting the package"
mkdir -p "$OUT"
# Copies the package out of a stopped container made from the image.
container="$(docker create "$IMAGE")"
trap 'docker rm -f "$container" >/dev/null 2>&1 || true' EXIT
docker cp "$container:/out/." "$OUT/"

say "Result"
for deb in "$OUT"/*.deb; do
  [ -e "$deb" ] || die "no .deb was produced"
  printf '  %s (%s)\n' "$(basename "$deb")" "$(du -h "$deb" | cut -f1)"
  # Shows the package's dependencies, including any the bundler added.
  dpkg -I "$deb" 2>/dev/null | sed -n 's/^ Depends: /  depends: /p'
done
echo
echo "  Install with:  sudo apt install $OUT/$(basename "$(ls -t "$OUT"/*.deb | head -1)")"
