#!/bin/bash
# Build the full hybrid runtime bytecode from the stock Violet bootcode:
#   - micstream  : server-triggered mic UDP stream (RS/RT commands)
#   - SV         : volume command
#   - fwota      : Ed25519-gated OTA (FW command) — needs the verifySig opcode,
#                  so it MUST be compiled with the patched compiler (see below)
#                  and only *runs* on Naboot firmware.
#   - mdns       : the mDNS announcer (publishes naboot.local)
#   - mdnsresolve: the mDNS resolver — resolves a .local server name over
#                  multicast so a rabbit whose gateway drops its unicast DNS can
#                  still find its server. The one piece that must never be lost.
#
# A rebuild can never silently drop any of these: this script applies them all,
# in order, with the right compiler, from a stock Violet bootcode tree.
#
# Usage: build-bytecode.sh <bootcode-dir> [compiler] [out.bin]
#   <bootcode-dir>  the stock Violet bootcode tree (has sources/, preproc.pl)
#                   e.g. /root/violet-bootcode in the nabi-build container.
#   [compiler]      Metal compiler. Default: RedoXyde mtl_linux's *patched* build,
#                   which knows the custom verifySig opcode (152). The plain
#                   mtl_compiler errors "unknown label 'verifySig'" on fwota, and
#                   the bundled compiler/mtl_linux/mtl_comp is 64-bit and SEGFAULTS.
#   [out.bin]       output (default: <bootcode-dir>/bootcode_hybrid.bin)
#
# All patch scripts are idempotent; re-running on an already-patched tree is safe.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BC="${1:?give the Violet bootcode dir (the one with sources/ and preproc.pl)}"
CC="${2:-/root/mtl_linux/mtl_compiler_patched}"
OUT="${3:-$BC/bootcode_hybrid.bin}"

[ -d "$BC/sources" ]         || { echo "no $BC/sources — is this a bootcode tree?"; exit 1; }
[ -f "$BC/sources/main.mtl" ] || { echo "no sources/main.mtl"; exit 1; }
[ -x "$CC" ]                 || { echo "compiler not executable: $CC"; exit 1; }
grep -q "fun dnsreq" "$BC/sources/dns.mtl" || { echo "sources/dns.mtl: no dnsreq — not stock Violet?"; exit 1; }

echo "==> copying our modules into sources/"
cp "$HERE/micstream.mtl" "$HERE/fwota.mtl" "$HERE/mdns.mtl" \
   "$HERE/mdnsresolve.mtl" "$BC/sources/"

echo "==> patch_main.py  (micstream/fwota/mdns includes + RS/RT/SV/FW + announcer tick)"
python3 "$HERE/patch_main.py" "$BC/sources/main.mtl"
echo "==> patch_dns.py   (mDNS resolver for .local)"
python3 "$HERE/patch_dns.py" "$BC/sources/dns.mtl"

echo "==> preprocessing + compiling (with $CC)"
( cd "$BC/sources" && ../preproc.pl < main.mtl | ../preproc_remove_extra_protos.pl > ../bootcode.mtl )
"$CC" -s "$BC/bootcode.mtl" "$OUT"

echo "==> built $OUT ($(wc -c < "$OUT") bytes)"
echo "    deploy: copy it to the add-on's /data/bootcode.hybrid"
echo "    note: the FW/OTA path only *runs* on Naboot firmware (verifySig opcode);"
echo "          on stock firmware it stays dormant unless a FW command is sent."
