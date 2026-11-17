#!/bin/bash
# Rebuild, byte for byte, a runtime the warren serves, from this repository
# alone: the control a new runtime is compared with before it replaces it.
#
#   bytecode/reproduce-served.sh https <out>   # baseline-https, 7d673c4b...
#   bytecode/reproduce-served.sh tls   <out>   # baseline-tls,   f62c1ca1...
#
# https is today's build-bytecode.sh without the turned-ear fix. tls is the
# runtime of 2026-11-12 (firmwares 1.0.0.x): the tree at 26c3227 plus the
# leading-blank parser fix of 0289095, in a throwaway worktree; today's
# script gives a different (hardened) one, since the update size cap and the
# microphone's 60 s came later. Prints the sha256 and whether it matches.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
KIND="${1:?https or tls}"
OUT="$(cd "$(dirname "${2:?output file}")" && pwd)/$(basename "$2")"
TREE="$(mktemp -d)"
cp -r "$REPO/firmware-arm/vendor/openjabnab/bootcode/." "$TREE/"
case "$KIND" in
  https)
    WANT=7d673c4b07ef72a4
    EARS_REARM=0 bash "$HERE/build-bytecode.sh" "$TREE" "$REPO/firmware-arm/tools/mtl_comp.py" "$OUT" >/dev/null ;;
  tls)
    WANT=f62c1ca16d49f4ca
    WT="$(mktemp -d)"
    git -C "$REPO" worktree add -q --detach "$WT" 26c3227
    git -C "$REPO" show 0289095:bytecode/patch_xml_leading_blank.py > "$WT/bytecode/patch_xml_leading_blank.py"
    python3 - "$WT/bytecode/build-bytecode.sh" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
a = 'echo "==> patch_btn_in_session.py'
s = s.replace(a, 'python3 "$HERE/patch_xml_leading_blank.py" "$BC/sources/xmlparser.mtl"\n' + a, 1)
open(p, "w").write(s)
PY
    bash "$WT/bytecode/build-bytecode.sh" "$TREE" "$WT/firmware-arm/tools/mtl_comp.py" "$OUT" >/dev/null
    git -C "$REPO" worktree remove --force "$WT" ;;
  *) echo "https or tls"; exit 2 ;;
esac
rm -rf "$TREE"
GOT="$(sha256sum "$OUT" | cut -c1-16)"
if [ "$GOT" = "$WANT" ]; then echo "$KIND: $GOT, the one the warren serves"; else echo "$KIND: $GOT, NOT $WANT"; exit 1; fi
