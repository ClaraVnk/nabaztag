#!/usr/bin/env bash
# Two boots on identical timers must not draw the same secrets.
#
#   tools/rng-boots.sh            (from the emulator directory, after make)
#
# The virtual clock is the same in every run, so the old RNG -- SHA-256 of
# the timer, the RSSI average and a timer-seeded xorshift -- drew the same
# SAE scalar and the same adoption key on every boot of the same scenario.
# The pool (crypto/nab_rng.c) is fed by things that differ between boots on
# the hardware: the RSSI of every frame, the power-up contents of the
# retained RAM, and the seed the first boot stores in the config sector.
# The emulator makes each of those an explicit knob and this checks, with a
# control, that each one alone changes the draw and that nothing else does.
#
# What is compared: the SAE scalar the station sends (the AP logs it) and
# the eight bytes the pool would hand kxBegin next (the run summary).
set -u
cd "$(dirname "$0")/.."
mkdir -p obj/rng
SC=scenarios/wpa3-sae.txt
fail=0

boot() {  # name eeprom-copy sram-seed rssi-noise
  local name=$1 ee=$2 sram=$3 noise=$4
  sed "s|^conf .*|conf $ee|" $SC > obj/rng/$name.txt
  ./rabbit obj/rng/$name.txt --eeprom-persist --sram-seed $sram --rssi-noise $noise > obj/rng/$name.log 2>&1
  local rc=$?
  local scalar key
  scalar=$(sed -n 's/.*SAE station scalar \([0-9a-f]*\).*/\1/p' obj/rng/$name.log | head -1)
  key=$(sed -n 's/.*next key bytes *\([0-9a-f]*\).*/\1/p' obj/rng/$name.log | head -1)
  printf '  %-26s exit %d  scalar %s  key %s\n' "$name" "$rc" "${scalar:-none}" "${key:-none}"
  if [ $rc -ne 0 ] || [ -z "$scalar" ] || [ -z "$key" ]; then fail=1; fi
  eval "S_$name=$scalar; K_$name=$key"
}

same() {   # a b what
  local a=$1 b=$2 what=$3 sa sb ka kb
  eval "sa=\$S_$a; sb=\$S_$b; ka=\$K_$a; kb=\$K_$b"
  if [ "$sa" = "$sb" ] && [ "$ka" = "$kb" ]; then echo "  ok    $what: identical"
  else echo "  FAIL  $what: expected identical draws"; fail=1; fi
}
differ() {
  local a=$1 b=$2 what=$3 sa sb ka kb
  eval "sa=\$S_$a; sb=\$S_$b; ka=\$K_$a; kb=\$K_$b"
  if [ "$sa" != "$sb" ] && [ "$ka" != "$kb" ]; then echo "  ok    $what: different scalar and key"
  else echo "  FAIL  $what: the same secret twice"; fail=1; fi
}

echo "== two boots on identical timers (scenario $SC)"
for e in a c d e; do python3 mkconf.py --ssid Terrier --psk nabaztag --security wpa2 -o obj/rng/$e.eeprom > /dev/null; done

boot control1   obj/rng/d.eeprom 0 0     # no seed, zero RAM, no noise
boot control2   obj/rng/e.eeprom 0 0     # the same again: the harness is deterministic
same control1 control2 "same inputs, same boot"

boot first      obj/rng/a.eeprom 0 1     # first boot: RSSI noise, writes the seed
grep -q "seed stored in the configuration sector" obj/rng/first.log \
  && echo "  ok    the first ready boot stored a seed" \
  || { echo "  FAIL  no seed was stored on the first boot"; fail=1; }
boot second     obj/rng/a.eeprom 0 1     # second boot, same RAM, same noise: the stored seed
grep -q "rng: pool started, a seed" obj/rng/second.log \
  && echo "  ok    the second boot read the stored seed" \
  || { echo "  FAIL  the second boot found no seed"; fail=1; }
differ first second "stored seed alone"
differ control1 first "RSSI noise alone"

boot noise2     obj/rng/c.eeprom 0 2     # another link's noise
differ first noise2 "a different RSSI noise"

boot sram       obj/rng/c.eeprom 7 0     # fresh eeprom, retained RAM differs, no noise
differ control1 sram "retained RAM alone"

# Readiness: the gate held until the pool earned its credit, and the first
# commit went out only after that.
grep -q "the entropy pool is not ready, listening first" obj/rng/control1.log \
  && echo "  ok    the first commit waited for the pool" \
  || { echo "  FAIL  the first commit did not wait for the pool"; fail=1; }
grep -q "listening first" obj/rng/second.log \
  && { echo "  FAIL  the second boot waited although it had a seed"; fail=1; } \
  || echo "  ok    the second boot, seeded, did not wait"
grep -q "credit [0-9]*, ready" obj/rng/control1.log && echo "  ok    the pool reports ready at the end" || { echo "  FAIL  pool never ready"; fail=1; }

echo "transcripts in obj/rng/"
exit $fail
