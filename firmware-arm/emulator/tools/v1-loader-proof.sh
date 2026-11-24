#!/bin/sh
# The native v1 firmware's loader (firmware-arm/v1-native, DESIGN.md 7), in
# the v1 emulator on Lazare's board (PIC18LF6525: 8-byte write blocks, 48 KB;
# AT45DB081B). Six runs, PASS or FAIL:
#   1. seed: the first boot puts the ICSP'd application (v30) into slot A,
#      with the HMAC-SHA-256 Python computes for the same bytes;
#   2. a good update: the warren offers v31 (frame 10) and serves its image;
#      the application streams it into slot B and resets; the loader checks
#      the HMAC, copies it row by row, reads it back, runs it; v31 polls;
#   3. a bad signature: v32 signed with another key is refused, v31 keeps
#      running, the PIC's flash is never written, the offer is not retried;
#   4. power cuts: V1_CUTS runs cut at random points between the offer and
#      the new image's banner (download, reset, HMAC, copy, verify, state),
#      each followed by a restart that must end on a polling image;
#   5. a corrupt PIC row: reinstalled from the slot; a corrupt slot on top:
#      fall back to the other slot;
#   6. an image that never feeds the watchdog (v33): after LS_MAX_TRIES boots
#      the loader falls back to v30.
#
#   tools/v1-loader-proof.sh          from firmware-arm/emulator, on a machine
#                                     with podman (the dev VM, never HAOS)
#
# V1_PORT overrides the warren's port (28161), V1_CUTS the number of power
# cuts (12), V1_SEED their random seed (2026).
set -eu
cd "$(dirname "$0")/.."
PORT=${V1_PORT:-28161}
CUTS=${V1_CUTS:-12}
SEED=${V1_SEED:-2026}
OUT=$(mktemp -d)
NATIVE=$(cd ../v1-native && pwd)
fail() { echo "FAIL: $*"; exit 1; }
need() { grep -q -- "$2" "$1" || fail "$3 ($1)"; }
none() { grep -q -- "$2" "$1" && fail "$3 ($1)"; return 0; }
ms_of() { grep -m1 -- "$2" "$1" | sed -E 's/^\[ *([0-9]+) ms\].*/\1/'; }

echo "== build"
podman build -q -t v1native-toolchain -f "$NATIVE/Containerfile" "$NATIVE" >/dev/null
podman run --rm -v "$NATIVE":/src:Z -w /src v1native-toolchain sh -c '
  make clean >/dev/null && make >/dev/null && make size &&
  make app image APP_VERSION=31 >/dev/null &&
  make app image APP_VERSION=32 KEY=keys/wrong.key | grep ^image &&
  make app image APP_VERSION=33 APP_TAG=33-broken APP_DEFS=-DAPP_BROKEN >/dev/null &&
  echo "== the 2.0b board (PIC18F6627):" && make DEV=18f6627 loader app size | grep -A1 -E "^(loader|app)" &&
  python3 mkimage.py image --app out/18f6525/app-30.hex --key keys/test-rabbit.key --out /tmp/a.img' \
  > "$OUT/build.log" 2>&1 || { cat "$OUT/build.log"; fail "the firmware does not build"; }
cat "$OUT/build.log"
HMAC30=$(grep -E '^image: board 0x25 version 30 ' "$OUT/build.log" | tail -1 | sed -E 's/.*hmac //')
podman build -q -t nabaztag-v1-native -f v1/Containerfile .. >/dev/null

