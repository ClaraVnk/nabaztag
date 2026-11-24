#!/bin/sh
# mkcpio.py on a tree that holds what a ramdisk's /dev holds once unpacked as
# root: a FIFO (and device nodes when the test runs as root). Before 2026-11-24
# the script read every entry as a file, and a FIFO in the tree hung it for
# good (--all copies the whole ramdisk, /dev included). PASS when the archive
# is written within 20 s and lists the FIFO as a FIFO and a file as a file.
#
#   karotz/kexec/test-mkcpio.sh
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/tree/bin" "$T/tree/dev" "$T/tree/lib"
printf '#!/bin/sh\n' > "$T/tree/bin/busybox"
mkfifo "$T/tree/dev/log"
# Device nodes need root; a tty node when it can be made.
mknod "$T/tree/dev/null" c 1 3 2> /dev/null || true
printf '#!/bin/sh\nexit 0\n' > "$T/init"
# A hang is the failure mode: the archive must come within 20 s.
if command -v timeout > /dev/null; then
    timeout 20 python3 "$HERE/mkcpio.py" --all "$T/out.cpio.gz" "$T/tree" "$T/init" > /dev/null
else
    python3 "$HERE/mkcpio.py" --all "$T/out.cpio.gz" "$T/tree" "$T/init" > /dev/null
fi
python3 - "$T/out.cpio.gz" <<'PY'
import gzip, stat, sys
raw = gzip.open(sys.argv[1]).read()
seen, at = {}, 0
while raw[at:at + 6] == b"070701":
    mode = int(raw[at + 14:at + 22], 16)
    size = int(raw[at + 54:at + 62], 16)
    nlen = int(raw[at + 94:at + 102], 16)
    name = raw[at + 110:at + 110 + nlen - 1].decode()
    seen[name] = mode
    at += 110 + nlen
    at += -at % 4
    at += size
    at += -at % 4
assert "TRAILER!!!" in seen, "no trailer"
assert stat.S_ISFIFO(seen["dev/log"]), "dev/log is not a FIFO: %o" % seen["dev/log"]
assert stat.S_ISREG(seen["bin/busybox"]), "bin/busybox is not a file"
assert stat.S_ISREG(seen["init"]) and seen["init"] & 0o111, "init not executable"
if "dev/null" in seen:
    assert stat.S_ISCHR(seen["dev/null"]), "dev/null is not a char device"
print("PASS: mkcpio.py keeps a FIFO%s and the files (%d entries)" % (
    " and a device node" if "dev/null" in seen else "", len(seen)))
PY
