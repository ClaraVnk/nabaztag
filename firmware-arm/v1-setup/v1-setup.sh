#!/bin/sh
# Set up a Nabaztag v1 (2005) from a Raspberry Pi (or any Linux with
# NetworkManager) and a USB Wi-Fi dongle: its Wi-Fi network and its server,
# on the rabbit's own setup page, without typing anything into it.
#
#   v1-setup.sh find                         the rabbits in setup mode around
#   v1-setup.sh show                         a rabbit's page as it stands
#   v1-setup.sh set --ssid NAME [--wep KEY [--shared]]
#                   [--server HOST[:PORT]]   default terrier.cyberloutre.fr:80
#                   [--static IP MASK GATEWAY DNS]   default: DHCP
#   options for all: --iface wlanX (default: the first Wi-Fi interface),
#                    --rabbit nabaztagXX (default: the only one found)
#
# Setup mode: unplug the rabbit, hold its head down and plug it back in; the
# belly blinks blue. It then runs an ad hoc (IBSS) cell `nabaztagXX` on
# channel 11 and answers at 192.168.0.1, handing out no address. Phones and
# Macs cannot join an ad hoc cell; what worked (2026-11-21 and 2026-11-24,
# Lazare, firmware 1.4b): an Edimax EW-7811Un (rtl8192cu) on a Raspberry Pi,
# the cell joined with its BSSID pinned, the fixed address 192.168.0.2.
# A first join can carry no traffic: unplug the dongle, plug it back, run
# the command again.
#
# The server is not a field of the page: b.htm?a=<host>&b=<port> sets it
# (docs.nabaztag.com, "Nabaztag v1"); then the form's own fields (r=1, k the
# network, l open/shared, m+n WEP, f DHCP or g/h/i/j the addresses) with
# z=Update and start, which restarts the rabbit on its network.
#
# The computer's usual Wi-Fi connection is put back at the end; run it over
# Ethernet or a screen and keyboard, not over that Wi-Fi.
set -eu

IFACE='' RABBIT='' SSID='' WEP='' SHARED=0 SERVER=terrier.cyberloutre.fr:80 STATIC=''
IP='' MASK='' GW='' DNS=''
CON=nabaztag-v1-setup
RABBIT_IP=192.168.0.1 OUR_IP=192.168.0.2/24

die() { echo "v1-setup: $*" >&2; exit 1; }
nm() { nmcli "$@" 2>/dev/null || sudo nmcli "$@"; }

cmd=${1:-}; [ -n "$cmd" ] && shift
while [ $# -gt 0 ]; do
    case $1 in
    --iface) IFACE=$2; shift 2 ;;
    --rabbit) RABBIT=$2; shift 2 ;;
    --ssid) SSID=$2; shift 2 ;;
    --wep) WEP=$2; shift 2 ;;
    --shared) SHARED=1; shift ;;
    --server) SERVER=$2; shift 2 ;;
    --static) STATIC=1 IP=$2 MASK=$3 GW=$4 DNS=$5; shift 5 ;;
    *) die "unknown option $1 (see the top of this file)" ;;
    esac
done
command -v nmcli > /dev/null || die "this needs NetworkManager (nmcli): Raspberry Pi OS Bookworm has it"
command -v curl > /dev/null || die "this needs curl"
[ -n "$IFACE" ] || IFACE=$(nmcli -t -f DEVICE,TYPE dev | awk -F: '$2 == "wifi" { print $1; exit }')
[ -n "$IFACE" ] || die "no Wi-Fi interface: plug in the USB dongle"

# The rabbits in setup mode: "SSID BSSID", one per line.
rabbits() {
    nm dev wifi list ifname "$IFACE" --rescan yes > /dev/null || true
    nmcli -t -f SSID,BSSID,MODE dev wifi list ifname "$IFACE" |
        sed 's/\\:/-/g' | awk -F: 'tolower($1) ~ /^nabaztag/ && $3 == "Ad-Hoc" { gsub("-", ":", $2); print $1, $2 }' | sort -u
}

