#!/bin/bash
# Rebuild, byte for byte, a runtime the warren serves, from this repository
# alone: the control a new runtime is compared with before it replaces it.
#
#   bytecode/reproduce-served.sh https <out>   # baseline-https, 812b23d7...
#   bytecode/reproduce-served.sh tls   <out>   # baseline-tls,   b697ef25...
#
# baseline-https is 08cf39b's (2026-11-23 afternoon: RB, a new runtime
# without unplugging, served since); baseline-tls is 4c82037's (the core
# batch of that morning: head in sleep, triple press, Linotte's DNS chain).
# build-bytecode.sh of that commit with RUNTIME set, in a throwaway worktree
# so later commits on main do not move them. The ones before (bfe26cdd...,
# 69ec1ea3..., 16efd859...) are in /data/bytecode on the warren as .bak or
# in /data/bytecode-archive. Prints the sha256 and whether it matches. The
# runtime releases (release-runtime.sh) are built by this script.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
KIND="${1:?https or tls}"
OUT="$(cd "$(dirname "${2:?output file}")" && pwd)/$(basename "$2")"
case "$KIND" in
  https) COMMIT=08cf39b; WANT=812b23d7c9b8813a ;;
  tls)   COMMIT=4c82037; WANT=b697ef2575895c78 ;;
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
