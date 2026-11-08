#!/bin/bash
# Run every check in this directory. One place, so none of them rots unnoticed:
#
#   1. mtl_asm.py --self-test     — hand-built program through encoder + checker
#   2. fixtures/*.mtl             — mtl_comp.py vs the committed C++ reference
#   3. fixtures/*.py              — mtl_pyc.py vs the SAME reference, i.e. the
#                                   Python and MTL spellings converge on the
#                                   bytes the C++ compiler emits
#   4. bootcode.hybrid            — bin -> masm -> bin round-trip
#
# Exits non-zero if anything differs. No network, no container, no hardware:
# the C++ references are committed next to the sources in fixtures/, so a
# claim in README.md can be re-checked long after the build image is gone.
#
# Regenerating a fixture's reference (only when the fixture itself changes)
# needs the real compiler, which lives in the firmware build image:
#
#   scp ./fixtures/NAME.mtl dev-loutre:cxxw/
#   ssh dev-loutre 'podman run --rm -v /home/loutre/cxxw:/w:z \
#       localhost/nab-firmware-build:lean bash -lc \
#       "/build/nabgcc/mtl/mtl_linux/mtl_compiler -s /w/NAME.mtl /w/NAME.bin"'
#   scp dev-loutre:cxxw/NAME.bin ./fixtures/
#
# Never build the image and never run this on the HAOS host.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PY="${PYTHON:-python3}"
fail=0
pass=0

note() { printf '%-18s %s\n' "$1" "$2"; }
ok()   { pass=$((pass + 1)); note "$1" "ok${2:+  $2}"; }
bad()  { fail=$((fail + 1)); note "$1" "FAIL  $2"; }

echo "== mtl_asm self-test =="
if out=$("$PY" -I "$HERE/mtl_asm.py" --self-test 2>&1); then
    ok "--self-test" "$(echo "$out" | tail -1)"
else
    bad "--self-test" "$(echo "$out" | tail -3 | tr '\n' ' ')"
fi

echo
echo "== fixtures: mtl_comp.py vs committed C++ mtl_compiler output =="
shopt -s nullglob
fixtures=("$HERE"/fixtures/*.mtl)
if [ ${#fixtures[@]} -eq 0 ]; then
    bad "fixtures" "no .mtl found in $HERE/fixtures"
fi
for src in "${fixtures[@]}"; do
    name="$(basename "$src" .mtl)"
    ref="$HERE/fixtures/$name.bin"
    if [ ! -f "$ref" ]; then
        bad "$name" "no committed reference $name.bin"
        continue
    fi
    if ! out=$("$PY" -I "$HERE/mtl_comp.py" "$src" -o "$TMP/$name.bin" 2>&1); then
        bad "$name" "compile error: $(echo "$out" | tail -1)"
        continue
    fi
    if cmp -s "$ref" "$TMP/$name.bin"; then
        # Sizes in README.md are bodies; the envelope is another 17 bytes.
        ok "$name" "$(( $(wc -c < "$ref") - 17 )) B body, byte-identical"
    else
        bad "$name" "differs from $name.bin ($(wc -c < "$ref") vs $(wc -c < "$TMP/$name.bin") bytes)"
    fi
done

echo
echo "== fixtures: mtl_pyc.py vs the same C++ reference =="
pyfixtures=("$HERE"/fixtures/*.py)
for src in "${pyfixtures[@]}"; do
    name="$(basename "$src" .py)"
    ref="$HERE/fixtures/$name.bin"
    if [ ! -f "$ref" ]; then
        bad "$name.py" "no committed reference $name.bin"
        continue
    fi
    if ! out=$("$PY" -I "$HERE/mtl_pyc.py" "$src" -o "$TMP/$name.py.bin" 2>&1); then
        bad "$name.py" "compile error: $(echo "$out" | tail -1)"
        continue
    fi
    if cmp -s "$ref" "$TMP/$name.py.bin"; then
        ok "$name.py" "byte-identical"
    else
        bad "$name.py" "differs from $name.bin"
    fi
done

echo
echo "== bootcode.hybrid: bin -> masm -> bin =="
hybrid="$REPO/nabaztag-violet/bootcode.hybrid"
if [ ! -f "$hybrid" ]; then
    note "bootcode.hybrid" "skipped (not in this checkout)"
elif ! "$PY" -I "$HERE/mtl_dis.py" "$hybrid" --format masm > "$TMP/h.masm" 2>"$TMP/h.err"; then
    bad "bootcode.hybrid" "disassembly failed: $(tail -1 "$TMP/h.err")"
elif ! "$PY" -I "$HERE/mtl_asm.py" "$TMP/h.masm" -o "$TMP/h.bin" >/dev/null 2>"$TMP/h.err"; then
    bad "bootcode.hybrid" "reassembly failed: $(tail -1 "$TMP/h.err")"
elif "$PY" -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from mtl_dis import _strip_amber_wrapper
a = _strip_amber_wrapper(open(sys.argv[2], "rb").read())
b = open(sys.argv[3], "rb").read()
sys.exit(0 if a == b else 1)
' "$HERE" "$hybrid" "$TMP/h.bin"; then
    ok "bootcode.hybrid" "$(wc -c < "$TMP/h.bin") B body round-trips"
else
    bad "bootcode.hybrid" "round-trip is not byte-identical"
fi

echo
if [ "$fail" -eq 0 ]; then
    echo "OK — $pass check(s) passed."
    exit 0
fi
echo "FAIL — $fail of $((pass + fail)) check(s) failed."
exit 1
