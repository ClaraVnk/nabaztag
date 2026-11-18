#!/bin/sh
# The access point and the station, static for ARMv5 (ARM926EJ-S; the
# Karotz's ARM920T runs the same instructions save Thumb-2, which this
# never uses): zig's clang and musl, OpenSSL 3.5 (what SAE's elliptic curves
# need in hostapd and wpa_supplicant 2.11), libnl for nl80211, and init.c.
set -eu
# One-word tools: autoconf's configure (libnl) refuses a compiler "zig cc ...".
# Always optimized: unoptimized, zig builds its compiler-rt for ARMv5 in debug
# mode, and that one does not link (__sync_lock_test_and_set_2, 2026-11-17);
# a later -O from the build wins.
mkdir -p /usr/local/bin
printf '#!/bin/sh\nexec zig cc -target arm-linux-musleabi -mcpu=arm926ej_s -Os "$@"\n' > /usr/local/bin/armcc
printf '#!/bin/sh\nexec zig ar "$@"\n' > /usr/local/bin/armar
printf '#!/bin/sh\nexec zig ranlib "$@"\n' > /usr/local/bin/armranlib
chmod 755 /usr/local/bin/armcc /usr/local/bin/armar /usr/local/bin/armranlib
export CC=armcc AR=armar RANLIB=armranlib
X=/x
mkdir -p $X /out/bin /out/sbin
cd /src
# build-user.sh libs: OpenSSL and libnl (cached apart); build-user.sh apps:
# hostapd, wpa_supplicant and init.c, which change with the proof.
if [ "${1:-}" = libs ]; then

tar xzf openssl-3.5.4.tar.gz && cd openssl-3.5.4
# no-threads: ARMv5 has no atomic instructions, and hostapd and
# wpa_supplicant are single-threaded. --with-rand-seed=devrandom: on a
# kernel before 4.8 OpenSSL's default seeding waits for /dev/random to be
# readable, which on 2.6.32 without an entropy source is never -- SAE's
# first commit hung there (2026-11-17), as the Karotz client 0.1.3 did on
# a real Karotz. A real image should also keep a seed across boots.
./Configure linux-armv4 no-asm no-shared no-tests no-docs no-module no-apps no-threads \
    --with-rand-seed=devrandom \
    --prefix=$X --libdir=lib > /dev/null
make -s -j"$(nproc)" build_libs > /dev/null && make -s install_dev > /dev/null
cd /src

tar xzf libnl-3.11.0.tar.gz && cd libnl-3.11.0
./configure --host=arm-linux-musleabi --prefix=$X --disable-shared --enable-static \
    --disable-cli --disable-debug > /dev/null
make -s -j"$(nproc)" > /dev/null && make -s install > /dev/null
cd /src

exit 0
fi

for p in hostapd wpa_supplicant; do
    tar xzf $p-2.11.tar.gz
    d=/src/$p-2.11/$p
    cp /src/$p.config $d/.config
    make -s -C $d -j"$(nproc)" CC="$CC" \
        EXTRA_CFLAGS="-I$X/include -I$X/include/libnl3 -Os" \
        LDFLAGS="-static -L$X/lib" PKG_CONFIG=false > /dev/null
done
cp /src/hostapd-2.11/hostapd/hostapd /out/sbin/
cp /src/wpa_supplicant-2.11/wpa_supplicant/wpa_supplicant /out/sbin/

armcc -static -Os -o /out/init /src/init.c
file /out/init /out/sbin/*
