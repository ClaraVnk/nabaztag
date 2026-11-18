#!/usr/bin/env bash
# The configuration pages a phone's browser gets, in the emulator.
#
#   tools/config-pages.sh <Nab.elf | boot.bc> [rabbit-vm args...]
#
# Builds the VM against the bootloader bytecode of the image (lifted from
# Nab.elf with bootbc.py), runs scenarios/vm/config-pages.txt, keeps every
# answer and checks them: the setup page plain, its stylesheet linked and
# not inlined; a.htm, s.css, u.htm, b.htm and the error page gzipped behind
# the right Content-Type, each one inflating to a whole page, and /v.js
# giving the setup page its values (gzip_pages, gzip_setup, 2026-11-18);
# then the form sent with z=1, after which the rabbit must join the network.
# Same environment as config-upload.sh: RUNTIME, STEPS, TAG, NO_BUILD.
# EEPROM=<file> boots on that config page instead of an empty one (a copy:
# the file is not changed); EXPECT_SERVER=<url> checks the server v.js
# offers (factory_server: the Terrier in place of Violet's, 2026-11-18).
set -euo pipefail

RUNTIME=${RUNTIME:-podman}
TAG=${TAG:-nabaztag-emulator}
DEFAULT_STEPS=$(sed -n 's/^STEPS=//p' "$(dirname "$0")/../run.sh")
STEPS=${STEPS:-$DEFAULT_STEPS}

if [ $# -lt 1 ]; then sed -n '2,18p' "$0"; exit 2; fi
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
CONF="--no-eeprom"
if [ -n "${EEPROM:-}" ]; then cp "$EEPROM" "$WORK/conf.eeprom"; CONF="--eeprom /work/conf.eeprom"; fi
"$RUNTIME" run --rm -v "$WORK:/work:Z" "$TAG" sh -c \
  "make -s NABGCC=/build/nabgcc BYTECODE=/work/boot.bc vm >/dev/null \
   && ./rabbit-vm /work/scenario.txt $CONF --save-dir /work/out $*" \
  > "$WORK/run.log" 2>&1 || true
grep -E "phone: (GET|answer|a gzipped|the page)|FAIL|PASS|ok  |resets" "$WORK/run.log"

EXPECT_SERVER="${EXPECT_SERVER:-}" python3 - "$WORK/out" <<'EOF'
import gzip, json, os, re, sys
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

for name, ctype, must in (("a.htm", "text/html", b"Connect rabbit"),
                          ("s.css", "text/css", b":root{"),
                          ("u.htm", "text/html", b"Firmware upgrade"),
                          ("b.htm", "text/html", b"Settings saved"),
                          ("c", "text/html", b"Something went wrong")):
    head, body = answer(name)
    if head is None:
        check(name, False, "no answer")
        continue
    gz = "Content-Encoding: gzip" in head
    check(name, gz and f"Content-Type: {ctype}" in head, f"gzip, {ctype}")
    cached = "max-age" in head
    check(name, cached == (name == "s.css"),
          "cached an hour" if cached else "not cached")
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
page = (gzip.decompress(body) if head and "Content-Encoding: gzip" in head
        else body or b"")
check("a.htm", b"src=v.js" in page and b"<MAC>" not in page,
      "loads v.js, no marker left")

head, body = answer("v.js")
try:
    text = body.decode("latin-1")
    assert text.startswith("v([") and text.endswith("])")
    a, scan = json.loads(re.sub(r",\s*\]", "]", "[" + text[2:-1] + "]"))
    ok = (len(a) == 13 and re.fullmatch(r"([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", a[0])
          and a[10] in ("0", "1") and a[11] in ("0", "1")
          and all(len(n) in (4, 5) for n in scan))
    why = f"13 values, MAC {a[0][-5:]}, {len(scan)} network(s) scanned"
except Exception as e:
    ok, why = False, f"not the v([...],[...]) call: {e}"
check("v.js", head is not None and "Content-Type: text/javascript" in head
      and "Content-Encoding" not in head and ok, why)
want = os.environ.get("EXPECT_SERVER")
if want:
    got = a[3] if ok else None
    check("v.js", got == want, f"offers the server {got!r} (want {want!r})")
sys.exit(1 if bad else 0)
EOF
! grep -q FAIL "$WORK/run.log"
