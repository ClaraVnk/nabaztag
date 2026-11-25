# Setting up a Nabaztag v1 from a Raspberry Pi

The first Nabaztag (2005) cannot be flashed by its owner: it is told where
its network and its server are on its own setup page, which it serves on an
**ad hoc** Wi-Fi cell that phones, tablets and Macs cannot join. A Raspberry
Pi with a USB Wi-Fi dongle can; `v1-setup.sh` does the whole page for you.

## What you need

- A Raspberry Pi with Raspberry Pi OS (Bookworm: it has NetworkManager), on
  **Ethernet** or with a screen and keyboard: its Wi-Fi is borrowed for a
  minute and put back after.
- A **USB Wi-Fi dongle**. Proven: Edimax EW-7811Un (rtl8192cu), 2026-11-21
  and 2026-11-24 on our v1 (firmware 1.4b).
- The rabbit's network: **open or WEP**. The v1 knows no WPA (its card's
  firmware is too old: `../v1-icsp/IMPROVEMENTS.md`). A separate open
  network for the rabbit, or a WEP one, on the 2.4 GHz band.

## Quick start, on the Raspberry Pi

```
sudo apt-get install -y git curl
git clone https://github.com/ClaraVnk/nabaztag && cd nabaztag
./firmware-arm/v1-setup/v1-setup.sh find                     # the rabbits in setup mode
./firmware-arm/v1-setup/v1-setup.sh set --ssid "My open network"
```

Before `find`: unplug the rabbit, **hold its head down and plug it back
in**: the belly blinks blue (setup mode, the cell `nabaztagXX`).

`set` joins that cell (its BSSID pinned, the address 192.168.0.2), sets the
server (default `terrier.cyberloutre.fr:80`, Le Terrier; `--server
host:port` for another), then the network with DHCP, and restarts the
rabbit on it. Options:

```
--wep KEY [--shared]          a WEP network (10 or 26 hex digits, or 5 or 13 characters)
--static IP MASK GATEWAY DNS  fixed addresses instead of DHCP
--server HOST[:PORT]          another server
--rabbit nabaztagXX           when several rabbits are in setup mode
--iface wlanX                 when the Pi has more than one Wi-Fi interface
show                          the rabbit's page as it stands
```

If it says it joined but the rabbit does not answer: unplug the dongle,
plug it back, run the same command again (it happened once, 2026-11-24).
