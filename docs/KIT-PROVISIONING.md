<<<<<<< HEAD
# Kit provisioning (shipping identity)

How to image and hand off a Pecan Vision Kit without campus credentials.

## Who gets a Linux account

| Who | Linux login? |
|-----|----------------|
| Plant / workshop operators | No — phone Wi‑Fi AP or on-Pi kiosk browser only |
| Kit OS account | Yes — one user: **`pecan`** |
| Separate “app” system user | No — not worth the complexity on these kits |
| Per-attendee Linux users | No |

**Do not ship `engr-ugaif` or any shared UGA password.** Lab machines can keep that account for development; field kits must not.

## Password policy

- Default Linux password for `pecan` is **`{hostname}1234`**, using the short hostname from the Pi (`hostname` / `hostname -s`). Example: hostname `pecancam-03` → password `pecancam-031234`.
- Override with `PECAN_KIT_PASSWORD` if needed; otherwise `ensure-kit-user.sh` always applies the hostname rule (no interactive `passwd` prompt).
- Write that password on the admin sticker / card next to the `PecanCam-XX` Wi‑Fi password. Set a distinct hostname per kit so passwords differ.
- The kit user is in the `sudo` group (setup and updates need it). Passwordless sudo is limited to service control + `apply-update.sh` via `/etc/sudoers.d/pecan-vision`.

## Canonical paths

| Item | Path |
|------|------|
| Kit user home | `/home/pecan` |
| App / repo | `/home/pecan/pecan-vision-kit` |
| Desktop | Launchers only — **not** the live git tree |
| Recordings + runtime config | `/mnt/recordings` (spare NVMe mount, or a directory on `/` when the OS lives on the only NVMe) |
| Model library | `/mnt/recordings/models/library` (what the dashboard runs) |
| Shipped models | `models/` in the repo. Copied into the library from the dashboard prompt or `scripts/install-bundled-models.sh` |
| Extra local weights | `~/pecan-vision-kit/weights/` (not in git, not replaced by updates) |

## First image / re-image steps

1. Flash Raspberry Pi OS (desktop) onto the kit storage.
2. Create the kit account and seed Desktop launcher:

   ```bash
   # From a temporary copy of the repo, or after cloning once as root-capable admin.
   # Sets password to $(hostname)1234 unless PECAN_KIT_PASSWORD is set:
   sudo ./scripts/ensure-kit-user.sh
   # Optional desktop autologin for kiosk kits:
   sudo PECAN_AUTOLOGIN=1 ./scripts/ensure-kit-user.sh
   ```

3. Log in as `pecan` (or let autologin do it).
4. Double-click **Start Pecan Vision** on the Desktop (or run `~/.local/share/pecan-vision/seed-start.sh`).
   That clones into `~/pecan-vision-kit` if needed, installs deps, enables `pecan-vision.service`, refreshes Desktop launchers, and opens the UI.
5. For Wi‑Fi AP, kiosk, or (lab only) spare-NVMe formatting, use **Setup / Repair Pecan Vision** (`scripts/setup-kit.sh`). Field kits with OS on NVMe never format that disk — videos go to `/mnt/recordings` on the root filesystem.
6. Write on the sticker:
   - AP SSID + password (from setup / `config/ap-credentials.txt`)
   - Linux user `pecan` + password `{hostname}1234` (VNC / SSH / sudo)

## Day-to-day

- Service starts on boot via systemd.
- **Start Pecan Vision** is for recover / after login.
- Updates: dashboard **Update** banner **or** Desktop **Update Pecan Vision** — both call `scripts/apply-update.sh` (GitHub Releases). Do not use `git pull` as the field update path.

## Scripts reference

| Script | Role |
|--------|------|
| `scripts/ensure-kit-user.sh` | Create `pecan`, groups, password, seed Desktop Start |
| `scripts/bootstrap-kit.sh` | Non-interactive: clone/deps/service/start |
| `scripts/desktop-start.sh` | Start launcher action |
| `scripts/desktop-update.sh` | Update launcher → same as dashboard updater |
| `scripts/desktop-setup.sh` | Opens interactive `setup-kit.sh` |
| `scripts/install-desktop-launchers.sh` | Writes Start / Update / Setup icons on Desktop |
| `scripts/setup-kit.sh` | Interactive full installer |
| `scripts/apply-update.sh` | Release install + rollback |
| `scripts/install-bundled-models.sh` | Ask, then copy shipped models into the dashboard library |

## Migrating a lab kit off `engr-ugaif`

**Option A — ship a clean image:** flash OS, run `ensure-kit-user.sh`, bootstrap as `pecan`. Copy weights and `/mnt/recordings` data if needed.

**Option B — keep developing as `engr-ugaif`:** leave the lab Pi alone. Service/sudoers templates use `@KIT_USER@` / `@KIT_ROOT@` and are rewritten for whoever runs setup/bootstrap. Shipping images should still use `pecan`.

**Option C — convert an existing kit in place:**

```bash
sudo ./scripts/ensure-kit-user.sh          # create pecan
# Copy or re-clone the tree to /home/pecan/pecan-vision-kit
sudo rsync -a /home/engr-ugaif/pecan-vision-kit/ /home/pecan/pecan-vision-kit/
sudo chown -R pecan:pecan /home/pecan/pecan-vision-kit
sudo -u pecan -H bash -lc 'cd ~/pecan-vision-kit && ./scripts/bootstrap-kit.sh'
```

Then stop using `engr-ugaif` on that device for the service (disable old unit paths if still pointing at the old home).
=======
# Kit provisioning (shipping identity)

