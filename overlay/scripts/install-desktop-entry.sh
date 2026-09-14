#!/usr/bin/env bash
# Installs a desktop entry for running from source, which the XDG portal needs before global shortcuts work; safe to re-run.
set -euo pipefail

# Must match APP_ID in src-tauri/src/appid.rs and productName in tauri.conf.json.
APP_ID="interviewr-overlay"
DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
FILE="$DIR/$APP_ID.desktop"

# Point Exec at a built binary if there is one, so launching from the app menu works too.
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

# Remove the entry an older version of this script created under a different name.
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
