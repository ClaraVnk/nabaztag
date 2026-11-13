#!/bin/bash
# Naboot 1.2.0.0's first boot over HTTPS (boot_https), proven on the
# whole-rabbit VM against a real Terrier behind a real Caddy, as in
# production. Nobody but Let's Encrypt can sign under ISRG Root X2, so the
# proof builds the firmware with its own root, shaped like X2 (EC P-384),
# and its own pinned host, warren.test.
#
#   1. tools/boot-https-proof.sh chain <dir>
#        a root, an intermediate and a leaf for warren.test (and a rogue
#        chain for the same name); writes ../keys/proof_boot_anchor.der
#   2. cd .. && BOOT_ANCHOR=keys/proof_boot_anchor.der BOOT_HTTPS_HOST=warren.test \
#        ./build.sh --mode naboot-1.2.0.0 --remote <dev-vm> --runtime podman
#   2b. the emulator, its VM built with the same anchor (from the repo root):
#        podman build --build-arg BOOT_ANCHOR_FILE=proof_boot_anchor.der \
#          --build-arg MOD_STEPS=<run.sh's STEPS>,tls_certs,boot_https \
#          -t emu-boothttps -f firmware-arm/emulator/Containerfile .
#   3. tools/boot-https-proof.sh run <dir> --terrier <image> --bytecode <runtime.hybrid> \
#        --emulator <image> --elf ../bin/naboot-1.2.0.0/Nab.elf
#
# run boots a rabbit out of the box twice, then the first one again: against
# the right chain (the key
# exchange and the bytecode must come through Caddy over TLS), then against
# the rogue chain (the rabbit must refuse it AND never ask the warren in the
# clear: the Terrier must see no kx.jsp at all).
#
# Needs python3 with `cryptography` on the host (or PY=path/to/python).
set -u
CMD=${1:-}; OUT=${2:-}; shift 2 || true
TERRIER= ; BYTECODE= ; EMU= ; ELF=
BASE=${BASE:-19800}
PY=${PY:-python3}
CADDY=${CADDY:-docker.io/library/caddy:2.8.4-alpine}
while [ $# -gt 0 ]; do
  case "$1" in
    --terrier) TERRIER=$2; shift 2 ;;
    --bytecode) BYTECODE=$2; shift 2 ;;
    --emulator) EMU=$2; shift 2 ;;
    --elf) ELF=$2; shift 2 ;;
    *) echo "unknown: $1"; exit 2 ;;
  esac
done
HERE="$(cd "$(dirname "$0")/.." && pwd)"
HOST=warren.test

if [ "$CMD" = chain ]; then
  mkdir -p "$OUT/pki"
  "$PY" - "$OUT/pki" "$HOST" "$HERE/../keys/proof_boot_anchor.der" <<'PY'
import datetime, sys
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
d, host, anchor = sys.argv[1:4]
now = datetime.datetime.now(datetime.timezone.utc)
def name(cn): return x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Proof"),
                                x509.NameAttribute(NameOID.COMMON_NAME, cn)])
def cert(subject, key, issuer, ikey, ca, san=None, days=60):
    b = (x509.CertificateBuilder().subject_name(name(subject)).issuer_name(name(issuer))
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=days))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if ca:
        b = b.add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
    if san:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
    return b.sign(ikey, hashes.SHA384())
pem = lambda c: c.public_bytes(serialization.Encoding.PEM)
key = lambda k: k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
for tag in ("good", "rogue"):
    rk, ik, lk = (ec.generate_private_key(ec.SECP384R1()), ec.generate_private_key(ec.SECP384R1()),
                  ec.generate_private_key(ec.SECP256R1()))
    # The rogue root has the very same name: only its key differs.
    root = cert("Proof Root X2", rk, "Proof Root X2", rk, True, days=5000)
    inter = cert("Proof E7", ik, "Proof Root X2", rk, True)
    leaf = cert(host, lk, "Proof E7", ik, False, host)
    open(f"{d}/{tag}-chain.pem", "wb").write(pem(leaf) + pem(inter))
    open(f"{d}/{tag}-key.pem", "wb").write(key(lk))
    if tag == "good":
        open(anchor, "wb").write(root.public_bytes(serialization.Encoding.DER))
print("chain ready; the firmware's anchor:", anchor)
PY
  exit $?
fi

