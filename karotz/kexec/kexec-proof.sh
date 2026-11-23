#!/bin/sh
# kexec from Terrier OS's 2.6.32 into the 6.1 kernel, in QEMU, before any
# Karotz: the 2.6.32 kernel built with CONFIG_KEXEC (LapinLibre fork branch
# terrier-os-kexec) boots as the Karotz (MACH=1912) on an initramfs holding
# kexec (karotz/kexec/build.sh), the 6.1 zImage and the 6.1 test ramdisk;
# its /init loads the 6.1 kernel and jumps into it. Nothing on a flash.
#
#   karotz/kexec/kexec-proof.sh <dir> <ramdisk tree>
#
# <dir> holds zImage-2632 (with kexec), kexec, zImage (6.1) and rootfs.cramfs
# (as for karotz/kernel/qemu-mini2440/boot.sh). <ramdisk tree> is a Terrier
# OS ramdisk unpacked (its /bin/busybox and /lib run the init script).
# PASS when the 6.1 kernel's banner follows the 2.6.32 one in one boot.
set -eu
DIR=$(cd "${1:?a directory with zImage-2632, kexec, zImage, rootfs.cramfs}" && pwd)
TREE=$(cd "${2:?an unpacked Terrier OS ramdisk}" && pwd)
python3 - "$DIR" "$TREE" <<'PY'
import gzip, os, stat, sys
d, tree = sys.argv[1], sys.argv[2]
INIT = b"""#!/bin/busybox sh
/bin/busybox mount -t proc p /proc
/bin/busybox echo KEXEC-LOAD
/kexec -l /zImage61 --atags --initrd=/rootfs61.cramfs --append="console=ttySAC0,115200 root=/dev/ram0 rootfstype=cramfs ramdisk_size=8192 init=/bin/sh"
/bin/busybox echo KEXEC-EXEC rc=$?
/kexec -e
/bin/busybox echo KEXEC-FAILED
exec /bin/busybox sh
"""
out, ino = [], [1]
def ent(name, mode, data=b"", rdev=(0, 0), target=None):
    if target is not None:
        data = target.encode()
    ino[0] += 1
    nb = name.encode() + b"\0"
    h = "070701" + "".join("%08X" % v for v in (
        ino[0], mode, 0, 0, 1, 0, len(data), 0, 0, rdev[0], rdev[1], len(nb), 0))
    rec = h.encode() + nb
    rec += b"\0" * (-len(rec) % 4) + data + b"\0" * (-len(data) % 4)
    out.append(rec)
for dd in ("bin", "lib", "dev", "proc", "sys", "tmp"):
    ent(dd, stat.S_IFDIR | 0o755)
ent("dev/console", stat.S_IFCHR | 0o600, rdev=(5, 1))
for sub in ("bin", "lib"):
    for root, dirs, files in os.walk(os.path.join(tree, sub)):
        rel = os.path.relpath(root, tree)
        if rel != sub:
            ent(rel, stat.S_IFDIR | 0o755)
        for f in files:
            p = os.path.join(root, f)
            r = os.path.relpath(p, tree)
            if os.path.islink(p):
                ent(r, stat.S_IFLNK | 0o777, target=os.readlink(p))
            else:
                ent(r, stat.S_IFREG | 0o755, open(p, "rb").read())
ent("init", stat.S_IFREG | 0o755, INIT)
ent("kexec", stat.S_IFREG | 0o755, open(f"{d}/kexec", "rb").read())
ent("zImage61", stat.S_IFREG | 0o644, open(f"{d}/zImage", "rb").read())
ent("rootfs61.cramfs", stat.S_IFREG | 0o644, open(f"{d}/rootfs.cramfs", "rb").read())
ent("TRAILER!!!", 0)
blob = b"".join(out)
open(f"{d}/kexec-initramfs.cpio.gz", "wb").write(gzip.compress(blob))
print("initramfs", len(blob), "bytes")
PY
LOG="$DIR/kexec-proof.log"
# An empty NAND, as boot.sh: this QEMU's mini2440 dies without -mtdblock.
podman run --rm -e MINI2440_MACH=1912 -e QEMU_ARM920_PIPELINE=1 -v "$DIR":/k:Z qemu-mini2440 sh -c "
  dd if=/dev/zero of=/tmp/nand.bin bs=2112 count=65536 2>/dev/null
  timeout 240 ./arm-softmmu/qemu-system-arm -M mini2440 -no-reboot -nographic \
    -mtdblock /tmp/nand.bin -kernel /k/zImage-2632 -initrd /k/kexec-initramfs.cpio.gz \
    -append 'console=ttySAC0,115200 rdinit=/init mem=65536K' 2>&1 | tr -d '\r'" > "$LOG" 2>&1 || true
rm -f "$DIR/kexec-initramfs.cpio.gz"
grep -aE "^Linux version|KEXEC-|Starting new kernel|Bye|Kernel panic|PASS|Run /bin/sh|VFS: Mounted" "$LOG" | head -20
if grep -aq "^Linux version 2.6.32" "$LOG" && grep -aq "^Linux version 6.1" "$LOG"; then
  echo "PASS: 2.6.32 started 6.1 by kexec ($LOG)"
else
  echo "FAIL ($LOG)"; exit 1
fi
