#!/bin/bash
# scenarios/vm/https-play.txt, run: the whole-rabbit VM on Naboot 1.2.0.0's
# bootloader, a Terrier with the https policy, and a real HTTPS host
# (openssl s_server) whose chain -- an RSA-4096 root, a P-384 intermediate,
# a P-256 leaf, Let's Encrypt's shape -- the Terrier verifies with a bundle
# holding that root (TERRIER_ROOTS_BUNDLE). Two boots: the key exchange, then
# confirmed; at --play-at the owner's API plays the station, at --stop-at it
# stops it.
#
#   tools/https-proof.sh --terrier <image> --bytecode <runtime.hybrid> \
#       --emulator <image> --elf <Nab.elf of naboot-1.2.0.0> --out <dir> \
#       [--play-at 110] [--stop-at 170] [--suite TLS_CHACHA20_POLY1305_SHA256]
#
# Needs python3 with `cryptography` on the host (or PY=path/to/python).
set -u
TERRIER= ; BYTECODE= ; EMU= ; ELF= ; OUT= ; PLAY_AT=110 ; STOP_AT=170
SUITE=TLS_CHACHA20_POLY1305_SHA256
BASE=${BASE:-19700}
PY=${PY:-python3}
while [ $# -gt 0 ]; do
  case "$1" in
    --terrier) TERRIER=$2; shift 2 ;;
    --bytecode) BYTECODE=$2; shift 2 ;;
    --emulator) EMU=$2; shift 2 ;;
    --elf) ELF=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    --play-at) PLAY_AT=$2; shift 2 ;;
    --stop-at) STOP_AT=$2; shift 2 ;;
    --suite) SUITE=$2; shift 2 ;;
    *) echo "unknown: $1"; exit 2 ;;
  esac
