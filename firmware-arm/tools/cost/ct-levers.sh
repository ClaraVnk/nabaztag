#!/bin/bash
# Did -Os (opt_size_crypto) leave the secret-independent paths alone? The
# firmware's own X25519 (the adoption key exchange), Ed25519 verification
# (verifySig) and the three tag compares of the warren session, built with
# the firmware's flags at -O1 (the release) and -Os (the lever), each run
# under qemu-arm on three inputs of the same length: a right tag, a tag
# wrong in its first byte, one wrong in its last; three scalars; three
# signed messages. Equal instruction counts within a group mean no path
# depends on the data; the compares' disassembly is printed too, so a
# branch on data can be seen and not inferred (review of 2026-11-18, O-1;
# measured 2026-11-19).
#
# From the repository root, in the image tools/cost/Containerfile builds:
#   podman build -t localhost/nab-qemu -f firmware-arm/tools/cost/Containerfile .
#   podman run --rm -v $PWD:/m:Z -w /m/firmware-arm/tools/cost localhost/nab-qemu bash ct-levers.sh
#   TLSDEF= bash ct-levers.sh          # nab_tls.c as the control compiles it (AES-GCM in)
#   LEVELS="-O1" bash ct-levers.sh     # one level only
#
# It counts instructions, not cycles: the ARM7TDMI's multiplier finishes
# early on small operands and no instruction count can see that (TweetNaCl's
# field arithmetic multiplies, like every X25519).
set -e
cd "$(dirname "$0")"
REPO=$(cd ../../.. && pwd)
FW=${FW:-../../crypto}
TN=${TN:-tweetnacl}
OUT=${OUT:-out/ct-levers}; mkdir -p $OUT/keys inc/net
LEVELS=${LEVELS:--O1 -Os}
TLSDEF=${TLSDEF--DNAB_TLS_CHACHA_ONLY}
CC=arm-none-eabi-gcc

# TweetNaCl as the firmware compiles it (sign_open_alias, verify_wdt), with
# crypto_scalarmult still there; upstream beside it for the host signer.
[ -f $TN/tweetnacl.c ] || python3 prep-tweetnacl.py "$REPO"
for h in $FW/*.h; do ln -sf "$(realpath $h)" inc/net/$(basename $h); done

# A throwaway Ed25519 key and three signed 64-byte messages, never the
# real key: nab_sig.c is built around $OUT/keys/signing_pubkey.h.
if [ ! -f $OUT/ed_inputs.h ]; then
  gcc -O1 -w -I$TN -o $OUT/sign-host sign.c $TN/tweetnacl_upstream.c
  $OUT/sign-host keygen $OUT/pk.bin $OUT/sk.bin
  python3 - $OUT/pk.bin > $OUT/keys/signing_pubkey.h <<'EOF'
import sys
pk = open(sys.argv[1], 'rb').read()
print('/* throwaway key for the cost harness */\n#ifndef NAB_SIGNING_PUBKEY_H\n#define NAB_SIGNING_PUBKEY_H\n#include <stdint.h>\nstatic const uint8_t nab_signing_pubkey[32] = {')
for i in range(0, 32, 8): print('    ' + ', '.join('0x%02x' % b for b in pk[i:i+8]) + ',')
print('};\n#endif')
EOF
  python3 -c 'import sys; open(sys.argv[1],"wb").write(bytes(64)); open(sys.argv[2],"wb").write(bytes([255])*64); open(sys.argv[3],"wb").write((b"nabaztag")*8)' $OUT/msg_a.bin $OUT/msg_b.bin $OUT/msg_c.bin
  for m in a b c; do $OUT/sign-host sign $OUT/sk.bin $OUT/msg_$m.bin $OUT/sig_$m.bin; done
  python3 - $OUT > $OUT/ed_inputs.h <<'EOF'
import sys
d = sys.argv[1]
def arr(name, path):
    b = open(path, 'rb').read()
    return f"static const unsigned char {name}[64] = {{" + ",".join("0x%02x" % x for x in b) + "};"
for m in "abc":
    print(arr(f"MSG_{m.upper()}", f"{d}/msg_{m}.bin"))
    print(arr(f"SIG_{m.upper()}", f"{d}/sig_{m}.bin"))
EOF
fi

