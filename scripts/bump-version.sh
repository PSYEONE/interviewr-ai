#!/usr/bin/env bash
# Sets the app version everywhere it is recorded, commits that, and tags the commit vX.Y.Z.
# The version is a committed fact, not something the build guesses, so the number inside the
# package always matches a tag in the history. build-deb.sh warns when the two disagree.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
die() { printf 'error: %s\n' "$1" >&2; exit 1; }

v="${1:-}"
[[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: scripts/bump-version.sh X.Y.Z" >&2; exit 2; }
cd "$ROOT"
git diff --quiet && git diff --cached --quiet || die "commit or stash your changes first"
git rev-parse -q --verify "refs/tags/v$v" >/dev/null && die "tag v$v already exists"

# package.json and package-lock.json together; tauri.conf.json is what the bundler stamps on the .deb.
(cd overlay && npm version "$v" --no-git-tag-version >/dev/null)
sed -i "s/^  \"version\": \"[^\"]*\",/  \"version\": \"$v\",/" overlay/src-tauri/tauri.conf.json
sed -i "0,/^version = \"[^\"]*\"/s//version = \"$v\"/" overlay/src-tauri/Cargo.toml
sed -i "s/^version = \"[^\"]*\"/version = \"$v\"/" backend/pyproject.toml
# The README's install line names the package file, which carries the version.
sed -i -E "s/(interviewr-ai_)[0-9]+\.[0-9]+\.[0-9]+(_amd64\.deb)/\\1$v\\2/" README.md
# Refreshes only our own entry in Cargo.lock, without touching dependency versions.
(cd overlay/src-tauri && cargo update -p interviewr-ai --offline --quiet)

git add overlay/package.json overlay/package-lock.json overlay/src-tauri/tauri.conf.json \
  overlay/src-tauri/Cargo.toml overlay/src-tauri/Cargo.lock backend/pyproject.toml README.md
git commit -q -m "release $v"
git tag -a "v$v" -m "interviewr-ai $v"
echo "committed and tagged v$v"
echo "next: ./scripts/build-deb.sh, then gh release create v$v dist/*.deb --title 'interviewr-ai $v' --notes-file <notes>"
echo "      and git push origin main --tags"
