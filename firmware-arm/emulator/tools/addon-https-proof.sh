#!/bin/bash
# An https:// stream opened by the rabbit itself, through the Home Assistant
# add-on (nabaztag-violet/server.py), as tools/https-proof.sh does through Le
# Terrier: the whole-rabbit VM on Naboot 1.2.0.x's bootloader, the add-on from
# the checkout in a python:3.13-alpine container, and a real HTTPS host
# (openssl s_server) whose chain -- an RSA-4096 root, a P-384 intermediate, a
# P-256 leaf, Let's Encrypt's shape -- the add-on verifies with a store
# holding that root (SSL_CERT_FILE). Two boots: the key exchange (the add-on
# serves bootcode.https, the boot URL saying v=1.2.0.x), then confirmed; at
# --play-at /api/play?url=https://... is called.
#
#   tools/addon-https-proof.sh --emulator <image> --elf <Nab.elf of naboot-1.2.0.x> \
#       --out <dir> [--play-at 110] [--real <host/path>]
#
# --real icecast.radiofrance.fr/fip-midfi.mp3: no test host, the real one.
# A relay on the host port passes the rabbit's bytes to <host>:443 untouched,
# so the rabbit meets that server's own chain, extensions and endless stream,
# and the add-on verifies it with its image's own store. relay.log says how
# many bytes went each way and who closed. Born 2026-11-16: every https
# radio on Le Terrier stopped by itself after 2 to 31 s while the test host's
# three-second file played whole.
#
# What to read: the add-on's "opens ... itself over HTTPS (root N bytes as
# res:M)", then in the rabbit's log `https: hello to`, `https: ready`, the
# test host's port opened by the rabbit and `audio: the rabbit started`.
#
# Needs python3 with `cryptography` on the host (or PY=path/to/python), and
# port 5223 free: the bootloader knows no other TLS port.
set -u
EMU= ; ELF= ; OUT= ; PLAY_AT=110 ; SECS=3 ; REAL=
SUITE=TLS_CHACHA20_POLY1305_SHA256
BASE=${BASE:-19800}
PY=${PY:-python3}
while [ $# -gt 0 ]; do
  case "$1" in
    --emulator) EMU=$2; shift 2 ;;
    --elf) ELF=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    --play-at) PLAY_AT=$2; shift 2 ;;
    --real) REAL=$2; shift 2 ;;
    *) echo "unknown: $1" >&2; exit 2 ;;
  esac
