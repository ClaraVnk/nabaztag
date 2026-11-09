#!/bin/bash
# scenarios/vm/xmpp-tls.txt, run: a real Terrier in a container, the
# whole-rabbit VM under the adoption bootloader, two boots -- a fresh
# configuration page (the key exchange, pending, plain XMPP), then the owner
# confirms and the rabbit boots again. With --strip, an attacker on the path
# (tools/locate_strip.py) edits locate.jsp between the two: the xmpp_tls line
# is dropped, or with --strip-port N the TLS port is rewritten to N.
#
#   tools/xmpp-tls-proof.sh --terrier <image> --bytecode <runtime.hybrid> \
#       --emulator <image> --elf <Nab.elf> --out <dir> [--strip | --strip-port N] [--tls-port N]
#       [--scenario <file>] [--third]
#
# --scenario runs boot 2 (the owner confirmed) on another scenario file than
# xmpp-tls.txt: scenarios/vm/xmpp-rfid.txt presents a tag to the reader at
# 120 s, and the verdict adds whether the Terrier saw it as plain rfid.jsp or
# inside the TLS session (bytecode/patch_rfid_xmpp.py).
#
# --third boots the rabbit once more after boot 2. A Naboot 1.0.0.2 rabbit
# learns that its owner confirmed the key from the HMAC on boot 2's bytecode,
# so it is boot 3 that asks for bc.jsp on the TLS port (boot-mods/boot_tls.mtl,
# port 5223: pass --tls-port 5223 for it to land on the Terrier).
#
# The rabbit's connections to port 80 (the ping server locate.jsp names, so
# rfid.jsp) go to the Terrier's HTTP port (rabbit-vm --port80).
#
# --tls-port sets the Terrier's TLS port (default BASE+23). The stripped case
# only ends in TLS when it is 5223: with the line gone the rabbit has nothing
# but the bytecode's XMPP_TLS_DEFAULT_PORT to go on, which is the point.
#
# The verdict is what the gateway prints -- which port the rabbit opened for
# XMPP -- and what the Terrier logs for that connection. Ports are chosen
# high and odd so several runs can coexist on one host.
set -u
TERRIER= ; BYTECODE= ; EMU= ; ELF= ; OUT= ; STRIP= ; STRIP_PORT= ; TLS_PORT= ; SCENARIO2= ; THIRD=
BASE=${BASE:-18700}
while [ $# -gt 0 ]; do
  case "$1" in
    --terrier) TERRIER=$2; shift 2 ;;
    --bytecode) BYTECODE=$2; shift 2 ;;
    --emulator) EMU=$2; shift 2 ;;
    --elf) ELF=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    --strip) STRIP=1; shift ;;
    --strip-port) STRIP=1; STRIP_PORT=$2; shift 2 ;;
    --base) BASE=$2; shift 2 ;;
    --tls-port) TLS_PORT=$2; shift 2 ;;
    --scenario) SCENARIO2=$2; shift 2 ;;
    --third) THIRD=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
