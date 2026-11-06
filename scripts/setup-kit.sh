#!/usr/bin/env bash
# Interactive installer / updater for Pecan Vision Kit (Raspberry Pi 5).
# Copy the whole pecan-vision-kit folder to each Pi, then run this script.
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

RED=$'\033[31m'; GREEN=$'\033[32m'; YEL=$'\033[33m'; CYA=$'\033[36m'; BLD=$'\033[1m'; RST=$'\033[0m'

say()  { printf '%s\n' "$*"; }
info() { printf '%s==>%s %s\n' "$CYA" "$RST" "$*"; }
ok()   { printf '%s[ok]%s %s\n' "$GREEN" "$RST" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$YEL" "$RST" "$*"; }
err()  { printf '%s[error]%s %s\n' "$RED" "$RST" "$*" >&2; }

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
  2) Prepare NVMe recordings drive
  3) Install web app systemd service
  4) Configure always-on Wi‑Fi Access Point (config network)
  5) Write kit id / sticker info
  6) Enable Chromium kiosk autostart (monitor)

EOF
}

install_packages() {
  info "Installing OS packages (apt)…"
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3-venv python3-pip python3-opencv python3-yaml \
    v4l-utils ffmpeg \
    network-manager
  ok "Packages installed"
}

setup_venv() {
  info "Creating Python virtualenv (with system site-packages for OpenCV)…"
  if [[ ! -d "$VENV" ]]; then
    python3 -m venv --system-site-packages "$VENV"
  fi
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  pip install --upgrade pip wheel
  pip install -r "$KIT_ROOT/app/requirements.txt"
  # ultralytics asks for opencv-python. The camera uses the headless build.
  if pip show opencv-python >/dev/null 2>&1; then
    pip uninstall -y opencv-python
    pip install --force-reinstall --no-deps "opencv-python-headless>=4.8"
  fi
  ok "Virtualenv ready: $VENV"
}

prepare_nvme() {
  info "Checking storage at $NVME_DEV…"
  if [[ ! -b "$NVME_DEV" ]]; then
    warn "No NVMe found at $NVME_DEV — skipping disk setup."
    sudo mkdir -p "$MOUNT_POINT/videos"
    sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT"
    return
  fi

  if lsblk -no FSTYPE "${NVME_DEV}p1" 2>/dev/null | grep -q .; then
    ok "NVMe already has a filesystem on ${NVME_DEV}p1"
  else
    warn "NVMe looks empty (no filesystem on partition 1)."
    if ask_yn "Partition + format $NVME_DEV now? THIS ERASES THE DISK" "n"; then
      sudo parted -s "$NVME_DEV" mklabel gpt
      sudo parted -s "$NVME_DEV" mkpart recordings ext4 1MiB 100%
      sleep 1
      sudo mkfs.ext4 -F -L recordings "${NVME_DEV}p1"
      ok "Formatted ${NVME_DEV}p1"
    else
      warn "Skipped formatting."
      return
    fi
  fi

  sudo mkdir -p "$MOUNT_POINT"
  UUID="$(sudo blkid -s UUID -o value "${NVME_DEV}p1" || true)"
  if [[ -n "$UUID" ]] && ! grep -q "$MOUNT_POINT" /etc/fstab; then
    echo "UUID=$UUID $MOUNT_POINT ext4 defaults,noatime 0 2" | sudo tee -a /etc/fstab >/dev/null
    ok "Added fstab entry for $MOUNT_POINT"
  fi
  sudo mount "$MOUNT_POINT" 2>/dev/null || sudo mount -a || true
  sudo mkdir -p "$MOUNT_POINT/videos"
  sudo chown -R "$USER_NAME:$USER_NAME" "$MOUNT_POINT"
  df -h "$MOUNT_POINT" || true
  ok "Recordings mount ready"
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
    -e "s|/home/engr-ugaif|$HOME_DIR|g" \
    -e "s|User=engr-ugaif|User=$USER_NAME|g" \
    -e "s|Group=engr-ugaif|Group=$USER_NAME|g" \
    "$SERVICE_SRC" > "$tmp"
  sudo cp "$tmp" /etc/systemd/system/pecan-vision.service
  rm -f "$tmp"

  local sudoers_tmp
  sudoers_tmp="$(mktemp)"
  sed \
    -e "s|engr-ugaif|$USER_NAME|g" \
    -e "s|/home/engr-ugaif|$HOME_DIR|g" \
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
    sudo systemctl restart pecan-vision.service
    sleep 1
    systemctl --no-pager --full status pecan-vision.service || true
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
  say "  3) NVMe / recordings disk only"
  say "  4) Web service only"
  say "  5) Access Point only"
  say "  6) Camera + VNC checks only"
  say "  7) Chromium kiosk only"
  say "  8) Quit"
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
      ;;
    2) install_packages; setup_venv ;;
    3) prepare_nvme ;;
    4) setup_venv; write_kit_config; install_service ;;
    5) configure_ap ;;
    6) verify_camera; ensure_vnc_hint ;;
    7) configure_kiosk ;;
    8) say "Bye."; exit 0 ;;
    *) err "Unknown selection"; exit 1 ;;
  esac

  cat <<EOF

${GREEN}${BLD}Done.${RST}
Next:
  • Open http://10.42.0.1:8080 (from phone on the kit AP)
    or http://localhost:8080 (on the Pi)
  • Admin / restart UI: http://…:8080/admin
  • Docs: $KIT_ROOT/docs/

EOF
}

main_menu
