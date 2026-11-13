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
# --down-at S --up-at S stops the Terrier container S seconds into boot 2 and
# starts it again later: long enough for the rabbit to spend its three
# connection attempts, which is where the stock gate fell back to BOSH
# (bytecode/patch_liveness.py). The verdict adds every port 80 open after
# the stop: a BOSH post shows up there.
#
# --api PATH JSON (repeatable, needs --speak-at) posts JSON to the owner API's
# /api/rabbits/<mac>/PATH at the same time, ten seconds apart -- `jingle
# '{"name":"..."}'` (MU) or `ears '{"left":8,"right":8}'` (CH): the verdict's
# `res:` lines then show the jingle or choreography taken from the session
# (bytecode/patch_rsc_in_session.py), a port 80 open that it was fetched.
# `listen '{"seconds":3}'` is "talk to it": the rabbit is given a
# transcription key that goes nowhere, so the capture runs and the verdict
# shows what the emulated microphone recorded (stubs/audio.c) against what
# the Terrier captured -- inside the session (bytecode/patch_mic_in_session.py)
# or over UDP.
#
# --ota FILE (needs --speak-at) stages FILE, a signed .sim, as the warren's
# naboot-latest and, ten seconds after the other calls, presses the update
# button on the rabbit's page as its owner would (log in, the form, its
# CSRF token). The verdict shows the image coming down the session
# (bytecode/patch_ota_in_session.py: `ota:` lines, the verifySig gate,
# flashFirmware) or an /ota/ fetch over HTTP.
#
# --speak-at S --speak TEXT has the warren make the rabbit say TEXT S seconds
# into boot 2, the way an owner's script does: an owner account and an API
# token are made for the rabbit after the confirmation, and at S the API's
# /speak is called from inside the container. The verdict adds what the
# rabbit did with it -- `res:` lines when the sound came down the session
# (bytecode/patch_res_in_session.py), a port 80 open and a `serving resource`
# when it was fetched over HTTP -- and how many bytes reached the player.
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
# --split runs the warren as two containers, the core (the rabbits) and the
# web (the pages and the API), as terrier/corelink.py does in production;
# --web-restart-at S restarts the web alone S seconds into boot 2, and the
# verdict says whether the core dropped the rabbit meanwhile (2026-11-13:
# the point of the split is that it does not).
set -u
SPLIT= ; WEB_RESTART_AT=
TERRIER= ; BYTECODE= ; EMU= ; ELF= ; OUT= ; STRIP= ; STRIP_PORT= ; TLS_PORT= ; SCENARIO2= ; THIRD= ; DOWN_AT= ; UP_AT= ; SPEAK_AT= ; SPEAK= ; APIS=() ; OTA=
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
    --down-at) DOWN_AT=$2; shift 2 ;;
    --up-at) UP_AT=$2; shift 2 ;;
    --speak-at) SPEAK_AT=$2; shift 2 ;;
    --speak) SPEAK=$2; shift 2 ;;
    --api) APIS+=("$2" "$3"); shift 3 ;;
    --ota) OTA=$2; shift 2 ;;
    --split) SPLIT=1; shift ;;
    --web-restart-at) WEB_RESTART_AT=$2; shift 2 ;;
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
# Both places: a Terrier from before 2026-11-13 reads /data/baseline.hybrid,
# a later one the TLS runtime in /data/bytecode/ (terrier #164).
mkdir -p "$OUT/data/bytecode"
cp "$BYTECODE" "$OUT/data/baseline.hybrid"
cp "$BYTECODE" "$OUT/data/bytecode/baseline-tls.hybrid"
cp "$ELF" "$OUT/host/Nab.elf"
[ -f "${ELF%.elf}.bin" ] && cp "${ELF%.elf}.bin" "$OUT/host/Nab.bin"   # bootbc.py reads both

