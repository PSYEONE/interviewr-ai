#!/usr/bin/env bash
# Keep the interviewr overlay always-on-top on KDE/KWin.
#
# Always-on-top cannot come from the toolkit on native Wayland (GTK ignores keep-above),
# so we add a KWin window rule that forces it. Idempotent; re-run safely. Pass the window
# class as $1 if yours differs from the default (verify via System Settings > Window
# Management > Window Rules > New > "Detect Window Properties", click the overlay).
set -euo pipefail

WMCLASS="${1:-interviewr-overlay}"
GROUP="b6a9f2c1-7e3d-4a8b-9c2e-0123456789ab"  # fixed -> re-running updates, never duplicates
FILE="kwinrulesrc"

kw=$(command -v kwriteconfig6 || command -v kwriteconfig5 || true)
kr=$(command -v kreadconfig6 || command -v kreadconfig5 || true)
if [[ -z "$kw" || -z "$kr" ]]; then
  echo "error: needs KDE's kwriteconfig6/5 (are you on KDE Plasma?)" >&2
  exit 1
fi

"$kw" --file "$FILE" --group "$GROUP" --key Description "interviewr overlay - keep above"
"$kw" --file "$FILE" --group "$GROUP" --key wmclass "$WMCLASS"
"$kw" --file "$FILE" --group "$GROUP" --key wmclassmatch 1      # 1 = exact match
"$kw" --file "$FILE" --group "$GROUP" --key wmclasscomplete false
"$kw" --file "$FILE" --group "$GROUP" --key above true
"$kw" --file "$FILE" --group "$GROUP" --key aboverule 2         # 2 = Force

# Register the rule group in [General] (rules is a comma-separated list; count must match).
rules=$("$kr" --file "$FILE" --group General --key rules 2>/dev/null || true)
if [[ ",${rules}," != *",${GROUP},"* ]]; then
  [[ -z "$rules" ]] && rules="$GROUP" || rules="${rules},${GROUP}"
  "$kw" --file "$FILE" --group General --key rules "$rules"
  "$kw" --file "$FILE" --group General --key count "$(awk -F, '{print NF}' <<<"$rules")"
fi

# Apply without logging out.
if command -v qdbus6 >/dev/null; then qdbus6 org.kde.KWin /KWin reconfigure
elif command -v qdbus >/dev/null; then qdbus org.kde.KWin /KWin reconfigure
else dbus-send --type=method_call --dest=org.kde.KWin /KWin org.kde.KWin.reconfigure; fi

echo "Installed KWin keep-above rule for WM_CLASS='$WMCLASS'."
echo "If the overlay still isn't pinned, its class differs - re-run with the real one:"
echo "  ./install-kwin-rule.sh <class>   (find it via Window Rules > Detect Window Properties)"
