#!/usr/bin/env bash
# Put eth0 in NetworkManager "shared" mode so the Pi is 10.42.0.1 and serves
# DHCP to a laptop (or switch) plugged into Ethernet. Same mechanism as the
# PecanCam Wi‑Fi AP (ipv4.method shared → dnsmasq).
#
# Safe to re-run. Prefer running this over Wi‑Fi / Tailscale / existing SSH so
# the Ethernet reconfiguration does not drop your session mid-change.
#
# Usage (on the Pi):
#   sudo ./scripts/enable-eth-share.sh
#   sudo PECAN_ETH_IFACE=eth0 PECAN_ETH_ADDR=10.42.0.1/24 ./scripts/enable-eth-share.sh
if [ -z "${BASH_VERSION:-}" ]; then
  echo "Need bash." >&2
  exit 1
fi
set -euo pipefail

IFACE="${PECAN_ETH_IFACE:-eth0}"
CONN_NAME="${PECAN_ETH_CONN:-Pecan-LAN-Share}"
ADDR="${PECAN_ETH_ADDR:-10.42.0.1/24}"

if [[ "$(id -u)" -ne 0 ]]; then
  exec sudo -E bash "$0" "$@"
fi

if ! command -v nmcli >/dev/null 2>&1; then
  echo "nmcli not found — install NetworkManager." >&2
  exit 1
fi

if [[ ! -d "/sys/class/net/$IFACE" ]]; then
  echo "Interface $IFACE not present." >&2
  exit 1
fi

echo "==> Configuring $IFACE as DHCP server ($ADDR, connection '$CONN_NAME')"

# Bring interface up if it was unavailable (no cable yet is OK).
ip link set "$IFACE" up 2>/dev/null || true

# Remove other profiles that claim this interface so shared mode wins.
while IFS=: read -r name uuid device; do
  [[ "$name" == "$CONN_NAME" ]] && continue
  if [[ "$device" == "$IFACE" ]] || nmcli -g connection.interface-name connection show "$uuid" 2>/dev/null | grep -qx "$IFACE"; then
    echo "    deleting competing connection: $name"
    nmcli connection delete "$uuid" >/dev/null 2>&1 || true
  fi
done < <(nmcli -t -f NAME,UUID,DEVICE connection show)

if nmcli -t -f NAME connection show | grep -qx "$CONN_NAME"; then
  nmcli connection delete "$CONN_NAME" >/dev/null 2>&1 || true
fi

nmcli connection add \
  type ethernet \
  ifname "$IFACE" \
  con-name "$CONN_NAME" \
  autoconnect yes \
  ipv4.method shared \
  ipv4.addresses "$ADDR" \
  ipv6.method ignore \
  connection.autoconnect-priority 50

nmcli connection up "$CONN_NAME" || {
  echo "Connection saved but not up yet (plug in Ethernet if needed)."
  echo "  sudo nmcli connection up '$CONN_NAME'"
}

echo
echo "Pi LAN address:  ${ADDR%%/*}  (DHCP server for clients on $IFACE)"
echo "On the laptop: set Ethernet to DHCP / obtain automatically, then:"
echo "  ping ${ADDR%%/*}"
echo "  ssh <user>@${ADDR%%/*}"
echo
nmcli -f GENERAL,IP4 device show "$IFACE" 2>/dev/null | sed -n '1,20p' || true
hostname -I || true
