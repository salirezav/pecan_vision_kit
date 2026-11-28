<<<<<<< HEAD
#!/usr/bin/env bash
# Non-interactive bootstrap: ensure repo, deps, systemd service, and start the app.
# Safe to re-run. Canonical install path: ~/pecan-vision-kit (not the Desktop).
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script needs bash. Do not run it with sh or sudo sh." >&2
  exit 1
fi
set -euo pipefail

REPO_URL="${PECAN_REPO_URL:-https://github.com/salirezav/pecan_vision_kit.git}"
# Use the account that is actually running this script. `sudo -u pecan` leaves
# SUDO_USER set to the caller (for example engr-ugaif), which is not the kit user.
USER_NAME="$(id -un)"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
KIT_ROOT="${PECAN_KIT_ROOT:-$HOME_DIR/pecan-vision-kit}"
SERVICE_SRC="$KIT_ROOT/systemd/pecan-vision.service"
SUDOERS_SRC="$KIT_ROOT/config/sudoers-pecan-vision"
VENV="$KIT_ROOT/.venv"
MOUNT_POINT="/mnt/recordings"
NVME_DEV="/dev/nvme0n1"
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

ensure_kit_ownership() {
  # Common failure: repo was cloned/copied as root or engr-ugaif, then pecan cannot create .venv.
  local owner
  owner="$(stat -c '%U' "$KIT_ROOT" 2>/dev/null || true)"
  if [[ -n "$owner" && "$owner" != "$USER_NAME" ]]; then
    warn "$KIT_ROOT is owned by $owner — fixing ownership for $USER_NAME"
    sudo chown -R "$USER_NAME:$USER_NAME" "$KIT_ROOT"
  fi
  if [[ ! -w "$KIT_ROOT" ]]; then
    err "$KIT_ROOT is not writable by $USER_NAME"
    err "Fix with:  sudo chown -R $USER_NAME:$USER_NAME $KIT_ROOT"
    exit 1
  fi
}

