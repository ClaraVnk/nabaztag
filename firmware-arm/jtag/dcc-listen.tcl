# The telemetry line, read straight off the EmbeddedICE comms channel.
#
# OpenOCD's own reader (`target_request debugmsgs`) took every word as a
# request; a word of all ones -- what the bus returns while the rabbit
# resets -- is "unknown target request: ff", after which it read nothing
# more, while the rabbit went on running. This reads the channel itself:
# scan chain 2 (the EmbeddedICE, reachable while the core runs), comms
# control at 4, comms data at 5. A word whose low byte is not
# TARGET_REQ_DEBUGCHAR (2) is counted and skipped, and the chain is selected
# again, in case the reset took the TAP with it. The core is never halted.
#
#   openocd -f nabaztag-pi-listen.cfg -f dcc-listen.tcl      (listen.sh)

set dcc_tap ml67q4051.cpu

proc dcc_select {} {
    global dcc_tap
    irscan $dcc_tap 0x2          ;# SCAN_N
    drscan $dcc_tap 4 2          ;# chain 2: EmbeddedICE
    irscan $dcc_tap 0xc          ;# INTEST
}

# An EmbeddedICE read: the address goes in one scan, the value comes out
# of the next.
proc dcc_ice {addr} {
    global dcc_tap
    drscan $dcc_tap 32 0 5 $addr 1 0
    return [expr {"0x[lindex [drscan $dcc_tap 32 0 5 $addr 1 0] 0]"}]
}

poll off
dcc_select
set line ""
set junk 0
while {1} {
    if {[dcc_ice 4] & 2} {
        set w [dcc_ice 5]
        if {($w & 0xff) == 2} {
            set ch [expr {($w >> 16) & 0xff}]
            if {$ch == 10} {
                echo $line
                set line ""
            } else {
                append line [format %c $ch]
            }
        } else {
            incr junk
            echo "(skipped a word that is not telemetry: [format 0x%08x $w], $junk so far)"
            set line ""
            sleep 200
            dcc_select
        }
    } else {
        sleep 20
    }
}
