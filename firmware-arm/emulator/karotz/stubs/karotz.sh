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
# --os-install: Terrier OS's install stands as the proof's script here (the
# Terrier's karotz/proof/scenario.py writes it); the real one runs on a
# simulated flash in ../ota (OTA_MODE=package).
EXTRA="--http-port ${TERRIER_HTTP:-8080} --mac ${KAROTZ_MAC:-0019dbdeadbe} --ok http://127.0.0.1 --dbus-monitor /usr/bin/dbus-monitor --os-install /proof/os-install.sh --os-keep /proof"
EOS
export DIR=/proof/state PREFIX="qemu-arm -cpu arm926 -r 2.6.28"
exec sh /proof/run.sh
