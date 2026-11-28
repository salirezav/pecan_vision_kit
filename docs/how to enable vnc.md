# How to enable VNC

WayVNC is the VNC server on current Raspberry Pi OS. It shares the desktop that is already on the Pi's screen. These steps were done over SSH from the Windows laptop, with the Pi at `192.168.137.40` (`usda-vision-08`).

The Ethernet address comes from [how to connect to pi via ethernet on windows.md](how%20to%20connect%20to%20pi%20via%20ethernet%20on%20windows.md).

## Turn the server on

In the SSH session:

```bash
pgrep -a labwc || pgrep -a wayfire
who
sudo raspi-config nonint do_vnc 0
systemctl status wayvnc --no-pager
sudo ss -tlnp | grep 5900
```

`pgrep` should print a process id. That means the graphical desktop is running, which WayVNC needs. `who` shows the account sitting at that desktop. On this kit that account has been `pecan`.

`do_vnc 0` enables VNC. `0` means on, `1` means off. `ss` should show WayVNC listening on port `5900`.

The same switch from the menu, if you want to see each prompt:

```bash
sudo raspi-config
```

Choose **Interface Options** → **VNC** → **Yes**, then press Esc to leave.

From the Pi's own desktop the same switch is **Preferences** → **Control Centre** → **Interfaces** → **VNC**.

If `do_vnc` fails because `wayvnc` is missing:

```bash
sudo apt update
sudo apt install wayvnc
sudo raspi-config nonint do_vnc 0
```

If `pgrep` prints nothing, the Pi booted to a text console. Desktop autologin is what this kit's setup uses. Turn that on, reboot, then enable VNC again:

```bash
sudo raspi-config nonint do_boot_behaviour B4
sudo reboot
```

After the reboot, SSH back in and run `sudo raspi-config nonint do_vnc 0` again.

To turn VNC off later:

```bash
sudo raspi-config nonint do_vnc 1
```

## Connect from Windows

Install [TigerVNC](https://github.com/TigerVNC/tigervnc/releases). On Windows, download the `vncviewer64` `.exe` from the latest release. It runs without an installer.

1. Open TigerVNC.
2. In **VNC server**, enter `192.168.137.40` or `usda-vision-08.local`.
3. Optional: **Options** → **Input** → **Show dot when no cursor**, so the pointer stays visible.
4. Click **Connect**.
5. If it warns that the hostname does not match the certificate, or that the certificate was signed by an unknown authority, click **Yes**. WayVNC uses a certificate the Pi created for itself. Connecting with `usda-vision-08.local` avoids the hostname warning.
6. Sign in as the account `who` lists on `seat0` or `tty1`, then click **OK**. On this kit that account is `pecan`. The SSH user (`engr-ugaif`) is a different login, and WayVNC rejected it.

The window is that existing desktop session.

## Check that it failed for a known reason

| What you see | What it means |
|---|---|
| `systemctl status wayvnc` says inactive | Run `sudo raspi-config nonint do_vnc 0` again |
| `ss` shows `127.0.0.1:5900` only | WayVNC is listening on the Pi itself, not on the cable |
| Connection refused on port 5900 | The server is not running, or the desktop is not up |
| Certificate hostname mismatch | You connected by the `192.168.137.40` address. Click **Yes**, or connect to `usda-vision-08.local` |
| Authentication failed | Use the desktop account from `who` (`pecan` here), not the SSH account |
| A black or empty window | `pgrep -a labwc` found no desktop. Set boot behaviour `B4`, reboot, enable VNC again |
