#!/usr/bin/env bash
# Build a runtime bytecode image with a Metal patch applied, so the emulator
# can test bytecode changes and not only C ones.
#
#   ./tools/mkbytecode.sh --sources <violet-bootcode-dir> \
#                         --patch ../../bytecode/patch_wifi_reconnect.py \
#                         --out with33.hybrid
#
# <violet-bootcode-dir> is the `bootcode/` directory of OpenJabNab/OpenJabNab,
# which carries `sources/`, `preproc.pl` and `preproc_remove_extra_protos.pl`.
#
# The compiler is firmware-arm/tools/mtl_comp.py, which compiles the real
# bootcode byte-identically to nabgcc's 32-bit C++ mtl_compiler and types it
# the same way. It needs python3 and perl, nothing else: the container this
# script used to run the 32-bit binary in (a Debian image with an i386
# loader) went with it (2026-11-12). --compiler still takes a native
# mtl_compiler, run as `<compiler> -s IN OUT`, for a host that can load it.
#
# Why this exists: a patch to wifi.mtl cannot be tested by the emulator until
# it is compiled into an image, and until this script there was no way to do
# that outside the firmware build container. PR #33 sat unverified for three
# weeks for exactly that reason, and the first thing this script proved was
# that it works.
set -euo pipefail

SOURCES="" ; COMPILER="" ; OUT="patched.hybrid" ; PATCHES=()

while [ $# -gt 0 ]; do
  case "$1" in
    --sources)  SOURCES="$2"; shift 2 ;;
    --compiler) COMPILER="$2"; shift 2 ;;
    --patch)    PATCHES+=("$2"); shift 2 ;;
    --out)      OUT="$2"; shift 2 ;;
    -h|--help)  sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "$0")" && pwd)"
COMPILER=${COMPILER:-$HERE/../../tools/mtl_comp.py}
[ -n "$SOURCES" ]  || { echo "--sources is required" >&2; exit 2; }
[ -f "$COMPILER" ] || { echo "no compiler at $COMPILER" >&2; exit 2; }
[ -d "$SOURCES/sources" ] || { echo "$SOURCES has no sources/ — not a bootcode tree" >&2; exit 2; }
[ -f "$SOURCES/preproc.pl" ] || { echo "$SOURCES has no preproc.pl" >&2; exit 2; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp -a "$SOURCES" "$WORK/bootcode"

for p in "${PATCHES[@]:-}"; do
  [ -n "$p" ] || continue
  echo "== $(basename "$p")"
  python3 "$p" "$WORK/bootcode/sources"
done
echo "== preprocessing"
( cd "$WORK/bootcode/sources" \
  && perl ../preproc.pl < main.mtl \
   | perl ../preproc_remove_extra_protos.pl > ../bootcode.mtl )
echo "== compiling ($(wc -c < "$WORK/bootcode/bootcode.mtl") bytes of Metal)"
case "$COMPILER" in
  *.py) python3 "$COMPILER" "$WORK/bootcode/bootcode.mtl" -o "$WORK/out.hybrid" ;;
  *)    "$COMPILER" -s "$WORK/bootcode/bootcode.mtl" "$WORK/out.hybrid" > "$WORK/compile.log"
        tail -1 "$WORK/compile.log" ;;
esac

cp "$WORK/out.hybrid" "$OUT"
echo "== $OUT: $(wc -c < "$OUT") bytes, header $(head -c 13 "$OUT")"
echo "   run it:  make NABGCC=<tree> BYTECODE=$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT") vm"
