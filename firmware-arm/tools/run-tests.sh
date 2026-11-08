#!/bin/bash
# Run every check in this directory. One place, so none of them rots unnoticed:
#
#   1. mtl_asm.py --self-test     — hand-built program through encoder + checker
#   2. fixtures/*.mtl             — mtl_comp.py vs the committed C++ reference:
#                                   the bytes (NAME.bin) and the type of every
#                                   declaration (NAME.types, the `Compiler :`
#                                   lines the C++ prints while it compiles)
#   3. fixtures/illtyped/*.mtl    — programs the C++ refuses: mtl_comp must
#                                   refuse them too, with the same lines
#                                   (NAME.err), and write nothing
#   4. fixtures/*.py              — mtl_pyc.py vs the SAME reference, i.e. the
#                                   Python and MTL spellings converge on the
#                                   bytes the C++ compiler emits
#   5. bootcode.hybrid            — bin -> masm -> bin round-trip
#   6. the real bootcode and the bootloader, when their sources are at hand:
#        BOOTCODE_MTL=path/to/bootcode.mtl  [BOOTCODE_REF=path/to/reference.bin]
#        BOOT_MTL=path/to/boot.0.0.0.13.mtl [BOOT_REF=path/to/boot.bin]
#                                   types vs fixtures/oracle/*.types (committed)
#                                   and bytes vs the C++ output if given
#
# Exits non-zero if anything differs. No network, no container, no hardware:
# the C++ references are committed next to the sources in fixtures/, so a
# claim in README.md can be re-checked long after the build image is gone.
#
# Regenerating a fixture's reference (only when the fixture itself changes)
# needs the real compiler, which lives in the firmware build image. The
# `Compiler :` lines come out on stderr, the .bin is the output file:
#
#   scp ./fixtures/NAME.mtl dev-loutre:cxxw/
#   ssh dev-loutre 'podman run --rm -v /home/loutre/cxxw:/w:z \
#       localhost/nab-firmware-build:lean bash -lc \
#       "/build/nabgcc/mtl/mtl_linux/mtl_compiler -s /w/NAME.mtl /w/NAME.bin" \
#       2> cxxw/NAME.log'
#   scp dev-loutre:cxxw/NAME.bin ./fixtures/
#   grep '^Compiler :' cxxw/NAME.log | grep -v 'compiling file\|done !' > fixtures/NAME.types
#
# For an ill-typed fixture, NAME.err is that log minus the banner, the
# `compiling file` line, the `>` source-position echo and the `Launcher :`
# verdict — see the filter in check_illtyped below.
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

note() { printf '%-22s %s\n' "$1" "$2"; }
ok()   { pass=$((pass + 1)); note "$1" "ok${2:+  $2}"; }
bad()  { fail=$((fail + 1)); note "$1" "FAIL  $2"; }

# Compile $2 with mtl_comp --types into $TMP/$1.bin, types to $TMP/$1.types.
compile_typed() {
    "$PY" -I "$HERE/mtl_comp.py" "$2" -o "$TMP/$1.bin" --types \
        > "$TMP/$1.out" 2> "$TMP/$1.err"
    local rc=$?
    grep '^Compiler :' "$TMP/$1.out" > "$TMP/$1.types"   # drop the `wrote N bytes` line
    return $rc
}

echo "== mtl_asm self-test =="
if out=$("$PY" -I "$HERE/mtl_asm.py" --self-test 2>&1); then
    ok "--self-test" "$(echo "$out" | tail -1)"
else
    bad "--self-test" "$(echo "$out" | tail -3 | tr '\n' ' ')"
fi