FW="$OUT/fw"; mkdir -p "$FW"
cp "$NATIVE/out/18f6525/full.hex" "$NATIVE"/out/18f6525/app-3[123]*.img "$FW/"
WARREN=
warren() {
  [ -z "$WARREN" ] || { podman rm -f "v1warren-$PORT" >/dev/null 2>&1 || true; wait "$WARREN" 2>/dev/null || true; }
  # In a container on the host network, like the emulator: on a Mac that
  # network is the podman machine's, where a warren on the Mac is unreachable.
  podman run --rm --network host --name "v1warren-$PORT" -v "$PWD/tools":/t:ro,Z -v "$FW":/img:ro,Z \
    docker.io/library/python:3.13-alpine python3 -u /t/v1_native_warren.py "$PORT" "$@" >> "$OUT/warren.log" 2>&1 &
  WARREN=$!
  sleep 2
}
trap '[ -z "$WARREN" ] || { kill $WARREN 2>/dev/null; podman rm -f "v1warren-$PORT" >/dev/null 2>&1; }' EXIT
emu() {
  podman run --rm --network host -v "$FW":/img:Z nabaztag-v1-native \
    --hex /img/full.hex --pic 6525 --flash-chip 081 --flash /img/sf.bin --pic-flash /img/pic.bin \
    --warren 127.0.0.1 --port80 "$PORT" "$@"
}
snap() { cp "$FW/sf.bin" "$OUT/$1.sf"; cp "$FW/pic.bin" "$OUT/$1.pic"; }
restore() { cp "$OUT/$1.sf" "$FW/sf.bin"; cp "$OUT/$1.pic" "$FW/pic.bin"; }
healthy() {
  none "$1" 'halted' "the PIC halted"
  none "$1" 'unknown opcode' "an opcode the emulator does not know"
  none "$1" 'nvm: write refused' "a flash write without the unlock sequence"
  none "$1" 'L: no good slot' "the loader found no good slot"
}
# The slot's HMAC as the loader wrote it (page p, bytes 16..47; the emulator
# stores every page on 528 bytes).
slot_hmac() { python3 -c "import sys; p=int(sys.argv[2]); d=open(sys.argv[1],'rb').read(); print(d[528*p+16:528*p+48].hex())" "$FW/sf.bin" "$1"; }

echo "== 1. first boot: the application seeded into slot A"
rm -f "$FW/sf.bin" "$FW/pic.bin"; : > "$OUT/warren.log"
warren
emu --seconds 16 > "$OUT/run1.log" 2>&1 || true
L="$OUT/run1.log"; healthy "$L"
need "$L" 'uart: L: active - ' "the loader did not start from a blank state"
need "$L" 'uart: L: seeded A' "slot A was not seeded"
need "$L" 'uart: L: run' "the loader did not hand over"
need "$L" 'uart: nabv1 native 30' "the application did not start"
need "$L" 'uart: poll 1 done' "no poll answered"
none "$L" 'uart: update: image confirmed' "the seeded image is confirmed by the loader itself; the application should not write the state"
[ "$(slot_hmac 16)" = "$HMAC30" ] || fail "slot A's HMAC $(slot_hmac 16) is not Python's $HMAC30"
none "$L" 'flash: write' "the seed wrote the PIC's flash"
SEED_MS=$(ms_of "$L" 'uart: L: seeded A')
echo "   seeded A in ${SEED_MS} ms (HMAC over $(grep -E '^image: board 0x25 version 30 ' "$OUT/build.log" | tail -1 | sed -E 's/.*length ([0-9]+).*/\1/') bytes, the same HMAC as mkimage.py); run at $(ms_of "$L" 'uart: L: run') ms"
snap s1

