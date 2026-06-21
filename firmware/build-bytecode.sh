#!/bin/bash
# Add the mDNS resolver to the (already prepared) hybrid bootcode tree and
# compile it. The point of this script is that a rebuild can never silently drop
# the mDNS resolver (patch_dns) — without it, a rabbit whose gateway refuses its
# unicast DNS can never resolve its server and stays all-orange. See ./README.md
# and ../nabaztag-violet/tools/DNS-HELPER.md.
#
# BASE: <bootcode-dir>/sources/ is the Violet bootcode tree as it builds the
# served bytecode today (stock + the micstream module). THIS script adds the
# mDNS resolver on top — the one piece that must survive every rebuild — and
# compiles. (The full hybrid's other patches — fwota/announcer/RS-SV-FW via
# patch_main.py — currently fail to compile ("Launcher : Syntax error"); fixing
# that is tracked separately and is NOT required for mDNS resolution.)
#
# Usage: build-bytecode.sh <bootcode-dir> [compiler] [out.bin]
#   <bootcode-dir>  the prepared Violet bootcode tree (has sources/, preproc.pl)
#                   e.g. /root/violet-bootcode in the nabi-build container.
#   [compiler]      Metal compiler. Default: RedoXyde mtl_linux's (-m32). Do NOT
#                   use the bundled compiler/mtl_linux/mtl_comp — it's the 64-bit
#                   build and SEGFAULTS.
#   [out.bin]       output (default: <bootcode-dir>/bootcode_hybrid.bin)
#
# patch_dns.py is idempotent, so re-running on an already-patched tree is safe.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BC="${1:?give the prepared bootcode dir (the one with sources/ and preproc.pl)}"
CC="${2:-/root/mtl_linux/mtl_compiler}"
OUT="${3:-$BC/bootcode_hybrid.bin}"

[ -d "$BC/sources" ]          || { echo "no $BC/sources — is this a bootcode tree?"; exit 1; }
[ -f "$BC/sources/dns.mtl" ]  || { echo "no sources/dns.mtl"; exit 1; }
[ -x "$CC" ]                  || { echo "compiler not executable: $CC"; exit 1; }
grep -q "fun dnsreq" "$BC/sources/dns.mtl" || {
  echo "sources/dns.mtl has no dnsreq — not the stock Violet bootcode?"; exit 1; }

echo "==> adding the mDNS resolver (mdnsresolve.mtl + patch_dns)"
cp "$HERE/mdnsresolve.mtl" "$BC/sources/"
python3 "$HERE/patch_dns.py" "$BC/sources/dns.mtl"

echo "==> preprocessing + compiling"
( cd "$BC/sources" && ../preproc.pl < main.mtl | ../preproc_remove_extra_protos.pl > ../bootcode.mtl )
"$CC" -s "$BC/bootcode.mtl" "$OUT"

echo "==> built $OUT ($(wc -c < "$OUT") bytes)"
echo "    deploy: copy it to the add-on's /data/bootcode.hybrid"
