#!/usr/bin/env bash
# The configuration pages a phone's browser gets, in the emulator.
#
#   tools/config-pages.sh <Nab.elf | boot.bc> [rabbit-vm args...]
#
# Builds the VM against the bootloader bytecode of the image (lifted from
# Nab.elf with bootbc.py), runs scenarios/vm/config-pages.txt, keeps every
# answer and checks them: the setup page plain, its stylesheet linked and
# not inlined; s.css, u.htm, b.htm and the error page gzipped behind the
# right Content-Type, and each one inflating to a whole page (gzip_pages,
# 2026-11-18). Same environment as config-upload.sh: RUNTIME, STEPS, TAG,
# NO_BUILD.
set -euo pipefail

RUNTIME=${RUNTIME:-podman}
TAG=${TAG:-nabaztag-emulator}
DEFAULT_STEPS=$(sed -n 's/^STEPS=//p' "$(dirname "$0")/../run.sh")
STEPS=${STEPS:-$DEFAULT_STEPS}

if [ $# -lt 1 ]; then sed -n '2,13p' "$0"; exit 2; fi
IMAGE=$1; shift

HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/out"

case "$IMAGE" in
  *.elf|*.ELF|*/Nab.elf)
    python3 "$HERE/tools/bootbc.py" "$IMAGE" -o "$WORK/boot.bc" ;;
  *)
    cp "$IMAGE" "$WORK/boot.bc" ;;
esac
cp "$HERE/scenarios/vm/config-pages.txt" "$WORK/scenario.txt"

if [ -z "${NO_BUILD:-}" ]; then
  echo "== building the emulator image (nabgcc mods: $STEPS)"
  ( cd "$HERE/../.." && "$RUNTIME" build --build-arg "MOD_STEPS=$STEPS" \
      -t "$TAG" -f firmware-arm/emulator/Containerfile . )
fi

echo "== $(basename "$IMAGE") in config mode, a phone asking for every page"
"$RUNTIME" run --rm -v "$WORK:/work:Z" "$TAG" sh -c \
  "make -s NABGCC=/build/nabgcc BYTECODE=/work/boot.bc vm >/dev/null \
   && ./rabbit-vm /work/scenario.txt --no-eeprom --save-dir /work/out $*" \
  | grep -E "phone: (GET|answer|a gzipped|the page)|FAIL|PASS|resets"

python3 - "$WORK/out" <<'EOF'
import gzip, sys
from pathlib import Path

out = Path(sys.argv[1])
bad = 0

def answer(name):
    p = out / name
    if not p.exists():
        return None, None
    head, _, body = p.read_bytes().partition(b"\r\n\r\n")
    return head.decode("latin-1"), body

def check(name, ok, why):
    global bad
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}: {why}")
    bad += not ok

for name, ctype, must in (("s.css", "text/css", b":root{"),
                          ("u.htm", "text/html", b"Firmware upgrade"),
                          ("b.htm", "text/html", b"Settings saved"),
                          ("c", "text/html", b"Something went wrong")):
    head, body = answer(name)
    if head is None:
        check(name, False, "no answer")
        continue
    gz = "Content-Encoding: gzip" in head
    check(name, gz and f"Content-Type: {ctype}" in head, f"gzip, {ctype}")
    try:
        page = gzip.decompress(body) if gz else body
    except Exception as e:
        check(name, False, f"does not inflate: {e}")
        continue
    check(name, must in page, f"{len(body)} bytes inflate to {len(page)}")
    if ctype == "text/html":
        check(name, b"href=s.css" in page and b"<style>" not in page,
              "links the stylesheet")

head, body = answer("a.htm")
check("a.htm", head is not None and "Content-Encoding" not in head
      and b"href=s.css" in body and b"<style>" not in body
      and b"<MAC>" not in body, "plain, fields filled, stylesheet linked")
sys.exit(1 if bad else 0)
EOF