# The usual connection of the interface, to put back at the end.
BEFORE=$(nmcli -t -f GENERAL.CONNECTION dev show "$IFACE" | cut -d: -f2-)
restore() {
    nm con down "$CON" > /dev/null 2>&1 || true
    nm con delete "$CON" > /dev/null 2>&1 || true
    if [ -n "$BEFORE" ] && [ "$BEFORE" != "$CON" ] && [ "$BEFORE" != "--" ]; then
        nm con up "$BEFORE" > /dev/null 2>&1 || echo "v1-setup: put your Wi-Fi back by hand: nmcli con up \"$BEFORE\""
    fi
}

join() {
    found=$(rabbits)
    [ -n "$found" ] || die "no rabbit in setup mode: hold its head down while plugging it in (belly blinking blue)"
    if [ -n "$RABBIT" ]; then
        line=$(echo "$found" | awk -v r="$RABBIT" '$1 == r' | head -1)
    else
        [ "$(echo "$found" | wc -l)" -eq 1 ] || die "several rabbits: name one with --rabbit ($(echo "$found" | awk '{print $1}' | tr '\n' ' '))"
        line=$found
    fi
    [ -n "$line" ] || die "$RABBIT is not in setup mode around"
    cell=${line% *}; bssid=${line#* }
    trap restore EXIT
    nm con delete "$CON" > /dev/null 2>&1 || true
    nm con add type wifi ifname "$IFACE" con-name "$CON" ssid "$cell" \
        802-11-wireless.mode adhoc 802-11-wireless.band bg 802-11-wireless.channel 11 \
        802-11-wireless.bssid "$bssid" ipv4.method manual ipv4.addresses "$OUR_IP" \
        ipv4.never-default yes ipv6.method disabled > /dev/null
    nm con up "$CON" > /dev/null
    i=0
    until curl -s -m 5 -o /dev/null "http://$RABBIT_IP/"; do
        i=$((i + 1))
        [ $i -lt 6 ] || die "joined $cell but the rabbit does not answer: unplug the dongle, plug it back, try again"
        sleep 3
    done
    echo "v1-setup: $cell answers"
}

page() { curl -s -m 10 "http://$RABBIT_IP/$1" | sed 's/<[^>]*>/ /g; s/&nbsp;/ /g' | tr -s ' ' | grep -v '^ *$'; }

case $cmd in
find)
    rabbits | awk '{ print $1 }' | grep . || echo "none: hold the rabbit's head down while plugging it in"
    ;;
show)
    join
    page ""
    ;;
set)
    [ -n "$SSID" ] || die "set needs --ssid <your network> (an open or WEP one: the v1 knows no WPA)"
    join
    host=${SERVER%:*}; port=${SERVER##*:}; [ "$port" != "$SERVER" ] || port=80
    curl -s -m 10 -o /dev/null -G "http://$RABBIT_IP/b.htm" --data-urlencode "a=$host" --data-urlencode "b=$port"
    page "" | grep -q "Server name : $host:$port" || die "the server was not taken"
    echo "v1-setup: server $host:$port"
    set -- -G "http://$RABBIT_IP/b.htm" --data "r=1" --data-urlencode "k=$SSID" --data "l=$SHARED"
    [ -z "$WEP" ] || set -- "$@" --data "m=on" --data-urlencode "n=$WEP"
    if [ -z "$STATIC" ]; then
        set -- "$@" --data "f=on"
    else
        set -- "$@" --data "g=$IP" --data "h=$MASK" --data "i=$GW" --data "j=$DNS"
    fi
    reply=$(curl -s -m 15 "$@" --data "z=Update+and+start" | sed 's/<[^>]*>/ /g')
    echo "$reply" | grep -q "Starting" || die "the rabbit did not take the settings"
    echo "v1-setup: network $SSID$([ -n "$WEP" ] && echo ", WEP")$([ -n "$STATIC" ] && echo ", fixed addresses" || echo ", DHCP"): the rabbit restarts on it"
    ;;
*)
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