done
HERE="$(cd "$(dirname "$0")/.." && pwd)"
NAME=terrier-https-$BASE
HTTP=$((BASE+33)); XMPP=$((BASE+22)); XMPPS=$((BASE+23)); UI=$((BASE+34)); HOST_PORT=$((BASE+43))
HOSTNAME=podcast.example.test
mkdir -p "$OUT/data" "$OUT/host" "$OUT/www"
rm -rf "$OUT/data"/* "$OUT/host"/*.eeprom "$OUT"/vm-*.log
cp "$BYTECODE" "$OUT/data/baseline.hybrid"
cp "$ELF" "$OUT/host/Nab.elf"
[ -f "${ELF%.elf}.bin" ] && cp "${ELF%.elf}.bin" "$OUT/host/Nab.bin"

# The host: a chain shaped like Let's Encrypt's, and three seconds of sound.
"$PY" - "$OUT" "$HOSTNAME" <<'PY'
import datetime, struct, sys, math
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ec
out, host = sys.argv[1], sys.argv[2]
now = datetime.datetime.now(datetime.timezone.utc)
def name(cn): return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
def cert(subject, key, issuer, ikey, h, ca, san=None):
    b = (x509.CertificateBuilder().subject_name(name(subject)).issuer_name(name(issuer))
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=60))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
         .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ikey.public_key()), critical=False))
    if ca:
        b = b.add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
    if san:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
    return b.sign(ikey, h)
rk = rsa.generate_private_key(public_exponent=65537, key_size=4096)
ik = ec.generate_private_key(ec.SECP384R1()); lk = ec.generate_private_key(ec.SECP256R1())
root = cert("Proof Root X1", rk, "Proof Root X1", rk, hashes.SHA256(), True)
inter = cert("Proof E5", ik, "Proof Root X1", rk, hashes.SHA384(), True)
leaf = cert(host, lk, "Proof E5", ik, hashes.SHA384(), False, host)
pem = lambda c: c.public_bytes(serialization.Encoding.PEM)
open(f"{out}/data/bundle.pem", "wb").write(pem(root))
open(f"{out}/host/leaf.pem", "wb").write(pem(leaf))
open(f"{out}/host/inter.pem", "wb").write(pem(inter))
open(f"{out}/host/key.pem", "wb").write(lk.private_bytes(serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
# 3 s of a 440 Hz tone, 8 kHz mono 16-bit: something the player plays out.
n = 8000 * 3
pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 8000))) for i in range(n))
hdr = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
       + b"data" + struct.pack("<I", len(pcm)))
open(f"{out}/www/ep.wav", "wb").write(hdr + pcm)
PY

pkill -f "s_server -accept $HOST_PORT" 2>/dev/null
( cd "$OUT/www" && openssl s_server -accept $HOST_PORT -tls1_3 -ciphersuites "$SUITE" \
    -cert "$OUT/host/leaf.pem" -cert_chain "$OUT/host/inter.pem" -key "$OUT/host/key.pem" \
    -WWW -quiet > "$OUT/host.log" 2>&1 ) &
HOST_PID=$!

podman rm -f "$NAME" >/dev/null 2>&1 || true
podman run -d --name "$NAME" --network host --add-host "$HOSTNAME:127.0.0.1" \
  -e TERRIER_DOMAIN=warren.test -e TERRIER_SECRET_KEY=emulator-proof-not-a-secret \
  -e TERRIER_HTTP_PORT=$HTTP -e TERRIER_XMPP_PORT=$XMPP -e TERRIER_XMPP_TLS_PORT=$XMPPS \
  -e TERRIER_UI_PORT=$UI -e TERRIER_DATA=/data -e TERRIER_LOG_LEVEL=INFO \
  -e TERRIER_ROOTS_BUNDLE=/data/bundle.pem -e TERRIER_WEBHOOK_ALLOW_PRIVATE=1 \
  -v "$OUT/data:/data:z" "$TERRIER" >/dev/null
sleep 5

boot() {  # boot <label> <eeprom-name> [scenario]
  SC=${3:-scenarios/vm/xmpp-tls.txt}
  podman run --rm --network host -v "$HERE:/r:z" -v "$OUT/host:/host:z" -v "$OUT:/out:z" --entrypoint sh "$EMU" -c "
    cd /build/emulator
    cp /r/scenarios/vm/*.txt scenarios/vm/
    test -f /out/rabbit-vm.boot || { python3 tools/bootbc.py /host/Nab.elf -o obj/boot.bc > /out/bootbc.log 2>&1 && make -s NABGCC=/build/nabgcc BYTECODE=obj/boot.bc vm >/out/make-boot.log 2>&1 && cp rabbit-vm /out/rabbit-vm.boot; }
    if ! test -f /host/$2.eeprom; then
      python3 mkconf.py --ssid Terrier --security open --server 127.0.0.1:$HTTP/vl --ip 10.0.0.42 --gateway 10.0.0.1 --dns 10.0.0.1 -o /host/$2.eeprom >/dev/null
      python3 -c \"p='/host/$2.eeprom'; b=bytearray(open(p,'rb').read()); b[208:221]=b'd'+b'emulatorpass'; open(p,'wb').write(b)\"
    fi
    /out/rabbit-vm.boot $SC --eeprom /host/$2.eeprom --warren 127.0.0.1 --port80 $HTTP --realtime" > "$OUT/vm-$1.log" 2>&1
  echo "== boot $1: vm exit $?  $(grep -a 'final state' "$OUT/vm-$1.log" | head -1)"
}

boot 1-pending proof-h
MAC=$(podman logs "$NAME" 2>&1 | grep -a "kx: .* agreed" | head -1 | sed 's/.*kx: \([0-9a-f]*\) agreed.*/\1/')
echo "   rabbit mac: $MAC, booted as $(podman logs "$NAME" 2>&1 | grep -ao 'v=[0-9.]*' | head -1)"
TOKEN=$(podman exec -w /app "$NAME" python3 -c "
from terrier import tenant
import json
s=tenant.Store('/data/terrier.db'); print('kx', s.kx_decide('$MAC', True), file=__import__('sys').stderr)
o=s.create_owner('proof@warren.test','!',True)
assert s.redeem_pair_code(s.mint_pair_code('$MAC'),o)=='$MAC'
s.set_setting('catalog:radio', json.dumps([{'key':'proof','name':'Proof','url':'https://$HOSTNAME:$HOST_PORT/ep.wav'}]))
print(s.create_api_token(o,'proof'))")
echo "   owner, API token and the station made"
api() {
  podman exec "$NAME" python3 -c "
import json, sys, urllib.request
r=urllib.request.Request('http://127.0.0.1:$UI/api/rabbits/$MAC/radio', method='POST',
  data=sys.argv[1].encode(), headers={'Authorization': 'Bearer $TOKEN', 'Content-Type': 'application/json'})
print('   radio:', urllib.request.urlopen(r, timeout=60).read().decode())" "$1"
}
( sleep "$PLAY_AT"; echo "   [play at +${PLAY_AT}s]"; api '{"station":"proof"}'
  sleep $((STOP_AT - PLAY_AT)); echo "   [stop at +${STOP_AT}s]"; api '{"stop":1}' ) &
boot 2-confirmed proof-h scenarios/vm/https-play.txt
wait
podman logs "$NAME" > "$OUT/terrier.log" 2>&1
echo "   terrier:"
grep -a "bc_https\|plays .* itself\|cannot be verified\|https:\|bound and idle" "$OUT/terrier.log" | sed 's/^/   /' | tail -8
echo "   rabbit:"
grep -a "https: \|live: cut\|audio: the rabbit started\|audio: end of clip\|audio clips played\|gw: the rabbit opened .*:$HOST_PORT" "$OUT/vm-2-confirmed.log" | sed 's/^/   /' | head -20
echo "   host:"
tail -5 "$OUT/host.log" | sed 's/^/   /'
podman rm -f "$NAME" >/dev/null 2>&1
kill $HOST_PID 2>/dev/null
echo "proof-done: $OUT"
