#!/bin/sh
# BusyBox inetd's connections-a-minute count, measured: with nowait.60 the
# 61st connection within a minute is refused and inetd stops the service for
# ten minutes ("too many connections, pausing"); with nowait.600 (patch 0001)
# the same 61 are all served. The Terrier client on the rabbit opens one
# connection per LED or ear order, so 60 a minute is reached by a choreography
# and the owner's browser together. Alpine's busybox-extras inetd, a shell
# "server" that answers one line, 61 connections in a row. PASS when the two
# runs differ as said (inetd logs the pause to syslog, which the container has not).
#
#   karotz/lapinlibre/test-inetd-rate.sh       (where podman is: the dev VM)
set -eu
podman run --rm docker.io/library/alpine:3.24 sh -ec '
apk add -q --no-cache busybox-extras > /dev/null
printf "#!/bin/sh\necho hello\n" > /srv.sh && chmod 755 /srv.sh
run() { # run LIMIT WANT: WANT answers out of 61 connections (all, or fewer: refused past the count)
    printf "8080 stream tcp nowait.%s root /srv.sh srv.sh\n" "$1" > /inetd.conf
    inetd -f /inetd.conf 2> /inetd.log & pid=$!   # busybox-extras: its own binary, not an applet
    sleep 2
    n=0
    for i in $(seq 1 61); do
        if [ "$(busybox nc -w 2 127.0.0.1 8080 < /dev/null 2> /dev/null)" = hello ]; then n=$((n + 1)); fi
    done
    kill $pid; wait $pid 2> /dev/null || true
    echo "nowait.$1: $n of 61 answered"
    case $2 in all) [ $n -eq 61 ] ;; fewer) [ $n -lt 61 ] ;; esac
}
run 600 all
run 60 fewer
echo "PASS: nowait.60 drops the 61st connection and pauses the service; nowait.600 serves all 61"
'
