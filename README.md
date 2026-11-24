# Pecan Vision Kit

Web recorder and vision tools for Raspberry Pi 5 workshop kits (USB global-shutter camera + NVMe storage).

**Docs site:** [salirezav.github.io/pecan_vision_kit](https://salirezav.github.io/pecan_vision_kit/) (this README, the quick starts, and the kit guides, published via GitHub Pages).

**The `0.x` line is a preview.** It is not a finished, bullet-proof release. Expect rough edges, missing polish, and breaking changes until `1.0.0`.

## Hardware

- Raspberry Pi 5 (Raspberry Pi OS / Debian)
- USB3 UVC global-shutter camera (ELP/Ailipu AR0234-class; **not** CSI / `picamera2`)
- Storage for videos and runtime config at `/mnt/recordings` — either a spare NVMe (when the OS is on SD) or a directory on the OS disk (NVMe-only kits). Setup never formats the system disk.

## Quick install

On a kit, the sign-in is `pecan`. The password is the name on the label followed immediately by `1234` (kit `usda-vision-08` uses `usda-vision-081234`). To open the dashboard from a laptop, see [Open the dashboard](docs/open-the-dashboard.md).

```bash
# On a new kit image (as root once):
sudo ./scripts/ensure-kit-user.sh

# As the kit user — non-interactive bootstrap (clone/deps/service/Desktop icons):
git clone https://github.com/salirezav/pecan_vision_kit.git ~/pecan-vision-kit
cd ~/pecan-vision-kit
chmod +x scripts/*.sh
./scripts/bootstrap-kit.sh
```

Or double-click **Start Pecan Vision** on the Desktop after `ensure-kit-user.sh` seeded the launcher.

For AP / NVMe format / kiosk options, run the interactive installer:

```bash
./scripts/setup-kit.sh
```

Then open `http://127.0.0.1:8080` on the Pi, or `http://<pi-ip>:8080` on the LAN.

The interactive setup script can:

1. Install OS packages (including `hailo-all` for HEF / Hailo-8), Python venv with system site-packages
2. Prepare recordings storage (spare NVMe or directory on the OS disk)
3. Install the `pecan-vision` systemd service
4. Configure an always-on Wi‑Fi config access point
5. Enable Chromium kiosk autostart

After installing Hailo packages on a fresh board, reboot once if `/dev/hailo0` is missing so the PCIe driver loads.

## Model weights

`.pt` / `.pth` files are **not** shipped in this repository (they are gitignored). Place models under `weights/` on each kit, or upload them from the **Model** page. The page reads the task and class list from the file when the checkpoint stores them. RF-DETR segmentation files and YOLO checkpoints (detection, segmentation, classification, pose, rotated boxes, semantic segmentation, and depth) can be loaded. YOLO support uses [Ultralytics](https://github.com/ultralytics/ultralytics), which is AGPL-3.0. App updates do not replace local weights.

## Updates

Kits install newer versions from the public [GitHub Releases](https://github.com/salirezav/pecan_vision_kit/releases) page. The kit downloads the release with plain `curl`. A GitHub login is not required.

The Pi needs a path to the internet, usually Ethernet. The config Wi-Fi access point alone cannot reach GitHub. Stop any recording before you update. Recordings, `/mnt/recordings/config.yaml`, and model files under `weights/` stay on the kit.

### From the dashboard or Desktop

When a newer release exists, every page shows a banner with **Update**. The Desktop **Update Pecan Vision** launcher does the same thing. Both run `scripts/apply-update.sh`, which downloads the release tarball, checks `SHA256SUMS`, installs the files, refreshes Python dependencies, and restarts `pecan-vision`. If the health check fails, it puts the previous app files back.

The banner stays on “Update requested from the dashboard” until the site comes back. It does not list each step. To watch the installer on the Pi:

```bash
cat /mnt/recordings/update-status.json
tail -f /mnt/recordings/update-apply.log
```

`state` in the status file moves through `starting`, `downloading`, `extracting`, `backing_up`, `installing`, `deps`, `verifying`, and `ok`. Refreshing Python packages (`deps`) is the slow step. `failed` means the installer stopped and restored the previous app files.

The website drops while the service restarts. Open it again afterward. The footer should show the new version, for example `0.2.0 preview`.

### If the dashboard update does not run

SSH in as the kit user and read `/mnt/recordings/update-apply.log`.

`sudo: a password is required` means the button tried to start the installer without a password and this Pi refused. The download did not start. The status file can stay on `starting` with “Update requested from the dashboard” even though nothing else happened.

Clicking **Update** still saves the release it intended to install in `/mnt/recordings/update-request.json`. Finish that same update from the shell. This asks for the Pi login password once:

```bash
sudo bash ~/pecan-vision-kit/scripts/apply-update.sh
```

Watch the log. A working run prints `Downloading`, then `SHA-256 OK`, then restarts the service. When the site loads again, check the footer version.

That successful run also installs the passwordless sudo rule in `/etc/sudoers.d/pecan-vision`, so the next dashboard **Update** does not ask for a password. To install that rule on its own, re-run `./scripts/setup-kit.sh` and choose **4) Web service only**.

### If the install fails after it starts

The script keeps a snapshot at `/var/lib/pecan-vision/backup/<previous-version>/` and restores it when dependency install or the health check fails. It also puts back the previous `pecan-vision` service file and sudoers rule. Read the last lines of `/mnt/recordings/update-apply.log` and the `message` field in `/mnt/recordings/update-status.json`.

If the site stays down and the installer is no longer running, start the service again:

```bash
sudo systemctl start pecan-vision.service
sudo systemctl status pecan-vision.service
journalctl -u pecan-vision -n 80 --no-pager
```

## Docs

| Doc | Purpose |
|-----|---------|
| [Open the dashboard](docs/open-the-dashboard.md) | Connect a laptop and open the kit in a browser |
| [Quick start](docs/USER-GUIDE.md) | Record, watch, and change camera settings |
| [Admin](docs/ADMIN.md) | Service control, updates, troubleshooting |

## License

MIT — see [LICENSE](LICENSE).