# A registry image is pulled first: a stale local copy proves nothing.
case "$TERRIER" in *.*/*) podman pull -q "$TERRIER" >/dev/null || exit 1 ;; esac
podman rm -f "$NAME" >/dev/null 2>&1 || true
ROLE=() ; API=$NAME
[ -n "$SPLIT" ] && { ROLE=(-e TERRIER_ROLE=core); API=$NAME-web; }
terrier_run() {  # terrier_run <name> [-e ...]
  local n=$1; shift
  podman run -d --name "$n" --network host "$@" \
    -e TERRIER_DOMAIN=warren.test -e TERRIER_SECRET_KEY=emulator-proof-not-a-secret \
    -e TERRIER_HTTP_PORT=$HTTP -e TERRIER_XMPP_PORT=$XMPP -e TERRIER_XMPP_TLS_PORT=$XMPPS \
    -e TERRIER_UI_PORT=$UI -e TERRIER_DATA=/data -e TERRIER_LOG_LEVEL=INFO \
    -v "$OUT/data:/data:z" "$TERRIER" >/dev/null
}
terrier_run "$NAME" "${ROLE[@]}"
if [ -n "$SPLIT" ]; then
  sleep 4; podman rm -f "$NAME-web" >/dev/null 2>&1 || true
  terrier_run "$NAME-web" -e TERRIER_ROLE=web
  sleep 4; podman logs "$NAME-web" 2>&1 | grep -a "web only" | sed 's/^/   web: /'
fi
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
    cp /r/scenarios/vm/*.txt scenarios/vm/
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
if [ -n "$SPEAK_AT" ]; then
  TOKEN=$(podman exec -w /app "$NAME" python3 -c "
try: from terrier import tenant
except ImportError: import tenant
s=tenant.Store('/data/terrier.db'); o=s.create_owner('proof@warren.test','!',True)
assert s.redeem_pair_code(s.mint_pair_code('$MAC'),o)=='$MAC'; print(s.create_api_token(o,'proof'))
r=s.get_rabbit('$MAC'); c=dict(r['config'] or {}); c.update(stt_provider='openai', stt_key='proof-not-a-key')
s.save_rabbit_config('$MAC', c)
from werkzeug.security import generate_password_hash; s.set_password(o, generate_password_hash('proof-password'))")
  echo "   owner and API token made for $MAC"
fi
if [ -n "$OTA" ]; then
  podman exec "$NAME" mkdir -p /data/ota
  podman cp "$OTA" "$NAME":/data/ota/naboot-latest.signed.sim
  echo "   staged $(basename "$OTA") as naboot-latest"
fi
T0=$(date +%s)
if [ -n "$SPEAK_AT" ]; then
  ( sleep "$SPEAK_AT"; echo "   [speak at +${SPEAK_AT}s, $(date +%T.%3N)]"
    podman exec "$API" python3 -c "
import json, sys, urllib.request
r=urllib.request.Request('http://127.0.0.1:$UI/api/rabbits/$MAC/speak', method='POST',
  data=json.dumps({'text': sys.argv[1]}).encode(),
  headers={'Authorization': 'Bearer $TOKEN', 'Content-Type': 'application/json'})
print('   speak:', urllib.request.urlopen(r, timeout=60).read().decode())" "$SPEAK"
    for ((i = 0; i < ${#APIS[@]}; i += 2)); do
      sleep 10; echo "   [${APIS[i]} at $(date +%T.%3N)]"
      podman exec "$API" python3 -c "
import sys, urllib.request
r=urllib.request.Request('http://127.0.0.1:$UI/api/rabbits/$MAC/'+sys.argv[1], method='POST',
  data=sys.argv[2].encode(),
  headers={'Authorization': 'Bearer $TOKEN', 'Content-Type': 'application/json'})
print('   '+sys.argv[1]+':', urllib.request.urlopen(r, timeout=60).read().decode())" "${APIS[i]}" "${APIS[i+1]}"
    done
    if [ -n "$OTA" ]; then
      sleep 10; echo "   [ota at $(date +%T.%3N)]"
      podman exec "$API" python3 -c "
import http.cookiejar, re, urllib.parse, urllib.request
base='http://127.0.0.1:$UI'
op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
tok=lambda p: re.search(r'name=_csrf value=\"([^\"]+)', op.open(base+p).read().decode()).group(1)
post=lambda p, d: op.open(base+p, urllib.parse.urlencode(d).encode())
post('/login', {'_csrf': tok('/login'), 'email': 'proof@warren.test', 'password': 'proof-password'})
r=post('/rabbit/$MAC/ota', {'_csrf': tok('/rabbit/$MAC')})
print('   ota:', r.status, r.geturl())"
    fi ) &
fi
if [ -n "$WEB_RESTART_AT" ]; then
  ( sleep "$WEB_RESTART_AT"; echo "   [web restarted alone at +${WEB_RESTART_AT}s, $(date +%T.%3N)]"
    podman restart -t 2 "$NAME-web" >/dev/null ) &
fi
if [ -n "$DOWN_AT" ]; then
  ( sleep "$DOWN_AT"; podman stop -t 1 "$NAME" >/dev/null; echo "   [terrier stopped at +${DOWN_AT}s]";
    [ -n "$UP_AT" ] && { sleep $((UP_AT - DOWN_AT)); podman start "$NAME" >/dev/null; echo "   [terrier started at +${UP_AT}s]"; } ) &
fi
boot 2-confirmed proof-a ${SCENARIO2:-scenarios/vm/xmpp-tls.txt}
wait
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
if [ -n "$SPEAK_AT" ]; then
  echo "   the sound, rabbit side (boot 2):"
  grep -a "res: \|mic: \|ota: \|verifySig gate\|flashFirmware\|microphone: \|exec ST\|exec MU\|exec CH\|loading \|audio: the rabbit started\|audio: end of clip\|audio clips played\|gw: the rabbit opened .*:$HTTP\$" "$OUT/vm-2-confirmed.log" | sed 's/^/   /' | tail -24
  echo "   the sound, terrier side:"
  grep -a "inside the session\|serving resource\|GET /res/\|GET /jingle/\|sent by URL\|mic: \|firmware image\|OTA: " "$OUT/terrier.log" | sed 's/^/   /' | head -12
fi
if [ -n "$SCENARIO2" ]; then
  echo "   terrier, the tag:"
  grep -a "RFID\|rfid" "$OUT/terrier.log" | sed 's/^/   /' | head -12
fi
if [ -n "$SPLIT" ]; then
  echo "   core, the rabbit's sessions during boot 2 (a web restart must not close one):"
  podman logs --since "@$T0" "$NAME" 2>&1 | grep -a "XMPP disconnect\|bound and idle" | sed 's/^/   /' | head -12
  podman logs "$NAME-web" > "$OUT/web.log" 2>&1; podman rm -f "$NAME-web" >/dev/null 2>&1
fi
podman rm -f "$NAME" >/dev/null 2>&1
[ -n "$MITM_PID" ] && kill $MITM_PID 2>/dev/null
echo "proof-done: $OUT"
