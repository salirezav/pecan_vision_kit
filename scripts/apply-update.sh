#!/usr/bin/env bash
# Download a GitHub release tarball, swap app files, restart pecan-vision.
# Invoked via passwordless sudo from the web UI (setsid / detached).
set -euo pipefail

REQUEST_FILE="/mnt/recordings/update-request.json"
# Prefer request file (sudo env_reset drops PECAN_*). Env vars remain a fallback.
if [[ -f "$REQUEST_FILE" ]]; then
  eval "$(python3 - <<'PY'
import json
from pathlib import Path
data = json.loads(Path("/mnt/recordings/update-request.json").read_text())
def esc(value: str) -> str:
    return value.replace("'", "'\"'\"'")
print(f"VERSION='{esc(str(data.get('version') or ''))}'")
print(f"TARBALL_URL='{esc(str(data.get('tarball_url') or ''))}'")
print(f"SHA256_URL='{esc(str(data.get('sha256_url') or ''))}'")
print(f"KIT_ROOT='{esc(str(data.get('kit_root') or ''))}'")
PY
)"
fi
KIT_ROOT="${KIT_ROOT:-${PECAN_KIT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}}"
VERSION="${VERSION:-${PECAN_UPDATE_VERSION:-}}"
TARBALL_URL="${TARBALL_URL:-${PECAN_UPDATE_TARBALL_URL:-}}"
SHA256_URL="${SHA256_URL:-${PECAN_UPDATE_SHA256_URL:-}}"
SERVICE="pecan-vision.service"
BACKUP_ROOT="/var/lib/pecan-vision/backup"
STATUS_FILE="/mnt/recordings/update-status.json"
WORK="$(mktemp -d /tmp/pecan-update.XXXXXX)"
PREV_VERSION="$(tr -d '[:space:]' < "$KIT_ROOT/VERSION" 2>/dev/null || echo unknown)"
BACKUP_DIR="$BACKUP_ROOT/$PREV_VERSION"

export PECAN_STATUS_FILE="$STATUS_FILE"
export PECAN_STATUS_VERSION="$VERSION"
export PECAN_STATUS_PREV="$PREV_VERSION"

write_status() {
  local state="$1"
  local message="$2"
  PECAN_STATUS_STATE="$state" PECAN_STATUS_MESSAGE="$message" python3 - <<'PY'
import json, os, time
from pathlib import Path
path = Path(os.environ["PECAN_STATUS_FILE"])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    "state": os.environ["PECAN_STATUS_STATE"],
    "target_version": os.environ.get("PECAN_STATUS_VERSION", ""),
    "previous_version": os.environ.get("PECAN_STATUS_PREV", ""),
    "message": os.environ.get("PECAN_STATUS_MESSAGE", ""),
    "updated_at": time.time(),
}, indent=2) + "\n")
PY
}

cleanup() {
  rm -rf "$WORK"
}
trap cleanup EXIT

log() { printf '%s %s\n' "$(date -Is)" "$*"; }

fail() {
  local msg="$1"
  log "ERROR: $msg"
  write_status "failed" "$msg"
  exit 1
}

backup_items=(app scripts systemd docs config VERSION LICENSE README.md .gitignore)

snapshot_tree() {
  local dest="$1"
  mkdir -p "$dest"
  local item
  for item in "${backup_items[@]}"; do
    if [[ -e "$KIT_ROOT/$item" ]]; then
      rsync -a --exclude '__pycache__/' --exclude '*.pyc' "$KIT_ROOT/$item" "$dest/"
    fi
  done
}

restore_backup() {
  local backup="$1"
  if [[ ! -d "$backup" ]]; then
    log "No backup at $backup"
    systemctl start "$SERVICE" || true
    return
  fi
  log "Restoring backup from $backup"
  local item
  for item in "${backup_items[@]}"; do
    if [[ -e "$backup/$item" ]]; then
      rsync -a --delete --exclude '__pycache__/' --exclude '*.pyc' "$backup/$item" "$KIT_ROOT/"
    fi
  done
  systemctl start "$SERVICE" || true
}

[[ -n "$VERSION" ]] || fail "PECAN_UPDATE_VERSION is empty"
[[ -n "$TARBALL_URL" ]] || fail "PECAN_UPDATE_TARBALL_URL is empty"
[[ -d "$KIT_ROOT" ]] || fail "Kit root missing: $KIT_ROOT"

write_status "downloading" "Downloading release $VERSION"
log "Downloading $TARBALL_URL"
mkdir -p "$WORK/dl" "$WORK/extract"
TARBALL="$WORK/dl/release.tar.gz"
curl -fsSL -A "pecan-vision-kit-updater" -o "$TARBALL" "$TARBALL_URL"

EXPECTED=""
SUMS=""
if [[ -n "$SHA256_URL" ]]; then
  SUMS="$WORK/dl/SHA256SUMS"
  curl -fsSL -A "pecan-vision-kit-updater" -o "$SUMS" "$SHA256_URL"
  BASE="$(basename "${TARBALL_URL%%\?*}")"
  LINE="$(grep -F "$BASE" "$SUMS" 2>/dev/null | head -1 || true)"
  if [[ -z "$LINE" ]]; then
    LINE="$(head -1 "$SUMS" || true)"
  fi
  EXPECTED="$(awk '{print $1}' <<<"$LINE" | tr -d '[:space:]')"
