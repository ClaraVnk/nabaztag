#!/bin/bash
# The rabbit: OpenKarotz's web server, then the client through the very
# runner a Karotz uses (karotz/run.sh), the binary under qemu-arm.
set -u
: > /proof/hw.log
mkdir -p /proof/state
httpd -p 127.0.0.1:80 -h /www
cp /proof/terrier-karotz /proof/state/terrier-karotz
cat > /proof/state/settings <<EOS
SERVER=${TERRIER:-terrier}
EXTRA="--http-port ${TERRIER_HTTP:-8080} --mac ${KAROTZ_MAC:-0019dbdeadbe} --ok http://127.0.0.1 --dbus-monitor /usr/bin/dbus-monitor"
EOS
export DIR=/proof/state PREFIX="qemu-arm -cpu arm926 -r 2.6.28"
exec sh /proof/run.sh
