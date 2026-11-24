# Violet's v1 wait program, reviewed (2026-11-24)

What a Nabaztag v1 runs between two messages is a program its server sends
at every boot: `Nabaztag_wait.vasm` (Sylvain Huet, Antoine Schmitt, Violet,
2005-2006; `firmware-arm/vendor/violet-platform/vasm/`), 33,747 bytes once
Violet's assembler (`Vasm.java`) has put it together, over plain HTTP. The
Terrier serves it as an option on the v1 pages, with its own patches
(`patches/*.patch`, applied in name order into `Nabaztag_wait_terrier.vasm`
when the `violet-vasm` image builds; Violet's file is never edited). The
Terrier's frame is 33,860 bytes, Violet's 33,747 (sha256
a486e196...).

This is a review of that program, of the server side that builds it, and of
the two other programs a v1 may receive (`Nabaztag_asleep_v1.0.vasm`,
`Nabaztag_msg_v0.9.vasm`). Every claim below was either read in the source
(label and line of `Nabaztag_wait.vasm`) or measured in the v1 emulator
(`firmware-arm/emulator/v1`, Violet's own firmware 2.0b and Lazare's 1.4b
interpreting the frame); what was not measured says so. The semantics of
the machine language are Violet's manual, *Langage machine de Nabaztag,
Architecture A* (December 2005): a step about every 50 ms, `WAIT n` holds
the program n steps, 16 8-bit registers, R15 the stack pointer in a 256-byte
RAM shared with the stack, 64 bytes of read-only "sources" the server
writes, interrupts 0 (press), 1 (release), 2 (three-state switch), 15
(timer).

## Findings, by value over cost

| # | Finding | Where | Cost | Status |
|---|---|---|---|---|
| 1 | A stalled ear hangs the acquisition turn for ever, motor on | `@motorturn` 1149-1238 | small | **fixed**, `p3-acquisition-timeout.patch`, proven |
| 2 | A click with no message program coming hangs the program for ever | `@clicwait` 459, `@sendwaitloop` 814-818 | small | **fixed**, `p4-click-timeout.patch`, proven |
| 3 | The homing turn at boot and the acquisition turn start both motors together | `@motorreset` 1038-1040, `@motorturn` 1155-1156 | small | **fixed**, `p2-acquisition-one-motor.patch`, proven |
| 4 | Eight sources of type 14 stop the rabbit polling its server | `@info` 634-642 | small | **fixed**, `p5-info-hops.patch`, proven |
| 5 | The first spontaneous animation comes 11-12 minutes after the ears last moved, not "soon" | `@respire` 556-595 | none (a fact for the Terrier's page) | measured |
| 6 | A hand of three holes or fewer is never reported (but not lost) | `@tstears` 960-973 | small | documented, not changed |
| 7 | Source values and types are used as table indexes unchecked | `@info` 653-677 | small | documented; the Terrier bounds what it sends |
| 8 | A click during an ear order can delay the stop | `@clicm` 1303-1345 | medium | not reproduced, documented |
| 9 | The frame is 84 % animation data; the code is 8 % | `@data` 1362-5594 | large | measured, left alone |
| 10 | Dead code and leftovers | various | tiny | documented, left alone |
| 11 | The server is unauthenticated; what the program can and cannot check | protocol | -- | documented |
| 12 | The homing turn's deadline register is overwritten by the right ear's target | `@motorreset` 1047, 1109-1110 | tiny | **fixed** in `p2-acquisition-one-motor.patch`, proven |

### 1. A stalled ear hangs the acquisition turn (fixed)

After a hand moves an ear (`@tstears`: more than three holes counted while
the motors are off), the program waits for the ear to be still for 60 of its
loop turns, then runs `@motorturn`: both motors on, each until its counter
has advanced 17 holes, the longest stretch without a hole being the wheel's
gap, from which the position follows. The loop at `@m0t0` ends only when
both motors have finished (`rflag` = 3, line 1214-1216). There is no
deadline. `@motorreset`, the homing turn at boot, has one (30 s, line
1047-1054); `@motorturn` has none. An ear whose motor runs but whose wheel
does not turn -- Lazare's right ear, measured on 2026-11-24, its motor
drawing current and the ear still -- keeps the loop going for ever, the
motor energised, and the program never returns to its wait loop: no ear
orders, no belly, no breathing, no hand reports; only a click still works
(the interrupt), and a click with a message waiting then leaves this state
through finding 2.

Measured (`review-proof.sh`, "stall", `--stall 1`, a hand of 9 holes on
ear 0 at 70 s, 160 s): Violet's frame starts ear 1's motor at the
acquisition and never stops it; no `sd=8...` is ever sent. With
`p3-acquisition-timeout.patch` the turn gives up 40 s after it started
(`sec` deadline in R13, checked every loop turn like `@motorreset`'s), stops
both motors, counts an unfinished ear as position 0 (`rstart = rpmax - 15`,
so that `start + 17 - pmax - 2` is 0) and goes on to the report: on 2.0b
the hand let go at 73.9 s, the turn began at 76.8 s, ear 0 finished its
turn in 8.4 s, ear 1 ran stalled until 124.2 s, and poll 11 carried
`sd=8700` (left 7, right 0); the same on 1.4b. 40 s because the turn now
runs the ears one after the other (finding 3): one ear takes about 10 s (a
turn of 8.5 s and the gap), two about 20 s. The firmware's `sec` second is
about 1.18 s on the emulator's clock (the `03` = 1 poll interval is 11.8 s
on the same clock), so these 40 s show as 47 s; the real rabbit's ratio
was not measured.

### 2. A click with no message program coming hangs the program (fixed)

A click while the program waits and source 18 (the first message colour) is
not 0 sends `sd=0003` and enters `@sendwaitloop` (line 814): `inputrst 0`
(the click interrupt off), `@sendwait` until the send went and no download
is running, and again, for ever. Violet's server answered that poll with
the message program, which replaces this one, so the loop never had to end.
The Terrier answers `0003` with a sound program only when it still holds a
sound for that rabbit (`nabv1.answer`); otherwise it answers the parameters
alone, and the rabbit stays in this loop, its nose and belly pulsing,
until the next program -- a reboot. Measured (`out/click-hang`, 2.0b,
one message waiting, a click at 60 s, then an order L5 R10 from poll 8 at
96 s, 150 s): `sd=0003` on poll 6, the ears never moved again.

With `p4-click-timeout.patch` the loop keeps the second it started in R11
and, 40 s later with no program having come, re-arms the click interrupt
and leaves through `rtij @startwait`, as the delete (`@arch`) and the long
press (`@reread`) already do: the program shows its signature, homes the
ears and waits again. 40 s is three poll intervals at the Terrier's `03` = 1
(11.8 s in the emulator): a message program that exists arrives on the
next poll, during which `@sendwait` stays inside its download wait, and
then the new program has the machine. Measured (`review-proof.sh`,
"click"): Violet's frame never moves its ears after the click; the patched
one goes to the order's slots 4 and 9 after the 40 s, on both firmwares.

### 3. The homing and acquisition turns start both motors together (fixed)

PR #240 made an order start one motor at a time (`@moveears`, patch
`one-motor-at-a-time.patch`), because the 1.4b firmware keeps the PIC's
brown-out reset on and two motors starting in the same millisecond are the
larger dip on the supply. The two other places that start both motors are
`@motorreset` (line 1038-1040, at boot and at every return to
`@startwait`) and `@motorturn` (line 1155-1156, after a hand).
`p2-acquisition-one-motor.patch` starts the right motor (motor 1) only once
the left one has stopped in both: in `@motorreset` a flag in R11 and the
`r8` bit 2 ("motor 0 stopped at its target"), in `@motorturn` the `rflag`
bit 0 and the counter of motor 1 read again when it starts. The homing
deadline goes from 30 s to 40 s since the two ears now home one after the
other (about 11 s each at worst: a turn, the gap, two holes). Measured
(`one-motor-proof.sh`, extended): on both firmwares Violet's frame starts
both motors in the same millisecond at boot and after the hand, the
Terrier's starts the right one the millisecond the left one stops.

### 4. Eight sources of type 14 stop the rabbit polling (fixed)

`@info` (line 634-642) skips a pair of type 14 (`_respiretype_`, the
"moves by itself" setting) by jumping back to `@infoinc`, which moves to
the next pair; with the eight pairs all of type 14 the jump never ends. The
step's 1,000,000-instruction limit (manual §4.2) is what ends it, every
step, and that is enough to starve the firmware's own loop: measured
(`out/all14-20b`, `out/all14-14b`, eight pairs (14, 1), 90 s): on both
firmwares the rabbit took the program, homed its ears, polled a second
time and never again; the 1.4b watchdog did not fire (so the firmware
clears it inside the machine's instruction loop: a spinning program does
not reset the rabbit, it deafens it until someone unplugs it). The Terrier
sends one pair of type 14 at most; a proxy or a bug could send eight.
`p5-info-hops.patch` counts the hops in R7 and gives up after eight
(`@inforst`: belly off), 13 bytes. Measured (`review-proof.sh`, "all14"):
Violet's frame polls twice in 90 s, the patched one keeps polling, on both
firmwares.

### 5. The spontaneous animations come every 11-12 minutes at the earliest

`@respire` (line 556) loads a 16-bit countdown of 46 x 256 = 11,776 turns
of the wait loop (`@cmdend`), decremented only while both ears are still,
and reset at every animation and at every `@startwait`. Then `rnd` draws
a byte, and the animation plays when it is below the value of source 14
(`@getrespire`: the last pair of type 14 among the eight; 0 when none).
Measured (`out/breathe2`, 2.0b, source 14 = 255, 1400 s): the wait loop
began at about 27 s and the first animation at 718 s (ear 1 turned
backward for 8 s), nothing else before 1400 s: one turn of the loop is
about 59 ms in the emulator (the manual's "about 20 steps a second" plus
the `wait 1`), the countdown 11.5 minutes. So with source 14 at 255 the
rabbit moves 11-12 minutes after its ears last moved, then again 11-12
minutes later; at the Terrier's "vif" (160) each draw has 5 chances in 8,
"normal" (64) one in 4, "discret" (16) one in 16. The Terrier's comment
("not yet seen moving on the emulator at 255 in 240 s: beta") and its page
can say this instead.

### 6. A hand of three holes or fewer is never reported

`@tstears` (line 965, 971) wants more than three holes counted while the
motors are off before it treats a move as a hand. The emulator's hand turns
at the motor's speed, 430 ms a hole; four holes starting in the wheel's
1.4 s gap count three edges, and so do six (`review-proof.sh` needed nine
from the home slot). Measured (`out/down-hand`, 2.0b, a hand of 4 holes on
ear 1 at 60 s from the home slot 16): no acquisition, no report.
Not lost though: `@moveears` folds the counter's increments into `/dst1` at
the next order (line 943-951), and the order L5 R10 ended at slot 9 all the
same. The Terrier's own program reports any hole. A threshold of one would
make the three-second violet screen and the acquisition turn (a full turn
of both ears, 20 s with finding 3) follow every brush of an ear, which is
why Violet set three; left as is.

### 7. Source types and values index tables unchecked

`@info` turns (type, value) into an animation through four tables (line
653-668): `@infofirsttype[type - 1]` (8 entries), then `@infospeed`,
`@infomskframe`, `@infofirst` at `base + value` (35 entries: weather 0-5,
stocks 0-6, type 3 0-6, mail 0-3, air 0-10), then `@infoframes` by
`3 x frame` (16-bit), then the 16-entry colour tables. Nothing is checked:
a type above 8 or a value above its range reads bytes of the program as
colours and frame counts. Nothing worse: `LDT` and `LDTW` read program
memory, the indexes are 8 and 16 bits, the result only lights LEDs. Violet's
server bounded only the mail count (`MessageSerializer.filtersrcval`); the
Terrier bounds all four it sends (weather 0-5, air 0-10, mail 1-3, moves).
Likewise `@showmsg` (line 768) walks the message colours from source 18
until a 0 byte, up to index 255 of a 64-byte RAM: Violet's server always
wrote the 0; the Terrier's 19-byte parameters frame stops at index 18 and
relies on index 19 never having been written. A clamp in the program
(about 30 bytes) would only protect against the Terrier's own mistakes,
and the emulator shows LED on/off, not colours: documented, not changed.

### 8. A click during an ear order can delay the stop

The click handler `@clicm` runs inside the interrupt for 12 turns after a
short click (the double-click window, 0.7 s) and up to 40 during a long
press (2.4 s), with its own `wait 1` loops, while the main loop that stops
the motors (`@moveears`, `@wait0`) is suspended: a stop that falls in that
window comes late, and `/dst` is then off by the overshoot until the next
hand. Measured once (`out/click-move`, 2.0b, order L5 R10 at 47.8 s, a
click at 49.5 s): the ears stopped at slots 4 and 9 after 3351 and 4944
ms, as without the click (3351 ms in `out/down-hand`) -- the window closed
before either stop. Not reproduced, left as is: the fix (stop the motors
from the handler) is the one piece of this program worth a redesign.

### 9. The frame: 2,692 bytes of program, 28,413 of animation data

Measured from the frame: code and tables 2,692 B, the TaiChiPop clip
(`@data`, 3,026 `set_led_color` of 8 bytes each, 30 ear and LED
animations drawn at random) 28,413 B, the 30 MIDI files 2,495 B, headers
147 B. The emulator downloads it in about 12 s (program start to the first
poll with `st=01`); a real v1 on 802.11b was not measured. The sixth byte
of every `set_led_color` ("type de transition", line 204) is read and
ignored: dropping it saves 3,026 B (9 %) for an edit of 3,026 data lines
and of the handler, which the emulator cannot verify colour by colour. A
palette form would save 6 KB more. Once per boot, and a v1 boots rarely:
left alone.

### 10. Dead code and leftovers

`@pause` (line 512, never called), `@showsign` (1249, `@showsign2` is the
one used), `/ledonly` (written at 121, never read), the clip command
`@echo` (367, reads and does nothing), `_empty_`, `/iclic` `/idclic`
`/ilclic`, `nothing`, `@clicplay`/`@dclicplay`/`@lclicplay` (one `rti` under
three labels), 17 `echo` instructions (two bytes each, UART output: three in
the wait loop, one at every turn of `@sendwait`). About 120 bytes in all;
harmless; left so that the Terrier's copy stays a small diff of Violet's.

### 11. Security: what the program can and cannot do about the server

A v1 speaks plain HTTP to whatever answers for its server; the frame's
checksum (all bytes sum to 255) is against corruption, not against anyone.
Whoever answers sends the program, so the program cannot defend the rabbit
from a hostile server -- it *is* what the server sent. What the program
can do is fail safe on parameters: the Terrier's, or a proxy's, mistakes.
The bound it lacks (finding 7) only wastes LEDs; the four hangs
(findings 1, 2, 4 and 12) are the costly failures and are fixed. The
rabbit itself sends nothing but its MAC, firmware version and the `sd`
events; the three-state switch sets the volume locally (`@vol`, interrupt
2). The 1.4b watchdog (about 512 ms) belongs to the firmware, which clears
it inside the machine's instruction loop: no run of either proof ever saw
it fire, stall, hang and spin included.

### 12. The homing turn's deadline is overwritten (fixed)

`@motorreset` keeps its 30 s deadline, a second count, in R10 (line
1047-1048) and compares the current second with it at every turn (line
1050-1052). When the right ear's gap is found, `@reset5` (line 1109-1110)
loads R10 with that ear's target counter. From then on the "deadline" is a
hole count between 0 and 255, and the homing ends by it only when the
second counter happens to equal it: a left ear that never turns next to a
right one that homes keeps the left motor on for up to 256 s, or stops both
ears at once at a random moment with `/dst` 0. Found while reading the
serial homing (finding 3); the Terrier's copy keeps the target in R13.
Measured (`review-proof.sh`, "stall0", ear 0 stalled from boot, 130 s): the
patched homing stops the left motor 40 s after it started and the rabbit
goes on polling; the control's log is printed, not asserted, since its end
depends on that chance match.

## The two other programs

`Nabaztag_asleep_v1.0.vasm` (the night): the signature screen, the homing
turn, then `wait 20` for ever; only the volume interrupt stays armed, a
click does nothing, the server wakes the rabbit by sending the wait program.
Its homing starts both motors together (same lines as finding 3) and homes
the ears 12 holes past the gap instead of 2 (`add r9,12`, `add r10,12`):
the ears down. The Terrier does not serve it; if it does one day, the same
patch applies. `Nabaztag_msg_v0.9.vasm` (a message): the clip player of
the wait program with palettes and a `reset` command, and a click during
the message ignored; Violet's server filled `<data>` with the message's
clip. The Terrier plays its sounds with its own program and never serves
it. Neither was run in the emulator.

## For the Terrier (not changed here)

- `nabv1.parse_sd` reads `8RxL`: right ear in the second hex digit, left in
  the fourth, and only when the first digit is 8. Violet's program sends
  `(0x80 | left) << 8 | right` (`@tstear1`, line 1010-1015: `/dst0` is the
  ear of source 16, `ear_left` in the file's own header). So a report with
  the left ear at 16 starts with 9 and is dropped, and the two ears are
  read swapped, which `queue_ears` then sends back as the next order. Which
  physical ear motor 0 is, Violet's source says left; the Terrier's own
  program says right; one order on a real v1 settles it.
- The parameters frame could end with a 0 after the message colours (one
  more byte) as Violet's did, for `@showmsg`.
- The page can say when the rabbit moves by itself (finding 5).

## What was not established

- The real step period on hardware (the manual says "about 20 a second";
  the emulator's PIC runs the firmware cycle-accurately but the firmware's
  own loop is what sets it).
- The download time of the 33 KB frame over a real 802.11b link.
- Why a spinning program stops the polls (finding 4): the firmware is a
  PIC18 binary; the emulator shows the effect, not the loop that causes it.
- Anything about sound: the OKI is a register log in the emulator.
