#!/bin/sh
# A Nabaztag v1's serial log (J3, wiring.svg), on the Pi: 9600 baud, 8N1, every line timed.
# The serial port without its console first: raspi-config > Interface > Serial (login shell No,
# hardware Yes). Ctrl-C to stop; the log is also kept in ~/v1-serial.log.
DEV=${1:-/dev/serial0}
stty -F "$DEV" 9600 cs8 -cstopb -parenb raw -echo || exit 1
cat "$DEV" | while IFS= read -r line; do
    printf '%s %s\n' "$(date '+%H:%M:%S')" "$line"
done | tee -a "$HOME/v1-serial.log"
