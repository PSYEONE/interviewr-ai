#!/usr/bin/env bash
# Give the overlay a desktop entry, which the XDG portal needs before it will name us.
#
# The portal derives an app id from the systemd scope the process runs in (the app claims one
# itself, see src-tauri/src/appid.rs) and then refuses to use that id unless a matching .desktop
# file exists. Packaged builds get one from the Tauri bundle, which names it after productName;
# this installs the same file for a run from source. Idempotent; re-run safely.
set -euo pipefail

# Must match APP_ID in src-tauri/src/appid.rs and productName in tauri.conf.json, because that is
# the file name tauri-bundler writes and therefore the id an installed build resolves to.
APP_ID="interviewr-overlay"
DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
FILE="$DIR/$APP_ID.desktop"

# Point Exec at the built binary when one exists, so launching from the menu works too. The
# desktop entry's identity matters even when it is never launched from here.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec_path="$root/src-tauri/target/release/$APP_ID"
[ -x "$exec_path" ] || exec_path="$root/src-tauri/target/debug/$APP_ID"
[ -x "$exec_path" ] || exec_path="$APP_ID"

mkdir -p "$DIR"
cat >"$FILE" <<EOF
[Desktop Entry]
Type=Application
Name=interviewr
Comment=Real-time interview and meeting copilot
Exec=$exec_path
Icon=$root/src-tauri/icons/128x128.png
Terminal=false
Categories=Utility;
StartupWMClass=$APP_ID
EOF

# An earlier version of this script installed the entry under the bundle identifier. No scope name
# ever asks for that one, so it is dead weight and a second menu entry.
rm -f "$DIR/dev.interviewr.overlay.desktop"

# Refresh the desktop database so the portal sees the entry without a re-login.
if command -v update-desktop-database >/dev/null; then
  update-desktop-database "$DIR" 2>/dev/null || true
fi

echo "Installed $FILE (Exec=$exec_path)."
echo
echo "Restart the overlay, then re-bind a shortcut and check which component it lands in:"
echo "  grep -A8 '\\[$APP_ID\\]' ~/.config/kglobalshortcutsrc"
echo "If they still land under the launching app, the scope was not claimed; say so rather than"
echo "assuming it worked."
