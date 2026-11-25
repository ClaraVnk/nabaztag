#!/bin/sh
# FLASH-PLAN step 3 in the nandsim emulator: karotz/kexec/flash-kernel writes
# a kernel into mtd1 from the key, reads it back, and leaves the ramdisk and
# /usr alone; a spoiled read-back is written once more; two spoiled ones put
# 2.6.32 back; go-back writes 2.6.32; a bad MD5SUMS writes nothing.
#
#   firmware-arm/emulator/karotz/ota/flash-proof.sh
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../../.." && pwd)
podman build -q -t karotz-ota-emu "$HERE" > /dev/null
fail=0
for mode in kernel kernel-fault1 kernel-fault2; do
    echo "== $mode"
    out=$(podman run --rm -e OTA_MODE=$mode -v "$ROOT/karotz/kexec/flash-kernel:/ota/flash-kernel:ro,z" karotz-ota-emu)
    echo "$out" | sed -n '/^OTA: kernel write/,/^OTA-RESULT/p'
    echo "$out" | grep -q "^OTA-RESULT" || { echo "FAIL: $mode did not finish"; fail=1; }
    echo "$out" | grep -q "^OTA-FAIL" && fail=1
done
[ $fail = 0 ] && echo "PASS flash-proof: write, read-back, retry, way back, preflight" || { echo "FAIL flash-proof"; exit 1; }
