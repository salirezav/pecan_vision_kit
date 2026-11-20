#!/usr/bin/env bash
# Desktop "Start Pecan Vision" action: bootstrap if needed, ensure service, open UI.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script needs bash." >&2
  exit 1
fi
set -euo pipefail

USER_NAME="${SUDO_USER:-$USER}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
KIT_ROOT="${PECAN_KIT_ROOT:-$HOME_DIR/pecan-vision-kit}"
REPO_URL="${PECAN_REPO_URL:-https://github.com/salirezav/pecan_vision_kit.git}"
URL="${PECAN_KIOSK_URL:-http://127.0.0.1:8080}"
SERVICE="pecan-vision.service"

_SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$_SCRIPT_ROOT/app/server.py" && -z "${PECAN_KIT_ROOT:-}" ]]; then
  KIT_ROOT="$_SCRIPT_ROOT"
fi

CYA=$'\033[36m'; GREEN=$'\033[32m'; YEL=$'\033[33m'; RST=$'\033[0m'
info() { printf '%s==>%s %s\n' "$CYA" "$RST" "$*"; }
ok()   { printf '%s[ok]%s %s\n' "$GREEN" "$RST" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$YEL" "$RST" "$*"; }

ensure_checkout() {
  if [[ -x "$KIT_ROOT/scripts/bootstrap-kit.sh" ]]; then
    return
  fi
  info "Repo missing — cloning into $KIT_ROOT"
  mkdir -p "$(dirname "$KIT_ROOT")"
  git clone "$REPO_URL" "$KIT_ROOT"
  chmod +x "$KIT_ROOT/scripts/"*.sh 2>/dev/null || true
}

ensure_checkout

info "Running bootstrap (safe to re-run)…"
"$KIT_ROOT/scripts/bootstrap-kit.sh"

info "Ensuring $SERVICE is running…"
if ! systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
  sudo systemctl start "$SERVICE" || true
fi

info "Waiting for $URL …"
for _ in $(seq 1 30); do
  if curl -sf --max-time 2 "$URL" >/dev/null 2>&1; then
    ok "Web UI is up"
    break
  fi
  sleep 1
done

open_ui() {
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
    return
  fi
  if command -v chromium >/dev/null 2>&1; then
    chromium --new-window "$URL" >/dev/null 2>&1 || true
    return
  fi
  warn "Open a browser to $URL"
}

open_ui
ok "Start complete — $URL"
