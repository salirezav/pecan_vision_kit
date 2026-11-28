#!/usr/bin/env bash
# Create / configure the dedicated kit Linux account (default: pecan).
# Run once when imaging a kit. Do not ship campus accounts or shared passwords.
#
# Usage (as root or with sudo):
#   sudo ./scripts/ensure-kit-user.sh
#   # Default password is "$(hostname)1234" unless PECAN_KIT_PASSWORD is set.
#   sudo PECAN_KIT_PASSWORD='…' ./scripts/ensure-kit-user.sh
#   sudo PECAN_KIT_USER=pecan PECAN_AUTOLOGIN=1 ./scripts/ensure-kit-user.sh
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script needs bash." >&2
  exit 1
fi
set -euo pipefail

KIT_USER="${PECAN_KIT_USER:-pecan}"
AUTOLOGIN="${PECAN_AUTOLOGIN:-0}"
SEED_DIR=""
HOME_DIR=""

RED=$'\033[31m'; GREEN=$'\033[32m'; YEL=$'\033[33m'; CYA=$'\033[36m'; BLD=$'\033[1m'; RST=$'\033[0m'
info() { printf '%s==>%s %s\n' "$CYA" "$RST" "$*"; }
ok()   { printf '%s[ok]%s %s\n' "$GREEN" "$RST" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$YEL" "$RST" "$*"; }
err()  { printf '%s[error]%s %s\n' "$RED" "$RST" "$*" >&2; }

if [[ "$(id -u)" -ne 0 ]]; then
  err "Run as root: sudo $0"
  exit 1
fi

if [[ ! "$KIT_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
  err "Invalid PECAN_KIT_USER: $KIT_USER"
  exit 1
fi

if id -u "$KIT_USER" >/dev/null 2>&1; then
  info "User $KIT_USER already exists"
else
  info "Creating user $KIT_USER"
  useradd --create-home --shell /bin/bash "$KIT_USER"
  ok "Created $KIT_USER"
fi

HOME_DIR="$(getent passwd "$KIT_USER" | cut -d: -f6)"
[[ -n "$HOME_DIR" && -d "$HOME_DIR" ]] || { err "Home for $KIT_USER not found"; exit 1; }

for grp in sudo video; do
  if getent group "$grp" >/dev/null 2>&1; then
    usermod -aG "$grp" "$KIT_USER"
    ok "Added $KIT_USER to $grp"
  else
    warn "Group $grp not found — skipped"
  fi
done

set_password() {
  local host pass
  host="$(hostname -s 2>/dev/null || hostname || true)"
  host="${host%%.*}"
  host="$(printf '%s' "$host" | tr -d '[:space:]')"
  if [[ -z "$host" ]]; then
    err "Could not read hostname for default password"
    exit 1
  fi
  # Default: {hostname}1234 (e.g. pecancam-01 → pecancam-011234)
  pass="${PECAN_KIT_PASSWORD:-${host}1234}"
  echo "$KIT_USER:$pass" | chpasswd
  if [[ -n "${PECAN_KIT_PASSWORD:-}" ]]; then
    ok "Password set from PECAN_KIT_PASSWORD"
  else
    ok "Password set to \${hostname}1234 → ${host}1234"
  fi
  KIT_PASSWORD_HINT="${host}1234"
  if [[ -n "${PECAN_KIT_PASSWORD:-}" ]]; then
    KIT_PASSWORD_HINT="(custom via PECAN_KIT_PASSWORD)"
  fi
}

set_password

configure_passwordless_sudo() {
  local sudoers_tmp sudoers_dst
  sudoers_dst="/etc/sudoers.d/${KIT_USER}-nopasswd"
  sudoers_tmp="$(mktemp)"
  printf '%s\n' "${KIT_USER} ALL=(ALL) NOPASSWD:ALL" > "$sudoers_tmp"
  if ! visudo -cf "$sudoers_tmp"; then
    rm -f "$sudoers_tmp"
    err "Refusing to install passwordless sudo rule that visudo rejected"
    exit 1
  fi
  cp "$sudoers_tmp" "$sudoers_dst"
  chmod 440 "$sudoers_dst"
  rm -f "$sudoers_tmp"
  ok "Passwordless sudo installed: $sudoers_dst"
}

configure_passwordless_sudo

configure_autologin() {
  [[ "$AUTOLOGIN" == "1" || "$AUTOLOGIN" == "yes" || "$AUTOLOGIN" == "y" ]] || return 0
  if [[ -f /etc/lightdm/lightdm.conf ]]; then
    info "Enabling LightDM autologin for $KIT_USER"
    if grep -q '^\[Seat:\*\]' /etc/lightdm/lightdm.conf; then
      sed -i \
        -e "s/^#\\?autologin-user=.*/autologin-user=$KIT_USER/" \
        /etc/lightdm/lightdm.conf
      if ! grep -q '^autologin-user=' /etc/lightdm/lightdm.conf; then
        sed -i "/^\[Seat:\*\]/a autologin-user=$KIT_USER" /etc/lightdm/lightdm.conf
      fi
    else
      printf '\n[Seat:*]\nautologin-user=%s\n' "$KIT_USER" >> /etc/lightdm/lightdm.conf
    fi
    ok "LightDM autologin configured"
    return
  fi
  if command -v raspi-config >/dev/null 2>&1; then
    info "Enabling desktop autologin via raspi-config"
    raspi-config nonint do_boot_behaviour B4 || warn "raspi-config autologin failed"
    # B4 enables autologin for the current console user context; also stamp user name when possible.
    if [[ -f /etc/lightdm/lightdm.conf ]]; then
      sed -i "s/^#\\?autologin-user=.*/autologin-user=$KIT_USER/" /etc/lightdm/lightdm.conf || true
    fi
    ok "Autologin requested"
    return
  fi
  warn "Could not configure autologin automatically — enable it in Raspberry Pi configuration."
}

configure_autologin

SEED_DIR="$HOME_DIR/.local/share/pecan-vision"
mkdir -p "$SEED_DIR" "$HOME_DIR/Desktop"
REPO_URL="${PECAN_REPO_URL:-https://github.com/salirezav/pecan_vision_kit.git}"
KIT_ROOT_DEFAULT="$HOME_DIR/pecan-vision-kit"

cat > "$SEED_DIR/seed-start.sh" <<EOF
#!/usr/bin/env bash
# Written by ensure-kit-user.sh — works before the repo exists.
set -euo pipefail
KIT_ROOT="\${PECAN_KIT_ROOT:-$KIT_ROOT_DEFAULT}"
REPO_URL="\${PECAN_REPO_URL:-$REPO_URL}"
if [[ ! -x "\$KIT_ROOT/scripts/bootstrap-kit.sh" ]]; then
  echo "Cloning Pecan Vision Kit into \$KIT_ROOT …"
  mkdir -p "\$(dirname "\$KIT_ROOT")"
  if [[ -d "\$KIT_ROOT/.git" ]]; then
    echo "Repo folder exists but bootstrap script is missing — check \$KIT_ROOT"
    exit 1
  fi
  git clone "\$REPO_URL" "\$KIT_ROOT"
fi
chmod +x "\$KIT_ROOT/scripts/"*.sh 2>/dev/null || true
exec "\$KIT_ROOT/scripts/desktop-start.sh"
EOF
chmod +x "$SEED_DIR/seed-start.sh"

cat > "$HOME_DIR/Desktop/Start Pecan Vision.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Start Pecan Vision
Comment=Clone (if needed), install deps, start the recorder service, open the UI
Exec=x-terminal-emulator -e bash -lc '$SEED_DIR/seed-start.sh; echo; read -r -p "Press Enter to close…"'
Icon=applications-internet
Terminal=false
Categories=Utility;
EOF
chmod +x "$HOME_DIR/Desktop/Start Pecan Vision.desktop"

chown -R "$KIT_USER:$KIT_USER" "$HOME_DIR/.local" "$HOME_DIR/Desktop"

cat <<EOF

${GREEN}${BLD}Kit user ready.${RST}
  User:     $KIT_USER
  Home:     $HOME_DIR
  Password: $KIT_PASSWORD_HINT
  Canonical install path: $KIT_ROOT_DEFAULT
  Seed launcher: $HOME_DIR/Desktop/Start Pecan Vision.desktop

Next (as $KIT_USER):
  1. Double-click Start Pecan Vision, or run:
       $SEED_DIR/seed-start.sh
  2. Confirm the Linux password on the kit admin sticker
     (default is hostname + 1234, e.g. \$(hostname)1234).
  3. Do ${YEL}not${RST} ship campus accounts (e.g. engr-ugaif) or shared UGA passwords.

EOF
