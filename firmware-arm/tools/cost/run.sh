#!/bin/bash
# What does each crypto primitive cost on the rabbit's CPU? Build it against
# the firmware's own sources with the firmware's flags, run it under
# qemu-arm, count executed instructions (count.py), read the code size.
# Runs inside localhost/nab-qemu (qemu-arm + arm-none-eabi-gcc 12.2) with
# this directory mounted at /m:
#   podman run --rm -v $PWD:/m:Z -w /m localhost/nab-qemu bash run.sh
#   bash run.sh x25519 p256        a subset; NOSIZES=1 skips the size tables
# Inputs, laid out by hand before the first run:
#   fw/crypto/      copy of firmware-arm/crypto
#   fw/tests/       copy of firmware-arm/tests (the sae_* cases include its
#                   vectors_h2e.h, the Annex J.10 and RFC 9380 vectors)
#   tweetnacl/      tweetnacl.h + tweetnacl.c with sign_open_alias and
#                   verify_wdt applied but NOT strip_tweetnacl (so X25519 is
#                   still there): python3 prep-tweetnacl.py <repo root>
#                   also puts upstream tweetnacl_upstream.c there, for sign.c
#   bootcode.bin    the 105 KB runtime bytecode (nabaztag-violet/bootcode.hybrid)
#   third/monocypher/   monocypher.[ch] + optional/monocypher-ed25519.[ch]
#   third/c25519/       the c25519 sources (src/c25519/ of DavyLandman/compact25519)
#   (the two third/ trees are only needed by the mc_* and c25519_* cases)
# qemu's instruction count is architectural: cycles are 1.5x to 4x that on
# an ARM7TDMI running Thumb from flash; see results-2026-11-08.txt.
set -e
cd "$(dirname "$0")"
FW=${FW:-fw/crypto}             # firmware-arm/crypto, synced here
TESTS=${TESTS:-fw/tests}        # firmware-arm/tests, for vectors_h2e.h
TN=${TN:-tweetnacl}             # patched tweetnacl.c (sign_open_alias + verify_wdt), unstripped
MC=${MC:-third/monocypher}      # monocypher.c/.h + monocypher-ed25519.c/.h (mc_* cases only)
C25=${C25:-third/c25519}        # c25519 sources (c25519_* cases only)
OUT=${OUT:-out}; mkdir -p $OUT inc/net keys
F="-O1 -mthumb -mthumb-interwork -mcpu=arm7tdmi -ffreestanding -nostdlib -fno-builtin \
   -ffunction-sections -fdata-sections -I. -Iinc -I$TN -Istub -Ikeys -I$FW -I$MC -I$C25 -I$TESTS"
CC=arm-none-eabi-gcc

# The firmware includes its crypto headers as "net/x.h".
for h in $FW/*.h; do ln -sf ../../$h inc/net/$(basename $h); done

# Throwaway Ed25519 key, never the real one.
if [ ! -f keys/signing_pubkey.h ]; then
  gcc -O1 -w -I$TN -o $OUT/sign-host sign.c $TN/tweetnacl_upstream.c
  $OUT/sign-host keygen $OUT/pk.bin $OUT/sk.bin
  python3 - $OUT/pk.bin > keys/signing_pubkey.h <<'EOF'
import sys
pk = open(sys.argv[1], 'rb').read()
print('/* throwaway key for the cost harness */\n#ifndef NAB_SIGNING_PUBKEY_H\n#define NAB_SIGNING_PUBKEY_H\n#include <stdint.h>\nstatic const uint8_t nab_signing_pubkey[32] = {')
for i in range(0, 32, 8): print('    ' + ', '.join('0x%02x' % b for b in pk[i:i+8]) + ',')
print('};\n#endif')
EOF
  head -c 64 bootcode.bin > msg64.bin
  $OUT/sign-host sign $OUT/sk.bin msg64.bin sig64.bin
  $OUT/sign-host sign $OUT/sk.bin bootcode.bin sigboot.bin
fi
for b in bootcode msg64 sig64 sigboot; do
  arm-none-eabi-objcopy -I binary -O elf32-littlearm -B arm $b.bin $OUT/$b.o
  arm-none-eabi-objcopy --rename-section .data=.rodata,alloc,load,readonly,data,contents $OUT/$b.o
done

