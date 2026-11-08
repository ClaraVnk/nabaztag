#!/usr/bin/env bash
# Run every whole-rabbit scenario under ./rabbit-vm, pass or fail.
#
#   tools/vm-check.sh [--warren <host>] [--vm ./rabbit-vm] [--boot-vm <vm>]
#
# --boot-vm names a rabbit-vm linked with the bootloader bytecode
# (tools/bootbc.py lifts it out of a built Nab.elf); the scenarios whose
# header says `bytecode: bootloader` run under it and are SKIPped without
# it. Everything else runs under --vm, which is linked with a warren's
# .hybrid -- the two bytecodes drive different code, and a WPA3 scenario
# under the wrong one fails for a reason that is not the firmware's.
#
# `make check` covers scenarios/*.txt with ./rabbit; nothing ran scenarios/vm/
# as a set, so a bytecode or VM change had no before/after. Each scenario
# boots from the configuration page its own header asks for: a static
# address, because DHCP under ./rabbit-vm is not there yet (README), and the
# passphrase the WPA2/WPA3 ones derive their PMK from. Two of them say
# `realtime` and take as long as they say.
set -u
# The transcripts carry the rabbit's raw frame dumps; BSD sed calls those an
# illegal byte sequence unless told to stop interpreting them.
export LC_ALL=C
WARREN=127.0.0.1
VM=./rabbit-vm
BOOT_VM=
while [ $# -gt 0 ]; do
  case "$1" in
    --warren) WARREN="$2"; shift 2 ;;
    --vm)     VM="$2"; shift 2 ;;
    --boot-vm) BOOT_VM="$2"; shift 2 ;;
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
  vm=$VM
  if grep -q '^# bytecode: bootloader' "$s"; then
    if [ -z "$BOOT_VM" ]; then
      printf 'SKIP  %-34s needs --boot-vm (bootloader bytecode)\n' "$s"
      continue
    fi
    vm=$BOOT_VM
  fi
  if "$vm" "$s" --eeprom "$ee" --warren "$WARREN" > "$log" 2>&1; then
    printf 'PASS  %-34s %s\n' "$s" "$(sed -n 's/^final state *//p' "$log")"
  else
    printf 'FAIL  %-34s %s\n' "$s" "$(sed -n 's/^final state *//p' "$log")"
    grep -a 'FAIL\|expected state' "$log" | head -3
    fail=$((fail + 1))
  fi
done
echo; echo "transcripts in obj/logs/vm-*.log"
exit $fail
