#!/usr/bin/env bash
# Build a runtime bytecode image with a Metal patch applied, so the emulator
# can test bytecode changes and not only C ones.
#
#   ./tools/mkbytecode.sh --sources <violet-bootcode-dir> \
#                         --compiler <mtl_compiler> \
#                         --patch ../../firmware/patch_wifi_reconnect.py \
#                         --out with33.hybrid
#
# <violet-bootcode-dir> is the `bootcode/` directory of OpenJabNab/OpenJabNab,
# which carries `sources/`, `preproc.pl` and `preproc_remove_extra_protos.pl`.
# <mtl_compiler> is nabgcc's, under mtl/.
#
# Why a container: mtl_compiler is a 32-bit x86 binary and a current x86_64
# host has no loader for it. tools/Containerfile.mtl supplies one. Nothing
# else about the build needs isolation.
#
# Why this exists: a patch to wifi.mtl cannot be tested by the emulator until
# it is compiled into an image, and until this script there was no way to do
# that outside the firmware build container. PR #33 sat unverified for three
# weeks for exactly that reason, and the first thing this script proved was
# that it works.
set -euo pipefail

RUNTIME=${RUNTIME:-podman}
SOURCES="" ; COMPILER="" ; OUT="patched.hybrid" ; PATCHES=()

while [ $# -gt 0 ]; do
  case "$1" in
    --sources)  SOURCES="$2"; shift 2 ;;
    --compiler) COMPILER="$2"; shift 2 ;;
    --patch)    PATCHES+=("$2"); shift 2 ;;
    --out)      OUT="$2"; shift 2 ;;
    --runtime)  RUNTIME="$2"; shift 2 ;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$SOURCES" ]  || { echo "--sources is required" >&2; exit 2; }
[ -n "$COMPILER" ] || { echo "--compiler is required" >&2; exit 2; }
[ -d "$SOURCES/sources" ] || { echo "$SOURCES has no sources/ — not a bootcode tree" >&2; exit 2; }
[ -f "$SOURCES/preproc.pl" ] || { echo "$SOURCES has no preproc.pl" >&2; exit 2; }

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cp -a "$SOURCES" "$WORK/bootcode"
cp "$COMPILER" "$WORK/mtl_compiler"
for p in "${PATCHES[@]:-}"; do [ -n "$p" ] && cp "$p" "$WORK/"; done

echo "== building the 32-bit-capable image"
"$RUNTIME" build -q -t nabaztag-mtl -f "$HERE/Containerfile.mtl" "$HERE" >/dev/null

PATCH_NAMES=""
for p in "${PATCHES[@]:-}"; do [ -n "$p" ] && PATCH_NAMES="$PATCH_NAMES $(basename "$p")"; done

"$RUNTIME" run --rm -v "$WORK:/w:z" -w /w nabaztag-mtl sh -c '
  set -e
  for p in '"$PATCH_NAMES"'; do
    echo "== $p"
    python3 "$p" bootcode/sources
  done
  echo "== preprocessing"
  ( cd bootcode/sources \
    && perl ../preproc.pl < main.mtl \
     | perl ../preproc_remove_extra_protos.pl > ../bootcode.mtl )
  echo "== compiling ($(wc -c < bootcode/bootcode.mtl) bytes of Metal)"
  ./mtl_compiler -s bootcode/bootcode.mtl /w/out.hybrid > /w/compile.log
  tail -1 /w/compile.log
'

cp "$WORK/out.hybrid" "$OUT"
echo "== $OUT: $(wc -c < "$OUT") bytes, header $(head -c 13 "$OUT")"
echo "   run it:  make NABGCC=<tree> BYTECODE=$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT") vm"
