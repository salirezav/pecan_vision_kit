#!/usr/bin/env bash
# Desktop "Setup / Repair" — opens the interactive installer in a terminal.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script needs bash." >&2
  exit 1
fi
set -euo pipefail

USER_NAME="${SUDO_USER:-$USER}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
KIT_ROOT="${PECAN_KIT_ROOT:-$HOME_DIR/pecan-vision-kit}"

_SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$_SCRIPT_ROOT/app/server.py" && -z "${PECAN_KIT_ROOT:-}" ]]; then
  KIT_ROOT="$_SCRIPT_ROOT"
fi

if [[ ! -x "$KIT_ROOT/scripts/setup-kit.sh" ]]; then
  echo "Kit not installed at $KIT_ROOT — run Start Pecan Vision first." >&2
  exit 1
fi

exec "$KIT_ROOT/scripts/setup-kit.sh"
