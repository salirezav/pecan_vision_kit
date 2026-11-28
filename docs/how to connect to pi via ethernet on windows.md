# How to connect to a Pi via Ethernet on Windows

Use this when a Windows laptop is plugged straight into a Raspberry Pi with an Ethernet cable. The laptop's Wi-Fi stays on the network that has internet. The cable is a separate link with only the two machines on it.

Commands below use this kit as the example:

| | |
|---|---|
| Pi hostname | `usda-vision-08` |
| SSH user | `engr-ugaif` |
| Pi Ethernet MAC | `98-FE-54-40-E4-67` |

Substitute the hostname and user for another Pi. Raspberry Pi OS answers to `raspberrypi.local` until the hostname is changed.

## Who assigns the addresses

The Wi-Fi address on the laptop (here `192.168.86.109`) comes from the Wi-Fi router. It is unrelated to the cable.

Something on the cable has to hand out the Ethernet addresses. There are three possibilities:

| Situation | Laptop Ethernet | Pi `eth0` | Pi has internet? |
|---|---|---|---|
| Pi is sharing (`Pecan-LAN-Share`, from `scripts/enable-eth-share.sh`) | `10.42.0.x`, gateway `10.42.0.1` | `10.42.0.1`, and the Pi is the DHCP server | Only if the Pi itself already has another route |
| Neither side is sharing | `169.254.x.x` after a short wait | `169.254.x.x` | No |
| Windows Internet Connection Sharing is on | `192.168.137.1` | `192.168.137.x` (this kit got `192.168.137.40`) | Yes. Windows forwards it out Wi-Fi |

`169.254.x.x` with the label **Autoconfiguration IPv4 Address** and mask `255.255.0.0` means both ends asked for DHCP and nobody answered. Windows assigns that to itself. It is not a failure of the cable.

Windows Settings → Network & internet → Ethernet shows the laptop's address only. The Pi's address comes from PowerShell.

## Find the Pi

Open **Terminal** or **PowerShell** on the laptop.

If you know the hostname, this prints the Pi's IPv4 address in brackets:

```powershell
ping -4 usda-vision-08.local
```

```text
Pinging usda-vision-08.local [192.168.137.40] with 32 bytes of data:
Reply from 192.168.137.40: bytes=32 time<1ms TTL=64
```

`-4` matters. Without it, Windows often answers with an IPv6 link-local address (`fe80::...%17`). That address works for ping and SSH, and it is a poor one to type.

To see every IPv4 neighbor on the cable:

```powershell
Get-NetAdapter | Where-Object Status -eq 'Up' | Format-Table Name, MacAddress, LinkSpeed
ipconfig
Get-NetNeighbor -InterfaceAlias "Ethernet" -AddressFamily IPv4 |
  Where-Object { $_.State -in 'Reachable','Stale' } |
  Format-Table IPAddress, LinkLayerAddress, State
```

On a single cable the other machine is the Pi. Its MAC starts with a Raspberry Pi prefix. This board uses `98-FE-54`. Others include `B8-27-EB`, `DC-A6-32`, `E4-5F-01`, `D8-3A-DD`, `28-CD-C1`, `2C-CF-67`, and `88-A2-9E`.

Filter to this Pi:

```powershell
Get-NetNeighbor -InterfaceAlias "Ethernet" -AddressFamily IPv4 |
  Where-Object { $_.LinkLayerAddress -like "98-FE-54*" } |
  Format-Table IPAddress, LinkLayerAddress, State
```

An empty IPv4 neighbor table while `ping usda-vision-08.local` still replies means the reply was IPv6. Run `ping -4`.

Give the Pi about a minute after power-on. Both Ethernet link lights should be lit. The adapter name is sometimes `Ethernet 2`; use the name from `Get-NetAdapter`.

SSH:

```powershell
ssh engr-ugaif@192.168.137.40
ssh engr-ugaif@usda-vision-08.local
```

## Share the laptop's Wi-Fi with the Pi

