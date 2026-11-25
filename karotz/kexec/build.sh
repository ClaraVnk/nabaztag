#!/bin/sh
# kexec for the Karotz (ARM920T), static: to start a newer kernel from the
# 2.6.32 one Terrier OS runs, from a USB key, with nothing written to the
# flash (2026-11-23: Gengar's 2.6.32 rebuilt with CONFIG_KEXEC, fork branch
# terrier-os-kexec; the 6.1 kernel comes next).
#
#   karotz/kexec/build.sh <Buildroot SDK bin dir> <out dir>
#
# The SDK is Terrier OS's own (LapinLibre `./build.sh toolchain`:
# ll/work/toolchain/sdk/bin), so the binary is built for the same CPU and
# C library as the system. kexec-tools is pinned by its sha256 (kernel.org's
# sha256sums.asc), at 2.0.20: 2.0.31 wants <linux/memfd.h>, which the
# toolchain's 2.6.32 headers do not have (2026-11-23).
set -eu
BIN=$(cd "${1:?the Buildroot SDK bin directory}" && pwd)
OUT=$(mkdir -p "${2:?an output directory}" && cd "$2" && pwd)
V=2.0.20
SHA=dad8077f0315445d1f6335579fc4ade222facf82a67124974c7be5303ba4f8c8
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
curl -fsSL -o "$W/k.tar.xz" "https://www.kernel.org/pub/linux/utils/kernel/kexec/kexec-tools-$V.tar.xz"
echo "$SHA  $W/k.tar.xz" | sha256sum -c - >/dev/null
tar -xJf "$W/k.tar.xz" -C "$W"
cd "$W/kexec-tools-$V"
T=arm-buildroot-linux-uclibcgnueabi
PATH="$BIN:$PATH" ./configure --host=$T --without-zlib --without-lzma --without-xen \
    CC="$T-gcc" CFLAGS="-O2 -fcommon" LDFLAGS="-static" >/dev/null   # -fcommon: gcc 14 refuses 2.0.20's shared my_debug
PATH="$BIN:$PATH" make -j"$(nproc)" >/dev/null
cp build/sbin/kexec "$OUT/kexec"
"$BIN/$T-strip" "$OUT/kexec"
file "$OUT/kexec" 2>/dev/null || true
"$BIN/$T-readelf" -A "$OUT/kexec" | grep -E "Tag_CPU_arch|Tag_ARM_ISA" || true
ls -l "$OUT/kexec"
