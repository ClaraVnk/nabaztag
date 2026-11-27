#!/bin/sh
# drivers-check.sh: every driver of the Karotz under Linux 6.1 made to answer, on the rabbit itself.
# Each check does something real (moves the ears, lights the LED, speaks, takes a picture, reads the
# flash) and looks at what the driver did: its interrupt counter, its device, its sysfs binding.
# The actions go through LapinLibre's local API, as the Le Terrier client does, so the daemons
# between the API and the drivers are checked on the way.
#
#   ssh root@<karotz> 'BUTTON_WAIT=20 sh -s' < karotz/kernel/drivers-check.sh
#
# BUTTON_WAIT: seconds to wait for a press on the head (0, the default, skips the button). The
# picture is taken, measured and deleted on the rabbit; nothing leaves it. The microphone is only
# checked as present: no sound is recorded. The watchdog is never opened (opening it arms it).
# Busybox sh. Last line: "drivers: N ok, M failed".
BUTTON_WAIT=${BUTTON_WAIT:-0}
ok=0 bad=0
pass() { ok=$((ok + 1)); printf 'ok    %-10s %s\n' "$1" "$2"; }
fail() { bad=$((bad + 1)); printf 'FAIL  %-10s %s\n' "$1" "$2"; }
# irq NAME: the interrupt count of the lines whose name holds NAME, summed (the UARTs and DMA
# channels each have several lines, one of them busy).
irq() { grep -- "$1" /proc/interrupts | awk '{s += $2} END {print s + 0}'; }
bound() { [ -e "/sys/bus/platform/devices/$1/driver" ]; }
# rose NAME BEFORE CHECK WHAT: pass when the interrupt counter moved.
rose() {
    _n=$(irq "$1")
    if [ "${_n:-0}" -gt "${2:-0}" ]; then pass "$3" "$4 (irq $2 -> $_n)"; else fail "$3" "$4: no interrupt (irq ${2:-?})"; fi
}