How to image and hand off a Pecan Vision Kit without campus credentials.

## Who gets a Linux account

| Who | Linux login? |
|-----|----------------|
| Plant / workshop operators | No — phone Wi‑Fi AP or on-Pi kiosk browser only |
| Kit OS account | Yes — one user: **`pecan`** |
| Separate “app” system user | No — not worth the complexity on these kits |
| Per-attendee Linux users | No |

**Do not ship `engr-ugaif` or any shared UGA password.** Lab machines can keep that account for development; field kits must not.

## Password policy

- Default Linux password for `pecan` is **`{hostname}1234`**, using the short hostname from the Pi (`hostname` / `hostname -s`). Example: hostname `pecancam-03` → password `pecancam-031234`.
- Override with `PECAN_KIT_PASSWORD` if needed; otherwise `ensure-kit-user.sh` always applies the hostname rule (no interactive `passwd` prompt).
- Write that password on the admin sticker / card next to the `PecanCam-XX` Wi‑Fi password. Set a distinct hostname per kit so passwords differ.
- The kit user is in the `sudo` group (setup and updates need it). Passwordless sudo is limited to service control + `apply-update.sh` via `/etc/sudoers.d/pecan-vision`.

## Canonical paths

| Item | Path |
|------|------|
| Kit user home | `/home/pecan` |
| App / repo | `/home/pecan/pecan-vision-kit` |
| Desktop | Launchers only — **not** the live git tree |
| Recordings + runtime config | `/mnt/recordings` (spare NVMe mount, or a directory on `/` when the OS lives on the only NVMe) |
| Model library | `/mnt/recordings/models/library` (what the dashboard runs) |
| Shipped models | `models/` in the repo. Copied into the library from the dashboard prompt or `scripts/install-bundled-models.sh` |
| Extra local weights | `~/pecan-vision-kit/weights/` (not in git, not replaced by updates) |

## First image / re-image steps

1. Flash Raspberry Pi OS (desktop) onto the kit storage.
2. Create the kit account and seed Desktop launcher:

   ```bash
   # From a temporary copy of the repo, or after cloning once as root-capable admin.
   # Sets password to $(hostname)1234 unless PECAN_KIT_PASSWORD is set:
   sudo ./scripts/ensure-kit-user.sh
   # Optional desktop autologin for kiosk kits:
   sudo PECAN_AUTOLOGIN=1 ./scripts/ensure-kit-user.sh
   ```

3. Log in as `pecan` (or let autologin do it).
4. Double-click **Start Pecan Vision** on the Desktop (or run `~/.local/share/pecan-vision/seed-start.sh`).
   That clones into `~/pecan-vision-kit` if needed, installs deps, enables `pecan-vision.service`, refreshes Desktop launchers, and opens the UI.
5. For Wi‑Fi AP, kiosk, or (lab only) spare-NVMe formatting, use **Setup / Repair Pecan Vision** (`scripts/setup-kit.sh`). Field kits with OS on NVMe never format that disk — videos go to `/mnt/recordings` on the root filesystem.
6. Write on the sticker:
   - AP SSID + password (from setup / `config/ap-credentials.txt`)
   - Linux user `pecan` + password `{hostname}1234` (VNC / SSH / sudo)

## Day-to-day

- Service starts on boot via systemd.
- **Start Pecan Vision** is for recover / after login.
- Updates: dashboard **Update** banner **or** Desktop **Update Pecan Vision** — both call `scripts/apply-update.sh` (GitHub Releases). Do not use `git pull` as the field update path.

## Scripts reference

| Script | Role |
|--------|------|
| `scripts/ensure-kit-user.sh` | Create `pecan`, groups, password, seed Desktop Start |
| `scripts/bootstrap-kit.sh` | Non-interactive: clone/deps/service/start |
| `scripts/desktop-start.sh` | Start launcher action |
| `scripts/desktop-update.sh` | Update launcher → same as dashboard updater |
| `scripts/desktop-setup.sh` | Opens interactive `setup-kit.sh` |
| `scripts/install-desktop-launchers.sh` | Writes Start / Update / Setup icons on Desktop |
| `scripts/setup-kit.sh` | Interactive full installer |
| `scripts/apply-update.sh` | Release install + rollback |
| `scripts/install-bundled-models.sh` | Ask, then copy shipped models into the dashboard library |

## Migrating a lab kit off `engr-ugaif`

**Option A — ship a clean image:** flash OS, run `ensure-kit-user.sh`, bootstrap as `pecan`. Copy weights and `/mnt/recordings` data if needed.

**Option B — keep developing as `engr-ugaif`:** leave the lab Pi alone. Service/sudoers templates use `@KIT_USER@` / `@KIT_ROOT@` and are rewritten for whoever runs setup/bootstrap. Shipping images should still use `pecan`.

**Option C — convert an existing kit in place:**

```bash
sudo ./scripts/ensure-kit-user.sh          # create pecan
# Copy or re-clone the tree to /home/pecan/pecan-vision-kit
sudo rsync -a /home/engr-ugaif/pecan-vision-kit/ /home/pecan/pecan-vision-kit/
sudo chown -R pecan:pecan /home/pecan/pecan-vision-kit
sudo -u pecan -H bash -lc 'cd ~/pecan-vision-kit && ./scripts/bootstrap-kit.sh'
```

Then stop using `engr-ugaif` on that device for the service (disable old unit paths if still pointing at the old home).
>>>>>>> 871aa392064a97663a2fda25a18dfd0b5b9317f7
