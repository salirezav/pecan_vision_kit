#!/usr/bin/env bash
# Desktop "Update Pecan Vision" — same path as the dashboard updater (GitHub Releases).
# Writes /mnt/recordings/update-request.json and launches scripts/apply-update.sh.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script needs bash." >&2
  exit 1
fi
set -euo pipefail

USER_NAME="${SUDO_USER:-$USER}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
KIT_ROOT="${PECAN_KIT_ROOT:-$HOME_DIR/pecan-vision-kit}"
GITHUB_REPO="${PECAN_GITHUB_REPO:-salirezav/pecan_vision_kit}"
GITHUB_API="https://api.github.com/repos/${GITHUB_REPO}/releases/latest"
REQUEST_FILE="/mnt/recordings/update-request.json"
STATUS_FILE="/mnt/recordings/update-status.json"
LOG_FILE="/mnt/recordings/update-apply.log"
USER_AGENT="pecan-vision-kit-updater"

_SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$_SCRIPT_ROOT/app/server.py" && -z "${PECAN_KIT_ROOT:-}" ]]; then
  KIT_ROOT="$_SCRIPT_ROOT"
fi

RED=$'\033[31m'; GREEN=$'\033[32m'; YEL=$'\033[33m'; CYA=$'\033[36m'; RST=$'\033[0m'
info() { printf '%s==>%s %s\n' "$CYA" "$RST" "$*"; }
ok()   { printf '%s[ok]%s %s\n' "$GREEN" "$RST" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$YEL" "$RST" "$*"; }
err()  { printf '%s[error]%s %s\n' "$RED" "$RST" "$*" >&2; }

[[ -d "$KIT_ROOT/app" ]] || { err "Kit root missing: $KIT_ROOT — run Start Pecan Vision first"; exit 1; }
[[ -x "$KIT_ROOT/scripts/apply-update.sh" ]] || { err "Missing apply-update.sh"; exit 1; }

LOCAL_VERSION="$(tr -d '[:space:]' < "$KIT_ROOT/VERSION" 2>/dev/null || echo 0.0.0)"
info "Current version: $LOCAL_VERSION"
info "Checking GitHub Releases…"

RELEASE_JSON="$(mktemp)"
cleanup() { rm -f "$RELEASE_JSON"; }
trap cleanup EXIT

if ! curl -fsSL -A "$USER_AGENT" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -o "$RELEASE_JSON" "$GITHUB_API"; then
  err "Could not reach GitHub. Plug in Ethernet (the config Wi‑Fi AP cannot reach the internet)."
  exit 1
fi

eval "$(python3 - "$RELEASE_JSON" "$LOCAL_VERSION" "$GITHUB_REPO" <<'PY'
import json, re, sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
local = sys.argv[2].strip().lstrip("vV")
repo = sys.argv[3]
tag = str(data.get("tag_name") or "").strip()
ver = tag.lstrip("vV")

def parse(v: str):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v.lstrip("vV"))
    return tuple(int(x) for x in m.groups()) if m else None

rp, lp = parse(ver), parse(local)
newer = bool(rp and lp and rp > lp) or (rp is None and bool(ver) and ver != local)

tarball = None
sha = None
for asset in data.get("assets") or []:
    if not isinstance(asset, dict):
        continue
    name = str(asset.get("name") or "").lower()
    url = asset.get("browser_download_url")
    if not url:
        continue
    if name.endswith(".tar.gz") and "pecan" in name:
        tarball = str(url)
    elif name in ("sha256sums", "sha256sums.txt") or name.endswith(".sha256"):
        sha = str(url)
if not tarball:
    tarball = f"https://github.com/{repo}/releases/download/{tag}/pecan_vision_kit-{ver}.tar.gz"

def esc(value: str) -> str:
    return value.replace("'", "'\"'\"'")

print(f"LATEST='{esc(ver)}'")
print(f"NEWER={'1' if newer else '0'}")
print(f"TARBALL_URL='{esc(tarball)}'")
print(f"SHA256_URL='{esc(sha or '')}'")
print(f"RELEASE_URL='{esc(str(data.get('html_url') or ''))}'")
PY
)"

if [[ "$NEWER" != "1" ]]; then
  ok "Already up to date (local $LOCAL_VERSION, latest ${LATEST:-unknown})."
  [[ -n "${RELEASE_URL:-}" ]] && echo "  $RELEASE_URL"
  exit 0
fi

info "Update available: $LOCAL_VERSION → $LATEST"
echo "  $RELEASE_URL"
echo "  Stop recording before updating. Recordings, config.yaml, and weights/ are kept."

if [[ -t 0 ]]; then
  read -r -p "Apply update now? [y/N]: " reply || true
  [[ "${reply:-}" =~ ^[Yy]$ ]] || { warn "Update cancelled."; exit 0; }
fi

mkdir -p /mnt/recordings
python3 - "$REQUEST_FILE" "$LATEST" "$TARBALL_URL" "${SHA256_URL:-}" "$KIT_ROOT" <<'PY'
import json, sys, time
from pathlib import Path
path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    "version": sys.argv[2],
    "tarball_url": sys.argv[3],
    "sha256_url": sys.argv[4],
    "kit_root": sys.argv[5],
    "requested_at": time.time(),
    "source": "desktop-update",
}, indent=2) + "\n")
PY

python3 - "$STATUS_FILE" "$LATEST" <<'PY'
import json, sys, time
from pathlib import Path
path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    "state": "starting",
    "target_version": sys.argv[2],
    "started_at": time.time(),
    "message": "Update requested from the Desktop launcher.",
}, indent=2) + "\n")
PY

info "Launching apply-update.sh…"
{
  echo
  echo "--- desktop apply $LATEST at $(date -Is) ---"
} >> "$LOG_FILE"

SCRIPT="$KIT_ROOT/scripts/apply-update.sh"
if sudo -n true 2>/dev/null; then
  sudo -n /usr/bin/setsid /usr/bin/bash "$SCRIPT" >>"$LOG_FILE" 2>&1 &
else
  warn "Passwordless sudo not available — you may be prompted for the kit password."
  sudo /usr/bin/setsid /usr/bin/bash "$SCRIPT" >>"$LOG_FILE" 2>&1 &
fi

ok "Updater started."
echo "  Status:  cat $STATUS_FILE"
echo "  Log:     tail -f $LOG_FILE"
echo "  The web UI will drop briefly while the service restarts."

for _ in $(seq 1 90); do
  if [[ -f "$STATUS_FILE" ]]; then
    state="$(python3 -c "import json; print(json.load(open('$STATUS_FILE')).get('state',''))" 2>/dev/null || true)"
    msg="$(python3 -c "import json; print(json.load(open('$STATUS_FILE')).get('message',''))" 2>/dev/null || true)"
    printf '\r  state=%s  %s' "$state" "$msg"
    if [[ "$state" == "ok" ]]; then
      echo
      ok "Updated to $LATEST"
      exit 0
    fi
    if [[ "$state" == "failed" ]]; then
      echo
      err "Update failed: $msg"
      echo "  See $LOG_FILE"
      exit 1
    fi
  fi
  sleep 2
done
echo
warn "Still running — check $STATUS_FILE and $LOG_FILE"
exit 0
