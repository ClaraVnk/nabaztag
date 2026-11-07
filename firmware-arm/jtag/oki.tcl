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
# identity -- and sector 30 was spared with it in June 2026, so erasing 0..29
# keeps a configured rabbit configured. Erase 31 and the rabbit comes up as
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
    mwb $TBGCON [expr {[oki_rb $TBGCON] | 0x80}]
}

proc oki_rb {addr} {
    # mdb prints "0x080000000: 12"; take the last field.
    set line [mdb $addr]
    return [expr {"0x[lindex [split [string trim $line]] end]"}]
}

# Programming is prohibited by default and permitted one command at a time,
# the way the firmware does it: FLACON=0x03 right before each unlock.
proc oki_permit {} {
    global FLACON
    mwb $FLACON 0x03
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
    mwb $UNLOCK1 0xAA
    mwb $UNLOCK2 0x55
    mwb $UNLOCK1 $cmd
}

# Autoselect: manufacturer 0x98 (OKI), device 0xCD on this part. Cheap proof
# that the five jumpers and the unlock addresses are both right, before
# anything is erased.
proc oki_id {} {
    global FLASH_BASE
    oki_permit
    oki_unlock 0x90
    set mfr [oki_rb $FLASH_BASE]
    set dev [oki_rb [expr {$FLASH_BASE + 1}]]
    mwb $FLASH_BASE 0xF0   ;# leave autoselect
    echo [format "flash id: manufacturer 0x%02x, device 0x%02x" $mfr $dev]
    if {$mfr != 0x98} {
        echo "  (expected 0x98 -- check the wiring before writing anything)"
    }
}

proc oki_erase_sector {addr} {
    oki_permit
    oki_unlock 0x80
    oki_unlock 0xAA
    mwb $addr 0x30
    oki_wait
}

# Four bytes per command, which is what the part accepts and why a full
# image takes ten minutes over bit-banged JTAG.
proc oki_program_word {addr b0 b1 b2 b3} {
    oki_permit
    oki_unlock 0xA0
    mwb $addr            $b0
    mwb [expr {$addr+1}] $b1
    mwb [expr {$addr+2}] $b2
    mwb [expr {$addr+3}] $b3
    oki_wait
}

# Erase the firmware sectors and leave the configuration alone. Sector 30 is
# spared together with 31: the June 2026 recovery did it that way, the
# readback was byte-exact, and a configured rabbit stayed configured.
proc oki_erase_firmware {{last 29}} {
    global FLASH_BASE SECTOR_SIZE
    oki_wd_off
    for {set s 0} {$s <= $last} {incr s} {
        set addr [expr {$FLASH_BASE + $s * $SECTOR_SIZE}]
        echo [format "erasing sector %2d at 0x%08x" $s $addr]
        oki_erase_sector $addr
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
    mwb $FLACON 0x02
}
