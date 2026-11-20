#!/usr/bin/env bash
# Interactive installer / updater for Pecan Vision Kit (Raspberry Pi 5).
# Copy the whole pecan-vision-kit folder to each Pi, then run this script.
# This check is POSIX so `sh setup-kit.sh` fails here instead of midway.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script needs bash. Do not run it with sh or sudo sh." >&2
  echo "From the project folder, as the kit user (not root):" >&2
  echo "  ./scripts/setup-kit.sh" >&2
  exit 1
fi
set -euo pipefail

KIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
SERVICE_SRC="$KIT_ROOT/systemd/pecan-vision.service"
SUDOERS_SRC="$KIT_ROOT/config/sudoers-pecan-vision"
DEFAULTS_YAML="$KIT_ROOT/config/defaults.yaml"
VENV="$KIT_ROOT/.venv"
MOUNT_POINT="/mnt/recordings"
NVME_DEV="/dev/nvme0n1"
# Set by prepare_nvme: dedicated (spare NVMe mount) | rootfs (dir on OS disk) | none
STORAGE_MODE=""

RED=$'\033[31m'; GREEN=$'\033[32m'; YEL=$'\033[33m'; CYA=$'\033[36m'; BLD=$'\033[1m'; RST=$'\033[0m'

say()  { printf '%s\n' "$*"; }
info() { printf '%s==>%s %s\n' "$CYA" "$RST" "$*"; }
ok()   { printf '%s[ok]%s %s\n' "$GREEN" "$RST" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$YEL" "$RST" "$*"; }
err()  { printf '%s[error]%s %s\n' "$RED" "$RST" "$*" >&2; }

# Resolve findmnt SOURCE (/dev/…, /dev/disk/by-uuid/…, PARTUUID=…) to a real /dev node.
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

# True if disk (e.g. /dev/nvme0n1) backs the filesystem for path.
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

disk_has_mounted_parts() {
  local disk="$1" mp
  while IFS= read -r mp; do
    [[ -z "$mp" ]] && continue
    # lsblk may list several mountpoints separated by space or newline
    return 0
  done < <(lsblk -no MOUNTPOINTS "$disk" 2>/dev/null | sed '/^$/d')
  return 1
}

ensure_recordings_dir_on_rootfs() {
  # Used when the OS disk is the only storage (typical NVMe-only kits).
  # Keep the canonical app path /mnt/recordings as a normal directory on /.
  # Never format the OS disk and never unmount a live filesystem.
  info "Using $MOUNT_POINT on the root filesystem (no spare data disk)."
  local rec_src root_src pk
  root_src="$(resolve_block_dev "$(findmnt -no SOURCE / 2>/dev/null || true)")"
  if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    rec_src="$(resolve_block_dev "$(findmnt -no SOURCE "$MOUNT_POINT" 2>/dev/null || true)")"
    pk="$(lsblk -no PKNAME "$rec_src" 2>/dev/null | head -1 | tr -d '[:space:]')"
    if [[ -n "$rec_src" && "$rec_src" == "$root_src" ]]; then
      err "$MOUNT_POINT is mounted from system volume $rec_src — refusing to reconfigure. Fix /etc/fstab manually."
      return 1
    fi
    if [[ -n "$pk" ]] && is_system_disk "/dev/$pk"; then
      err "$MOUNT_POINT is mounted from system disk /dev/$pk ($rec_src) — refusing to reconfigure. Fix /etc/fstab manually."
      return 1
    fi
  fi
  if awk '{print $2}' /etc/fstab 2>/dev/null | grep -qx "$MOUNT_POINT"; then
    warn "Removing $MOUNT_POINT from /etc/fstab (OS-on-disk kits must not mount a separate recordings volume over the OS disk)."
    sudo sed -i "\\|[[:space:]]${MOUNT_POINT}[[:space:]]|d" /etc/fstab
    sudo systemctl daemon-reload || true
    if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
      warn "Not unmounting $MOUNT_POINT while it is in use — reboot later so it becomes a normal directory on /."
    fi
  fi
  sudo mkdir -p "$MOUNT_POINT/videos" "$MOUNT_POINT/models"
  sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT"
  df -h "$MOUNT_POINT" || df -h / || true
  STORAGE_MODE="rootfs"
  ok "Recordings path ready on root filesystem: $MOUNT_POINT/videos"
}

need_sudo() {
  if [[ "$(id -u)" -eq 0 ]]; then
    err "Run this script as the normal kit user (it will ask for sudo when needed)."
    exit 1
  fi
  if ! sudo -v; then
    err "sudo failed — cannot continue."
    exit 1
  fi
  # keep sudo warm
  ( while true; do sudo -n true; sleep 50; done ) &
  SUDO_KEEPER=$!
  trap 'kill '"$SUDO_KEEPER"' 2>/dev/null || true' EXIT
}

ask() {
  # ask "Prompt" "default"
  local prompt="$1" default="${2:-}" reply
  if [[ -n "$default" ]]; then
    read -r -p "$prompt [$default]: " reply || true
    printf '%s' "${reply:-$default}"
  else
    read -r -p "$prompt: " reply || true
    printf '%s' "$reply"
  fi
}

ask_yn() {
  local prompt="$1" default="${2:-y}" reply
  read -r -p "$prompt [${default}/$([[ $default == y ]] && echo n || echo y)]: " reply || true
  reply="${reply:-$default}"
  [[ "$reply" =~ ^[Yy] ]]
}

banner() {
  cat <<EOF

${BLD}Pecan Vision Kit — interactive setup${RST}
Project: $KIT_ROOT
User:    $USER_NAME

This script can:
  1) Install packages + Python venv
  2) Prepare recordings storage (spare NVMe or root filesystem)
  3) Install web app systemd service
  4) Configure always-on Wi‑Fi Access Point (config network)
  5) Write kit id / sticker info
  6) Enable Chromium kiosk autostart (monitor)
  7) Install Desktop Start / Update / Setup launchers