for v in TERRIER BYTECODE EMU ELF OUT; do [ -n "${!v}" ] || { echo "--$(echo $v | tr A-Z a-z) is required" >&2; exit 2; }; done
HERE="$(cd "$(dirname "$0")/.." && pwd)"
HTTP=$((BASE+33)); XMPP=$((BASE+22)); XMPPS=${TLS_PORT:-$((BASE+23))}; UI=$((BASE+34)); MITM=$((BASE+80))
NAME=terrier-proof-$BASE
mkdir -p "$OUT/data" "$OUT/host"
OUT="$(cd "$OUT" && pwd)"
rm -rf "$OUT/data"/* "$OUT/host"/*.eeprom "$OUT"/vm-*.log "$OUT"/mitm.log
cp "$BYTECODE" "$OUT/data/baseline.hybrid"
cp "$ELF" "$OUT/host/Nab.elf"
[ -f "${ELF%.elf}.bin" ] && cp "${ELF%.elf}.bin" "$OUT/host/Nab.bin"   # bootbc.py reads both

podman rm -f "$NAME" >/dev/null 2>&1 || true
podman run -d --name "$NAME" --network host \
  -e TERRIER_DOMAIN=warren.test -e TERRIER_SECRET_KEY=emulator-proof-not-a-secret \
  -e TERRIER_HTTP_PORT=$HTTP -e TERRIER_XMPP_PORT=$XMPP -e TERRIER_XMPP_TLS_PORT=$XMPPS \
  -e TERRIER_UI_PORT=$UI -e TERRIER_DATA=/data -e TERRIER_LOG_LEVEL=INFO \
  -v "$OUT/data:/data:z" "$TERRIER" >/dev/null
sleep 4
podman logs "$NAME" 2>&1 | grep -a "listening on\|starting" | head -4

RABBIT_HTTP=$HTTP
MITM_PID=
if [ -n "$STRIP" ]; then
  RABBIT_HTTP=$MITM
  python3 "$HERE/tools/locate_strip.py" --listen $MITM --upstream $HTTP ${STRIP_PORT:+--tls-port $STRIP_PORT} > "$OUT/mitm.log" 2>&1 &
  MITM_PID=$!
  sleep 1
fi

boot() {  # boot <label> <eeprom-name> [scenario]
  SC=${3:-scenarios/vm/xmpp-tls.txt}
  podman run --rm --network host -v "$HERE:/r:z" -v "$OUT/host:/host:z" -v "$OUT:/out:z" --entrypoint sh "$EMU" -c "
    cd /build/emulator
    cp /r/scenarios/vm/xmpp-tls.txt /r/scenarios/vm/xmpp-rfid.txt scenarios/vm/
    test -f /out/rabbit-vm.boot || { python3 tools/bootbc.py /host/Nab.elf -o obj/boot.bc > /out/bootbc.log 2>&1 && make -s NABGCC=/build/nabgcc BYTECODE=obj/boot.bc vm >/out/make-boot.log 2>&1 && cp rabbit-vm /out/rabbit-vm.boot; }
    if ! test -f /host/$2.eeprom; then
      python3 mkconf.py --ssid Terrier --security open --server 127.0.0.1:$RABBIT_HTTP/vl --ip 10.0.0.42 --gateway 10.0.0.1 --dns 10.0.0.1 -o /host/$2.eeprom >/dev/null
      # The XMPP password of a rabbit registered years ago: 'd' and twelve
      # bytes at 208 (main.mtl GetXmppPassword).
      python3 -c \"p='/host/$2.eeprom'; b=bytearray(open(p,'rb').read()); b[208:221]=b'd'+b'emulatorpass'; open(p,'wb').write(b)\"
    fi
    /out/rabbit-vm.boot $SC --eeprom /host/$2.eeprom --warren 127.0.0.1 --port80 $HTTP --realtime" > "$OUT/vm-$1.log" 2>&1
  echo "== boot $1: vm exit $?  $(grep -a 'final state' "$OUT/vm-$1.log" | head -1)"
  grep -a "gw: the rabbit opened" "$OUT/vm-$1.log" | grep -v ":$HTTP$\|:$MITM$" | sed 's/^/   /' | head -6
  grep -a "rfid:" "$OUT/vm-$1.log" | sed 's/^/   /' | head -4
}

boot 1-pending proof-a
MAC=$(podman logs "$NAME" 2>&1 | grep -a "kx: .* agreed" | head -1 | sed 's/.*kx: \([0-9a-f]*\) agreed.*/\1/')
echo "   rabbit mac: $MAC"
podman logs "$NAME" 2>&1 | grep -a "TLS\|registered\|bound and idle\|refused\|plain port" | tail -3 | sed 's/^/   /'
echo "== the owner confirms the colours"
podman exec -w /app "$NAME" python3 -c "
try: from terrier import tenant
except ImportError: import tenant
s=tenant.Store('/data/terrier.db'); print('   kx_decide', s.kx_decide('$MAC', True), s.kx_get('$MAC')['state'])"
T0=$(date +%s)
boot 2-confirmed proof-a ${SCENARIO2:-scenarios/vm/xmpp-tls.txt}
echo "   terrier, since the owner confirmed:"
podman logs --since "@$T0" "$NAME" 2>&1 | grep -a "TLS\|registered\|bound and idle\|refused\|plain port\|locate.jsp\|serving bytecode\|rfid\|RFID" | sed 's/^/   /' | head -16
if [ -n "$THIRD" ]; then
  T1=$(date +%s)
  boot 3-tls-boot proof-a
  echo "   terrier, boot 3:"
  podman logs --since "@$T1" "$NAME" 2>&1 | grep -a "TLS\|bc.jsp\|serving bytecode\|bound and idle\|refused" | sed 's/^/   /' | head -12
fi
[ -n "$STRIP" ] && { echo "   attacker:"; sed 's/^/   /' "$OUT/mitm.log" | grep -v forwarded; }
podman logs "$NAME" > "$OUT/terrier.log" 2>&1
if [ -n "$SCENARIO2" ]; then
  echo "   terrier, the tag:"
  grep -a "RFID\|rfid" "$OUT/terrier.log" | sed 's/^/   /' | head -12
fi
podman rm -f "$NAME" >/dev/null 2>&1
[ -n "$MITM_PID" ] && kill $MITM_PID 2>/dev/null
echo "proof-done: $OUT"
