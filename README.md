# Pecan Vision Kit

Web recorder and vision tools for Raspberry Pi 5 workshop kits (USB global-shutter camera + NVMe storage).

**Version 0.1.0 is a preview.** The `0.x` line is not a finished, bullet-proof release. Expect rough edges, missing polish, and breaking changes until `1.0.0`.

## Hardware

- Raspberry Pi 5 (Raspberry Pi OS / Debian)
- USB3 UVC global-shutter camera (ELP/Ailipu AR0234-class; **not** CSI / `picamera2`)
- NVMe SSD mounted at `/mnt/recordings` for videos and runtime config

## Quick install

```bash
git clone https://github.com/salirezav/pecan_vision_kit.git
cd pecan_vision_kit
chmod +x scripts/setup-kit.sh
./scripts/setup-kit.sh
```

Then open `http://127.0.0.1:8080` on the Pi, or `http://<pi-ip>:8080` on the LAN.

The interactive setup script can:

1. Install OS packages and the Python virtualenv
2. Prepare the NVMe recordings drive
3. Install the `pecan-vision` systemd service
4. Configure an always-on Wi‑Fi config access point
5. Enable Chromium kiosk autostart

## Model weights

`.pt` / `.pth` files are **not** shipped in this repository (they are gitignored). Place models under `weights/` on each kit, or upload them from the **Model** page. The page reads the task and class list from the file when the checkpoint stores them. RF-DETR segmentation files and YOLO checkpoints (detection, segmentation, classification, pose, rotated boxes, semantic segmentation, and depth) can be loaded. YOLO support uses [Ultralytics](https://github.com/ultralytics/ultralytics), which is AGPL-3.0. App updates do not replace local weights.

## Updates

When the kit can reach GitHub (usually via Ethernet), the dashboard checks [Releases](https://github.com/salirezav/pecan_vision_kit/releases) and shows a banner if a newer version is available. Click **Update** to download, install, and restart the service. Recordings and `/mnt/recordings/config.yaml` stay on the kit.

## Docs

| Doc | Purpose |
|-----|---------|
| [docs/USER-GUIDE.md](docs/USER-GUIDE.md) | Simple guide for plant attendees |
| [docs/ADMIN.md](docs/ADMIN.md) | Service control, VNC, updates, troubleshooting |
| [docs/NETWORK.md](docs/NETWORK.md) | Wi‑Fi AP + plant Ethernet design |
| [docs/STEPS.md](docs/STEPS.md) | Chronological kit build log |

## License

MIT — see [LICENSE](LICENSE).