echo "== 2. a good update: v31 offered, fetched into B, installed by the loader"
warren --offer 31 --image /img/app-31.img
emu --seconds 45 > "$OUT/run2.log" 2>&1 || true
L="$OUT/run2.log"; healthy "$L"
need "$L" 'uart: update: v31 offered' "frame 10 was not taken"
need "$L" 'uart: update: image .* B into B' "the image header was not accepted"
need "$L" 'uart: update: written, rebooting' "the slot was not completed"
need "$L" 'reset: RESET instruction' "the application did not reset"
need "$L" 'uart: L: pending B v001f' "the loader did not see the pending slot"
need "$L" 'uart: L: copy B' "the loader did not copy"
need "$L" 'uart: L: installed' "the install did not complete"
need "$L" 'uart: nabv1 native 31' "v31 did not start"
need "$OUT/warren.log" 'p3.jsp?sn=0019DB00BEEF&v=31' "v31 never polled"
need "$L" 'uart: update: image confirmed' "v31 did not confirm itself"
need "$L" 'pic flash: .* rows erased, .* blocks of 8 written' "no flash statistics"
T_OFFER=$(ms_of "$L" 'uart: update: v31 offered'); T_WR=$(ms_of "$L" 'uart: update: written')
T_PEND=$(ms_of "$L" 'uart: L: pending B'); T_COPY=$(ms_of "$L" 'uart: L: copy B'); T_INST=$(ms_of "$L" 'uart: L: installed'); T_31=$(ms_of "$L" 'uart: nabv1 native 31')
echo "   offered at ${T_OFFER} ms, slot written at ${T_WR} ms (download $((T_WR - T_OFFER)) ms);"
echo "   loader: HMAC check $((T_COPY - T_PEND)) ms, copy + read-back $((T_INST - T_COPY)) ms; v31 banner at ${T_31} ms"
grep -o 'pic flash: .*' "$L" | sed 's/^/   /'
snap s2

echo "== 3. a bad signature: v32 signed with another key"
warren --offer 32 --image /img/app-32.img
emu --seconds 40 > "$OUT/run3.log" 2>&1 || true
L="$OUT/run3.log"; healthy "$L"
need "$L" 'uart: update: v32 offered' "v32 was not offered"
need "$L" 'uart: L: pending A v0020' "the loader did not look at the pending slot"
need "$L" 'uart: L: bad hmac in A' "the bad signature was not detected"
need "$L" 'uart: L: refused' "the image was not refused"
need "$L" 'uart: nabv1 native 31' "v31 did not run after the refusal"
none "$L" 'flash: write' "the PIC's flash was written for a refused image"
[ "$(grep -c 'uart: update: v32 offered' "$L")" = 1 ] || fail "the refused version was offered again after the reboot"
[ "$(grep -c 'uart: nabv1 native 31' "$L")" = 2 ] || fail "expected two boots of v31"
echo "   refused in $(( $(ms_of "$L" 'uart: L: refused') - $(ms_of "$L" 'uart: L: pending A') )) ms; v31 polled again: $(grep -c 'v=31' "$OUT/warren.log") polls so far"