fi

if [[ -n "$EXPECTED" ]]; then
  ACTUAL="$(sha256sum "$TARBALL" | awk '{print $1}')"
  if [[ "$ACTUAL" != "$EXPECTED" ]]; then
    fail "SHA-256 mismatch (expected $EXPECTED, got $ACTUAL)"
  fi
  log "SHA-256 OK"
else
  log "No SHA256SUMS asset — continuing without checksum verify"
fi

write_status "extracting" "Extracting release archive"
tar -xzf "$TARBALL" -C "$WORK/extract"
SRC="$(find "$WORK/extract" -mindepth 1 -maxdepth 1 -type d | head -1)"
[[ -n "$SRC" && -d "$SRC" ]] || fail "Could not find extracted release directory"
[[ -f "$SRC/VERSION" || -d "$SRC/app" ]] || fail "Release archive looks incomplete"

write_status "backing_up" "Saving rollback snapshot to $BACKUP_DIR"
snapshot_tree "$BACKUP_DIR"

write_status "installing" "Stopping service and installing $VERSION"
systemctl stop "$SERVICE" || true
sleep 1

rsync -a --delete \
  --exclude '.venv/' \
  --exclude 'weights/' \
  --exclude 'config/ap-credentials.txt' \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude 'dist/' \
  --exclude '*.pt' \
  --exclude '*.pth' \
  "$SRC/" "$KIT_ROOT/"

chmod +x "$KIT_ROOT/scripts/"*.sh 2>/dev/null || true

write_status "deps" "Refreshing Python dependencies"
if [[ -x "$KIT_ROOT/.venv/bin/pip" ]]; then
  if ! "$KIT_ROOT/.venv/bin/pip" install -r "$KIT_ROOT/app/requirements.txt"; then
    restore_backup "$BACKUP_DIR"
    fail "pip install failed; rolled back to $PREV_VERSION"
  fi
  # ultralytics asks for opencv-python. Put the headless camera build back
  # only when that full package was actually installed.
  if "$KIT_ROOT/.venv/bin/pip" show opencv-python >/dev/null 2>&1; then
    "$KIT_ROOT/.venv/bin/pip" uninstall -y opencv-python || true
    if ! "$KIT_ROOT/.venv/bin/pip" install --force-reinstall --no-deps "opencv-python-headless>=4.8"; then
      restore_backup "$BACKUP_DIR"
      fail "OpenCV restore failed; rolled back to $PREV_VERSION"
    fi
  fi
fi

USER_NAME="$(stat -c '%U' "$KIT_ROOT" 2>/dev/null || echo engr-ugaif)"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
if [[ -f "$KIT_ROOT/systemd/pecan-vision.service" ]]; then
  TMP="$(mktemp)"
  sed \
    -e "s|/home/engr-ugaif|$HOME_DIR|g" \
    -e "s|User=engr-ugaif|User=$USER_NAME|g" \
    -e "s|Group=engr-ugaif|Group=$USER_NAME|g" \
    "$KIT_ROOT/systemd/pecan-vision.service" > "$TMP"
  cp "$TMP" /etc/systemd/system/pecan-vision.service
  rm -f "$TMP"
  systemctl daemon-reload
fi

if [[ -f "$KIT_ROOT/config/sudoers-pecan-vision" ]]; then
  SUDO_TMP="$(mktemp)"
  sed \
    -e "s|engr-ugaif|$USER_NAME|g" \
    -e "s|/home/engr-ugaif|$HOME_DIR|g" \
    "$KIT_ROOT/config/sudoers-pecan-vision" > "$SUDO_TMP"
  if visudo -cf "$SUDO_TMP"; then
    cp "$SUDO_TMP" /etc/sudoers.d/pecan-vision
    chmod 440 /etc/sudoers.d/pecan-vision
  fi
  rm -f "$SUDO_TMP"
fi

systemctl start "$SERVICE"
write_status "verifying" "Waiting for web app health check"

OK=0
for _ in $(seq 1 40); do
  sleep 1
  if curl -fsS "http://127.0.0.1:8080/api/status" >/tmp/pecan-status.json 2>/dev/null; then
    GOT="$(python3 - <<'PY'
import json
from pathlib import Path
try:
    data = json.loads(Path("/tmp/pecan-status.json").read_text())
    print((data.get("version") or "").lstrip("vV"))
except Exception:
    print("")
PY
)"
    WANT="${VERSION#v}"
    WANT="${WANT#V}"
    FILE_VER="$(tr -d '[:space:]' < "$KIT_ROOT/VERSION" || true)"
    FILE_VER="${FILE_VER#v}"
    FILE_VER="${FILE_VER#V}"
    if [[ -n "$GOT" && ( "$GOT" == "$WANT" || "$FILE_VER" == "$WANT" ) ]]; then
      OK=1
      break
    fi
  fi
done

if [[ "$OK" -ne 1 ]]; then
  restore_backup "$BACKUP_DIR"
  fail "Health check failed after update; rolled back to $PREV_VERSION"
fi

write_status "ok" "Updated to $VERSION successfully"
log "Update to $VERSION complete"
exit 0
