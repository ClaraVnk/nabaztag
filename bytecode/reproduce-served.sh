#!/bin/bash
# Rebuild, byte for byte, a runtime the warren serves, from this repository
# alone: the control a new runtime is compared with before it replaces it.
#
#   bytecode/reproduce-served.sh https <out>   # baseline-https, bfe26cdd...
#   bytecode/reproduce-served.sh tls   <out>   # baseline-tls,   b697ef25...
#
# Both are the runtimes of the 2026-11-23 core batch (head in sleep, triple
# press, Linotte's DNS chain): build-bytecode.sh of 4c82037 with RUNTIME set,
# in a throwaway worktree so later commits on main do not move them. The
# ones before (69ec1ea3... and 16efd859...) are in /data/bytecode-archive on
# the warren. Prints the sha256 and whether it matches.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
KIND="${1:?https or tls}"
OUT="$(cd "$(dirname "${2:?output file}")" && pwd)/$(basename "$2")"
COMMIT=4c82037
case "$KIND" in
  https) WANT=bfe26cddd6c1c313 ;;
  tls)   WANT=b697ef2575895c78 ;;
  *) echo "https or tls"; exit 2 ;;
esac
WT="$(mktemp -d)"
TREE="$(mktemp -d)"
git -C "$REPO" worktree add -q --detach "$WT" "$COMMIT"
cp -r "$WT/firmware-arm/vendor/openjabnab/bootcode/." "$TREE/"
RUNTIME="$KIND" bash "$WT/bytecode/build-bytecode.sh" "$TREE" "$WT/firmware-arm/tools/mtl_comp.py" "$OUT" >/dev/null
git -C "$REPO" worktree remove --force "$WT"
rm -rf "$TREE"
GOT="$(sha256sum "$OUT" | cut -c1-16)"
if [ "$GOT" = "$WANT" ]; then echo "$KIND: $GOT, the one the warren serves"; else echo "$KIND: $GOT, NOT $WANT"; exit 1; fi
