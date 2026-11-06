#!/usr/bin/env bash
# Wait for the local web UI, then open Chromium in kiosk mode.
# Installed via setup-kit.sh → ~/.config/autostart/pecan-vision-kiosk.desktop
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
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --check-for-update-interval=31536000 \
  --disable-session-crashed-bubble \
  "$URL"
