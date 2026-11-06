# Admin / VNC / troubleshooting

## Open the admin page

- From config AP: `http://10.42.0.1:8080/admin`
- From Ethernet / localhost: `http://<pi-ip>:8080/admin` or `http://localhost:8080/admin`

From there you can **start / stop / restart** the `pecan-vision` service, see disk space, camera status, network, and recent logs.

## VNC into the Pi

WayVNC is part of Raspberry Pi OS and is already enabled on the first kit.

1. Connect to the Pi (config AP or plant Ethernet).
2. Use RealVNC Viewer / TigerVNC / similar → Pi IP (`10.42.0.1` on the AP).
3. Log in as the kit user.
4. Open a terminal or browser as needed.

Useful terminal commands:

```bash
sudo systemctl status pecan-vision
sudo systemctl restart pecan-vision
journalctl -u pecan-vision -n 80 --no-pager

lsusb | grep -i shutter
v4l2-ctl --list-devices
df -h /mnt/recordings
ls -lt /mnt/recordings/videos | head
nmcli device status
nmcli connection show
```

## Service name

- Unit file: `pecan-vision.service`
- App path: `~/pecan-vision-kit/app/server.py`
- Virtualenv: `~/pecan-vision-kit/.venv`
- Version file: `~/pecan-vision-kit/VERSION` (footer shows `0.x preview` until 1.0.0)

## Software updates

The dashboard checks [GitHub Releases](https://github.com/salirezav/pecan_vision_kit/releases) about once an hour when the kit can reach the internet (usually Ethernet). If a newer release exists, every page shows a banner with **Update**.

On click, `scripts/apply-update.sh` downloads the release tarball, verifies `SHA256SUMS` when present, backs up the current app tree, installs the new files, refreshes Python deps, restarts `pecan-vision`, and rolls back if the health check fails.

- Status file: `/mnt/recordings/update-status.json`
- Apply log: `/mnt/recordings/update-apply.log`
- Recordings and `/mnt/recordings/config.yaml` are left alone
- Model weights under `weights/` are left alone
- The config Wi‑Fi AP alone does not provide GitHub access

Re-run `./scripts/setup-kit.sh` option **4** (web service) after pulling updater changes so `/etc/sudoers.d/pecan-vision` includes the apply-update path.

## Videos + save locations

- UI: `http://<pi>:8080/videos` — play, rename, delete; browse save folder.
- Allowed browse roots: `/mnt/recordings`, `/media`, `/run/media` (USB automounts appear under `/media/<user>/…`).
- Speed check API: `POST /api/storage/analyze` with optional `path`, `width`, `height`, `fps` — compares a write probe to estimated MJPG bitrate and returns hints.

## Chromium kiosk (monitor on the Pi)

When enabled, desktop login opens Chromium fullscreen to `http://127.0.0.1:8080`.

- Script: `~/pecan-vision-kit/scripts/start-kiosk.sh`
- Autostart file: `~/.config/autostart/pecan-vision-kiosk.desktop`
- Enable / re-install: `./scripts/setup-kit.sh` → option **7**
- Disable: delete `~/.config/autostart/pecan-vision-kiosk.desktop` (or uncheck in session autostart settings)
- Manual test: `~/pecan-vision-kit/scripts/start-kiosk.sh`

## Access Point (kit 01 status)

Always-on AP (`PecanCam-XX` on `wlan0`) is documented in `docs/NETWORK.md` but **not activated on kit 01 yet** (would drop the current Wi‑Fi client). Prefer Ethernet on `eth0`, then:

```bash
./scripts/setup-kit.sh   # option 5 — Access Point only
# or later: sudo nmcli connection up PecanCam-AP
```

Credentials (when created) live in `config/ap-credentials.txt` (chmod 600; gitignored).

## Re-run setup on a kit

```bash
cd ~/pecan-vision-kit
./scripts/setup-kit.sh
```

The script is interactive and safe to re-run; it asks before destructive disk operations.
