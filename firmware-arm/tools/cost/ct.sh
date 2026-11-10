#!/bin/bash
# Is SAE's hunting-and-pecking constant time? Builds ct.c (the firmware's own
# sae.c, p256.c and sha256.c, its flags) once per case and counts the
# instructions qemu-arm executes. The two cases of a pair run the same code
# on different data -- a valid and an invalid x for p256_decompress, a
# password whose first point is at round 1 and one whose first point is at
# round 12 -- so equal counts mean no secret-dependent path.
#
# From the repository root:
#   podman build -t localhost/nab-qemu -f firmware-arm/tools/cost/Containerfile .
#   podman run --rm -v $PWD:/m:Z -w /m/firmware-arm/tools/cost localhost/nab-qemu bash ct.sh
#   ... bash ct.sh /tmp/old/firmware-arm/crypto      # another version, e.g. from git archive
#   DIFF=1 bash ct.sh                                  # which functions differ, per pair
#
# It counts instructions, not cycles: the ARM7TDMI's multiplier finishes
# early on small operands and no instruction count can see that.
set -e
cd "$(dirname "$0")"
FW=${1:-../../crypto}
OUT=out/ct-$(echo "$FW" | tr / _); mkdir -p $OUT inc/net
for h in $FW/*.h; do ln -sf "$(realpath $h)" inc/net/$(basename $h); done
F="-O1 -mthumb -mthumb-interwork -mcpu=arm7tdmi -ffreestanding -nostdlib -fno-builtin -ffunction-sections -fdata-sections -I. -Iinc -Istub -I$FW"
for c in dec_valid dec_invalid pwe_round1 pwe_round12; do
  o=""
  for s in rt.c ct.c $FW/p256.c $FW/sha256.c; do
    obj=$OUT/$c-$(basename ${s%.c}).o
    arm-none-eabi-gcc $F -w -DCT=$c -DBENCH=nop -c $s -o $obj; o="$o $obj"
  done
  arm-none-eabi-gcc $F -static -Wl,--gc-sections -Wl,-e,_start -o $OUT/$c.elf $o -lgcc
  set +e; qemu-arm $OUT/$c.elf >/dev/null; rc=$?; set -e
  n=$(qemu-arm -d in_asm,exec,nochain -D /dev/stderr $OUT/$c.elf 2>&1 >/dev/null | python3 count.py | sed 's/.*instructions //')
  echo "$c rc=$rc instructions=$n"
done
# DIFF=1: per-function instruction difference between the two cases of a pair
if [ -n "$DIFF" ]; then
  for pair in "dec_valid dec_invalid" "pwe_round1 pwe_round12"; do
    set -- $pair
    for c in $1 $2; do qemu-arm -d in_asm,exec,nochain -D $OUT/$c.trace $OUT/$c.elf >/dev/null || true; done
    echo "-- $2 minus $1:"; python3 blkdiff.py $OUT/$1.trace $OUT/$2.trace $OUT/$1.elf | head -12
    rm -f $OUT/$1.trace $OUT/$2.trace
  done
fi
