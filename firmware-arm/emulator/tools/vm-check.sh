#!/usr/bin/env bash
# Run every whole-rabbit scenario under ./rabbit-vm, pass or fail.
#
#   tools/vm-check.sh [--warren <host>] [--vm ./rabbit-vm]
#
# `make check` covers scenarios/*.txt with ./rabbit; nothing ran scenarios/vm/
# as a set, so a bytecode or VM change had no before/after. Each scenario
# boots from the configuration page its own header asks for: a static
# address, because DHCP under ./rabbit-vm is not there yet (README), and the
# passphrase the WPA2/WPA3 ones derive their PMK from. Two of them say
# `realtime` and take as long as they say.
set -u
WARREN=127.0.0.1
VM=./rabbit-vm
while [ $# -gt 0 ]; do
  case "$1" in
    --warren) WARREN="$2"; shift 2 ;;
    --vm)     VM="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
cd "$(dirname "$0")/.."
mkdir -p obj/conf obj/logs
COMMON=(--server "$WARREN:8080/vl" --ip 10.0.0.42 --gateway 10.0.0.1 --dns 10.0.0.1)
python3 mkconf.py --ssid Terrier --security open "${COMMON[@]}" -o obj/conf/vm-open.eeprom >/dev/null
python3 mkconf.py --ssid Terrier --psk nabaztag --security wpa2 "${COMMON[@]}" -o obj/conf/vm-wpa2.eeprom >/dev/null

fail=0
for s in scenarios/vm/*.txt; do
  name=$(basename "$s" .txt)
  case "$name" in
    *wpa2*|*wpa3*) ee=obj/conf/vm-wpa2.eeprom ;;
    *)             ee=obj/conf/vm-open.eeprom ;;
  esac
  log=obj/logs/vm-$name.log
  if "$VM" "$s" --eeprom "$ee" --warren "$WARREN" > "$log" 2>&1; then
    printf 'PASS  %-34s %s\n' "$s" "$(sed -n 's/^final state *//p' "$log")"
  else
    printf 'FAIL  %-34s %s\n' "$s" "$(sed -n 's/^final state *//p' "$log")"
    grep -a 'FAIL\|expected state' "$log" | head -3
    fail=$((fail + 1))
  fi
done
echo; echo "transcripts in obj/logs/vm-*.log"
exit $fail