ensure_packages() {
  info "Ensuring OS packages…"
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3-venv python3-pip python3-opencv python3-yaml \
    v4l-utils ffmpeg \
    network-manager \
    curl ca-certificates git \
    hailo-all
  ok "Packages ready"
  if [[ ! -e /dev/hailo0 ]]; then
    warn "Hailo packages installed but /dev/hailo0 is missing — reboot once so the PCIe driver loads (needed for .hef models)."
  else
    ok "Hailo device present: /dev/hailo0"
  fi
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
  # 0.13.x extracts wheels as empty files on this Pi and uv sync then fails.
  curl -LsSf https://astral.sh/uv/0.9.17/install.sh | env UV_INSTALL_DIR="$HOME_DIR/.local/bin" sh
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

resolve_block_dev() {
  local src="$1" candidate=""
  [[ -z "$src" ]] && return 1
  case "$src" in
    UUID=*) candidate="/dev/disk/by-uuid/${src#UUID=}" ;;
    PARTUUID=*) candidate="/dev/disk/by-partuuid/${src#PARTUUID=}" ;;
    LABEL=*) candidate="/dev/disk/by-label/${src#LABEL=}" ;;
    /dev/*) candidate="$src" ;;
    *) candidate="$src" ;;
  esac
  if [[ -e "$candidate" ]]; then
    readlink -f "$candidate" 2>/dev/null || printf '%s' "$candidate"
    return 0
  fi
  printf '%s' "$src"
}

disk_backs_path() {
  local disk="$1" path="$2" src parent disk_base
  src="$(findmnt -no SOURCE "$path" 2>/dev/null || true)"
  [[ -z "$src" ]] && return 1
  src="$(resolve_block_dev "$src")"
  disk_base="${disk#/dev/}"
  parent="$(lsblk -no PKNAME "$src" 2>/dev/null | head -1 | tr -d '[:space:]')"
  if [[ -n "$parent" ]]; then
    [[ "$parent" == "$disk_base" ]]
    return
  fi
  [[ "$src" == "$disk" || "$src" == "$disk"* ]]
}

is_system_disk() {
  local disk="$1"
  disk_backs_path "$disk" "/" \
    || disk_backs_path "$disk" "/boot" \
    || disk_backs_path "$disk" "/boot/firmware"
}

ensure_recordings_mount() {
  # Non-interactive: never format or unmount. Prefer existing spare-NVMe mount;
  # otherwise use /mnt/recordings as a directory on the OS disk (NVMe-only kits).
  info "Ensuring recordings path at $MOUNT_POINT…"

  if is_system_disk "$NVME_DEV"; then
    warn "$NVME_DEV is the OS disk — not formatting or mounting it as a data volume."
    if awk '{print $2}' /etc/fstab 2>/dev/null | grep -qx "$MOUNT_POINT"; then
      warn "Removing $MOUNT_POINT from /etc/fstab (will not unmount if currently mounted)."
      sudo sed -i "\\|[[:space:]]${MOUNT_POINT}[[:space:]]|d" /etc/fstab
      sudo systemctl daemon-reload || true
    fi
    sudo mkdir -p "$MOUNT_POINT/videos" "$MOUNT_POINT/models"
    sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT" || true
    ok "Recordings directory on root filesystem: $MOUNT_POINT"
    return
  fi

  if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    sudo mkdir -p "$MOUNT_POINT/videos" "$MOUNT_POINT/models"
    sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT" || true
    ok "Recordings mount present: $MOUNT_POINT"
    return
  fi

  # Spare NVMe with existing filesystem and fstab — try mount once, never format.
  if [[ -b "${NVME_DEV}p1" ]] && ! is_system_disk "$NVME_DEV"; then
    sudo mkdir -p "$MOUNT_POINT"
    if sudo mount "$MOUNT_POINT" 2>/dev/null; then
      sudo mkdir -p "$MOUNT_POINT/videos" "$MOUNT_POINT/models"
      sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT" || true
      ok "Mounted spare NVMe at $MOUNT_POINT"
      return
    fi
  fi

  sudo mkdir -p "$MOUNT_POINT/videos" "$MOUNT_POINT/models"
  sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT" || true
  ok "Recordings path ready as directory: $MOUNT_POINT"
  warn "For interactive spare-NVMe formatting (SD OS + data SSD only), run setup-kit.sh."
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
    err "Service failed to start. Check: journalctl -u pecan-vision"
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
  ensure_kit_ownership
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

Shipped models live in models/. The dashboard asks before copying any that
are not already in the model library. To copy them from a terminal:
  $KIT_ROOT/scripts/install-bundled-models.sh

EOF
}

main "$@"
=======
#!/usr/bin/env bash
# Non-interactive bootstrap: ensure repo, deps, systemd service, and start the app.
# Safe to re-run. Canonical install path: ~/pecan-vision-kit (not the Desktop).
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script needs bash. Do not run it with sh or sudo sh." >&2
  exit 1
fi
set -euo pipefail

REPO_URL="${PECAN_REPO_URL:-https://github.com/salirezav/pecan_vision_kit.git}"
# Use the account that is actually running this script. `sudo -u pecan` leaves
# SUDO_USER set to the caller (for example engr-ugaif), which is not the kit user.
USER_NAME="$(id -un)"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
KIT_ROOT="${PECAN_KIT_ROOT:-$HOME_DIR/pecan-vision-kit}"
SERVICE_SRC="$KIT_ROOT/systemd/pecan-vision.service"
SUDOERS_SRC="$KIT_ROOT/config/sudoers-pecan-vision"
VENV="$KIT_ROOT/.venv"
MOUNT_POINT="/mnt/recordings"
NVME_DEV="/dev/nvme0n1"
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

ensure_kit_ownership() {
  # Common failure: repo was cloned/copied as root or engr-ugaif, then pecan cannot create .venv.
  local owner
  owner="$(stat -c '%U' "$KIT_ROOT" 2>/dev/null || true)"
  if [[ -n "$owner" && "$owner" != "$USER_NAME" ]]; then
    warn "$KIT_ROOT is owned by $owner — fixing ownership for $USER_NAME"
    sudo chown -R "$USER_NAME:$USER_NAME" "$KIT_ROOT"
  fi
  if [[ ! -w "$KIT_ROOT" ]]; then
    err "$KIT_ROOT is not writable by $USER_NAME"
    err "Fix with:  sudo chown -R $USER_NAME:$USER_NAME $KIT_ROOT"
    exit 1
  fi
}

ensure_packages() {
  info "Ensuring OS packages…"
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3-venv python3-pip python3-opencv python3-yaml \
    v4l-utils ffmpeg \
    network-manager \
    curl ca-certificates git \
    hailo-all
  ok "Packages ready"
  if [[ ! -e /dev/hailo0 ]]; then
    warn "Hailo packages installed but /dev/hailo0 is missing — reboot once so the PCIe driver loads (needed for .hef models)."
  else
    ok "Hailo device present: /dev/hailo0"
  fi
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
  # 0.13.x extracts wheels as empty files on this Pi and uv sync then fails.
  curl -LsSf https://astral.sh/uv/0.9.17/install.sh | env UV_INSTALL_DIR="$HOME_DIR/.local/bin" sh
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

resolve_block_dev() {
  local src="$1" candidate=""
  [[ -z "$src" ]] && return 1
  case "$src" in
    UUID=*) candidate="/dev/disk/by-uuid/${src#UUID=}" ;;
    PARTUUID=*) candidate="/dev/disk/by-partuuid/${src#PARTUUID=}" ;;
    LABEL=*) candidate="/dev/disk/by-label/${src#LABEL=}" ;;
    /dev/*) candidate="$src" ;;
    *) candidate="$src" ;;
  esac
  if [[ -e "$candidate" ]]; then
    readlink -f "$candidate" 2>/dev/null || printf '%s' "$candidate"
    return 0
  fi
  printf '%s' "$src"
}

disk_backs_path() {
  local disk="$1" path="$2" src parent disk_base
  src="$(findmnt -no SOURCE "$path" 2>/dev/null || true)"
  [[ -z "$src" ]] && return 1
  src="$(resolve_block_dev "$src")"
  disk_base="${disk#/dev/}"
  parent="$(lsblk -no PKNAME "$src" 2>/dev/null | head -1 | tr -d '[:space:]')"
  if [[ -n "$parent" ]]; then
    [[ "$parent" == "$disk_base" ]]
    return
  fi
  [[ "$src" == "$disk" || "$src" == "$disk"* ]]
}

is_system_disk() {
  local disk="$1"
  disk_backs_path "$disk" "/" \
    || disk_backs_path "$disk" "/boot" \
    || disk_backs_path "$disk" "/boot/firmware"
}

ensure_recordings_mount() {
  # Non-interactive: never format or unmount. Prefer existing spare-NVMe mount;
  # otherwise use /mnt/recordings as a directory on the OS disk (NVMe-only kits).
  info "Ensuring recordings path at $MOUNT_POINT…"

  if is_system_disk "$NVME_DEV"; then
    warn "$NVME_DEV is the OS disk — not formatting or mounting it as a data volume."
    if awk '{print $2}' /etc/fstab 2>/dev/null | grep -qx "$MOUNT_POINT"; then
      warn "Removing $MOUNT_POINT from /etc/fstab (will not unmount if currently mounted)."
      sudo sed -i "\\|[[:space:]]${MOUNT_POINT}[[:space:]]|d" /etc/fstab
      sudo systemctl daemon-reload || true
    fi
    sudo mkdir -p "$MOUNT_POINT/videos" "$MOUNT_POINT/models"
    sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT" || true
    ok "Recordings directory on root filesystem: $MOUNT_POINT"
    return
  fi

  if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    sudo mkdir -p "$MOUNT_POINT/videos" "$MOUNT_POINT/models"
    sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT" || true
    ok "Recordings mount present: $MOUNT_POINT"
    return
  fi

  # Spare NVMe with existing filesystem and fstab — try mount once, never format.
  if [[ -b "${NVME_DEV}p1" ]] && ! is_system_disk "$NVME_DEV"; then
    sudo mkdir -p "$MOUNT_POINT"
    if sudo mount "$MOUNT_POINT" 2>/dev/null; then
      sudo mkdir -p "$MOUNT_POINT/videos" "$MOUNT_POINT/models"
      sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT" || true
      ok "Mounted spare NVMe at $MOUNT_POINT"
      return
    fi
  fi

  sudo mkdir -p "$MOUNT_POINT/videos" "$MOUNT_POINT/models"
  sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT" || true
  ok "Recordings path ready as directory: $MOUNT_POINT"
  warn "For interactive spare-NVMe formatting (SD OS + data SSD only), run setup-kit.sh."
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
    err "Service failed to start. Check: journalctl -u pecan-vision"
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
  ensure_kit_ownership
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

Shipped models live in models/. The dashboard asks before copying any that
are not already in the model library. To copy them from a terminal:
  $KIT_ROOT/scripts/install-bundled-models.sh

EOF
}

main "$@"
>>>>>>> 871aa392064a97663a2fda25a18dfd0b5b9317f7
