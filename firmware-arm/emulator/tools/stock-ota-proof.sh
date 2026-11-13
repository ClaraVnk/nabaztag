#!/bin/bash
# A rabbit on Violet's own bootloader, adopted on a Terrier, updated over the
# air: the path of a rabbit nobody ever flashed. 2026-11-13: proven with
# boot 0.0.0.10 taking Naboot 1.2.0.0 (125,756 B by the binary .bin path,
# the VM heap at 31%, flashFirmware reached).
#
#   tools/stock-ota-proof.sh --terrier <image> --runtime <baseline-tls.hybrid> \
#       --otafix <baseline-otafix.hybrid> --sim <naboot-X.signed.sim> \
#       --emulator <emu-stockflash image> --out <dir> [--boot 0.0.0.10] [--ota-at 120]
#
# --emulator must be built with Containerfile.stockflash: Violet's bootloader
# never calls verifySig, and our flash guard would refuse what the rabbit's
# own firmware takes. The bootloader is compiled here from
# vendor/nabgcc/mtl/boot/boot.<version>.mtl with tools/mtl_comp.py.
# The Terrier serves the recovery runtime (otafix) to a firmware before
# 1.0.0.0, and the owner's update button sends the image; the verdict is the
# VM's: "asked to flash itself with a N-byte image", flash writes, resets.
set -u
TERRIER= ; RUNTIME= ; OTAFIX= ; SIM= ; EMU= ; OUT= ; BOOT=0.0.0.10 ; OTA_AT=120
BASE=${BASE:-21000}
while [ $# -gt 0 ]; do
  case "$1" in
    --terrier) TERRIER=$2; shift 2 ;;  --runtime) RUNTIME=$2; shift 2 ;;
    --otafix) OTAFIX=$2; shift 2 ;;    --sim) SIM=$2; shift 2 ;;
    --emulator) EMU=$2; shift 2 ;;     --out) OUT=$2; shift 2 ;;
    --boot) BOOT=$2; shift 2 ;;        --ota-at) OTA_AT=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
for v in TERRIER RUNTIME OTAFIX SIM EMU OUT; do [ -n "${!v}" ] || { echo "--$(echo $v | tr A-Z a-z) is required" >&2; exit 2; }; done
HERE="$(cd "$(dirname "$0")/.." && pwd)"
HTTP=$((BASE+33)); XMPP=$((BASE+22)); UI=$((BASE+34)); NAME=terrier-stock-ota-$BASE; MAC=0019db00beef
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
rm -rf "$OUT"/data "$OUT"/host; mkdir -p "$OUT/data/bytecode" "$OUT/data/ota" "$OUT/host"
# both layouts of the runtime: before and after terrier #164
cp "$RUNTIME" "$OUT/data/baseline.hybrid"; cp "$RUNTIME" "$OUT/data/bytecode/baseline-tls.hybrid"
cp "$OTAFIX" "$OUT/data/bytecode/baseline-otafix.hybrid"
cp "$SIM" "$OUT/data/ota/naboot-latest.signed.sim"
python3 "$HERE/../tools/mtl_comp.py" "$HERE/../vendor/nabgcc/mtl/boot/boot.$BOOT.mtl" -o "$OUT/host/boot.bc" || exit 1

# A registry image is pulled first: a copy left on the machine runs an old
# warren and proves nothing about today's (2026-11-13, a 3-day-old image
# looked like a regression).
case "$TERRIER" in *.*/*) podman pull -q "$TERRIER" >/dev/null || exit 1 ;; esac
echo "   terrier: $(podman run --rm --entrypoint sh "$TERRIER" -c 'echo ${TERRIER_VERSION:-unknown}' | cut -c1-12)"
podman rm -f $NAME >/dev/null 2>&1
podman run -d --name $NAME --network host -e TERRIER_DOMAIN=warren.test -e TERRIER_SECRET_KEY=proof \
  -e TERRIER_HTTP_PORT=$HTTP -e TERRIER_XMPP_PORT=$XMPP -e TERRIER_XMPP_TLS_PORT=0 -e TERRIER_UI_PORT=$UI \
  -e TERRIER_DATA=/data -e TERRIER_LOG_LEVEL=INFO -v "$OUT/data:/data:z" "$TERRIER" >/dev/null
sleep 5
# adopted before its first boot: it is served its runtime, not the orphan one
podman exec -w /app $NAME python3 -c "
from terrier import tenant
s=tenant.Store('/data/terrier.db'); s.touch_rabbit('$MAC', ip='81.2.69.142')
o=s.create_owner('proof@warren.test','!',True)
assert s.redeem_pair_code(s.mint_pair_code('$MAC'),o)=='$MAC'
from werkzeug.security import generate_password_hash; s.set_password(o, generate_password_hash('proof-password'))"
( sleep "$OTA_AT"; echo "   [update button at +${OTA_AT}s]"
  podman exec $NAME python3 -c "
import http.cookiejar, re, urllib.parse, urllib.request
base='http://127.0.0.1:$UI'
op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
tok=lambda p: re.search(r'name=_csrf value=\"([^\"]+)', op.open(base+p).read().decode()).group(1)
post=lambda p, d: op.open(base+p, urllib.parse.urlencode(d).encode())
post('/login', {'_csrf': tok('/login'), 'email': 'proof@warren.test', 'password': 'proof-password'})
print('   ota:', post('/rabbit/$MAC/ota', {'_csrf': tok('/rabbit/$MAC')}).status)" ) &
STOP=$(( (OTA_AT + 300) * 1000 ))
podman run --rm --network host -v "$HERE:/r:z" -v "$OUT/host:/host:z" --entrypoint sh "$EMU" -c "
  cd /build/emulator && make -s NABGCC=/build/nabgcc BYTECODE=/host/boot.bc vm >/dev/null 2>&1
  python3 mkconf.py --ssid Terrier --security open --server 127.0.0.1:$HTTP/vl --ip 10.0.0.42 --gateway 10.0.0.1 --dns 10.0.0.1 -o /host/open.eeprom >/dev/null
  sed 's/^stop .*/stop $STOP/' /r/scenarios/vm/boot-open.txt > /tmp/s.txt
  ./rabbit-vm /tmp/s.txt --eeprom /host/open.eeprom --warren 127.0.0.1 --port80 $HTTP --realtime" > "$OUT/vm.log" 2>&1
wait
podman logs $NAME > "$OUT/terrier.log" 2>&1; podman rm -f $NAME >/dev/null
echo "== rabbit (boot $BOOT)"
grep -a "asked to flash\|flash writes\|resets asked\|final state" "$OUT/vm.log" | tail -5
echo "   heap: $(grep -a '#GC' "$OUT/vm.log" | sed 's/.*used=\([0-9]*\)%.*/\1/' | sort -n | tail -1)% at most"
echo "== terrier"
grep -a "serving the baseline recovery\|held for its update\|OTA: " "$OUT/terrier.log" | tail -5
grep -aq "asked to flash itself" "$OUT/vm.log"
