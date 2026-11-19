#!/bin/sh
# LapinLibre's tests run the rabbit's own ARM BusyBox directly (tests/image/
# test_core_boot.py arm_busybox: "qemu-user through binfmt"). Without that,
# they fall back to the host's tools and 300 web API tests fail on the lock
# (2026-11-19). This registers qemu-arm for ARM ELF binaries on the build
# host, with the F flag (the interpreter is opened now, so it also works
# inside containers), and removes it again.
#   sudo sh karotz/lapinlibre/binfmt-arm.sh on    # before the tests
#   sudo sh karotz/lapinlibre/binfmt-arm.sh off   # after
# The interpreter is Ubuntu's qemu-arm-static, taken from the build image.
set -eu
REG=/proc/sys/fs/binfmt_misc
QEMU=/usr/local/bin/qemu-arm-static-lapinlibre
case "${1:-}" in
on)
  [ -e "$REG/register" ] || mount -t binfmt_misc binfmt_misc "$REG"
  if [ ! -x "$QEMU" ]; then
    c=$(podman create lapinlibre-build)
    podman cp "$c:/usr/bin/qemu-arm-static" "$QEMU"; podman rm -f "$c" >/dev/null
    chmod 755 "$QEMU"
  fi
  [ -e "$REG/qemu-arm" ] && echo -1 > "$REG/qemu-arm"
  printf '%s' ':qemu-arm:M::\x7fELF\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x28\x00:\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:'"$QEMU"':F' > "$REG/register"
  cat "$REG/qemu-arm" ;;
off)
  [ -e "$REG/qemu-arm" ] && echo -1 > "$REG/qemu-arm"; echo "qemu-arm unregistered" ;;
*) echo "usage: binfmt-arm.sh on|off" >&2; exit 2 ;;
esac
