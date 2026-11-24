#!/usr/bin/env bash
# Install Desktop (.desktop) launchers that call repo scripts.
# Canonical app tree stays at ~/pecan-vision-kit — only shortcuts go on the Desktop.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script needs bash." >&2
  exit 1
fi
set -euo pipefail

USER_NAME="${SUDO_USER:-$USER}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
KIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP_DIR="${PECAN_DESKTOP_DIR:-$HOME_DIR/Desktop}"
APPS_DIR="$HOME_DIR/.local/share/applications"
WRAPPER_DIR="$HOME_DIR/.local/share/pecan-vision"

GREEN=$'\033[32m'; CYA=$'\033[36m'; RST=$'\033[0m'
info() { printf '%s==>%s %s\n' "$CYA" "$RST" "$*"; }
ok()   { printf '%s[ok]%s %s\n' "$GREEN" "$RST" "$*"; }

mkdir -p "$DESKTOP_DIR" "$APPS_DIR" "$WRAPPER_DIR"
chmod +x "$KIT_ROOT/scripts/"*.sh 2>/dev/null || true

# Thin wrappers keep .desktop Exec= lines simple (no nested quoting).
write_wrapper() {
  local name="$1"
  local script="$2"
  local path="$WRAPPER_DIR/$name"
  cat > "$path" <<EOF
#!/usr/bin/env bash
set -euo pipefail
"$script"
status=\$?
echo
read -r -p "Press Enter to close…" _ || true
exit "\$status"
EOF
  chmod +x "$path"
  printf '%s' "$path"
}

write_launcher() {
  local filename="$1"
  local name="$2"
  local comment="$3"
  local wrapper="$4"
  local icon="$5"
  local body
  body=$(cat <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=$name
Comment=$comment
Exec=x-terminal-emulator -e "$wrapper"
Icon=$icon
Terminal=false
Categories=Utility;
StartupNotify=true
EOF
)
  printf '%s\n' "$body" > "$DESKTOP_DIR/$filename"
  chmod +x "$DESKTOP_DIR/$filename"
  printf '%s\n' "$body" > "$APPS_DIR/$filename"
  chmod +x "$APPS_DIR/$filename"
  ok "$DESKTOP_DIR/$filename"
}

info "Installing Desktop launchers for $USER_NAME → $KIT_ROOT"

W_START="$(write_wrapper start.sh "$KIT_ROOT/scripts/desktop-start.sh")"
W_UPDATE="$(write_wrapper update.sh "$KIT_ROOT/scripts/desktop-update.sh")"
W_SETUP="$(write_wrapper setup.sh "$KIT_ROOT/scripts/desktop-setup.sh")"

write_launcher \
  "Start Pecan Vision.desktop" \
  "Start Pecan Vision" \
  "Ensure install, start the service, open the web UI" \
  "$W_START" \
  "applications-internet"

write_launcher \
  "Update Pecan Vision.desktop" \
  "Update Pecan Vision" \
  "Install the latest GitHub Release (same as the dashboard Update button)" \
  "$W_UPDATE" \
  "system-software-update"

write_launcher \
  "Setup Pecan Vision.desktop" \
  "Setup / Repair Pecan Vision" \
  "Interactive installer (packages, NVMe, AP, kiosk, service)" \
  "$W_SETUP" \
  "preferences-system"

if command -v gio >/dev/null 2>&1; then
  for f in \
    "$DESKTOP_DIR/Start Pecan Vision.desktop" \
    "$DESKTOP_DIR/Update Pecan Vision.desktop" \
    "$DESKTOP_DIR/Setup Pecan Vision.desktop"
  do
    gio set "$f" metadata::trusted true 2>/dev/null || true
  done
fi

ok "Desktop launchers installed"
echo "  App tree stays at: $KIT_ROOT"
echo "  Shortcuts only:    $DESKTOP_DIR"
