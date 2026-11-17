#!/bin/sh
# The emulator's stand-in for FROS's /usr/scripts/waitfornetwork.sh.
#
# On the rabbit that script writes the owner's Wi-Fi name and password into
# wpa.conf and brings up wlan0 with wpa_supplicant. Here there is no radio:
# QEMU's Ethernet is renamed wlan0 and given user-mode networking's fixed
# address, and the script answers 0 like a rabbit that found its network,
# so startup.sh goes on to D-Bus, cron and the daemons.
ifconfig lo up
ip link set eth0 name wlan0 2>/dev/null
ifconfig wlan0 10.0.2.15 netmask 255.255.255.0 up
route add default gw 10.0.2.2
echo "nameserver 10.0.2.3" >/etc/resolv.conf
exit 0
