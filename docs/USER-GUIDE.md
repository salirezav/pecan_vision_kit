<<<<<<< HEAD
# Pecan Vision Kit — quick user guide

To connect a laptop and open the dashboard, follow [Open the dashboard](open-the-dashboard.md). The steps below are what to do after the dashboard is on screen.

## Record a video

Start with the dashboard already open in the laptop browser. [Open the dashboard](open-the-dashboard.md) is how you get there, on Windows or on a Mac. The address is the Raspberry Pi’s address: `http://usda-vision-08.local:8080` over the Ethernet cable, or `http://` plus the Pi’s Wi-Fi numbers plus `:8080` after the Pi has joined that Wi-Fi. Use the number from your own label. The camera is a USB camera. It does not have an address.

1. You should see the live camera picture.
2. Press **Start recording**. A red **RECORDING** badge appears.
3. Leave the page open while the kit is recording.
4. Press **Stop recording** when you are done.
5. The video is stored on the kit, not on the laptop. Open **Videos** to watch it.

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
=======
# Pecan Vision Kit — quick user guide

To connect a laptop and open the dashboard, follow [Open the dashboard](open-the-dashboard.md). The steps below are what to do after the dashboard is on screen.

## Record a video

Start with the dashboard already open in the laptop browser. [Open the dashboard](open-the-dashboard.md) is how you get there, on Windows or on a Mac. The address is the Raspberry Pi’s address: `http://usda-vision-08.local:8080` over the Ethernet cable, or `http://` plus the Pi’s Wi-Fi numbers plus `:8080` after the Pi has joined that Wi-Fi. Use the number from your own label. The camera is a USB camera. It does not have an address.

1. You should see the live camera picture.
2. Press **Start recording**. A red **RECORDING** badge appears.
3. Leave the page open while the kit is recording.
4. Press **Stop recording** when you are done.
5. The video is stored on the kit, not on the laptop. Open **Videos** to watch it.

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
>>>>>>> 88aeec995734a46bdc17b17edad6836fdba9ef7f