EOF
}

install_packages() {
  info "Installing OS packages (apt)…"
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3-venv python3-pip python3-opencv python3-yaml \
    v4l-utils ffmpeg \
    network-manager \
    curl ca-certificates
  ok "Packages installed"
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

setup_venv() {
  info "Creating Python virtualenv (with system site-packages for OpenCV)…"
  ensure_uv
  if [[ ! -d "$VENV" ]]; then
    uv venv --python python3 --system-site-packages "$VENV"
  fi
  # --inexact keeps apt packages visible through system-site-packages.
  # CPU torch is selected by pyproject.toml; cuDNN and the CUDA stack are excluded.
  uv sync --directory "$KIT_ROOT" --frozen --inexact --no-install-project
  ok "Virtualenv ready: $VENV"
}

prepare_nvme() {
  # Two supported layouts:
  #  1) OS on SD/eMMC + spare NVMe  → mount NVMe at /mnt/recordings
  #  2) OS on NVMe only (most field kits) → /mnt/recordings is a directory on /
  # Never format, repartition, or unmount the system disk.
  info "Detecting storage layout…"
  local root_src
  root_src="$(resolve_block_dev "$(findmnt -no SOURCE / 2>/dev/null || true)")"
  info "Root filesystem is on ${root_src:-unknown}"

  if is_system_disk "$NVME_DEV"; then
    warn "$NVME_DEV holds the OS — will not partition, format, or unmount it."
    ensure_recordings_dir_on_rootfs
    return
  fi

  if [[ ! -b "$NVME_DEV" ]]; then
    warn "No spare NVMe at $NVME_DEV — recordings go on the root filesystem."
    ensure_recordings_dir_on_rootfs
    return
  fi

  if disk_has_mounted_parts "$NVME_DEV"; then
    warn "$NVME_DEV has mounted partitions — refusing to format or repartition."
    # If it is already mounted at our recordings path, reuse it.
    if findmnt -no SOURCE "$MOUNT_POINT" 2>/dev/null | grep -q .; then
      local msrc
      msrc="$(resolve_block_dev "$(findmnt -no SOURCE "$MOUNT_POINT")")"
      if [[ "$msrc" == "$NVME_DEV"* ]]; then
        sudo mkdir -p "$MOUNT_POINT/videos" "$MOUNT_POINT/models"
        sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT"
        STORAGE_MODE="dedicated"
        ok "Reusing existing recordings mount: $MOUNT_POINT ($msrc)"
        df -h "$MOUNT_POINT" || true
        return
      fi
    fi
    ensure_recordings_dir_on_rootfs
    return
  fi

  # Spare, unmounted NVMe (lab/kit with SD OS + data SSD).
  info "Spare NVMe detected at $NVME_DEV (not the OS disk)."
  if lsblk -no FSTYPE "${NVME_DEV}p1" 2>/dev/null | grep -q .; then
    ok "NVMe already has a filesystem on ${NVME_DEV}p1"
  else
    warn "NVMe looks empty (no filesystem on partition 1)."
    if ask_yn "Partition + format $NVME_DEV as recordings? THIS ERASES ONLY THIS SPARE DISK" "n"; then
      if is_system_disk "$NVME_DEV" || disk_has_mounted_parts "$NVME_DEV"; then
        err "Safety check failed — aborting format."
        ensure_recordings_dir_on_rootfs
        return
      fi
      sudo parted -s "$NVME_DEV" mklabel gpt
      sudo parted -s "$NVME_DEV" mkpart recordings ext4 1MiB 100%
      sleep 1
      sudo mkfs.ext4 -F -L recordings "${NVME_DEV}p1"
      ok "Formatted ${NVME_DEV}p1"
    else
      warn "Skipped formatting spare NVMe — falling back to root filesystem path."
      ensure_recordings_dir_on_rootfs
      return
    fi
  fi

  sudo mkdir -p "$MOUNT_POINT"
  local UUID=""
  UUID="$(sudo blkid -s UUID -o value "${NVME_DEV}p1" || true)"
  if [[ -n "$UUID" ]] && ! awk '{print $2}' /etc/fstab 2>/dev/null | grep -qx "$MOUNT_POINT"; then
    echo "UUID=$UUID $MOUNT_POINT ext4 defaults,noatime 0 2" | sudo tee -a /etc/fstab >/dev/null
    ok "Added fstab entry for $MOUNT_POINT"
  fi
  # Mount the spare disk only — never unmount anything.
  if ! mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    sudo mount "$MOUNT_POINT" 2>/dev/null || sudo mount -a || true
  fi
  if ! mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    warn "Could not mount spare NVMe at $MOUNT_POINT — using directory on root filesystem."
    ensure_recordings_dir_on_rootfs
    return
  fi
  sudo mkdir -p "$MOUNT_POINT/videos" "$MOUNT_POINT/models"
  sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT"
  STORAGE_MODE="dedicated"
  df -h "$MOUNT_POINT" || true
  ok "Dedicated recordings mount ready: $MOUNT_POINT"
}

write_kit_config() {
  local kit_id
  kit_id="$(ask "Kit ID (two digits, e.g. 01)" "01")"
  mkdir -p "$MOUNT_POINT"
  if [[ -f "$MOUNT_POINT/config.yaml" ]]; then
    warn "Runtime config already exists: $MOUNT_POINT/config.yaml"
    if ! ask_yn "Overwrite kit_id in runtime config?" "y"; then
      return
    fi
  fi
  # Merge defaults + kit_id
  "$VENV/bin/python" - <<PY
import yaml
from pathlib import Path
defaults = yaml.safe_load(Path("$DEFAULTS_YAML").read_text()) or {}
defaults["kit_id"] = "$kit_id"
path = Path("$MOUNT_POINT/config.yaml")
path.parent.mkdir(parents=True, exist_ok=True)
# Keep existing keys if present
if path.exists():
    cur = yaml.safe_load(path.read_text()) or {}
    cur.update({"kit_id": "$kit_id"})
    defaults = {**defaults, **cur}
    defaults["kit_id"] = "$kit_id"
path.write_text(yaml.safe_dump(defaults, sort_keys=False))
print(path)
PY
  # Also stamp kit id into defaults copy for documentation
  printf '%s\n' "$kit_id" > "$KIT_ROOT/config/kit_id.txt"
  ok "Kit ID set to $kit_id"
  export KIT_ID_CHOSEN="$kit_id"
}

install_service() {
  info "Installing systemd service…"
  # Rewrite User/paths if username differs
  local tmp
  tmp="$(mktemp)"
  sed \
    -e "s|@KIT_ROOT@|$KIT_ROOT|g" \
    -e "s|@KIT_USER@|$USER_NAME|g" \
    "$SERVICE_SRC" > "$tmp"
  sudo cp "$tmp" /etc/systemd/system/pecan-vision.service
  rm -f "$tmp"

  local sudoers_tmp
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
  sudo systemctl enable pecan-vision.service
  if ask_yn "Start / restart pecan-vision service now?" "y"; then
    if ! sudo systemctl restart pecan-vision.service; then
      err "pecan-vision did not start. Check: journalctl -u pecan-vision"
    else
      sleep 1
      systemctl --no-pager --full status pecan-vision.service || true
    fi
  fi
  ok "Service installed"
}

eth0_has_carrier() {
  local carrier
  carrier="$(cat /sys/class/net/eth0/carrier 2>/dev/null || echo 0)"
  [[ "$carrier" == "1" ]]
}

configure_ap() {
  cat <<EOF

${BLD}Always-on config Access Point${RST}
Recommended design: Wi‑Fi AP for phones + Ethernet for the plant network.
Creating the AP will ${YEL}disconnect the Pi from any current Wi‑Fi client network${RST}.
Ethernet (eth0) is unaffected.

EOF
  if ! eth0_has_carrier; then
    warn "eth0 has no carrier — activating the AP will drop the current Wi‑Fi client link."
    warn "Prefer plugging Ethernet first (or configure now and activate later)."
  fi
  if ! ask_yn "Configure always-on Wi‑Fi Access Point now?" "y"; then
    warn "Skipped AP setup."
    return
  fi

  local kit_id ssid pass
  kit_id="${KIT_ID_CHOSEN:-$(cat "$KIT_ROOT/config/kit_id.txt" 2>/dev/null || echo 01)}"
  ssid="$(ask "AP Wi‑Fi name (SSID)" "PecanCam-$kit_id")"
  while true; do
    pass="$(ask "AP Wi‑Fi password (8+ characters)" "")"
    if [[ ${#pass} -ge 8 ]]; then
      break
    fi
    warn "Password must be at least 8 characters."
  done

  local con="PecanCam-AP"
  if nmcli -t -f NAME connection show | grep -qx "$con"; then
    info "Updating existing connection $con"
    sudo nmcli connection delete "$con" || true
  fi

  sudo nmcli connection add \
    type wifi ifname wlan0 con-name "$con" autoconnect yes ssid "$ssid" \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    ipv4.method shared \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$pass"

  # Prefer AP over old client profiles on boot
  sudo nmcli connection modify "$con" connection.autoconnect-priority 100

  # Default n: activating drops Wi‑Fi client; do this when Ethernet is ready.
  if ask_yn "Activate Access Point now? (drops current Wi‑Fi client link)" "n"; then
    sudo nmcli connection up "$con" || warn "Could not bring AP up — check rfkill / wifi"
  else
    warn "AP connection saved but not activated. Run: sudo nmcli connection up PecanCam-AP"
  fi

  cat > "$KIT_ROOT/config/ap-credentials.txt" <<EOF
SSID=$ssid
PASSWORD=$pass
WEB=http://10.42.0.1:8080
EOF
  chmod 600 "$KIT_ROOT/config/ap-credentials.txt"

  say ""
  ok "Access Point configured"
  say "  ${BLD}Sticker info${RST}"
  say "  SSID:     $ssid"
  say "  Password: $pass"
  say "  Web UI:   http://10.42.0.1:8080"
  say "  Credentials also saved in: $KIT_ROOT/config/ap-credentials.txt"
  say "  ${YEL}Write these on the physical kit sticker.${RST}"
}

configure_kiosk() {
  cat <<EOF

${BLD}Chromium kiosk (monitor attached)${RST}
On desktop login, Chromium opens fullscreen to http://127.0.0.1:8080
after waiting for the pecan-vision web service.

EOF
  if ! ask_yn "Enable Chromium kiosk autostart?" "y"; then
    warn "Skipped kiosk setup."
    return
  fi

  if ! command -v chromium >/dev/null 2>&1; then
    info "Installing chromium…"
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y chromium
  fi

  chmod +x "$KIT_ROOT/scripts/start-kiosk.sh"

  local autostart_dir="$HOME_DIR/.config/autostart"
  mkdir -p "$autostart_dir"
  cat > "$autostart_dir/pecan-vision-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Pecan Vision Kiosk
Comment=Open recorder UI fullscreen after login
Exec=$KIT_ROOT/scripts/start-kiosk.sh
X-GNOME-Autostart-enabled=true
StartupNotify=false
Terminal=false
EOF
  # Ensure kit user owns the file when setup runs under sudo-warmed shell
  chown "$USER_NAME:$USER_NAME" "$autostart_dir/pecan-vision-kiosk.desktop" 2>/dev/null || true
  chown -R "$USER_NAME:$USER_NAME" "$HOME_DIR/.config/autostart" 2>/dev/null || true

  ok "Kiosk autostart installed: $autostart_dir/pecan-vision-kiosk.desktop"
  say "  Disable: remove that .desktop file (or uncheck in desktop session settings)."
  say "  Test now: $KIT_ROOT/scripts/start-kiosk.sh"
}

verify_camera() {
  info "Camera check…"
  if lsusb | grep -qi "32e4:5234\|Global Shutter"; then
    ok "USB Global Shutter camera detected"
  else
    warn "Camera not seen on USB — plug it into a USB3 port and re-check"
  fi
  v4l2-ctl --list-devices || true
}

ensure_vnc_hint() {
  if systemctl is-active --quiet wayvnc.service 2>/dev/null; then
    ok "WayVNC is active (VNC server)"
  else
    warn "WayVNC not active. Enable via Raspberry Pi configuration if you need desktop VNC."
    if ask_yn "Try enabling wayvnc.service?" "y"; then
      sudo systemctl enable --now wayvnc.service || warn "Could not enable wayvnc"
    fi
  fi
}

main_menu() {
  banner
  need_sudo

  say "Choose what to run:"
  say "  1) Full setup (recommended on a new kit)"
  say "  2) Packages + Python only"
  say "  3) Recordings storage only (safe for OS-on-NVMe)"
  say "  4) Web service only"
  say "  5) Access Point only"
  say "  6) Camera + VNC checks only"
  say "  7) Chromium kiosk only"
  say "  8) Desktop launchers only"
  say "  9) Quit"
  local choice
  choice="$(ask "Selection" "1")"

  case "$choice" in
    1)
      install_packages
      setup_venv
      prepare_nvme
      write_kit_config
      install_service
      configure_ap
      verify_camera
      ensure_vnc_hint
      configure_kiosk
      "$KIT_ROOT/scripts/install-desktop-launchers.sh" || warn "Desktop launchers skipped"
      ;;
    2) install_packages; setup_venv ;;
    3) prepare_nvme ;;
    4) setup_venv; write_kit_config; install_service ;;
    5) configure_ap ;;
    6) verify_camera; ensure_vnc_hint ;;
    7) configure_kiosk ;;
    8)
      if [[ -x "$KIT_ROOT/scripts/install-desktop-launchers.sh" ]]; then
        "$KIT_ROOT/scripts/install-desktop-launchers.sh" || warn "Desktop launchers skipped"
      fi
      ;;
    9) say "Bye."; exit 0 ;;
    *) err "Unknown selection"; exit 1 ;;
  esac

  cat <<EOF

${GREEN}${BLD}Done.${RST}
Next:
  • Open http://10.42.0.1:8080 (from phone on the kit AP)
    or http://localhost:8080 (on the Pi)
  • Admin / restart UI: http://…:8080/admin
  • Desktop launchers: Start / Update / Setup Pecan Vision
  • Docs: $KIT_ROOT/docs/

EOF
}

main_menu
