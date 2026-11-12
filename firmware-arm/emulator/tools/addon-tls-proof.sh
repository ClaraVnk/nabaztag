#!/bin/bash
# The adoption key and TLS against the Home Assistant add-on
# (nabaztag-violet/server.py), as tools/xmpp-tls-proof.sh does against Le
# Terrier: the add-on runs from the checkout in a python:3.13-alpine
# container, the whole-rabbit VM boots three times on one eeprom.
#
#   1  fresh configuration: kx.jsp agrees a key (pending), the TLS runtime
#      without HMAC, plain XMPP.
#      ... the owner confirms the colours through the add-on's API ...
#   2  the signed runtime with TLS required, xmpp_tls in locate.jsp, XMPP
#      inside TLS 1.3; at +SAY s the add-on makes the rabbit speak.
#   3  the bootloader (1.0.0.2 and later) asks for bc.jsp on the TLS port.
#
#   tools/addon-tls-proof.sh --emulator <image> --elf <Nab.elf> --out <dir> [--say S]
#       [--idle] [--keepalive S] [--mic] [--ota <signed.sim>] [--stress]
#
# What a confirmed rabbit is sent comes down its TLS session: the sound of
# --say as `ST res:N`, no GET /res/. --mic turns the microphone on fifteen
# seconds after the sound (`RS session`) and reads what the add-on captured
# ten seconds later. --ota stages a signed image and pushes it five seconds
# after that: `FW res:N <sig>`, the rabbit's verifySig gate and flash.
#
# --stress fills the rabbit's resource store: four long sentences three
# seconds apart, each some 60 KB, so three are kept (the 200 000-byte
# budget) while one still plays -- the worst case patch_res_in_session.py
# allows -- and the microphone is on after them. The verdict prints the
# heap after every garbage collection (#GC used=) and any out-of-memory.
#
# --idle runs boot 3 on scenarios/vm/addon-idle.txt, seven quiet minutes, and
# counts the XMPP connections: one when the add-on keepalives, more when it
# does not (--keepalive 0 is that control).
#
# The TLS port is 5223 on the host: boot 3's bootloader knows no other.
set -u
EMU= ; ELF= ; OUT= ; SAY=100 ; IDLE= ; KA=45 ; MIC= ; OTA= ; STRESS=
BASE=${BASE:-19300}
while [ $# -gt 0 ]; do
  case "$1" in
    --emulator) EMU=$2; shift 2 ;;
    --elf) ELF=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    --say) SAY=$2; shift 2 ;;
    --base) BASE=$2; shift 2 ;;
    --idle) IDLE=1; shift ;;
    --keepalive) KA=$2; shift 2 ;;
    --mic) MIC=1; shift ;;
    --ota) OTA=$2; shift 2 ;;
    --stress) STRESS=1; MIC=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