echo
echo "== fixtures: mtl_comp.py vs committed C++ mtl_compiler output (bytes + types) =="
shopt -s nullglob
fixtures=("$HERE"/fixtures/*.mtl)
if [ ${#fixtures[@]} -eq 0 ]; then
    bad "fixtures" "no .mtl found in $HERE/fixtures"
fi
for src in "${fixtures[@]}"; do
    name="$(basename "$src" .mtl)"
    ref="$HERE/fixtures/$name.bin"
    reftypes="$HERE/fixtures/$name.types"
    if [ ! -f "$ref" ]; then
        bad "$name" "no committed reference $name.bin"
        continue
    fi
    if ! compile_typed "$name" "$src"; then
        bad "$name" "compile error: $(tail -1 "$TMP/$name.err")"
        continue
    fi
    if ! cmp -s "$ref" "$TMP/$name.bin"; then
        bad "$name" "differs from $name.bin ($(wc -c < "$ref") vs $(wc -c < "$TMP/$name.bin") bytes)"
        continue
    fi
    if [ ! -f "$reftypes" ]; then
        bad "$name" "bytes ok, but no committed $name.types"
        continue
    fi
    if ! cmp -s "$reftypes" "$TMP/$name.types"; then
        bad "$name" "bytes ok, types differ: $(diff "$reftypes" "$TMP/$name.types" | head -3 | tr '\n' ' ')"
        continue
    fi
    # Sizes in README.md are bodies; the envelope is another 17 bytes.
    ok "$name" "$(( $(wc -c < "$ref") - 17 )) B body byte-identical, $(wc -l < "$reftypes") type lines identical"
done

echo
echo "== fixtures/illtyped: programs the C++ refuses — mtl_comp must refuse them the same way =="
illtyped=("$HERE"/fixtures/illtyped/*.mtl)
if [ ${#illtyped[@]} -eq 0 ]; then
    bad "illtyped" "no .mtl found in $HERE/fixtures/illtyped"
fi
for src in "${illtyped[@]}"; do
    name="$(basename "$src" .mtl)"
    referr="$HERE/fixtures/illtyped/$name.err"
    if [ ! -f "$referr" ]; then
        bad "$name" "no committed verdict $name.err"
        continue
    fi
    if compile_typed "bad_$name" "$src"; then
        bad "$name" "ACCEPTED — the C++ refuses it ($(wc -c < "$TMP/bad_$name.bin") bytes written)"
        continue
    fi
    if [ -f "$TMP/bad_$name.bin" ]; then
        bad "$name" "refused but still wrote $TMP/bad_$name.bin"
        continue
    fi
    # The types printed before the error, then the error lines, in the
    # C++'s order; its `compile error:` trailer is mtl_comp's own.
    { cat "$TMP/bad_$name.types"; grep -v '^compile error:' "$TMP/bad_$name.err"; } > "$TMP/bad_$name.all"
    if cmp -s "$referr" "$TMP/bad_$name.all"; then
        ok "$name" "refused: $(grep -m1 'cannot be unified\|requires [0-9]* arguments\|is a const\|prototype does not match\|is smaller' "$referr" | sed 's/^Compiler : //')"
    else
        bad "$name" "refused, but not the C++'s words: $(diff "$referr" "$TMP/bad_$name.all" | head -3 | tr '\n' ' ')"
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
echo "== the real bootcode and the bootloader (sources not in the repo) =="
# $1 label, $2 source path (may be empty), $3 committed .types, $4 C++ .bin (may be empty)
check_big() {
    local label="$1" src="$2" reftypes="$3" ref="$4"
    if [ -z "$src" ]; then
        note "$label" "skipped (set ${label}_MTL=path/to/source.mtl)"
        return
    fi
    if [ ! -f "$src" ]; then
        bad "$label" "source not found: $src"
        return
    fi
    if ! compile_typed "big_$label" "$src"; then
        bad "$label" "compile error: $(tail -1 "$TMP/big_$label.err")"
        return
    fi
    if ! cmp -s "$reftypes" "$TMP/big_$label.types"; then
        bad "$label" "types differ from $(basename "$reftypes"): $(diff "$reftypes" "$TMP/big_$label.types" | head -3 | tr '\n' ' ')"
        return
    fi
    if [ -n "$ref" ] && ! cmp -s "$ref" "$TMP/big_$label.bin"; then
        bad "$label" "types identical, bytes differ from $ref"
        return
    fi
    ok "$label" "$(wc -l < "$reftypes") type lines identical${ref:+, $(wc -c < "$ref") B byte-identical}"
}
check_big BOOTCODE "${BOOTCODE_MTL:-}" "$HERE/fixtures/oracle/bootcode.types" "${BOOTCODE_REF:-}"
check_big BOOT "${BOOT_MTL:-}" "$HERE/fixtures/oracle/boot.0.0.0.13.types" "${BOOT_REF:-}"

echo
if [ "$fail" -eq 0 ]; then
    echo "OK — $pass check(s) passed."
    exit 0
fi
echo "FAIL — $fail of $((pass + fail)) check(s) failed."
exit 1
