# Admin / VNC / troubleshooting

## Open the admin page

Use the same address as the dashboard, with `/admin` at the end.

- Over the Ethernet cable: `http://usda-vision-08.local:8080/admin` (use the number on your label)
- After the kit is on Wi-Fi: `http://192.168.137.64:8080/admin` (use the Wi-Fi numbers you wrote down)
- On the Pi itself: `http://localhost:8080/admin`

From there you can **start / stop / restart** the `pecan-vision` service, see disk space, camera status, network, and recent logs.

## VNC into the Pi

The kit’s desktop is already available over VNC. The full steps are in [Quick start](open-the-dashboard.md).

1. Keep the Ethernet cable connected for the first connection.
2. In RealVNC Viewer, or another VNC app, connect to `usda-vision-08.local:5900` (use the number on your label).
3. Log in as `pecan`. The password is the kit’s name on the label followed immediately by `1234`. Kit `usda-vision-08` uses `usda-vision-081234`.
4. Open a terminal or browser on the Pi’s desktop as needed.

Opening the dashboard from a laptop is covered in [Quick start](open-the-dashboard.md).

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
- Kit user (shipped): `pecan`
- App path: `~/pecan-vision-kit/app/server.py` (canonical; not on the Desktop)
- Virtualenv: `~/pecan-vision-kit/.venv`
- Version file: `~/pecan-vision-kit/VERSION` (footer shows `0.x preview` until 1.0.0)

## Desktop launchers

After bootstrap (or `./scripts/install-desktop-launchers.sh`), the kit Desktop has:

- **Start Pecan Vision** — ensure clone/deps/service, open the UI
- **Update Pecan Vision** — same GitHub Releases path as the dashboard banner
- **Setup / Repair Pecan Vision** — interactive `setup-kit.sh`

## Software updates

The dashboard checks [GitHub Releases](https://github.com/salirezav/pecan_vision_kit/releases) about once an hour when the kit can reach the internet (usually Ethernet). If a newer release exists, every page shows a banner with **Update**. You can also use the Desktop **Update Pecan Vision** launcher.

On click, `scripts/apply-update.sh` downloads the release tarball, verifies `SHA256SUMS` when present, backs up the current app tree, installs the new files, refreshes Python deps, restarts `pecan-vision`, and rolls back if the health check fails.

- Status file: `/mnt/recordings/update-status.json`
- Apply log: `/mnt/recordings/update-apply.log`
- Recordings and `/mnt/recordings/config.yaml` are left alone
- Model weights under `weights/` are left alone
- Models shipped in `models/` are part of the release. The dashboard asks before copying any that are not already in `/mnt/recordings/models/library`. The same prompt is `scripts/install-bundled-models.sh`.
- The kit needs a path to the internet before an update can download. That path is the Wi-Fi network the kit has joined, or an Ethernet connection that reaches the internet.

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

## Re-run setup on a kit

```bash
cd ~/pecan-vision-kit
./scripts/setup-kit.sh
```

The script is interactive and safe to re-run; it asks before destructive disk operations.