done
for v in EMU ELF OUT; do [ -n "${!v}" ] || { echo "--$(echo $v | tr A-Z a-z) is required" >&2; exit 2; }; done
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ADDON="$(cd "$HERE/../../nabaztag-violet" && pwd)"
HTTP=$((BASE+33)); XMPP=$((BASE+22)); XMPPS=5223; API=$((BASE+34)); HOST_PORT=$((BASE+43))
HOSTNAME=podcast.example.test ; URLPATH=/ep.wav
[ -n "$REAL" ] && { HOSTNAME=${REAL%%/*}; URLPATH=/${REAL#*/}; }
NAME=addon-https-$BASE
mkdir -p "$OUT/data" "$OUT/host" "$OUT/www"
OUT="$(cd "$OUT" && pwd)"
rm -rf "$OUT/data"/* "$OUT/host"/*.eeprom "$OUT"/vm-*.log
cp "$ELF" "$OUT/host/Nab.elf"
[ -f "${ELF%.elf}.bin" ] && cp "${ELF%.elf}.bin" "$OUT/host/Nab.bin"

CERT_ENV=(-e SSL_CERT_FILE=/data/bundle.pem)
if [ -n "$REAL" ]; then
CERT_ENV=()
pkill -f "[r]elay-real $HOST_PORT" 2>/dev/null
"$PY" - "$HOST_PORT" "$HOSTNAME" relay-real > "$OUT/relay.log" 2>&1 <<'PY' &
# relay-real: <port> -> <host>:443, untouched, counting each way
import socket, sys, threading, time
port, host = int(sys.argv[1]), sys.argv[2]
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port)); s.listen(8)
t0 = time.time()
def pipe(a, b, n, way, tot):
    while True:
        try: d = a.recv(65536)
        except OSError as e: print(f"#{n} {way} error {e}", flush=True); break
        if not d: print(f"#{n} {way} closed by {'the rabbit' if way == 'up' else 'the host'} after {tot[0]} bytes, {time.time()-t0:.1f} s", flush=True); break
        tot[0] += len(d)
        try: b.sendall(d)
        except OSError as e: print(f"#{n} {way} send error {e}", flush=True); break
    for x in (a, b):
        try: x.shutdown(socket.SHUT_RDWR)
        except OSError: pass
n = 0
while True:
    c, _ = s.accept(); n += 1
    u = socket.create_connection((host, 443), timeout=15); u.settimeout(None)
    print(f"#{n} open at {time.time()-t0:.1f} s", flush=True)
    threading.Thread(target=pipe, args=(c, u, n, "up", [0]), daemon=True).start()
    threading.Thread(target=pipe, args=(u, c, n, "down", [0]), daemon=True).start()
PY
HOST_PID=$!
else
# The host: a chain shaped like Let's Encrypt's, and three seconds of sound.
"$PY" - "$OUT" "$HOSTNAME" "$SECS" <<'PY'
import datetime, struct, sys, math
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ec
out, host, secs = sys.argv[1], sys.argv[2], int(sys.argv[3])
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
n = 8000 * secs
pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 8000))) for i in range(n))
hdr = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
       + b"data" + struct.pack("<I", len(pcm)))
open(f"{out}/www/ep.wav", "wb").write(hdr + pcm)
PY

pkill -f "s_server -accept $HOST_PORT" 2>/dev/null
( cd "$OUT/www" && openssl s_server -accept $HOST_PORT -tls1_3 -ciphersuites "$SUITE" \
    -cert "$OUT/host/leaf.pem" -cert_chain "$OUT/host/inter.pem" -key "$OUT/host/key.pem" \
    -WWW -state > "$OUT/host.log" 2>&1 ) &
HOST_PID=$!
fi

podman rm -f "$NAME" >/dev/null 2>&1 || true
podman run -d --name "$NAME" --network host --add-host "$HOSTNAME:127.0.0.1" \
  -e SERVER_ADDRESS=127.0.0.1 -e HTTP_PORT=$HTTP -e XMPP_PORT=$XMPP -e XMPP_TLS_PORT=$XMPPS \
  -e API_PORT=$API -e API_TOKEN=proof -e ADOPTION_KEYS_FILE=/data/adoption-keys.json -e LOG_LEVEL=INFO \
  "${CERT_ENV[@]}" \
  -v "$ADDON:/app:ro,z" -v "$OUT/data:/data:z" docker.io/library/python:3.13-alpine \
  python3 -u /app/server.py >/dev/null
for _ in $(seq 60); do podman logs "$NAME" 2>&1 | grep -q "HTTP boot server" && break; sleep 1; done
podman logs "$NAME" 2>&1 | grep -a "adopted rabbits" | sed 's/^/   /'

api() { podman exec "$NAME" python3 -c "
import json, sys, urllib.request
d = sys.argv[2]
r = urllib.request.Request('http://127.0.0.1:$API' + sys.argv[1], method='POST' if d else 'GET',
    data=d.encode() if d else None, headers={'Content-Type': 'application/json', 'X-API-Token': 'proof'})
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
}

boot 1-pending
podman logs "$NAME" 2>&1 | grep -a "serving bootcode" | sed -E 's/\[[^]]*\]$//; s/^/   /' | head -2
echo "== the owner confirms the colours"
FP=$(api /api/adoption | python3 -c "import json,sys; k=json.load(sys.stdin)['keys'][0]; print(k['mac'], k['fingerprint'])")
set -- $FP
api /api/adoption/confirm "{\"mac\":\"$1\",\"fingerprint\":\"$2\"}" | cut -c1-120 | sed 's/^/   /'
( sleep "$PLAY_AT"; echo "   [play at +${PLAY_AT}s]"
  api "/api/play?url=https://$HOSTNAME:$HOST_PORT$URLPATH" "" | sed 's/^/   play: /' ) &
PLAY_PID=$!
boot 2-confirmed scenarios/vm/https-play.txt
wait $PLAY_PID   # a bare `wait` waited for s_server too, forever (2026-11-15)
podman logs "$NAME" > "$OUT/addon.log" 2>&1
echo "   add-on:"
grep -a "serving bootcode\|itself over HTTPS\|cannot be verified\|https:" "$OUT/addon.log" | sed -E 's/\[[^]]*\]$//; s/^/   /' | tail -6
echo "   rabbit:"
grep -a "https: \|audio: the rabbit started\|audio: end of clip\|audio clips played\|gw: the rabbit opened .*:$HOST_PORT" "$OUT/vm-2-confirmed.log" | sed 's/^/   /' | head -20
echo "   host:"
if [ -n "$REAL" ]; then tail -8 "$OUT/relay.log"; else tail -5 "$OUT/host.log"; fi | sed 's/^/   /'
podman rm -f "$NAME" >/dev/null 2>&1
kill $HOST_PID 2>/dev/null; pkill -f "s_server -accept $HOST_PORT" 2>/dev/null
echo "proof-done: $OUT"
