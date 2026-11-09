# The OKI ML67Q4051 internal-flash sequence, in TCL, for stock OpenOCD.
#
# Stock OpenOCD has no driver for this flash, and RedoXyde's patched OpenOCD
# 0.8.0 -- which does -- cannot drive a Pi's GPIO, because it predates
# linuxgpiod and modern kernels dropped the sysfs GPIO interface it used. So
# the sequence is reimplemented here on top of plain `mwb`/`mdb`, and the
# authority for it is the firmware's own writer: write_uc_flash_sec() in
# nabgcc src/utils/mem.c, which is what the rabbit runs when it flashes
# itself. Same unlock addresses, same commands, same completion test.
#
# This file was lost once already, with the SD card of the flash station it
# lived on. It is versioned now.
#
#   openocd -f nabaztag-pi.cfg -f oki.tcl -c "oki_id; shutdown"
#   openocd -f nabaztag-pi.cfg -f oki.tcl -c "oki_dump /tmp/flash.bin; shutdown"
#   openocd -f nabaztag-pi.cfg -f oki.tcl -f /tmp/fwprog.tcl -c shutdown
#
# 128 KB of flash at 0x08000000, 32 sectors of 4096 bytes. Sector 31
# (0x1F000) holds the rabbit's configuration -- Wi-Fi, server, MAC-derived
# identity -- so erasing 0..30, the firmware's 124 KB, keeps a configured
# rabbit configured (sector 30 was spared too in June 2026; images have
# outgrown that since). Erase 31 and the rabbit comes up as
# it left the factory.

set FLASH_BASE  0x08000000
set FLASH_SIZE  0x20000
set SECTOR_SIZE 0x1000
set CONF_SECTOR 31

set FLACON      0xB7000100  ;# flash control: bit0 FLWE, bit1 FWEND, bit2/3 BUSY
set TBGCON      0xB7E00004  ;# time base control: bit7 halts the watchdog clock

set UNLOCK1     0x08015554
set UNLOCK2     0x0800AAA8

# The watchdog keeps counting while the core is halted, and a reset in the
# middle of a sector write leaves that sector half programmed. The firmware
# stops the time base for exactly this reason; so do we, once, up front.
proc oki_wd_off {} {
    global TBGCON
    oki_wb $TBGCON [expr {[oki_rb $TBGCON] | 0x80}]
}

# read_memory and write_memory rather than mdb and mwb: they take and return
# values instead of printing them. That matters here -- a full image is some
# 27000 words, each polled until the controller says it is done, and mdb
# would print a line every single time. The log would cost more than the
# flashing.
proc oki_rb {addr} {
    return [lindex [read_memory $addr 8 1] 0]
}

proc oki_wb {addr v} {
    write_memory $addr 8 [list $v]
}

# Programming is prohibited by default and permitted one command at a time,
# the way the firmware does it: FLACON=0x03 right before each unlock.
proc oki_permit {} {
    global FLACON
    oki_wb $FLACON 0x03
}

# A command is finished when BUSY is clear and FWEND is set. The firmware
# spins on exactly this mask and has no timeout; we keep one, because a
# worn-out or protected sector otherwise hangs the session with no message,
# and silence is what cost us three days in November 2026.
proc oki_wait {{tries 2000}} {
    global FLACON
    for {set i 0} {$i < $tries} {incr i} {
        if {([oki_rb $FLACON] & 0x0E) == 0x02} { return }
    }
    error "FLACON never reported the command complete (0x[format %02x [oki_rb $FLACON]])"
}

proc oki_unlock {cmd} {
    global UNLOCK1 UNLOCK2
    oki_wb $UNLOCK1 0xAA
    oki_wb $UNLOCK2 0x55
    oki_wb $UNLOCK1 $cmd
}

# Put the part back in plain read mode. A session that was interrupted
# mid-command -- a killed OpenOCD, a cable pulled, a timeout -- leaves the
# flash in autoselect or program mode, and then every read is nonsense: the
# ID came back 0x18/0x18 once, which looks exactly like bad wiring and is
# not. 0xF0 costs one write and removes a whole class of false alarms.
proc oki_reset_mode {} {
    global FLASH_BASE
    # The watchdog first, always. A rabbit whose sector 0 is erased executes
    # 0xFF, faults, and the watchdog resets it over and over -- so every read
    # lands in a different reset and comes back as garbage. That is what made
    # the ID read 0x18/0x18. Halting the time base makes the part hold still.
    oki_wd_off
    oki_permit
    oki_wb $FLASH_BASE 0xF0
    oki_reset_status
}

