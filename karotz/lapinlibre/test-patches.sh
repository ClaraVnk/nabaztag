#!/bin/sh
# Do the Terrier OS patches (patches/) still apply to the LapinLibre commit the
# Containerfile builds (LL_COMMIT), and does the Python they touch compile with
# the version override in place? A fresh checkout of that one commit, the
# patches applied as the Containerfile applies them, then python3's compiler
# and build_version() with and without LL_BUILD_VERSION. PASS or the first
# failure. Needs git, python3 and the network (the fork on GitHub).
#
#   karotz/lapinlibre/test-patches.sh
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(sed -n 's/^ARG LL_REPO=//p' "$HERE/Containerfile")
COMMIT=$(sed -n 's/^ARG LL_COMMIT=//p' "$HERE/Containerfile")
W=$(mktemp -d)
TOP=$W
trap 'rm -rf "$TOP"' EXIT
# A short LL_COMMIT cannot be fetched alone: the history without the blobs, then that commit.
git clone -q --filter=blob:none "$REPO" "$W/ll" && git -C "$W/ll" checkout -q "$COMMIT"
W=$W/ll
for p in "$HERE"/patches/*.patch; do
    git -C "$W" apply --index "$p" || { echo "FAIL: $(basename "$p") does not apply to $COMMIT"; exit 1; }
done
python3 -m py_compile "$W/image/tools/image.py" || { echo "FAIL: image.py does not compile"; exit 1; }
# build_version() alone, without the module's imports (they need the build tree).
python3 - "$W/image/tools/image.py" <<'PY'
import os, re, sys
src = open(sys.argv[1]).read()
m = re.search(r"^def build_version\(\):.*?(?=^def )", src, re.S | re.M)
assert m, "no build_version()"
ns = {"os": os, "git_describe": lambda: "v0.2.0-58-gabc"}
exec(m.group(0), ns)
os.environ["LL_BUILD_VERSION"] = "1.0.0"
assert ns["build_version"]() == "1.0.0", "the override is not taken"
del os.environ["LL_BUILD_VERSION"]
assert ns["build_version"]() == "v0.2.0-58-gabc", "git describe is not the default"
PY
grep -q "nowait.600 web" "$W/image/tools/image.py" || { echo "FAIL: inetd line not patched"; exit 1; }
grep -q "inittab /usr/etc/inetd.conf" "$W/image/usbkey/autorun" || { echo "FAIL: autorun not patched"; exit 1; }
echo "PASS: $(ls "$HERE"/patches/*.patch | wc -l | tr -d ' ') patches apply to $COMMIT, image.py compiles, LL_BUILD_VERSION taken"
