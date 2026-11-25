#!/bin/sh
# Patch 0004 run: LapinLibre's own inetd.sh (with Terrier OS's patches) writes
# the web server's rules for the rabbit itself only when the install drew the
# password (/usr/etc/conf/web_local_only), for the local network otherwise.
# inetd.sh runs in a scratch directory, its paths moved there, its last
# `exec inetd` replaced by an exit; nothing else of it is changed.
#
#   karotz/lapinlibre/test-web-local.sh
#
# Why (2026-11-24): the drawn password is the client's alone, which calls the
# API from the rabbit itself; nothing on the network needs the API (Clara:
# "fermé par défaut").
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(sed -n 's/^ARG LL_REPO=//p' "$HERE/Containerfile")
COMMIT=$(sed -n 's/^ARG LL_COMMIT=//p' "$HERE/Containerfile")
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
git clone -q --filter=blob:none "$REPO" "$W/ll" && git -C "$W/ll" checkout -q "$COMMIT"
for p in "$HERE"/patches/*.patch; do git -C "$W/ll" apply "$p"; done

run() {   # run local|lan -> the rules inetd.sh writes
    T=$W/$1; mkdir -p "$T/conf" "$T/run" "$T/karotz/etc" "$T/karotz/scripts" "$T/etc" "$T/bin"
    printf 'I:index.html\nD:*\n' > "$T/karotz/etc/httpd.conf"
    printf '#!/bin/sh\nexit 0\n' > "$T/karotz/scripts/web-files.sh"; chmod +x "$T/karotz/scripts/web-files.sh"
    printf '#!/bin/sh\nexit 0\n' > "$T/bin/chgrp"; chmod +x "$T/bin/chgrp"
    printf '#!/bin/sh\nexit 0\n' > "$T/bin/logger"; chmod +x "$T/bin/logger"
    # a SHA-512 crypt hash's shape (16-char salt, 86-char hash; inetd.sh refuses
    # anything over 120 characters): the login is on in both cases
    echo '$6$abcdefghijklmnop$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' > "$T/conf/web_password.hash"
    [ "$1" = local ] && : > "$T/conf/web_local_only"
    sed -e "s#/usr/etc/conf#$T/conf#g" -e "s#/var/run#$T/run#g" -e "s#/karotz/#$T/karotz/#g" \
        -e "s#/etc/inetd.conf#$T/etc/inetd.conf#g" -e 's#^exec /sbin/inetd .*#exit 0#' \
        "$W/ll/image/core/rootfs/karotz/scripts/inetd.sh" > "$T/inetd.sh"
    # one service, or inetd.sh waits for ever instead of starting inetd
    echo "80 stream tcp nowait.600 web /bin/busybox httpd" > "$T/etc/inetd.conf"
    PATH="$T/bin:$PATH" sh "$T/inetd.sh"
    cat "$T/run/httpd.conf"
}
local=$(run local); lan=$(run lan)
[ -n "${TRACE:-}" ] && printf "local:\n%s\nlan:\n%s\n" "$local" "$lan"
fail() { echo "FAIL: $*"; exit 1; }
echo "$local" | grep -qx 'A:127.0.0.1' || fail "local: the rabbit itself is not allowed"
echo "$local" | grep -q 'A:192.168\|A:10\.\|A:172\.\|A:169' && fail "local: the network is allowed"
echo "$local" | grep -q '^/:admin:' || fail "local: the login is gone"
echo "$lan" | grep -qx 'A:192.168.0.0/16' || fail "lan: the local network is not allowed"
echo "$lan" | grep -q '^/:admin:' || fail "lan: the login is gone"
grep -q 'web_local_only' "$W/ll/image/usbkey/applysettings.sh" || fail "applysettings.sh writes no web_local_only"
echo "PASS: drawn password -> $(echo "$local" | grep -c '^A:') allow rule (127.0.0.1); owner's password -> $(echo "$lan" | grep -c '^A:') (the local network); the login kept in both"
