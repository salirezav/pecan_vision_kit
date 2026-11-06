# Pecan Vision Kit — quick user guide

## Record a video (easiest path)

1. Power on the kit and wait ~1 minute.
2. On your phone, open Wi‑Fi and join **`PecanCam-XX`** (name and password are on the sticker on the case).
3. Open the phone browser and go to: **`http://10.42.0.1:8080`**
4. You should see the live camera picture.
5. Press **Start recording**. The red **RECORDING** badge appears.
6. Press **Stop recording** when done.
7. Videos are stored on the kit’s internal drive (not on your phone).

## If a monitor is plugged into the Pi

On kits with **kiosk mode** enabled, the recorder UI opens automatically fullscreen after login. Use **Start recording** / **Stop recording** the same way.

If the browser did not open by itself:

1. Open the web browser on the Pi.
2. Go to **`http://localhost:8080`**
3. Use Start / Stop the same way.

## Watch, rename, download, or delete videos

Open **Videos**. Tap a file to play it. Use **Download**, **Rename**, or **Delete** on each row. **Browse save folder** lets you pick where new recordings go (kit drive or a USB stick).

After you press **Stop recording**, the kit converts the capture to **H.264 MP4** (saves space and plays in the browser). The Videos list shows **Converting…** until that finishes — then you can watch or download.

## Change resolution or frame rate

Open **Settings**. The live camera preview stays on the left so you can see what each change does. Prefer **1280×720** and **60–120 fps** for fast product on the line.

Use **Browse** next to Save folder to pick a location. The kit runs a **storage speed check** against your current resolution/FPS — if the drive is too slow, follow the on-screen hints (lower FPS/resolution or use kit NVMe storage).

## Plant Ethernet (optional)

If IT gives you a network cable, plug it into the Pi’s Ethernet port. The config Wi‑Fi (`PecanCam-XX`) stays available.
