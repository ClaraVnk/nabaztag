#!/bin/sh
# The loader's SHA-256 and HMAC-SHA-256 (firmware-arm/v1-native/src/loader/
# sha256.asm) run on the emulated PIC18LF6525 and compared with Python's
# hashlib and hmac: FIPS 180-4's "abc", the 448-bit message, the million
# 'a' and the empty message, RFC 4231's cases 1 to 5 (6 and 7 need a
# 131-byte key; the rabbit's keys are 32 bytes), a 1000-byte pattern through
# both. The test image is src/loader/shatest.c (`make shatest`), linked like
# the loader, the watchdog on. PASS or FAIL, with the time per 64-byte block
# measured on the million 'a' (15 625 blocks) and the loader's size.
#
#   tools/v1-sha-proof.sh             from firmware-arm/emulator, on a machine
#                                     with podman (the dev VM, never HAOS)
#
# V1_SHA_SECONDS overrides the run's length in PIC seconds (300).
set -eu
cd "$(dirname "$0")/.."
SECONDS_=${V1_SHA_SECONDS:-300}
OUT=$(mktemp -d)
NATIVE=$(cd ../v1-native && pwd)
fail() { echo "FAIL: $*"; exit 1; }
ms_of() { grep -m1 -- "$2" "$1" | sed -E 's/^\[ *([0-9]+) ms\].*/\1/'; }

echo "== build"
podman build -q -t v1native-toolchain -f "$NATIVE/Containerfile" "$NATIVE" >/dev/null
podman run --rm -v "$NATIVE":/src:Z -w /src v1native-toolchain sh -c '
  make clean >/dev/null && make loader shatest >/dev/null && make size | grep -A2 ^loader &&
  make DEV=18f6627 loader shatest >/dev/null && echo "== the 2.0b board (PIC18F6627):" && make DEV=18f6627 loader size | grep -A1 ^loader' \
  > "$OUT/build.log" 2>&1 || { cat "$OUT/build.log"; fail "the test image does not build"; }
cat "$OUT/build.log"
podman build -q -t nabaztag-v1-native -f v1/Containerfile .. >/dev/null
mkdir -p "$OUT/fw"
cp "$NATIVE/out/18f6525/shatest.hex" "$OUT/fw/"

echo "== run: $SECONDS_ PIC seconds"
podman run --rm -v "$OUT/fw":/img:Z nabaztag-v1-native \
  --hex /img/shatest.hex --pic 6525 --flash-chip 081 --seconds "$SECONDS_" > "$OUT/run.log" 2>&1 || true
L="$OUT/run.log"
grep -q 'halted' "$L" && fail "the PIC halted ($L)"
grep -q 'unknown opcode' "$L" && fail "an opcode the emulator does not know ($L)"
grep -q 'watchdog:' "$L" && fail "the watchdog fired ($L)"
grep -q 'uart: sha done' "$L" || fail "the test image did not finish in $SECONDS_ s ($L)"

# What Python says for the same inputs, one `kind name digest` per line.
python3 - > "$OUT/expected.txt" <<'EOF'
import hashlib, hmac
pattern = bytes((i * 7 + 3) & 255 for i in range(1000))
sha = [("empty", b""), ("abc", b"abc"),
       ("448", b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
       ("pattern", pattern), ("million", b"a" * 1000000)]
mac = [("rfc1", b"\x0b" * 20, b"Hi There"),
       ("rfc2", b"Jefe", b"what do ya want for nothing?"),
       ("rfc3", b"\xaa" * 20, b"\xdd" * 50),
       ("rfc4", bytes(range(1, 26)), b"\xcd" * 50),
       ("rfc5", b"\x0c" * 20, b"Test With Truncation"),
       ("pattern", bytes(range(32)), pattern)]
for name, m in sha: print("sha", name, hashlib.sha256(m).hexdigest())
for name, k, m in mac: print("hmac", name, hmac.new(k, m, hashlib.sha256).hexdigest())
EOF
sed -nE 's/^\[ *[0-9]+ ms\] uart: ((sha|hmac) [a-z0-9]+ [0-9a-f]{64})$/\1/p' "$L" | sort > "$OUT/got.txt"
sort "$OUT/expected.txt" > "$OUT/exp.txt"
if ! cmp -s "$OUT/got.txt" "$OUT/exp.txt"; then
  diff "$OUT/exp.txt" "$OUT/got.txt" || true
  fail "a digest differs from Python's ($L)"
fi
N=$(wc -l < "$OUT/got.txt" | tr -d ' ')
T0=$(ms_of "$L" 'uart: sha million start'); T1=$(ms_of "$L" 'uart: sha million [0-9a-f]')
BLOCK_US=$(( (T1 - T0) * 1000 / 15625 ))
echo "   $N digests equal to Python's; the million 'a' in $((T1 - T0)) ms: $BLOCK_US us per block, $(( (T1 - T0) * 1000 / 1000000 )) us per byte"
echo "   a 27 712-byte image (433 blocks + the HMAC's 3): $(( 436 * BLOCK_US / 1000 )) ms"
echo "PASS v1-sha: $N vectors on the emulated PIC ($OUT)"