# Autoselect: manufacturer 0x98 (OKI), device 0xCD on this part. Cheap proof
# that the five jumpers and the unlock addresses are both right, before
# anything is erased.
proc oki_id {} {
    global FLASH_BASE
    oki_reset_mode
    oki_unlock 0x90
    # Autoselect answers on 32-bit boundaries on this part: the
    # manufacturer repeats over base+0..3 and the device over base+4..7.
    set mfr [oki_rb $FLASH_BASE]
    set dev [oki_rb [expr {$FLASH_BASE + 4}]]
    oki_wb $FLASH_BASE 0xF0   ;# leave autoselect
    echo [format "flash id: manufacturer 0x%02x, device 0x%02x" $mfr $dev]
    if {$mfr != 0x98 || $dev != 0xCD} {
        echo "  (expected 0x98 / 0xcd -- check the wiring before writing anything)"
    }
    return [list $mfr $dev]
}

# The identifier is NOT a usable gate on this part, and believing it was cost
# an evening. The autoselect command never engages here: the reads come back
# as the array's own contents. 0x18/0x18 is simply the first byte of the two
# words at the reset vector (0xe59ff018 twice), and 0xff/0xff is what the same
# read gives once sector 0 is blank. Neither says anything about the wiring or
# about a mode left over from an earlier session, which is what README.md used
# to claim and what a gate built on it then refused every legitimate write
# for. oki_id stays, as information and nothing more.
#
# What does have a basis: erasing sector 0 while the core is running pulls the
# code out from under it mid-instruction, which is what actually wrecked the
# write of 2026-11-07 -- the chip left debug state and OpenOCD lost it after
# three sectors. So the gate asks the question that matters.
proc oki_require_halted {} {
    set t [target current]
    set s [$t curstate]
    if {$s ne "halted"} {
        error "the core is $s, not halted. Erasing under a running core takes\
               its instructions away mid-flight; halt it first (and expect the\
               first halt on a live rabbit to time out -- run it again)."
    }
}

# Sector erase is a six-write sequence and the last two writes are NOT a
# third unlock: AA, 55, 80 to the unlock addresses, then AA, 55 again, then
# 0x30 to the sector itself. Reusing oki_unlock for that second half slips an
# extra 0xAA into the command and the part simply never starts -- FLACON sits
# at 0x01 with FWEND never set, which is exactly how the first run of this
# script failed. Nothing is erased in that case, so it costs a retry, not a
# rabbit.
proc oki_erase_sector {addr} {
    global UNLOCK1 UNLOCK2
    oki_permit
    oki_unlock 0x80
    oki_wb $UNLOCK1 0xAA
    oki_wb $UNLOCK2 0x55
    oki_wb $addr 0x30
    oki_wait
}

# An erased sector reads 0xFFFFFFFF everywhere. One batched read per sector,
# so this is nearly free, and it is the difference between knowing the erase
# worked and assuming it. The firmware assumes.
proc oki_verify_erased {addr} {
    global SECTOR_SIZE
    set words [read_memory $addr 32 [expr {$SECTOR_SIZE / 4}]]
    set i 0
    foreach w $words {
        if {$w != 0xFFFFFFFF} {
            error [format "sector at 0x%08x did not erase: word %d reads 0x%08x" \
                   $addr $i $w]
        }
        incr i
    }
}

# Four bytes per command, which is what the part accepts and why a full
# image takes ten minutes over bit-banged JTAG.
proc oki_program_word {addr b0 b1 b2 b3} {
    oki_permit
    oki_unlock 0xA0
    # The part wants four separate byte writes; one write_memory is one JTAG
    # round-trip instead of four.
    write_memory $addr 8 [list $b0 $b1 $b2 $b3]
    oki_wait
}

# Erase the firmware sectors, 0..30, and leave the configuration (31) alone,
# so a configured rabbit stays configured.
proc oki_erase_firmware {{last 30}} {
    global FLASH_BASE SECTOR_SIZE
    oki_wd_off
    oki_reset_mode
    for {set s 0} {$s <= $last} {incr s} {
        set addr [expr {$FLASH_BASE + $s * $SECTOR_SIZE}]
        echo [format "erasing sector %2d at 0x%08x" $s $addr]
        oki_erase_sector $addr
        oki_verify_erased $addr
    }
}

# Read the whole flash back to a file. This is the only thing in this project
# that can answer "what is this rabbit actually running", which three
# successful-looking flashes in a row could not.
proc oki_dump {path {base 0} {len 0}} {
    global FLASH_BASE FLASH_SIZE
    if {$base == 0} { set base $FLASH_BASE }
    if {$len == 0}  { set len  $FLASH_SIZE }
    dump_image $path $base $len
    echo "dumped [format 0x%x $len] bytes from [format 0x%08x $base] to $path"
}

proc oki_reset_status {} {
    global FLACON
    oki_wb $FLACON 0x02
}