[ "$CMD" = run ] || { sed -n 2,22p "$0"; exit 2; }
NAME=terrier-boothttps-$BASE
HTTP=$((BASE+33)); XMPP=$((BASE+22)); XMPPS=$((BASE+23)); UI=$((BASE+34)); TLS=$((BASE+43))
mkdir -p "$OUT/data/bytecode" "$OUT/host"
rm -rf "$OUT/data"/* "$OUT/host"/*.eeprom "$OUT"/vm-*.log "$OUT"/caddy-*.log
mkdir -p "$OUT/data/bytecode"
cp "$BYTECODE" "$OUT/data/baseline.hybrid"
cp "$BYTECODE" "$OUT/data/bytecode/baseline-tls.hybrid"
cp "$ELF" "$OUT/host/Nab.elf"
[ -f "${ELF%.elf}.bin" ] && cp "${ELF%.elf}.bin" "$OUT/host/Nab.bin"

podman pull -q "$TERRIER" >/dev/null 2>&1 || true
podman rm -f "$NAME" >/dev/null 2>&1 || true
podman run -d --name "$NAME" --network host \
  -e TERRIER_DOMAIN=$HOST -e TERRIER_SECRET_KEY=emulator-proof-not-a-secret \
  -e TERRIER_HTTP_PORT=$HTTP -e TERRIER_XMPP_PORT=$XMPP -e TERRIER_XMPP_TLS_PORT=$XMPPS \
  -e TERRIER_UI_PORT=$UI -e TERRIER_DATA=/data -e TERRIER_LOG_LEVEL=INFO \
  -v "$OUT/data:/data:z" "$TERRIER" >/dev/null
sleep 5

caddy() {  # caddy <good|rogue>: Caddy in front of the Terrier, that chain
  podman rm -f "$NAME-caddy" >/dev/null 2>&1
  cat > "$OUT/pki/Caddyfile" <<EOF
{
	admin off
	auto_https off
}
https://$HOST:$TLS {
	tls /pki/$1-chain.pem /pki/$1-key.pem
	log {
		output stderr
		format console
	}
	reverse_proxy 127.0.0.1:$HTTP
}
EOF
  podman run -d --name "$NAME-caddy" --network host -v "$OUT/pki:/pki:z" "$CADDY" \
    caddy run --config /pki/Caddyfile --adapter caddyfile >/dev/null
  sleep 3
}

boot() {  # boot <label> <eeprom-name>
  podman run --rm --network host -v "$HERE:/r:z" -v "$OUT/host:/host:z" -v "$OUT:/out:z" --entrypoint sh "$EMU" -c "
    cd /build/emulator
    cp /r/scenarios/vm/*.txt scenarios/vm/
    cp /r/src/gw.c /r/src/gw.h /r/src/vm_main.c src/
    test -f /out/rabbit-vm.boot || { python3 tools/bootbc.py /host/Nab.elf -o obj/boot.bc > /out/bootbc.log 2>&1 && make -s NABGCC=/build/nabgcc BYTECODE=obj/boot.bc vm >/out/make-boot.log 2>&1 && cp rabbit-vm /out/rabbit-vm.boot; }
    test -f /host/$2.eeprom || python3 mkconf.py --ssid Terrier --security open --server $HOST/vl --ip 10.0.0.42 --gateway 10.0.0.1 --dns 10.0.0.1 -o /host/$2.eeprom >/dev/null
    /out/rabbit-vm.boot scenarios/vm/xmpp-tls.txt --eeprom /host/$2.eeprom --warren 127.0.0.1 --port80 $HTTP --port443 $TLS --realtime" > "$OUT/vm-$1.log" 2>&1
  echo "== boot $1: vm exit $?  $(grep -a 'final state' "$OUT/vm-$1.log" | head -1)"
}

kx() { podman logs "$NAME" 2>&1 | grep -ac "kx.jsp"; }

caddy good
boot 1-good fresh-good
podman logs "$NAME-caddy" > "$OUT/caddy-good.log" 2>&1
echo "   caddy (TLS) answered: $(grep -ac 'kx.jsp' "$OUT/caddy-good.log") kx.jsp, $(grep -ac 'bc.jsp' "$OUT/caddy-good.log") bc.jsp"
echo "   terrier: $(podman logs "$NAME" 2>&1 | grep -a 'kx: .* agreed' | head -1 | sed 's/.*\(kx: .*agreed\).*/\1/')"
echo "   rabbit opened: $(grep -ao 'gw: the rabbit opened [^ ]* -> [^ ]*' "$OUT/vm-1-good.log" | sed 's/.*-> //' | sort | uniq -c | tr '\n' ' ')"

BEFORE=$(kx)
caddy rogue
boot 2-rogue fresh-rogue
podman logs "$NAME-caddy" > "$OUT/caddy-rogue.log" 2>&1
echo "   caddy (TLS) with the rogue chain: $(grep -aci 'handshake error\|alert' "$OUT/caddy-rogue.log") handshake failure(s), $(grep -ac 'kx.jsp' "$OUT/caddy-rogue.log") kx.jsp served"
echo "   terrier: kx.jsp requests during the rogue boot: $(( $(kx) - BEFORE ))  (must be 0: no fallback to http)"
echo "   rabbit opened: $(grep -ao 'gw: the rabbit opened [^ ]* -> [^ ]*' "$OUT/vm-2-rogue.log" | sed 's/.*-> //' | sort | uniq -c | tr '\n' ' ')"

# Boot 3: the first rabbit again, now holding a pending key. A release skips
# the exchange; a kx_probe test image runs it anyway, takes the warren's 409
# and keeps its own key (2026-11-13, Bunny).
caddy good
boot 3-again fresh-good
podman logs "$NAME-caddy" > "$OUT/caddy-again.log" 2>&1
echo "   boot 3, the same rabbit with its key: $(grep -ac 'kx.jsp' "$OUT/caddy-again.log") kx.jsp, $(grep -ac 'bc.jsp' "$OUT/caddy-again.log") bc.jsp"
# &k= in each boot's bc.jsp: how the exchange went (kx_probe images only;
# 5 a key, 4 not a key (409), 3 connection/TLS failed, 2 no answer in time,
# 1 no entropy, 0 never ran).
for l in good rogue again; do
  echo "   &k= in bc.jsp, $l: $(grep -ao 'bc.jsp?[^ "]*' "$OUT/caddy-$l.log" | grep -ao '&k=[0-9]*' | tr '\n' ' ')"
done
echo "   terrier: $(podman logs "$NAME" 2>&1 | grep -ac 'already has a') exchange(s) refused for a rabbit with a key"

podman logs "$NAME" > "$OUT/terrier.log" 2>&1
podman rm -f "$NAME" "$NAME-caddy" >/dev/null 2>&1
echo "proof-done: $OUT"