srcs() {
  case $1 in
    nop) echo "" ;;
    x25519|x25519_base|sha512_long|sha512_short) echo "$TN/tweetnacl.c" ;;
    ed_short|ed_long) echo "$FW/nab_sig.c $TN/tweetnacl.c" ;;
    memcpy_105k) echo "" ;;
    p256|p256_base) echo "$FW/p256.c" ;;
    sae_pt|sae_commit_h2e|sae_commit_hnp) echo "$FW/sae.c $FW/p256.c $FW/sha256.c" ;;
    gcm_kat|gcm_1k|ctr_1k|ghash_1k|aes_block) echo "aes128_enc.c gcm.c" ;;
    hkdf_kat|hkdf) echo "$FW/sha256.c hkdf.c" ;;
    sha256_4k|sha256_1k) echo "$FW/sha256.c" ;;
    aes_block_cmac) echo "$FW/cmac.c" ;;
    mc_x25519) echo "$MC/monocypher.c" ;;
    mc_*) echo "$MC/monocypher.c $MC/monocypher-ed25519.c" ;;
    c25519_*) echo "$C25/c25519.c $C25/f25519.c $C25/fprime.c $C25/ed25519.c $C25/edsign.c $C25/sha512.c" ;;
  esac
}
objs() {
  case $1 in
    ed_short|mc_ed_short|c25519_ed_short) echo "$OUT/msg64.o $OUT/sig64.o" ;;
    ed_long|mc_ed_long|c25519_ed_long) echo "$OUT/bootcode.o $OUT/sigboot.o" ;;
    sha512_long|sha512_short|memcpy_105k|mc_sha512_long) echo "$OUT/bootcode.o" ;;
  esac
}

ALL="nop memcpy_105k x25519 x25519_base ed_short ed_long sha512_long sha512_short p256 p256_base sae_pt sae_commit_h2e sae_commit_hnp aes_block_cmac aes_block gcm_kat gcm_1k ctr_1k ghash_1k hkdf_kat hkdf sha256_1k sha256_4k"
[ $# -gt 0 ] && ALL="$*"

for b in $ALL; do
  elf=$OUT/$b.elf; o=""
  def=""; case $b in ed_*) def="-DHAVE_NAB_SIG" ;; esac
  for s in rt.c bench.c $(srcs $b); do
    obj=$OUT/$b-$(basename ${s%.c}).o
    $CC $F -w -DBENCH=$b $def -c $s -o $obj; o="$o $obj"
  done
  $CC $F -static -Wl,--gc-sections -Wl,-e,_start -o $elf $o $(objs $b) -lgcc
  text=$(arm-none-eabi-size $elf | awk 'NR==2{print $1}')
  rodata_blob=0; for ob in $(objs $b); do rodata_blob=$((rodata_blob + $(arm-none-eabi-size $ob | awk 'NR==2{print $1+$2}'))); done
  set +e; qemu-arm $elf > $OUT/$b.stdout; rc=$?; set -e
  tick=$(arm-none-eabi-nm $elf | awk '/ nab_verify_tick$/{print $1}')
  qemu-arm -d in_asm,exec,nochain -D /dev/stderr $elf 2>&1 >/dev/null | python3 count.py ${tick:-} > $OUT/$b.count
  echo "== $b rc=$rc text=$text (embedded blobs $rodata_blob) :: $(tr '\n' ' ' < $OUT/$b.count)"
  arm-none-eabi-nm -S --size-sort $elf | python3 symsz.py | grep -v _binary_ | tail -12 > $OUT/$b.syms
done
[ -n "$NOSIZES" ] && exit 0

echo "== code size per function (added code), bytes:"
for s in gcm.c hkdf.c aes128_enc.c; do
  $CC $F -w -c $s -o $OUT/sz-$s.o
  echo "-- $s: text+rodata $(arm-none-eabi-size $OUT/sz-$s.o | awk 'NR==2{print $1+$2}')"
  arm-none-eabi-nm -S --size-sort $OUT/sz-$s.o | python3 symsz.py
done
echo "== firmware crypto objects, text+rodata:"
for s in $FW/p256.c $FW/sha256.c $FW/aes128.c $FW/cmac.c $FW/sae.c $FW/nab_sig.c $TN/tweetnacl.c; do
  $CC $F -w -DHAVE_NAB_SIG -c $s -o $OUT/sz-$(basename $s).o
  echo "   $(arm-none-eabi-size $OUT/sz-$(basename $s).o | awk 'NR==2{print $1+$2}') $(basename $s)"
done
echo "== tweetnacl per function:"
arm-none-eabi-nm -S --size-sort $OUT/sz-tweetnacl.c.o | python3 symsz.py
echo "== p256.c per function:"
arm-none-eabi-nm -S --size-sort $OUT/sz-p256.c.o | python3 symsz.py
