#!/usr/bin/env bash
# clawd installer for macOS — sets up ~/.clawd, installs clawd.py,
# and wires up the SwiftBar menu bar plugin if SwiftBar is present.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAWD_DIR="$HOME/.clawd"
SCRIPT="$CLAWD_DIR/clawd.py"

echo "clawd installer"
echo "==============="

# 1. home directory + script ---------------------------------------------------
mkdir -p "$CLAWD_DIR"
if [ ! -f "$SRC_DIR/clawd.py" ]; then
  echo "  ! clawd.py not found next to this installer. Aborting."
  exit 1
fi
cp "$SRC_DIR/clawd.py" "$SCRIPT"
chmod +x "$SCRIPT"
echo "  installed:  $SCRIPT"

# 2. quick sanity check --------------------------------------------------------
if python3 -m py_compile "$SCRIPT" 2>/dev/null; then
  echo "  verified:   python3 syntax OK"
else
  echo "  ! python3 could not compile clawd.py — check your Python install."
  exit 1
fi

# 3. terminal-widget launcher (.command, double-clickable) ---------------------
LAUNCHER="$CLAWD_DIR/clawd-widget.command"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Opens the clawd live widget in a small Terminal window.
python3 "$SCRIPT" --widget
EOF
chmod +x "$LAUNCHER"
echo "  launcher:   $LAUNCHER  (double-click for the live widget)"

# 4. SwiftBar menu bar plugin --------------------------------------------------
echo
if ! command -v swiftbar >/dev/null 2>&1 \
   && [ ! -d "/Applications/SwiftBar.app" ]; then
  echo "  SwiftBar not detected. To enable the menu bar mode:"
  echo "      brew install --cask swiftbar"
  echo "  then re-run this installer."
else
  PLUGIN_DIR="$(defaults read com.ambar.SwiftBar PluginDirectory 2>/dev/null || true)"
  if [ -z "$PLUGIN_DIR" ] || [ ! -d "$PLUGIN_DIR" ]; then
    echo "  SwiftBar is installed but no plugin folder is set."
    echo "  Launch SwiftBar once, choose a plugin folder, then re-run this."
  else
    # 1m = refresh every minute; symlink keeps one source of truth
    ln -sf "$SCRIPT" "$PLUGIN_DIR/clawd.1m.py"
    echo "  menu bar:   linked into $PLUGIN_DIR/clawd.1m.py"
    echo "              (refreshes every minute — SwiftBar may need a reload)"
  fi
fi

# 5. done ----------------------------------------------------------------------
echo
echo "Done. Usage:"
echo "  Live widget   ->  python3 $SCRIPT --widget   (or double-click the .command)"
echo "  Full breakdown->  python3 $SCRIPT --showall"
echo "  Menu bar      ->  automatic via SwiftBar (if linked above)"
echo
echo "First run will tell you fast if the OAuth handshake needs a tweak:"
echo "  progress bars = working   |   'HTTP 401' = see ARCHITECTURE.md section 5"