for v in EMU ELF OUT; do [ -n "${!v}" ] || { echo "--$(echo $v | tr A-Z a-z) is required" >&2; exit 2; }; done
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ADDON="$(cd "$HERE/../../nabaztag-violet" && pwd)"
HTTP=$((BASE+33)); XMPP=$((BASE+22)); XMPPS=5223; API=$((BASE+34))
NAME=addon-proof-$BASE
mkdir -p "$OUT/data" "$OUT/host"
OUT="$(cd "$OUT" && pwd)"
rm -rf "$OUT/data"/* "$OUT/host"/*.eeprom "$OUT"/vm-*.log
cp "$ELF" "$OUT/host/Nab.elf"
[ -n "$OTA" ] && cp "$OTA" "$OUT/data/proof.signed.sim"
[ -f "${ELF%.elf}.bin" ] && cp "${ELF%.elf}.bin" "$OUT/host/Nab.bin"

podman rm -f "$NAME" >/dev/null 2>&1 || true
podman run -d --name "$NAME" --network host \
  -e SERVER_ADDRESS=127.0.0.1 -e HTTP_PORT=$HTTP -e XMPP_PORT=$XMPP -e XMPP_TLS_PORT=$XMPPS \
  -e API_PORT=$API -e ADOPTION_KEYS_FILE=/data/adoption-keys.json -e OTA_DIR=/data/ota \
  -e TTS_ENGINE=espeak -e LOG_LEVEL=INFO -e KEEPALIVE_S=$KA \
  -v "$ADDON:/app:ro,z" -v "$OUT/data:/data:z" docker.io/library/python:3.13-alpine \
  sh -c "apk add --no-cache espeak-ng ffmpeg >/dev/null 2>&1; exec python3 -u /app/server.py" >/dev/null
for _ in $(seq 60); do podman logs "$NAME" 2>&1 | grep -q "HTTP boot server" && break; sleep 1; done
podman logs "$NAME" 2>&1 | grep -a "listening\|HTTP boot server\|adopted rabbits\|no TLS" | head -5

api() { podman exec "$NAME" python3 -c "
import json, sys, urllib.request
d = sys.argv[2]
r = urllib.request.Request('http://127.0.0.1:$API' + sys.argv[1], method='POST' if d else 'GET',
    data=d.encode() if d else None, headers={'Content-Type': 'application/json'})
print(urllib.request.urlopen(r, timeout=60).read().decode())" "$1" "${2:-}"; }

boot() {  # boot <label> [scenario]
  SC=${2:-scenarios/vm/xmpp-tls.txt}
  podman run --rm --network host -v "$HERE:/r:z" -v "$OUT/host:/host:z" -v "$OUT:/out:z" --entrypoint sh "$EMU" -c "
    cd /build/emulator
    cp /r/scenarios/vm/*.txt scenarios/vm/
    test -f /out/rabbit-vm.boot || { python3 tools/bootbc.py /host/Nab.elf -o obj/boot.bc > /out/bootbc.log 2>&1 && make -s NABGCC=/build/nabgcc BYTECODE=obj/boot.bc vm >/out/make-boot.log 2>&1 && cp rabbit-vm /out/rabbit-vm.boot; }
    if ! test -f /host/proof.eeprom; then
      python3 mkconf.py --ssid Terrier --security open --server 127.0.0.1:$HTTP/vl --ip 10.0.0.42 --gateway 10.0.0.1 --dns 10.0.0.1 -o /host/proof.eeprom >/dev/null
      python3 -c \"p='/host/proof.eeprom'; b=bytearray(open(p,'rb').read()); b[208:221]=b'd'+b'emulatorpass'; open(p,'wb').write(b)\"
    fi
    /out/rabbit-vm.boot $SC --eeprom /host/proof.eeprom --warren 127.0.0.1 --port80 $HTTP --realtime" > "$OUT/vm-$1.log" 2>&1
  echo "== boot $1: vm exit $?  $(grep -a 'final state' "$OUT/vm-$1.log" | head -1)"
  grep -a "gw: the rabbit opened" "$OUT/vm-$1.log" | grep -v ":$HTTP$" | sed 's/^/   /' | head -6
  grep -a "resets asked\|audio clips played" "$OUT/vm-$1.log" | sed 's/^/   /'
}

T0=$(date +%s)
boot 1-pending
podman logs --since "@$T0" "$NAME" 2>&1 | grep -a "kx:\|serving bootcode\|locate\|registered\|TLS\|plain port" | sed 's/^/   /' | head -8
echo "== the owner confirms the colours"
FP=$(api /api/adoption | python3 -c "import json,sys; k=json.load(sys.stdin)['keys'][0]; print(k['mac'], k['fingerprint'])")
set -- $FP
api /api/adoption/confirm "{\"mac\":\"$1\",\"fingerprint\":\"$2\"}" | cut -c1-120 | sed 's/^/   /'
T1=$(date +%s)
LONG="Il%20etait%20une%20fois%20un%20lapin%20qui%20parlait%20au%20terrier%20par%20un%20tunnel%20chiffre%2C%20et%20qui%20racontait%20la%20meteo%20du%20jour%2C%20les%20nouvelles%20du%20matin%20et%20les%20histoires%20du%20soir%20a%20toute%20la%20maison."
( sleep "$SAY"; echo "   [say at +${SAY}s]"; api "/api/say?text=Bonjour%20depuis%20le%20TLS" "" | sed 's/^/   say: /'
  if [ -n "$STRESS" ]; then
    for i in 1 2 3 4; do sleep 3; echo "   [long say $i]"; api "/api/say?text=$LONG" "" | cut -c1-60 | sed 's/^/   say: /'; done
  fi
  if [ -n "$MIC" ]; then
    sleep 15; echo "   [mic on]"; api "/api/mic?on=1" "{}" | cut -c1-100 | sed 's/^/   mic: /'
    sleep 10; api /api/status | python3 -c "import json,sys; print('   captured:', json.load(sys.stdin).get('mic'))"
    api "/api/mic?on=0" "{}" >/dev/null
  fi
  if [ -n "$OTA" ]; then
    sleep 5; echo "   [ota]"; api "/api/ota?path=/data/proof.signed.sim&trigger=1" "{}" | cut -c1-160 | sed 's/^/   ota: /'
  fi ) &
boot 2-confirmed ${MIC:+scenarios/vm/addon-session.txt}
wait
podman logs --since "@$T1" "$NAME" 2>&1 | grep -a "serving bootcode\|locate\|registered\|TLS\|plain port\|refused\|serving resource\|disconnect\|inside the session\|OTA:\|auto-listen" | sed 's/^/   /' | head -16
echo "   rabbit side, boot 2:"
grep -a "res: \|mic: \|ota: \|verifySig gate\|flashFirmware\|microphone: \|exec ST\|audio clips played\|gw: the rabbit opened .*:$HTTP\$" "$OUT/vm-2-confirmed.log" | sed 's/^/   /' | tail -24
echo "   heap after each GC, boot 2 (peak, count), and out of memory:"
grep -a "#GC" "$OUT/vm-2-confirmed.log" | sed -E 's/.*used=([0-9]+)%.*/\1/' | sort -n | tail -1 | sed 's/^/   peak used=/;s/$/%/'
echo "   GCs: $(grep -ac '#GC' "$OUT/vm-2-confirmed.log"), out of memory: $(grep -ac '?OM' "$OUT/vm-2-confirmed.log")"
T2=$(date +%s)
boot 3-tls-boot ${IDLE:+scenarios/vm/addon-idle.txt}
podman logs --since "@$T2" "$NAME" 2>&1 | grep -a "serving bootcode\|HTTP over TLS\|registered\|TLS\|refused" | sed 's/^/   /' | head -10
if [ -n "$IDLE" ]; then
  # Boot 3 opens the TLS port three times in its first ten seconds (bc.jsp,
  # locate.jsp, XMPP); every later open is a session the rabbit gave up on.
  n=$(grep -a "gw: the rabbit opened .*:$XMPPS\$" "$OUT/vm-3-tls-boot.log" | awk -F'[][ ]+' '$2 > 20000' | wc -l)
  echo "   keepalive every ${KA}s: $n XMPP reconnection(s) after the boot"
fi
podman logs "$NAME" > "$OUT/addon.log" 2>&1
podman rm -f "$NAME" >/dev/null 2>&1
echo "proof-done: $OUT"
