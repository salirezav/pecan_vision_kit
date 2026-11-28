# Step log — kit build history

This file records what we did on the **first** kit so the other nine can be reproduced with `scripts/setup-kit.sh`. Newest entries at the bottom.

## 2026-11-03 — Discovery

1. Identified camera as USB UVC device `32e4:5234` (“Global Shutter Camera”), ELP/Ailipu AR0234-class — **not** a Pi CSI camera.
2. Confirmed `rpicam-*` / `picamera2` see no CSI cameras; `/dev/video0` is the USB camera.
3. Formats: MJPEG up to 1920×1200@120fps, 1280×720@200fps; YUYV also available.
4. Found blank Patriot M.2 P320 256GB NVMe at `/dev/nvme0n1` (no partitions).
5. Pi OS: Debian 13 (trixie), Raspberry Pi 5 Rev 1.1; OpenCV + ffmpeg + v4l-utils already present.
6. Decided: **web app** (not desktop GUI) for non-technical attendees.
7. Network decision: **always-on Wi‑Fi AP on wlan0 + plant network via Ethernet** (see NETWORK.md). Same-radio AP+client is not reliable enough.

## 2026-11-03 — Project bootstrap

1. Renamed project directory `~/alireza` → `~/pecan-vision-kit`.
2. Created layout: `app/`, `docs/`, `scripts/`, `systemd/`, `config/`.
3. Partitioned NVMe: GPT, single partition `nvme0n1p1`, label `recordings`.
4. Formatted ext4; mounted at `/mnt/recordings`; added to `/etc/fstab` by UUID.
5. Created `/mnt/recordings/videos` owned by `engr-ugaif`.
6. Verified camera still captures (ffmpeg smoke test earlier in session).
7. Noted WayVNC already active (`wayvnc.service`).

## 2026-11-03 — Web app install (first kit)

1. Created Flask app under `app/` (preview, record, settings, admin).
2. Created interactive `scripts/setup-kit.sh` and docs (USER-GUIDE, ADMIN, NETWORK).
3. Built `.venv` with system site-packages; Flask imports OK.
4. Wrote `/mnt/recordings/config.yaml` with `kit_id: "01"`.
5. Installed `pecan-vision.service` (enabled) + `/etc/sudoers.d/pecan-vision`.
6. Service is **active**; `http://127.0.0.1:8080` returns HTTP 200; camera has frames at 1280×720@60.

## 2026-11-03 — Recording smoke test

1. `GET /api/status` — camera live at 1280×720@60, NVMe mounted (~222 GB free).
2. `POST /api/record/start` → waited ~5s → `POST /api/record/stop`.
3. Result: `/mnt/recordings/videos/rec_20261103_111714_1280x720_60fps.avi` — **303 frames**, **8.0 MB**, OK.

## 2026-11-03 — Chromium kiosk + setup polish

1. Added `scripts/start-kiosk.sh` (waits up to 30s for `http://127.0.0.1:8080`, then Chromium `--kiosk`).
2. Installed autostart: `~/.config/autostart/pecan-vision-kiosk.desktop`.
3. `setup-kit.sh`: menu option **7) Chromium kiosk only**; full setup includes kiosk; AP activate default **n**; warns when eth0 has no carrier.
4. Added repo root `.gitignore` (`.venv/`, `config/ap-credentials.txt`, `__pycache__/`, etc.).

## 2026-11-03 — Videos library + storage browser + speed analyzer

1. Added **Videos** page: play / rename / delete recordings; nav link in header.
2. Folder **Browse** modal (Settings + Videos): always available under `/mnt/recordings`, `/media`, `/run/media`; highlights USB volumes when attached.
3. Storage analyzer: write-speed probe vs estimated MJPG bitrate for current width/height/fps; shows ok/warn/fail with concrete settings hints on Record, Settings, and Videos.

## 2026-11-03 — MP4 finalize + download + quality probe

1. After **Stop**, capture AVI is converted to **H.264 MP4** (CRF 20, `veryfast`); AVI deleted when done. UI shows converting state on Record + Videos.
2. Videos page: **Download** button (`/videos/download/<name>`).
3. Quality probe on a ~4s 1280×720@60 MJPEG sample (`rec_20261103_125723_…avi`, 2.18 MiB):

| CRF | Size | vs AVI | PSNR (avg) | SSIM |
|-----|------|--------|------------|------|
| 18 | 1.46 MiB | −33% | 57.7 | 0.9987 |
| **20 (default)** | **1.13 MiB** | **−48%** | **56.3** | **0.9983** |
| 23 | 0.61 MiB | −72% | 54.4 | 0.9976 |
| 28 | 0.16 MiB | −93% | 52.2 | 0.9969 |

   SSIM ≈ 1.0 means nearly indistinguishable from the MJPEG capture for this clip; CRF 20 is the kit default (`h264_crf` in config).

## Still TODO

- Configure always-on Wi‑Fi AP (`PecanCam-01`) — **deferred**: must keep current Wi‑Fi client link; do when Ethernet is available (or configure without activating via setup option 5).
- Stickers (SSID / password / `http://10.42.0.1:8080`) — blocked until AP credentials exist.
