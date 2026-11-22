#!/bin/sh
# The setup network's pieces from a built Terrier OS key, run with the key's own binaries
# (qemu-arm, user mode): what can be proven without the radio (2026-11-21).
#   - wifiscan.sh: a real `iwlist wlan0 scan` listing -> one line per name, strongest first,
#     WPA3/WPA2/open told apart, hidden names and names with a quote left out;
#   - dnsmasq as hotspot.sh starts it: every name is the rabbit, AAAA and HTTPS answered
#     empty (busybox dnsd answered them NOTIMP, and iPhones gave up on the page);
#   - httpd with the setup network's rules: the phones' check paths answer 200 and lead to
#     setup.html, any other path is the setup page.
#
#   podman run --rm -v "$PWD":/w:ro,Z -v <usbkey dir>:/key:ro,Z \
#       docker.io/library/alpine:3.22 sh /w/karotz/lapinlibre/test-hotspot.sh
set -eu
apk add -q python3 qemu-arm curl > /dev/null
W=${W:-/w}
T=$(mktemp -d)
gunzip -c /key/rootfs.img.gz > $T/root.cramfs
python3 "$W/firmware-arm/emulator/karotz/ota/uncramfs.py" $T/root.cramfs $T/r > /dev/null
mkdir -p $T/r/usr
gunzip -c /key/usr.tar.gz | tar -xf - -C $T/r/usr
R=$T/r
B="qemu-arm -L $R $R/bin/busybox"
fails=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }

cat > $T/iwlist.txt <<'EOF'
wlan0     Scan completed :
          Cell 01 - Address: 02:00:00:00:00:01
                    ESSID:"Maison"
                    Mode:Master
                    Channel:6
                    Quality=40/70  Signal level=-70 dBm
                    Encryption key:on
                    IE: IEEE 802.11i/WPA2 Version 1
                        Group Cipher : CCMP
                        Pairwise Ciphers (1) : CCMP
                        Authentication Suites (1) : PSK
          Cell 02 - Address: 02:00:00:00:00:02
                    ESSID:"Maison"
                    Channel:11
                    Quality=63/70  Signal level=-47 dBm
                    Encryption key:on
                    IE: IEEE 802.11i/WPA2 Version 1
                        Authentication Suites (2) : PSK unknown (8)
          Cell 03 - Address: 02:00:00:00:00:03
                    ESSID:"Cafe libre"
                    Quality=20/70  Signal level=-90 dBm
                    Encryption key:off
          Cell 04 - Address: 02:00:00:00:00:04
                    ESSID:""
                    Quality=50/70  Signal level=-60 dBm
                    Encryption key:on
          Cell 05 - Address: 02:00:00:00:00:05
                    ESSID:"Le \"lapin\""
                    Quality=30/70  Signal level=-80 dBm
                    Encryption key:on
          Cell 06 - Address: 02:00:00:00:00:06
                    ESSID:"Vieux WPA"
                    Quality=35/70  Signal level=-78 dBm
                    Encryption key:on
                    IE: WPA Version 1
EOF
out=$($B sh $R/karotz/scripts/wifiscan.sh $T/iwlist.txt)
echo "$out" | sed 's/^/  /'
tab=$(printf '\t')
check "the strongest Maison, once, as WPA3" "[ \"\$(echo \"\$out\" | grep -c 'Maison')\" = 1 ] && echo \"\$out\" | grep -q '^90${tab}WPA3${tab}Maison\$'"
check "the strongest first" "[ \"\$(echo \"\$out\" | head -1 | cut -f3)\" = Maison ]"
check "an open network said so" "echo \"\$out\" | grep -q '${tab}open${tab}Cafe libre\$'"
check "a WPA network said so" "echo \"\$out\" | grep -q '${tab}WPA${tab}Vieux WPA\$'"
check "no hidden name, no quoted name" "! echo \"\$out\" | grep -qE '${tab}\$|lapin'"

# dnsmasq with the answering options of the key's own hotspot.sh (on
# loopback here): a list copied into this test kept passing the old command
# while the image changed (2026-11-22).
opts=$(tr -d '\\\n' < $R/karotz/scripts/hotspot.sh | grep -o 'dnsmasq --port=53[^:]*' | grep -oE -- '--(no-resolv|no-hosts|address|local)(="[^"]*"|=[^ ]*)?' |
    sed -e 's/"//g' -e 's|\$ADDR|192.168.75.1|')
echo "  dnsmasq options from hotspot.sh: $(echo $opts)"
qemu-arm -L $R $R/usr/sbin/dnsmasq --port=5300 --listen-address=127.0.0.1 --bind-interfaces \
    $opts --pid-file=$T/dnsmasq.pid --user=root 2> $T/dnsmasq.err ||
    { echo "FAIL dnsmasq did not start: $(cat $T/dnsmasq.err)"; exit 1; }
sleep 2
dns=$(python3 - <<'EOF'
import socket, struct
def q(name, t):
    qn = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
    s.sendto(struct.pack(">HHHHHH", 7, 0x0100, 1, 0, 0, 0) + qn + struct.pack(">HH", t, 1), ("127.0.0.1", 5300))
    d = s.recv(512)
    rcode, an = struct.unpack(">H", d[2:4])[0] & 15, struct.unpack(">H", d[6:8])[0]
    ip = ".".join(str(b) for b in d[-4:]) if t == 1 and an else ""
    return f"{rcode}/{an}/{ip}"
for n in ("captive.apple.com", "connectivitycheck.gstatic.com", "anything.example"):
    print(n, q(n, 1), q(n, 28), q(n, 65))
EOF
)
echo "$dns" | sed 's/^/  /'
check "every name is the rabbit" "[ \"\$(echo \"\$dns\" | grep -c ' 0/1/192.168.75.1 ')\" = 3 ]"
check "AAAA and HTTPS answered, empty (no NOTIMP)" "[ \"\$(echo \"\$dns\" | grep -c ' 0/0/ 0/0/\$')\" = 3 ]"
kill "$(cat $T/dnsmasq.pid)" 2> /dev/null || true

# The web server with the setup network's rules (hotspot.sh), on the key's own pages.
printf 'A:127.0.0.0/8\nD:*\nI:setup.html\nE404:setup.html\n' > $T/httpd.conf
(cd / && $B httpd -p 8080 -h $R/usr/www -c $T/httpd.conf)
sleep 1
for p in /hotspot-detect.html /library/test/success.html /generate_204 /gen_204 /connecttest.txt /ncsi.txt /success.txt /canonical.html; do
    c=$(curl -s -o $T/b -w '%{http_code}' http://127.0.0.1:8080$p)
    check "$p answers 200 and leads to setup.html" "[ $c = 200 ] && grep -q 'url=http://192.168.75.1/setup.html' $T/b"
done
c=$(curl -s -o $T/b -w '%{http_code}' http://127.0.0.1:8080/whatever)
check "any other path is the setup page" "grep -q 'Network name' $T/b"
check "the setup page asks for the networks around" "grep -q '/cgi-bin/wifi_networks' $R/usr/www/setup.html && [ -x $R/usr/www/cgi-bin/wifi_networks ]"
check "the setup page has its Diagnostic, never a password" "grep -q '/cgi-bin/setup_diag' $R/usr/www/setup.html && [ -x $R/usr/www/cgi-bin/setup_diag ] && grep -q 'hotspot/active' $R/usr/www/cgi-bin/setup_diag && ! grep -qiE 'psk|password' $R/usr/www/cgi-bin/setup_diag"
echo "$fails failure(s)"
[ $fails = 0 ]