echo "== 4. $CUTS power cuts between the offer (${T_OFFER} ms) and the new banner (${T_31} ms)"
echo "   a third each in the download (${T_OFFER}-${T_WR} ms), the HMAC check (${T_PEND}-${T_COPY} ms), the copy and state write (${T_COPY}-${T_31} ms)"
warren --offer 31 --image /img/app-31.img
IN_COPY=0; IN_DL=0; REDO=0
for cut in $(python3 -c "
import random; random.seed($SEED)
w = [($T_OFFER, $T_WR), ($T_PEND, $T_COPY), ($T_COPY, $T_31)]
print(' '.join('%.3f' % (random.uniform(*w[i % 3]) / 1000) for i in range($CUTS)))"); do
  restore s1
  emu --seconds "$cut" > "$OUT/cut-$cut-a.log" 2>&1 || true
  emu --seconds 45 > "$OUT/cut-$cut-b.log" 2>&1 || true
  A="$OUT/cut-$cut-a.log"; B="$OUT/cut-$cut-b.log"
  healthy "$B"
  need "$B" 'uart: nabv1 native 31' "after a cut at $cut s the rabbit did not come back on v31"
  need "$B" 'uart: poll 1 done' "after a cut at $cut s the rabbit did not poll"
  if grep -q 'uart: L: copy B' "$A" && ! grep -q 'uart: L: installed' "$A"; then IN_COPY=$((IN_COPY + 1)); fi
  if ! grep -q 'uart: update: written' "$A"; then IN_DL=$((IN_DL + 1)); fi
  if grep -q 'uart: L: pending B v001f' "$B"; then REDO=$((REDO + 1)); fi
  printf '   cut at %s s: %s -> %s\n' "$cut" \
    "$(grep -oE 'uart: (update: (v31 offered|image .*|written.*)|L: (pending B v001f|copy B|installed|run)|nabv1 native 31)' "$A" | tail -1 | sed 's/uart: //')" \
    "$(grep -oE 'uart: L: (pending B v001f|PIC differs from B|copy B|installed|active B tries [0-9a-f]+ flags [0-9a-f]+)' "$B" | head -3 | sed 's/uart: L: //' | tr '\n' ',' | sed 's/,$//')"
done
echo "   $CUTS cuts: $IN_DL during the download, $IN_COPY during the copy, $REDO restarts that installed the pending slot again; all came back on v31"

echo "== 5. a corrupt row in the PIC, then a corrupt slot"
warren
restore s2
python3 -c "import sys; p=sys.argv[1]; d=bytearray(open(p,'rb').read()); d[0x4000]^=0xFF; open(p,'wb').write(d)" "$FW/pic.bin"
emu --seconds 16 > "$OUT/run5a.log" 2>&1 || true     # HMAC 10 s, copy, boot, poll
L="$OUT/run5a.log"; healthy "$L"
need "$L" 'uart: L: PIC differs from B' "the corrupt row was not noticed"
need "$L" 'uart: L: copy B' "the slot was not reinstalled"
need "$L" 'uart: nabv1 native 31' "v31 did not run after the repair"
need "$L" 'uart: poll 1 done' "no poll after the repair"
echo "   PIC row corrupted: reinstalled from B, v31 polls"
restore s2
python3 -c "import sys; p=sys.argv[1]; d=bytearray(open(p,'rb').read()); d[0x4000]^=0xFF; open(p,'wb').write(d)" "$FW/pic.bin"
python3 -c "import sys; p=sys.argv[1]; d=bytearray(open(p,'rb').read()); d[528*209+100]^=0x01; open(p,'wb').write(d)" "$FW/sf.bin"
emu --seconds 30 > "$OUT/run5b.log" 2>&1 || true     # two HMACs, a copy, boot, poll
L="$OUT/run5b.log"; healthy "$L"
need "$L" 'uart: L: PIC differs from B' "the corrupt row was not noticed"
need "$L" 'uart: L: bad hmac in B' "the corrupt slot passed the HMAC"
need "$L" 'uart: L: fall back to A' "no fallback"
need "$L" 'uart: L: copy A' "slot A was not installed"
need "$L" 'uart: nabv1 native 30' "v30 did not run after the fallback"
need "$L" 'uart: poll 1 done' "no poll after the fallback"
echo "   PIC row and slot B corrupted: B refused, A installed, v30 polls"

echo "== 6. an image that never feeds the watchdog: v33"
restore s1
warren --offer 33 --image /img/app-33-broken.img
emu --seconds 60 > "$OUT/run6.log" 2>&1 || true
L="$OUT/run6.log"; healthy "$L"
need "$L" 'uart: L: pending B v0021' "v33 was not pending"
need "$L" 'uart: L: installed' "v33 was not installed"
need "$L" 'uart: nabv1 native 33' "v33 did not start"
need "$L" 'watchdog: no CLRWDT' "the watchdog did not fire on v33"
need "$L" 'uart: L: never confirmed' "the loader did not give up on v33"
need "$L" 'uart: L: fall back to A' "no fallback to A"
need "$L" 'uart: nabv1 native 30' "v30 did not come back"
need "$L" 'uart: poll 1 done' "v30 did not poll after the fallback"
echo "   v33 booted $(grep -c 'uart: nabv1 native 33' "$L") times, $(grep -c 'watchdog:' "$L") watchdog resets, then A: v30 polls at $(ms_of "$L" 'uart: poll 1 done') ms"

echo "PASS v1-loader: seed, update, bad signature, $CUTS power cuts, corrupt row, corrupt slot, broken image ($OUT)"
