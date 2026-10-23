#!/usr/bin/env bash
# install.sh — install (or --uninstall) nosleep for the current user.
#
#   code      -> ~/.local/lib/nosleep/
#   launcher  -> ~/.local/bin/nosleep
#   icons     -> ~/.local/share/icons/hicolor/scalable/status/nosleep-{awake,asleep}.svg
#   .desktop  -> ~/.local/share/applications/nosleep.desktop
#   service   -> ~/.config/systemd/user/nosleep.service  (enabled + started)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/lib/nosleep"
BIN_DIR="$HOME/.local/bin"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/status"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PYTHON="${PYTHON:-/usr/bin/python3}"

if [[ "${1:-}" == "--uninstall" ]]; then
    echo "==> Stopping and disabling service ..."
    systemctl --user disable --now nosleep.service 2>/dev/null || true
    rm -f "$SERVICE_DIR/nosleep.service"
    systemctl --user daemon-reload
    echo "==> Removing files ..."
    rm -rf "$INSTALL_DIR"
    rm -f "$BIN_DIR/nosleep" "$APP_DIR/nosleep.desktop" \
          "$ICON_DIR/nosleep-awake.svg" "$ICON_DIR/nosleep-asleep.svg"
    echo "Done. Config left in ${XDG_CONFIG_HOME:-$HOME/.config}/nosleep/ — delete it if you want."
    exit 0
fi

echo "==> Checking dependencies ..."
if ! "$PYTHON" -c "import PySide6.QtDBus, PySide6.QtSvg, PySide6.QtWidgets" 2>/dev/null; then
    echo "PySide6 (with QtDBus/QtSvg) is required. On Arch/CachyOS:"
    echo "    sudo pacman -S --needed pyside6"
    exit 1
fi

echo "==> Installing code to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
rm -rf "$INSTALL_DIR/nosleep"
cp -r "$HERE/nosleep" "$INSTALL_DIR/nosleep"
find "$INSTALL_DIR" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> Installing launcher to $BIN_DIR/nosleep ..."
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/nosleep" <<LAUNCHER
#!$PYTHON
import sys
sys.path.insert(0, "$INSTALL_DIR")
from nosleep.__main__ import main
sys.exit(main())
LAUNCHER
chmod +x "$BIN_DIR/nosleep"

echo "==> Installing icons ..."
mkdir -p "$ICON_DIR"
cp "$HERE/nosleep/icons/eye-open.svg"   "$ICON_DIR/nosleep-awake.svg"
cp "$HERE/nosleep/icons/eye-closed.svg" "$ICON_DIR/nosleep-asleep.svg"
touch "${ICON_DIR%/scalable/status}"   # nudge icon-theme caches

echo "==> Installing desktop entry ..."
mkdir -p "$APP_DIR"
# Plasma's session PATH usually lacks ~/.local/bin, so write the absolute path.
sed "s|^Exec=nosleep |Exec=$BIN_DIR/nosleep |" "$HERE/nosleep.desktop" > "$APP_DIR/nosleep.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APP_DIR" || true

echo "==> Installing systemd user service ..."
mkdir -p "$SERVICE_DIR"
cp "$HERE/nosleep.service" "$SERVICE_DIR/nosleep.service"
systemctl --user daemon-reload
systemctl --user enable nosleep.service
if systemctl --user is-active --quiet nosleep.service; then
    systemctl --user restart nosleep.service
else
    systemctl --user start nosleep.service
fi

echo ""
echo "Done. nosleep is running; look for the eye icon in the system tray."
echo "  status:   nosleep --status"
echo "  settings: nosleep --settings"
echo "  logs:     journalctl --user -u nosleep -f"
