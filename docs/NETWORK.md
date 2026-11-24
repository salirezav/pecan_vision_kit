# Network design — is always-on Access Point doable?

**Short answer:** Yes, with one important hardware constraint.

A Raspberry Pi 5 has **one** onboard Wi‑Fi radio. That radio cannot reliably be a Wi‑Fi **client** (joined to the plant network) **and** a full Access Point at the same time. Linux “concurrent AP+STA” on this chip is flaky and not something we want plant staff depending on.

## Recommended design (what we implement)

```
  Phone / laptop                Plant network
       │                              │
       │  Wi‑Fi                       │  Ethernet cable
       ▼                              ▼
 ┌─────────────────────────────────────────────┐
 │  Pi 5                                       │
 │  wlan0 = ALWAYS-ON config Access Point      │
 │  eth0  = plant LAN (DHCP or static)         │
 │  Web UI on port 8080 (both interfaces)      │
 └─────────────────────────────────────────────┘
```

| Interface | Role | Always on? |
|-----------|------|------------|
| **wlan0** | Config Access Point (`PecanCam-XX`) | **Yes** — staff can always join it |
| **eth0** | Plant LAN / internet | When a cable is plugged in |

### What attendees do

1. On their phone: join Wi‑Fi **`PecanCam-XX`** (password on the kit sticker).
2. Open browser to **`http://10.42.0.1:8080`** (AP gateway address).
3. Use the recorder UI, or open **Network** in the admin page to set plant Ethernet / see status.
4. Optional: plug Ethernet into the plant switch so the kit is also on the plant LAN (VNC, shared drives, etc.).

### Why this is friendliest

- No VNC required for day‑to‑day recording.
- Config Wi‑Fi never “disappears” after joining the plant network.
- Plant IT usually prefers a cable for industrial cameras/recorders anyway.

## Alternatives (if a plant has no Ethernet)

| Option | Always-on AP? | Notes |
|--------|---------------|-------|
| **A. Ethernet + AP (recommended)** | Yes | Best reliability |
| **B. USB Wi‑Fi dongle as 2nd radio** | Yes | Dongle = plant Wi‑Fi client; onboard = AP. Extra hardware per kit (~$15–25). |
| **C. Temporary AP, then join plant Wi‑Fi** | No | AP stops after plant Wi‑Fi connects; harder for non‑technical users to recover |
| **D. Same-radio concurrent AP+STA** | Unreliable | Not supported as a workshop default |

If you later buy USB Wi‑Fi adapters for all 10 kits, we can extend `setup-kit.sh` for Option B without changing the web app.

## Access Point defaults (set by setup script)

| Setting | Default |
|---------|---------|
| SSID | `PecanCam-<kit-id>` (e.g. `PecanCam-03`) |
| Password | Chosen interactively during setup (write on sticker) |
| AP IP | `10.42.0.1` |
| Web UI | `http://10.42.0.1:8080` |
| DHCP for phones | Yes (NetworkManager shared/hotspot) |

## VNC

WayVNC is enabled on Raspberry Pi OS. Use it for **admin** work (editing files, hard troubleshooting), not as the primary recorder UI.

- From plant LAN (Ethernet): VNC to the Pi’s LAN IP.
- From the config AP: VNC to `10.42.0.1`.