srcs() {
  case $1 in
    x25519_*|v32_*) echo "$TN/tweetnacl.c" ;;
    ed_*) echo "$FW/nab_sig.c $TN/tweetnacl.c" ;;
    cteq_*) echo "$FW/sha256.c $FW/cmac.c $FW/chachapoly.c" ;;   # nab_tls.c is #included
    cp_*) echo "$FW/chachapoly.c" ;;
  esac
}
defs() {
  case $1 in
    ed_*) echo "-DHAVE_NAB_SIG" ;;
    cteq_*) echo "$TLSDEF" ;;
  esac
}
CASES="x25519_a x25519_b x25519_c ed_a ed_b ed_c cteq_eq cteq_first cteq_last cp_ok cp_first cp_last v32_eq v32_first v32_last"
echo "== instructions executed (qemu-arm), $(arm-none-eabi-gcc --version | head -1), nab_tls.c with '${TLSDEF:-(no define: AES-GCM in)}'"
printf '%-12s' case; for OPT in $LEVELS; do printf '%18s' "$OPT"; done; echo
declare -A N
for c in $CASES; do
  printf '%-12s' $c
  for OPT in $LEVELS; do
    F="$OPT -mthumb -mthumb-interwork -mcpu=arm7tdmi -ffreestanding -nostdlib -fno-builtin -ffunction-sections -fdata-sections -I. -Iinc -Istub -I$FW -I$TN -I$OUT -I$OUT/keys"
    o=""
    for s in rt.c ct-levers.c $(srcs $c); do
      obj=$OUT/$c$OPT-$(basename ${s%.c}).o
      $CC $F -w -DCT=$c $(defs $c) -c $s -o $obj; o="$o $obj"
    done
    $CC $F -static -Wl,--gc-sections -Wl,-e,_start -o $OUT/$c$OPT.elf $o -lgcc
    set +e; qemu-arm $OUT/$c$OPT.elf >/dev/null; rc=$?; set -e
    n=$(qemu-arm -d in_asm,exec,nochain -D /dev/stderr $OUT/$c$OPT.elf 2>&1 >/dev/null | python3 count.py | sed 's/.*instructions //')
    N[$c$OPT]=$n
    printf '%18s' "$n$([ $rc = 0 ] || echo ' WRONG-RESULT')"
  done
  echo
done
echo
echo "== groups: equal counts = no path depends on the data"
echo "   (a compare's 'eq' case differs from the other two by the harness's own xor that"
echo "    makes the wrong tag, and by the branch on the RESULT, which is public: the"
echo "    first/last pair is the test -- the compare must cost the same wherever the"
echo "    difference sits)"
fail=0
verdict() {  # verdict <label> <case> <case> [case]
  local label=$1; shift
  for OPT in $LEVELS; do
    local v=equal
    if [ $# -eq 3 ]; then
      { [ "${N[$1$OPT]}" = "${N[$2$OPT]}" ] && [ "${N[$2$OPT]}" = "${N[$3$OPT]}" ]; } || { v=DIFFER; fail=1; }
    else
      [ "${N[$1$OPT]}" = "${N[$2$OPT]}" ] || { v=DIFFER; fail=1; }
    fi
    printf '%-36s %-4s %s\n' "$label" "$OPT" "$v"
  done
}
verdict "x25519 a/b/c (three scalars)" x25519_a x25519_b x25519_c
verdict "ed25519 verify a/b/c (three messages)" ed_a ed_b ed_c
verdict "ct_eq first/last (nab_tls.c)" cteq_first cteq_last
verdict "chachapoly first/last" cp_first cp_last
verdict "crypto_verify_32 first/last" v32_first v32_last

# DIFF=1: which functions ran a different number of instructions between the
# first two cases of the two arithmetic groups -- where a difference lives,
# when there is one (blkdiff.py, the traces are large and removed after).
if [ -n "${DIFF:-}" ]; then
  echo
  echo "== per-function difference, second case minus first (blkdiff.py)"
  for pair in "x25519_a x25519_b" "ed_a ed_b"; do
    set -- $pair
    for OPT in $LEVELS; do
      for c in $1 $2; do qemu-arm -d in_asm,exec,nochain -D $OUT/$c$OPT.trace $OUT/$c$OPT.elf >/dev/null || true; done
      echo "-- $2 minus $1 at $OPT:"; python3 blkdiff.py $OUT/$1$OPT.trace $OUT/$2$OPT.trace $OUT/$1$OPT.elf | head -14
      rm -f $OUT/$1$OPT.trace $OUT/$2$OPT.trace
    done
  done
fi

echo
echo "== the compares, disassembled: a conditional branch may count or loop, never read the data"
echo "   (a static compare -O1 inlines shows inside its caller: bench for ct_eq, crypto_verify_32 for vn)"
dis() {  # dis <elf> <symbol regex>
  arm-none-eabi-objdump -d $1 | awk -v re="^[0-9a-f]+ <($2)>:" '$0 ~ re {p=1} p && /^$/ {p=0} p'
}
for OPT in $LEVELS; do
  for f in "cteq_eq ct_eq[.a-z0-9]*|bench" "v32_eq vn[.a-z0-9]*|crypto_verify_32[._a-z0-9]*" "cp_ok chachapoly"; do
    set -- $f
    dis $OUT/$1$OPT.elf "$2" > $OUT/$1$OPT.dis
    br=$(grep -cE '\sb(ne|eq|cs|cc|hs|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)(\.n|\.w)?\s' $OUT/$1$OPT.dis || true)
    echo "-- $1 at $OPT ($2): $(grep -c '^ ' $OUT/$1$OPT.dis) instructions, $br conditional branch(es)"
    cat $OUT/$1$OPT.dis
  done
done
exit $fail
