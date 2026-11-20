#!/usr/bin/env bash
# Non-interactive bootstrap: ensure repo, deps, systemd service, and start the app.
# Safe to re-run. Canonical install path: ~/pecan-vision-kit (not the Desktop).
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script needs bash. Do not run it with sh or sudo sh." >&2
  exit 1
fi
set -euo pipefail

REPO_URL="${PECAN_REPO_URL:-https://github.com/salirezav/pecan_vision_kit.git}"
USER_NAME="${SUDO_USER:-$USER}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
KIT_ROOT="${PECAN_KIT_ROOT:-$HOME_DIR/pecan-vision-kit}"
SERVICE_SRC="$KIT_ROOT/systemd/pecan-vision.service"
SUDOERS_SRC="$KIT_ROOT/config/sudoers-pecan-vision"
VENV="$KIT_ROOT/.venv"
MOUNT_POINT="/mnt/recordings"
SERVICE="pecan-vision.service"

# If this script already lives inside a checkout, prefer that tree.
_SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$_SCRIPT_ROOT/app/server.py" && -z "${PECAN_KIT_ROOT:-}" ]]; then
  KIT_ROOT="$_SCRIPT_ROOT"
  SERVICE_SRC="$KIT_ROOT/systemd/pecan-vision.service"
  SUDOERS_SRC="$KIT_ROOT/config/sudoers-pecan-vision"
  VENV="$KIT_ROOT/.venv"
fi

RED=$'\033[31m'; GREEN=$'\033[32m'; YEL=$'\033[33m'; CYA=$'\033[36m'; BLD=$'\033[1m'; RST=$'\033[0m'
info() { printf '%s==>%s %s\n' "$CYA" "$RST" "$*"; }
ok()   { printf '%s[ok]%s %s\n' "$GREEN" "$RST" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$YEL" "$RST" "$*"; }
err()  { printf '%s[error]%s %s\n' "$RED" "$RST" "$*" >&2; }

need_sudo() {
  if [[ "$(id -u)" -eq 0 ]]; then
    err "Run as the kit user (not root). The script will ask for sudo when needed."
    exit 1
  fi
  if ! sudo -v; then
    err "sudo failed — cannot continue."
    exit 1
  fi
  ( while true; do sudo -n true; sleep 50; done ) &
  SUDO_KEEPER=$!
  trap 'kill '"$SUDO_KEEPER"' 2>/dev/null || true' EXIT
}

ensure_repo() {
  if [[ -f "$KIT_ROOT/app/server.py" ]]; then
    ok "Repo present: $KIT_ROOT"
    return
  fi
  if [[ -e "$KIT_ROOT" && ! -d "$KIT_ROOT/.git" ]]; then
    err "$KIT_ROOT exists but is not a Pecan Vision Kit checkout"
    exit 1
  fi
  info "Cloning $REPO_URL → $KIT_ROOT"
  mkdir -p "$(dirname "$KIT_ROOT")"
  git clone "$REPO_URL" "$KIT_ROOT"
  ok "Cloned into $KIT_ROOT"
}

ensure_packages() {
  info "Ensuring OS packages…"
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3-venv python3-pip python3-opencv python3-yaml \
    v4l-utils ffmpeg \
    network-manager \
    curl ca-certificates git
  ok "Packages ready"
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi
  local user_uv="$HOME_DIR/.local/bin/uv"
  if [[ -x "$user_uv" ]]; then
    export PATH="$HOME_DIR/.local/bin:$PATH"
    return
  fi
  info "Installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME_DIR/.local/bin" sh
  export PATH="$HOME_DIR/.local/bin:$PATH"
}

ensure_venv() {
  info "Ensuring Python virtualenv…"
  ensure_uv
  if [[ ! -d "$VENV" ]]; then
    uv venv --python python3 --system-site-packages "$VENV"
  fi
  uv sync --directory "$KIT_ROOT" --frozen --inexact --no-install-project
  ok "Virtualenv ready: $VENV"
}

ensure_recordings_mount() {
  if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    sudo mkdir -p "$MOUNT_POINT/videos"
    sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT" || true
    ok "Recordings mount: $MOUNT_POINT"
    return
  fi
  warn "$MOUNT_POINT is not mounted — service unit RequiresMountsFor may fail until NVMe is prepared."
  warn "Run ./scripts/setup-kit.sh and choose NVMe / recordings disk if needed."
  sudo mkdir -p "$MOUNT_POINT/videos" 2>/dev/null || true
}

render_and_install_service() {
  info "Installing systemd service for user $USER_NAME…"
  [[ -f "$SERVICE_SRC" ]] || { err "Missing $SERVICE_SRC"; exit 1; }
  local tmp sudoers_tmp
  tmp="$(mktemp)"
  sed \
    -e "s|@KIT_ROOT@|$KIT_ROOT|g" \
    -e "s|@KIT_USER@|$USER_NAME|g" \
    "$SERVICE_SRC" > "$tmp"
  sudo cp "$tmp" /etc/systemd/system/pecan-vision.service
  rm -f "$tmp"

  sudoers_tmp="$(mktemp)"
  sed \
    -e "s|@KIT_ROOT@|$KIT_ROOT|g" \
    -e "s|@KIT_USER@|$USER_NAME|g" \
    "$SUDOERS_SRC" > "$sudoers_tmp"
  sudo cp "$sudoers_tmp" /etc/sudoers.d/pecan-vision
  sudo chmod 440 /etc/sudoers.d/pecan-vision
  rm -f "$sudoers_tmp"
  if ! sudo visudo -cf /etc/sudoers.d/pecan-vision; then
    err "sudoers file invalid — removing"
    sudo rm -f /etc/sudoers.d/pecan-vision
    exit 1
  fi

  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE"
  ok "Service installed"
}

start_service() {
  info "Starting $SERVICE…"
  if ! sudo systemctl restart "$SERVICE"; then
    err "Service failed to start. Check: journalctl -u pecan-vision -u mnt-recordings.mount"
    sudo systemctl --no-pager --full status "$SERVICE" || true
    return 1
  fi
  sleep 1
  if systemctl is-active --quiet "$SERVICE"; then
    ok "Service is active"
  else
    warn "Service may still be starting"
    systemctl --no-pager --full status "$SERVICE" || true
  fi
}

install_launchers_if_present() {
  local installer="$KIT_ROOT/scripts/install-desktop-launchers.sh"
  if [[ -x "$installer" ]]; then
    info "Refreshing Desktop launchers…"
    "$installer" || warn "Desktop launcher install had issues (non-fatal)"
  fi
}

main() {
  cat <<EOF

${BLD}Pecan Vision Kit — bootstrap${RST}
User: $USER_NAME
Path: $KIT_ROOT

EOF
  need_sudo
  ensure_repo
  chmod +x "$KIT_ROOT/scripts/"*.sh 2>/dev/null || true
  ensure_packages
  ensure_venv
  ensure_recordings_mount
  render_and_install_service
  start_service
  install_launchers_if_present

  cat <<EOF

${GREEN}${BLD}Bootstrap complete.${RST}
  Web UI:  http://127.0.0.1:8080
  Admin:   http://127.0.0.1:8080/admin
  Service: sudo systemctl status pecan-vision

For AP / NVMe format / kiosk options, run the interactive installer:
  $KIT_ROOT/scripts/setup-kit.sh

EOF
}

main "$@"
