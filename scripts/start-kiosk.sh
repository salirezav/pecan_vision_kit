#!/usr/bin/env bash
# Wait for the local web UI, then open Chromium fullscreen without browser chrome.
# Installed via setup-kit.sh → ~/.config/autostart/pecan-vision-kiosk.desktop
#
# Do not pass --kiosk. Chromium treats that flag as "no context menu" and has
# no option to turn the right-click menu back on. --app is still chromeless
# (no tabs or address bar); --start-fullscreen covers the monitor.
set -euo pipefail

URL="${PECAN_KIOSK_URL:-http://127.0.0.1:8080}"
MAX_WAIT="${PECAN_KIOSK_WAIT:-30}"

for ((i = 0; i < MAX_WAIT; i++)); do
  if curl -sf --max-time 2 "$URL" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

exec chromium \
  --app="$URL" \
  --start-fullscreen \
  --start-maximized \
  --noerrdialogs \
  --disable-infobars \
  --check-for-update-interval=31536000 \
  --disable-session-crashed-bubble
