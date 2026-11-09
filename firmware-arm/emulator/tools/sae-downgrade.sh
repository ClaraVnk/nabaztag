#!/usr/bin/env bash
# A network that did SAE once is not joined with PSK after a power cycle.
#
#   tools/sae-downgrade.sh        (from the emulator directory, after make)
#
# scenarios/wpa3-downgrade.txt shows the refusal within one boot. The record
# that makes it hold has to be in the configuration sector or a power cycle
# forgets it -- and a rabbit is power-cycled at every reboot worth the name.
# So: boot 1 on a WPA3 AP writes the record (--eeprom-persist keeps the
# page); boot 2 from the same page finds a WPA2-only AP with the same SSID
# and must stay off it; boot 3 from a page written for another network --
# the owner reconfigured -- is not locked by boot 1's record. A build
# without sae_required joins the WPA2 AP in boot 2, and this fails.
set -u
cd "$(dirname "$0")/.."
mkdir -p obj/sae
fail=0

python3 mkconf.py --ssid Terrier --psk nabaztag --security wpa2 --rng-seed fixed -o obj/sae/rabbit.eeprom > /dev/null
python3 mkconf.py --ssid Terrier --psk otherpassword --security wpa2 --rng-seed fixed -o obj/sae/other.eeprom > /dev/null

boot() {  # name eeprom scenario-body...
  local name=$1 ee=$2; shift 2
  { echo "conf $ee"; printf '%s\n' "$@"; } > obj/sae/$name.txt
  ./rabbit obj/sae/$name.txt --eeprom-persist > obj/sae/$name.log 2>&1
  local rc=$?
  printf '  %-18s exit %d  %s\n' "$name" "$rc" "$(sed -n 's/^final state *//p' obj/sae/$name.log)"
  return $rc
}

echo "== SAE once, then a WPA2-only AP with the same SSID, across a power cycle"
boot sae-first obj/sae/rabbit.eeprom \
  "ap 02:00:5e:10:00:01 Terrier 6 wpa3-sae -52" "psk nabaztag" "tick 50" "stop 30000" \
  "at 20000 expect RUN" || fail=1
grep -q "requires SAE from now on (stored)" obj/sae/sae-first.log \
  && echo "  ok    boot 1 stored the record" \
  || { echo "  FAIL  boot 1 stored no record (is sae_required in the step list?)"; fail=1; }

boot psk-after obj/sae/rabbit.eeprom \
  "ap 02:00:5e:10:00:02 Terrier 6 wpa2-ccmp -52" "psk nabaztag" "tick 50" "stop 60000" \
  "at 30000 expect-not RUN" "at 59000 expect-not RUN" "at 59000 expect-sae-refused 1" \
  && echo "  ok    boot 2 refused PSK on the network that did SAE" \
  || { echo "  FAIL  boot 2 joined the WPA2-only AP: the downgrade went through"; fail=1; }

boot reconfigured obj/sae/other.eeprom \
  "ap 02:00:5e:10:00:02 Terrier 6 wpa2-ccmp -52" "psk otherpassword" "tick 50" "stop 30000" \
  "at 20000 expect RUN" \
  && echo "  ok    boot 3, another passphrase: not locked by boot 1" \
  || { echo "  FAIL  boot 3 was locked although the configuration changed"; fail=1; }

echo "transcripts in obj/sae/"
exit $fail