# The local API, with the client's password (its settings; the client masks it in its argv).
B=$(grep -o 'http://admin:[^ "]*' /usr/etc/conf/terrier/settings 2>/dev/null)
CRED=/tmp/.drivers-check-cred
trap 'rm -f "$CRED"' EXIT INT TERM
if [ -n "$B" ]; then
    U=${B#http://}
    (umask 077; printf 'user = "%s"\n' "${U%@*}" > "$CRED")
    B="http://${U#*@}"
else
    B=http://127.0.0.1
    : > "$CRED"
fi
api() { curl -s -m 20 -K "$CRED" "$B/cgi-bin/$1"; }
api_ok() { api "$1" | grep -q '"return":"0"'; }

echo "kernel $(uname -r), $(cat /usr/etc/conf/lapinlibre_build_id 2>/dev/null), up $(cut -d' ' -f1 /proc/uptime) s"
api_ok status || fail api "the local API doesn't answer"
api status | grep -q '"sleep":"1"' && { api_ok 'wakeup?silent=1'; sleep 3; }

# NAND and YAFFS: the bootloader's first page read, a file written, read back and removed on /usr.
if [ "$(grep -c '^mtd' /proc/mtd)" = 8 ] && [ "$(dd if=/dev/mtd0ro bs=2048 count=1 2>/dev/null | wc -c)" = 2048 ]; then
    pass nand "8 partitions, mtd0 read"
else fail nand "partitions or read"; fi
f=/usr/etc/conf/.drivers-check
if grep -q ' /usr yaffs.* rw' /proc/mounts && echo "$$" > $f && sync && [ "$(cat $f)" = "$$" ] && rm -f $f; then
    pass yaffs "/usr rw: written, read back, removed"
else rm -f $f; fail yaffs "/usr not rw or the round trip failed"; fi

# USB host and Wi-Fi: the dongle on the hub, the link, the gateway answering.
n=$(irq ohci)
p=$(cat /sys/bus/usb/devices/1-1.3/product 2>/dev/null)
gw=$(ip route | awk '/^default/ {print $3; exit}')
if [ -n "$p" ] && [ -n "$gw" ] && ping -c 3 -W 2 "$gw" >/dev/null 2>&1; then
    rose ohci "$n" usb "$p on the hub, gateway answers"
else fail usb "dongle ($p) or gateway"; fi
q=$(awk '/wlan0/ {print int($3)}' /proc/net/wireless)
if [ "$(cat /sys/class/net/wlan0/operstate)" = up ] && [ -n "$q" ]; then
    pass wifi "rt73usb up, link $q/70"
else fail wifi "wlan0 down or no link"; fi

# LED (SPI): a colour, then the API's own LED state back.
n=$(irq s3c2410-spi)
api_ok 'leds?color=FF00FF&nomemory=1' || fail led "the API refused the colour"
sleep 1
rose s3c2410-spi "$n" led "SPI written"
set -- $(cat /var/run/lapinlibre-web/led 2>/dev/null)
api_ok "leds?color=${1:-00FF00}&pulse=${2:-0}&color2=${3:-000000}&speed=${4:-700}&nomemory=1" >/dev/null

# Ears: out and back, each ear's encoder interrupt counted.
l=$(irq 'Left ear') r=$(irq 'Right Ear')
api_ok 'ears?left=6&right=6&noreset=1' && sleep 4 && api_ok 'ears?left=0&right=0&noreset=1' && sleep 4 \
    || fail ears "the API refused the move"
rose 'Left ear' "$l" ear-left "moved and back"
rose 'Right Ear' "$r" ear-right "moved and back"

# Sound (I2S, UDA1341, DMA): a sound file played, the playback stream seen running. Not the
# speech: voice-daemon speaks only through the speech server of voice.conf, Mindscape's, gone
# (Gengar 2026-11-27: the stream opened, never ran, and the API still said "played").
n=$(irq s3c2410-dma)
api 'sound?id=acknowledge' >/dev/null &
# The stream may play for a second only: looked at five times a second, for 15 s.
run="" i=0
while [ $i -lt 75 ]; do
    grep -qE 'RUNNING|DRAINING' /proc/asound/card0/pcm0p/sub0/status 2>/dev/null && { run=1; break; }
    usleep 200000
    i=$((i + 1))
done
wait
[ -n "$run" ] && rose s3c2410-dma "$n" sound "UDA1341 playing over DMA" || fail sound "the playback never ran"
[ -e /proc/asound/card0/pcm0c ] && pass mic "capture device present (nothing recorded)" || fail mic "no capture device"

# Camera: a picture taken, its JPEG measured, then deleted.
api_ok snapshot
j=$(ls -t /var/run/lapinlibre-web/snapshots/*.jpg 2>/dev/null | head -1)
s=$( [ -n "$j" ] && wc -c < "$j" )
m=$( [ -n "$j" ] && head -c 2 "$j" | od -An -tx1 | tr -d ' \n')
api_ok clear_snapshots >/dev/null
if [ "$m" = ffd8 ] && [ "${s:-0}" -gt 1000 ]; then pass camera "/dev/camera, JPEG $s bytes (deleted)"
else fail camera "no picture (${s:-0} bytes)"; fi

# RFID (I2C) and the microcontroller's UART: both polled all the time by their daemons.
n=$(irq s3c2440-i2c) u=$(irq s3c2440-uart)
sleep 2
rose s3c2440-i2c "$n" rfid "reader polled over I2C"
rose s3c2440-uart "$u" uart "serial-daemon talking"

# Head button: a press waited for, only when asked.
if [ "$BUTTON_WAIT" -gt 0 ]; then
    n=$(irq rabbit_button)
    echo "press the rabbit's head within $BUTTON_WAIT s"
    i=0
    while [ $i -lt "$BUTTON_WAIT" ] && [ "$(irq rabbit_button)" = "$n" ]; do sleep 1; i=$((i + 1)); done
    rose rabbit_button "$n" button "head pressed"
else
    [ -c /dev/rabbit_button ] && pass button "/dev/rabbit_button present (no press asked)" || fail button "no device"
fi

# The rest: bound to their devices.
[ -c /dev/rabbit_power ] && pidof power >/dev/null && pass power "/dev/rabbit_power, power daemon up" || fail power "device or daemon"
bound s3c2410-wdt && pass watchdog "bound (not opened)" || fail watchdog "not bound"
if bound s3c2410-rtc && hwclock -r >/dev/null 2>&1; then pass rtc "bound, $(hwclock -r 2>&1 | head -1)"
else fail rtc "not bound to s3c-rtc"; fi
bound s3c2440-usbgadget && pass gadget "bound (g_serial)" || fail gadget "not bound"
echo "drivers: $ok ok, $bad failed"