The Pi's `eth0` must be a DHCP client, not a DHCP server. Stock Raspberry Pi OS is a DHCP client. A kit that was set up with `scripts/enable-eth-share.sh` is a DHCP server at `10.42.0.1` until that profile is removed. See [Put Ethernet back to a DHCP client](#put-ethernet-back-to-a-dhcp-client) first if `ipconfig` shows the laptop at `10.42.0.x`.

Then turn on Internet Connection Sharing. Wi-Fi stays joined to the network that has internet.

1. Press `Win+R`, type `ncpa.cpl`, and press Enter.
2. Right-click **Wi-Fi** → **Properties** → **Sharing**.
3. Check **Allow other network users to connect through this computer's Internet connection**.
4. Set **Home networking connection** to **Ethernet**.
5. Click **OK** and accept the prompt. Windows changes the laptop's Ethernet address to `192.168.137.1` and starts a DHCP server on that port.

Check the laptop:

```powershell
ipconfig
```

Ethernet should now show **IPv4 Address** `192.168.137.1`, mask `255.255.255.0`. If it still shows `169.254.x.x`, Sharing is not on. Unplug and replug the cable after enabling it so the Pi asks for a lease again. `Restart-NetAdapter -Name "Ethernet"` does the same thing and requires an Administrator terminal (`sudo` is not a PowerShell command).

Find the address Windows gave the Pi with the commands in [Find the Pi](#find-the-pi). It is `192.168.137.x`. This kit received `192.168.137.40`.

On the Pi, confirm the route and the internet path:

```bash
ip -4 addr show eth0
ip route
ping -c 3 8.8.8.8
ping -c 3 google.com
```

`eth0` is `192.168.137.x`, the default route is `default via 192.168.137.1 dev eth0`, a reply from `8.8.8.8` means forwarding works, and a reply from `google.com` means DNS works.

The Wi-Fi access point profile `PecanCam-AP` is separate. It also uses `10.42.0.1`, on `wlan0`. Leave it in place.

## Put Ethernet back to a DHCP client

Do this on the Pi when `eth0` is stuck at `10.42.0.1` or the connection list still contains `Pecan-LAN-Share`. SSH in first (`ssh engr-ugaif@10.42.0.1` while the Pi is still serving that address).

```bash
nmcli -f NAME,UUID,DEVICE connection show
nmcli -g ipv4.method,ipv4.addresses connection show "Pecan-LAN-Share"
ip -4 addr show eth0
systemctl is-active dnsmasq isc-dhcp-server 2>/dev/null || true
```

`dnsmasq` and `isc-dhcp-server` print `inactive` in the normal case. NetworkManager runs the DHCP server itself for a shared connection.

Several profiles can share the name `Wired connection 1`. The live one is the row whose DEVICE column is `eth0`. Delete the other Ethernet profiles by UUID. `nmcli connection delete` accepts more than one UUID:

```bash
sudo nmcli connection delete UUID-OF-EXTRA UUID-OF-EXTRA
```

Leave the `lo` profile and any Wi-Fi profiles (`Niloofar Khanoom`, `SAV-G15`, `PecanCam-AP`) alone.

Point the live profile at automatic addressing. Paste the UUID from the `eth0` row:

```bash
sudo nmcli connection modify PASTE-UUID-HERE \
  ipv4.method auto \
  ipv4.addresses "" \
  ipv4.gateway "" \
  ipv4.dns "" \
  ipv6.method auto \
  connection.autoconnect yes \
  connection.autoconnect-priority 100
sudo reboot
```

The property name is `connection.autoconnect-priority`. The reboot closes SSH, because the Pi stops being `10.42.0.1`. That is expected. Wait for the Pi to boot, then enable Sharing in the section above.

If `Pecan-LAN-Share` still exists, delete it by name before the reboot:

```bash
sudo nmcli connection delete "Pecan-LAN-Share"
```

`nmcli connection add ... con-name "Wired connection 1"` creates another profile with that same name when one already exists. Delete the extras by UUID instead of adding another one.
