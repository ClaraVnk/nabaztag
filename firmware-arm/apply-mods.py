#!/usr/bin/env python3
"""Apply our custom modifications to a freshly cloned nabgcc tree.

Runs inside the build container after `git clone`. Idempotent: every step
checks first whether the change is already applied (so repeated runs are
safe, and "make clean" doesn't require re-cloning).

The script is intentionally chatty so the Docker build log shows what
landed.
"""

import argparse
import re
import sys
from pathlib import Path


# Most of this firmware's sources are CRLF: src/net/ieee80211.c,
# inc/net/ieee80211.h, src/usb/usbhcore.c, src/usb/usbctrl.c, src/hal/audio.c
# and src/vm/vinterp.c all are, while src/net/eapol.c is not. Path.read_text()
# and Path.write_text() use universal newlines, so reading turns CRLF into LF
# and writing leaves it that way: a one-line edit rewrites every line in the
# file.
#
# That is invisible until something else reads the same file expecting it
# unchanged. patches/wpa2-scanned-ciphers.patch does, and the first step to
# touch ieee80211.c -- group_cipher below -- broke the ARM build with
# "patch failed: src/net/ieee80211.c:50" while the edit itself was fine and 15
# lines BELOW that hunk. Use these for any source a patch might also read.
def read_src(path: Path) -> str:
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
        return f.read()


def write_src(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text)


def patch_vbc_h(root: Path) -> None:
    """Add OPverifySig opcode to both nabgcc and mtl_linux vbc.h copies."""
    targets = [
        root / "inc/vm/vbc.h",
        root / "mtl/mtl_linux/inc/vm/vbc.h",
    ]
    for p in targets:
        s = p.read_text()
        if "OPverifySig" in s:
            print(f"[skip] {p}: OPverifySig already present")
            continue
        new = re.sub(
            r"(#define OPi2cWrite 151\n)",
            r"\1#define OPverifySig 152\n",
            s,
            count=1,
        )
        if new == s:
            sys.exit(f"FAIL {p}: could not find #define OPi2cWrite 151 anchor")
        p.write_text(new)
        print(f"[ok]   {p}: added OPverifySig 152")


def patch_vinterp_c(root: Path) -> None:
    """Add the OPverifySig case to the VM dispatch loop."""
    p = root / "src/vm/vinterp.c"
    s = p.read_text()
    if "OPverifySig" in s:
        print(f"[skip] {p}: case already present")
        return

    needle = (
        '      case OPsndVol:\n'
        '        audioVol(VALTOINT(VSTACKGET(0)));\n'
        '        break;\n'
    )
    addition = (
        '      case OPverifySig:\n'
        '        {\n'
        '          int32_t sig=VALTOPNT(VPULL());\n'
        '          int32_t payload=VALTOPNT(VSTACKGET(0));\n'
        '          int32_t ok=0;\n'
        '          if ((payload!=NIL) && (sig!=NIL) && (VSIZEBIN(sig)==64))\n'
        '          {\n'
        '            int32_t scratch=VMALLOCBIN(VSIZEBIN(payload)+64);\n'
        '            if (scratch>=0)\n'
        '              ok = nab_verify_sig(\n'
        '                  (const uint8_t*)VSTARTBIN(payload), VSIZEBIN(payload),\n'
        '                  (const uint8_t*)VSTARTBIN(sig),\n'
        '                  (void*)VSTARTBIN(scratch));\n'
        '          }\n'
        '          VSTACKSET(0, INTTOVAL(ok));\n'
        '        }\n'
        '        break;\n'
    )
    if needle not in s:
        sys.exit(f"FAIL {p}: OPsndVol case anchor not found")
    s = s.replace(needle, needle + addition, 1)

    # Add the include at the top of the include block.
    inc_anchor = '#include "vbc.h"'
    if inc_anchor in s and '#include "nab_sig.h"' not in s:
        s = s.replace(inc_anchor, inc_anchor + '\n#include "nab_sig.h"', 1)
    elif '#include "nab_sig.h"' not in s:
        # Fallback: just prepend
        s = '#include "nab_sig.h"\n' + s
    p.write_text(s)
    print(f"[ok]   {p}: added OPverifySig case + nab_sig.h include")


def patch_stdlib_core(root: Path) -> None:
    """Register verifySig in the MTL compiler's stdlib tables. Critical
    detail: the `NBcore` #define caps the loop that walks the arrays, so
    every new entry MUST come with NBcore++. (The upstream source even
    marks this with /******** A ACTUALISER! ************/.)
    """
    p = root / "mtl/mtl_linux/src/vcomp/stdlib_core.cpp"
    s = p.read_text()
    if '"verifySig"' in s:
        print(f"[skip] {p}: verifySig already registered")
        return

    # Bump NBcore (will fail loudly if the upstream constant moves).
    nb_anchor = "#define NBcore 119"
    if nb_anchor not in s:
        sys.exit(f"FAIL {p}: NBcore anchor not found (upstream changed?)")
    s = s.replace(nb_anchor, "#define NBcore 120", 1)

    # Each of the four parallel arrays gets a new /*17*/ section with a single
    # entry. The arrays end with a closing brace; we splice before it.
    edits = [
        # corename[]
        (
            '/*16*/"strright","strcrypt8","loadf","savef"\n};',
            '/*16*/"strright","strcrypt8","loadf","savef",\n/*17*/"verifySig"\n};',
        ),
        # coreval[]
        (
            '/*16*/OPstrright,OPstrright,OPstrright,OPstrright,\n};',
            '/*16*/OPstrright,OPstrright,OPstrright,OPstrright,\n/*17*/OPverifySig,\n};',
        ),
        # corecode[]: number of args. verifySig takes 2 (payload, sig).
        (
            '/*16*/2,3,1,2\n};',
            '/*16*/2,3,1,2,\n/*17*/2\n};',
        ),
        # coretype[]: type signature. fun[S S]I — two bins → int.
        (
            '/*16*/"fun[S I]S","fun[S I I][S I]","fun[S]S","fun[S S]I"\n};',
            '/*16*/"fun[S I]S","fun[S I I][S I]","fun[S]S","fun[S S]I",\n/*17*/"fun[S S]I"\n};',
        ),
    ]
    ok_count = 0
    for old, new in edits:
        if old in s:
            s = s.replace(old, new, 1)
            ok_count += 1
        else:
            print(f"[warn] {p}: could not splice array — anchor not found, dumping a sample:")
            # Try a softer probe to help diagnostics
            head = old[:60]
            for line in s.splitlines():
                if line.startswith(head[:20]):
                    print(f"    nearby: {line}")
                    break
    if ok_count != len(edits):
        sys.exit(f"FAIL {p}: only {ok_count}/{len(edits)} arrays patched. Aborting so we don't ship a broken stdlib table.")
    p.write_text(s)
    print(f"[ok]   {p}: registered verifySig in all 4 stdlib arrays")


def strip_tweetnacl(root: Path) -> None:
    """Remove the TweetNaCl entry points we don't use (everything that isn't
    crypto_sign_open or its hash dependency). The orphaned static helpers
    those used will be garbage-collected by the linker — between this and
    --gc-sections we claw back ~3 KB of ROM.

    KEEP:  crypto_sign_open, crypto_hash, crypto_hashblocks, crypto_verify_32
    DROP:  crypto_box*, crypto_secretbox*, crypto_stream*, crypto_onetimeauth*,
           crypto_scalarmult* (X25519), crypto_sign (sign-side), crypto_sign_keypair,
           crypto_core_(h)salsa20, crypto_verify_16
    """
    p = root / "src/crypto/tweetnacl.c"
    s = p.read_text()
    if "/* tweetnacl-stripped */" in s:
        print(f"[skip] {p}: already stripped")
        return

    unused = [
        "crypto_core_salsa20", "crypto_core_hsalsa20",
        "crypto_stream_salsa20_xor", "crypto_stream_salsa20",
        "crypto_stream_xor", "crypto_stream",
        "crypto_onetimeauth_verify", "crypto_onetimeauth",
        "crypto_secretbox_open", "crypto_secretbox",
        "crypto_scalarmult_base", "crypto_scalarmult",
        "crypto_box_open_afternm", "crypto_box_afternm",
        "crypto_box_open", "crypto_box_beforenm",
        "crypto_box_keypair", "crypto_box",
        "crypto_sign_keypair", "crypto_sign",
        "crypto_verify_16",
    ]
    # Order longest-first so e.g. crypto_box matches AFTER crypto_box_keypair
    # has had its turn (avoids prefix collisions even though our regex anchors
    # on a literal `(` after the name).
    unused.sort(key=len, reverse=True)
    removed = 0
    for name in unused:
        pat = re.compile(
            rf"^(int|void)\s+{re.escape(name)}\s*\([^)]*\)\s*\n\{{",
            re.MULTILINE,
        )
        m = pat.search(s)
        if not m:
            continue
        # Find the matching closing brace (account for nested braces).
        i = m.end()
        depth = 1
        while i < len(s) and depth > 0:
            c = s[i]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
            i += 1
        if depth == 0:
            s = s[:m.start()] + s[i:]
            removed += 1
    s = "/* tweetnacl-stripped */\n" + s
    p.write_text(s)
    print(f"[ok]   {p}: stripped {removed}/{len(unused)} unused TweetNaCl entry points")


def modernize_pages(root: Path) -> None:
    """Replace the 2006-era config-mode HTML (page_a, page_done, page_u,
    page_error) with the modernized versions in firmware-arm/pages/.

    The new HTML:
      - keeps every form field name + action URL the firmware backend reads,
      - keeps every <MARKER> template placeholder the rabbit substitutes,
      - drops the inline <table> layout and per-element style attributes for
        one consolidated <style> block (dark theme, system font, mobile
        viewport).

    Net effect: ~15-20 KB of ROM freed.
    """
    pages_dir = Path(__file__).resolve().parent / "pages"
    boot = root / "mtl/boot/boot.0.0.0.13.mtl"
    s = boot.read_text()
    if "/* pages-modernized */" in s:
        print(f"[skip] {boot}: pages already modernized")
        return

    pages = {
        "page_a": pages_dir / "page_a.html",
        "page_done": pages_dir / "page_done.html",
        "page_u": pages_dir / "page_u.html",
        "page_error": pages_dir / "page_error.html",
    }
    for name, path in pages.items():
        if not path.is_file():
            sys.exit(f"FAIL {boot}: missing page {path}")

    # Each var assignment in boot.mtl spans many lines and terminates with
    # `;;` at end of line. The tricky case: page_a ends with `"::nil;;` (it
    # concatenates with nil), the others end with `";;`. So match up to the
    # FIRST `;;` that is at end of a line — covers both.
    block_re = lambda name: re.compile(
        rf'^var {name}=.*?;;[ \t]*$\r?\n?',
        re.MULTILINE | re.DOTALL,
    )
    total_before = sum(
        len(m.group(0))
        for name in pages
        for m in [block_re(name).search(s)]
        if m is not None
    )

    # In Metal, page_a is a *list* of string fragments interleaved with the
    # marker tokens (`"<MARKER>"`) — pagefill walks it via listreplacestr and
    # swaps each marker element for its computed value. So when the source HTML
    # contains <MARKER> placeholders, we must split the file at each marker and
    # emit a `"frag"::"<MARKER>"::"frag"::nil` chain rather than one big string.
    # When there are no markers we still wrap the string with `::nil` so callers
    # always see a list (httpindex feeds page_a into strcatlist).
    marker_re = re.compile(r'<[A-Z][A-Z0-9_-]*>')

    def _esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace('"', '\\"')

    def _to_metal_list(html: str) -> str:
        parts = []
        i = 0
        for m in marker_re.finditer(html):
            if m.start() > i:
                parts.append(f'"{_esc(html[i:m.start()])}"')
            parts.append(f'"{m.group(0)}"')
            i = m.end()
        if i < len(html):
            parts.append(f'"{_esc(html[i:])}"')
        if not parts:
            parts.append('""')
        return "::".join(parts) + "::nil"

    for name, path in pages.items():
        html = path.read_text()
        # page_a is the only one walked by pagefill → keep it list-shaped.
        # The others are returned as-is; emitting a string is shorter and
        # backwards-compatible (the cbhttp caller accepts both).
        if name == "page_a":
            value = _to_metal_list(html)
        else:
            value = f'"{_esc(html)}"'
        replacement = f'var {name}={value};;\n'
        new_s, n = block_re(name).subn(replacement, s, count=1)
        if n != 1:
            sys.exit(f"FAIL {boot}: could not find/replace {name} block")
        s = new_s

    s = "/* pages-modernized */\n" + s
    boot.write_text(s)

    # Report size delta — useful when iterating.
    total_after = sum(
        len(m.group(0))
        for name in pages
        for m in [block_re(name).search(s)]
        if m is not None
    )
    delta = total_after - total_before
    print(
        f"[ok]   {boot}: modernized 4 pages, "
        f"{total_before} → {total_after} bytes ({delta:+d})"
    )


def patch_makefile(root: Path) -> None:
    """Strip the upstream DEBUG_VM / DEBUG_AUDIO / DEBUG_MAIN defines.

    These wire DBG_VM(...) macros to sprintf+UART, which drags in
    _vfprintf_r / _printf_i / _sfvwrite_r / …  ~7 KB of newlib printf
    glue. Production firmware doesn't need them; the serial console is
    not wired up on a production rabbit anyway.
    """
    p = root / "Makefile"
    s = p.read_text()
    # Comment out the DEBUG defines (idempotent — leaves a `# disabled by
    # apply-mods` marker so re-runs are no-ops).
    disabled = False
    for flag in ("-DDEBUG_VM", "-DDEBUG_AUDIO", "-DDEBUG_MAIN"):
        live = f"OPTIONS += {flag}\n"
        dead = f"#OPTIONS += {flag}  # disabled by apply-mods\n"
        if live in s:
            s = s.replace(live, dead, 1)
            disabled = True
    if disabled:
        print(f"[ok]   {p}: disabled DEBUG_VM/AUDIO/MAIN (drops the printf family)")
    else:
        print(f"[skip] {p}: DEBUG_VM/AUDIO/MAIN already disabled")
    p.write_text(s)


def patch_linker_keep(root: Path) -> None:
    """Wrap .intvec / .startup.* / .bytecode.* / .ramfunc in KEEP() in
    the linker script. Prerequisite for safely enabling --gc-sections
    (see patch_gc_sections).

    These sections are referenced only by hardware:
      - .intvec: ARM7 reset/exception vectors (CPU reads at fixed offsets)
      - .startup.cstartup: Reset_Handler body (entry symbol pulls it in
        but a tighter linker could miss other init in this section)
      - .bytecode.*: dumpbc — the embedded Metal bootloader bytecode,
        referenced via a single extern from main.c
      - .ramfunc: flash_uc, runs from RAM while flash is being rewritten;
        invoked via function pointer from sysFlash
    """
    p = root / "sys/ml67q4051.ld"
    s = p.read_text()
    if "KEEP(*(.intvec))" in s:
        print(f"[skip] {p}: KEEP() already present")
        return

    edits = [
        ("    *(.intvec)\n", "    KEEP(*(.intvec))\n"),
        ("    *(.startup.*)\n", "    KEEP(*(.startup.*))\n"),
        ("    *(.bytecode.*)\n", "    KEEP(*(.bytecode.*))\n"),
        ("    *(.ramfunc)\n", "    KEEP(*(.ramfunc))\n"),
        # This step does not touch the .bss block. Bracketing *(.bss.*)
        # there is the right thing to do and `extbss` now does it -- but only
        # together with moving 8 KB of scratch buffers out to external RAM,
        # because on a 16 KB SRAM the collected .bss otherwise runs into the
        # stacks that init.s reserves. See patch_extbss.
        #
        # CORRECTION (2026-10-25) to the note that stood here from 2026-06-21:
        # it said bracketing *(.bss.*) "leaves ZERO VM heap -> the bytecode
        # loader (VMALLOC) can't allocate". That explanation was wrong, and
        # being wrong it argued against the real fix for four months. VMALLOC
        # does not use the newlib heap at all: `_vmem_heap` is a fixed address
        # in external RAM, `((int32_t*)(0xD0010000))` in inc/vm/vmem.h:16. The
        # newlib heap in IntRAM is reached only by libc rand(). What actually
        # bricked the rabbit on 2026-06-20 is that __bss_end__ climbed to
        # 0x10003208, above the 0x1680-byte stack reserve at 0x10002980 --
        # .bss landed on the stacks. preflight.py's check_memory_landmarks
        # tests for exactly that now.
    ]
    n = 0
    for old, new in edits:
        if old in s:
            s = s.replace(old, new, 1)
            n += 1
    if n != len(edits):
        sys.exit(f"FAIL {p}: only {n}/{len(edits)} sections patched (upstream changed?)")
    p.write_text(s)
    print(f"[ok]   {p}: wrapped {n} sections in KEEP() (vec/startup/bytecode/ramfunc)")


def patch_audio_ampli(root: Path) -> None:
    """Drive the amplifier from the player, not from the volume knob.

    pacifox's #27 finding 5: set_vlsi_volume() ends with
    `vlsi_ampli(volume<0x7f)`, so OPsndVol -> audioVol() -> set_vlsi_volume()
    switches the amplifier on with nothing playing. It is not a corner case.
    The boot sequence sets the volume once, early -- the emulator logs
    `audio: volume 0x00` at 0 ms -- and 0x00 is louder than 0x7f, so the
    amplifier comes on at power-up and stays on. The add-on then re-sends
    `SV <hw>` 3 s into every reconnect, which does it again. A rabbit sitting
    idle has its speaker amplifier powered, which is hiss and current for no
    sound.

    The same line has a second edge, pointing the other way: a volume at or
    above 0x7f turns the amplifier *off*, so asking a rabbit to be quiet
    silences it completely rather than quietly.

    Both go away by letting the player own the amplifier, which it already
    does: play_start() turns it on, play_stop() and the end of a clip turn it
    off, and rec_start() turns it off to record. Volume is a volume.

    NAB_AUDIO_AMPLI_PLAY_ONLY lets a host build model either behaviour.

    NOT APPLIED IN ANY MODE, measured on hardware 2026-10-23. With this step
    in, the rabbit plays a sound exactly as it should on the wire -- binds its
    streaming resource, fetches the clip, spends 5.5 s on it, returns to idle
    -- and nothing is heard. So play_start()'s vlsi_ampli(1) is not sufficient
    on the real device, and removing the line from set_vlsi_volume() takes
    away the thing that was actually keeping the amplifier powered.

    pacifox's finding stands: an idle rabbit does sit there amplifying
    nothing, and a volume at or above 0x7f does silence it. What is wrong is
    this fix. The emulator could not catch it -- it models the amplifier as a
    log line, so it cannot tell a sound that is heard from a sound played into
    a dead amplifier, which is precisely the failure this caused. Whatever
    replaces it needs a test with ears, or a reading of the GPIO on the
    device.
    """
    h = root / "inc/hal/audio.h"
    src = read_src(h)
    if "NAB_AUDIO_AMPLI_PLAY_ONLY" in src:
        print("[skip] audio_ampli: already applied")
        return
    nl = "\r\n" if "\r\n" in src else "\n"
    old = "void set_vlsi_volume(uint8_t volume);"
    if old not in src:
        sys.exit(f"FAIL {h}: set_vlsi_volume declaration not found (upstream changed?)")
    write_src(h, src.replace(
        old,
        nl.join(["/* Volume only. The amplifier belongs to play_start/play_stop. */",
                 "#define NAB_AUDIO_AMPLI_PLAY_ONLY 1",
                 old]), 1))
    print(f"[ok]   {h}: the amplifier is the player's business")

    c = root / "src/hal/audio.c"
    src = read_src(c)
    nl = "\r\n" if "\r\n" in src else "\n"
    old = nl.join([
        "  //Config VOLUME",
        "  vlsi_write_sci(VS1003_VOLUME,(volume<<8)|volume);",
        "  vlsi_ampli(volume<0x7f);",
    ])
    new = nl.join([
        "  //Config VOLUME",
        "  vlsi_write_sci(VS1003_VOLUME,(volume<<8)|volume);",
        "  /* No vlsi_ampli() here. Setting the volume used to power the",
        "     amplifier -- at boot, and again on every SV the server sends --",
        "     so an idle rabbit sat there amplifying nothing; and a volume at",
        "     or above 0x7f switched it off, so asking for quiet gave",
        "     silence. play_start/play_stop own it. */",
    ])
    if old not in src:
        sys.exit(f"FAIL {c}: set_vlsi_volume's amplifier line not found (upstream changed?)")
    write_src(c, src.replace(old, new, 1))
    print(f"[ok]   {c}: set_vlsi_volume no longer powers the amplifier")


def patch_usb_robustness(root: Path) -> None:
    """Five ways the radio went deaf or leaked, from pacifox's #25.

    Findings 3, 4 and 5. Each one is small; what they share is that a failure
    is detected and then dropped on the floor, so the rabbit stays
    "connected" with no radio and nothing ever retries.

    1. The 8051 start request was unchecked (finding 3). rt2501_setup()
       writes the firmware image, checking every rt2501_write, and then sends
       the vendor "start firmware" request through usbh_control_transfer with
       its return value discarded. If that one request fails the driver
       reports success and the stick never runs the image. It now returns 0,
       which usbh_rt2501 already handles by deleting the pipes and letting
       enumeration start over.

       Not done, deliberately: the VBUS off/on cycle pacifox suggests in the
       same finding. VBUS_OFF exists in usbctrl.h and nothing calls it, but
       whether VBUS to the stick is actually switched by the controller is a
       board question, and a rabbit whose stick never comes back up is worse
       than one that needs a power cycle. It needs a measurement, not a
       patch.

    2. A failed enumeration was reported as a success (finding 4).
       usbh_connect() cleans up after usbh_enumeration() fails -- releases
       the descriptors, deletes pipe 0 -- and then returns `dev` anyway. Its
       caller stores that as hcd_info.rh_device and the stack carries on with
       a device that was never enumerated. It now frees dev and returns NULL,
       the same way it already does when the allocation fails.

       Also in finding 4: main.c's three `while(1);` after a failed
       usbctrl_init / usbhost_init / rt2501_driver_install, described as
       silent hangs with no reset. They are not, and this is worth being
       exact about because it changes what to do: wdt_start() runs at
       main.c:183, before all three, so the watchdog is armed and those loops
       reset the chip after about 2.09 s. They are reset loops. What made
       them look like hangs is the next thing along -- the reset is a
       software reset, and usbhost_init_status survived it, so the retry did
       nothing. That is finding 1, and `extbss` is what fixes it. Left as
       they are.

    3. One failed allocation left the rabbit deaf for good (finding 5).
       rt2501_submit_rx() returns silently when hcd_malloc gives it no URB,
       and reception is re-armed only from the receive callback -- which will
       now never run again. A flag records the miss and rt2501_timer(), which
       main's loop already calls, retries. Reception resumes on the next
       tick instead of never.

    4. A USB error was reported as a short frame (finding 5). hcd.c sets
       `urb->status = URB_XFERERR` in the error branch and then, four lines
       later and unconditionally, overwrites it with `(int8_t)urb->result` --
       the small positive OHCI condition code. Every caller that tests
       `urb->status < 0` therefore sees a success. The overwrite now happens
       only when no error was flagged.

    5. ComRAM was leaked on every failed transmit (finding 5). On success
       the frame is freed by usbh_free_urb_callback; when the submit fails
       nobody frees it, and no caller of rt2501_tx does either -- not
       ieee80211_send_auth, not ieee80211_associate, not rt2501_send. Freeing
       it in rt2501_tx covers all of them, which is also where ownership
       actually ends. ComRAM is 4 KB.

       And the busy-loop that waits for it (`do { fr = hcd_malloc(...) }
       while((fr == NULL) && mayblock)`) gets a bound. It has no CLR_WDT, so
       today it is a 2-second watchdog reset whenever ComRAM is momentarily
       full. Dropping one frame is a better answer than rebooting, and
       rt2501_send already has `if(fr == NULL) return 0` one line below.
    """
    # These files are CRLF (the 2006 sources are, inconsistently), so every
    # pattern below is written with \n and translated per file. Matching
    # against the wrong newline is a silent no-match, which the sys.exit
    # guards would otherwise report as "upstream changed".
    def edit(rel, pairs, done_marker, label):
        f = root / rel
        src = read_src(f)
        if done_marker in src:
            print(f"[skip] usb_robustness: {rel} already done")
            return
        nl = "\r\n" if "\r\n" in src else "\n"
        for old, new, what in pairs:
            o, n = old.replace("\n", nl), new.replace("\n", nl)
            if o not in src:
                sys.exit(f"FAIL {f}: cannot find {what} (upstream changed?)")
            src = src.replace(o, n, 1)
        f.write_text(src)
        print(f"[ok]   {f}: {label}")

    edit("src/usb/rt2501usb.c", [
        # 1. the 8051 start request
        ("""  /* Start firmware */
  usbh_control_transfer(rt2501_dev,""",
         """  /* Start firmware. The result matters: if this one request fails the
     stick never runs the image we just wrote, and reporting success here
     leaves a driver bound to a radio that will never answer. */
  if(usbh_control_transfer(rt2501_dev,""",
         "the 8051 start request"),
        ("""        0,        /* wLength */
        NULL);
  DBG_WIFI("OK!"EOL);""",
         """        0,        /* wLength */
        NULL) < 0) {
    DBG_WIFI("8051 start request failed"EOL);
    return 0;
  }
  DBG_WIFI("OK!"EOL);""",
         "the end of the 8051 start request"),
        # 3. re-arm reception after a failed allocation
        ("static void rt2501_submit_rx(void);",
         """static void rt2501_submit_rx(void);
/* Set when rt2501_submit_rx could not get an URB; retried from
   rt2501_timer(). */
static uint8_t rt2501_rx_starved;
/* Consecutive receive errors, reset by any good frame. Only the USB connect
   hook sets rt2501_connected back to 1, so giving up has to be a decision
   rather than a reflex. */
static uint8_t rt2501_rx_errors;
#define RT2501_RX_ERROR_LIMIT 10""",
         "rt2501_submit_rx's declaration"),
        ("""  disable_ohci_irq();
  urb = hcd_malloc(sizeof(*urb), EXTRAM,17);
  enable_ohci_irq();
  if(urb == NULL) return;""",
         """  disable_ohci_irq();
  urb = hcd_malloc(sizeof(*urb), EXTRAM,17);
  enable_ohci_irq();
  if(urb == NULL) {
    /* Reception is re-armed only from the receive callback, and without an
       URB that callback will never run again -- one failed allocation used
       to leave the rabbit connected and deaf for good. Remember it and let
       rt2501_timer() try again on the next tick. */
    rt2501_rx_starved = 1;
    return;
  }
  rt2501_rx_starved = 0;""",
         "rt2501_submit_rx's allocation"),
        ("""void rt2501_timer(void)
{
  if(!rt2501_connected) return;""",
         """void rt2501_timer(void)
{
  if(!rt2501_connected) return;
  if(rt2501_rx_starved) rt2501_submit_rx();""",
         "rt2501_timer"),
        # 6. a transient receive error no longer kills the radio for good
        ("""  if(urb->status < 0) {
    sprintf(dbg_buffer, "USB BULK RX ERROR, status=%d, result=%ld"EOL,
      urb->status, urb->result);
    DBG_WIFI(dbg_buffer);
    rt2501_connected = 0;
    hcd_free(urb);
    return;
  }""",
         """  if(urb->status < 0) {
    sprintf(dbg_buffer, "USB BULK RX ERROR, status=%d, result=%ld"EOL,
      urb->status, urb->result);
    DBG_WIFI(dbg_buffer);
    hcd_free(urb);
    /* Clearing rt2501_connected here is a one-way door: it makes
       rt2501_state() report RT2501_S_BROKEN for ever, because only the USB
       connect hook ever sets it back, and a stick soldered to the board
       never disconnects. So a single transient error used to cost the radio
       until the next power cycle. Re-arm a bounded number of times first;
       if they all fail, give up exactly as before. */
    if(++rt2501_rx_errors <= RT2501_RX_ERROR_LIMIT) {
      rt2501_frame_position = 0;
      rt2501_submit_rx();
      return;
    }
    DBG_WIFI("too many RX errors, radio down"EOL);
    rt2501_connected = 0;
    return;
  }
  rt2501_rx_errors = 0;""",
         "rt2501_rx_callback's error branch"),
        # 7. an overrun is a software condition, and recoverable
        ("""      DBG_WIFI("RX Buffer overrun"EOL);
      rt2501_connected = 0;
      return;""",
         """      /* An overrun is our own frame cursor running off the end, not a
          dead radio. Reset it and keep receiving; killing the link here
          meant RT2501_S_BROKEN until the next power cycle. */
      DBG_WIFI("RX Buffer overrun"EOL);
      rt2501_frame_position = 0;
      hcd_free(urb);
      rt2501_submit_rx();
      return;""",
         "the RX overrun branch"),
        # 5a. free the frame when the submit fails
        ("  ret = usbh_bulk_transfer_async(rt2501_dev, 1, buffer, length);",
         """  ret = usbh_bulk_transfer_async(rt2501_dev, 1, buffer, length);
  if(!((ret > 0) || (ret == URB_PENDING))) {
    /* On success usbh_free_urb_callback frees the frame. On failure nobody
       did -- not here, not in any caller -- and ComRAM is 4 KB. */
    disable_ohci_irq();
    hcd_free(buffer);
    enable_ohci_irq();
    return 0;
  }""",
         "rt2501_tx's submit"),
    ], "rt2501_rx_starved", "8051 start checked, rx re-armed, TX frame freed")

    # 2. a failed enumeration is a failure
    edit("src/usb/usbhcore.c", [
        ("""  if(usbh_enumeration(dev)<0){
    DBG_USB(" USBH: Error!! failed enumeration"EOL);
    usbh_release_descriptor_all(dev);
    if(dev->pipe[0]){
      usbh_delete_pipe(dev, 0);
    }
  }

  return dev;""",
         """  if(usbh_enumeration(dev)<0){
    DBG_USB(" USBH: Error!! failed enumeration"EOL);
    usbh_release_descriptor_all(dev);
    if(dev->pipe[0]){
      usbh_delete_pipe(dev, 0);
    }
    /* And say so. Returning dev here made the caller store a device that
       was never enumerated as the live root-hub device. */
    hcd_free(dev);
    return NULL;
  }

  return dev;""",
         "usbh_connect's enumeration branch"),
    ], "was never enumerated as the live root-hub device",
       "a failed enumeration returns NULL and frees the device")

    # 4. do not overwrite a flagged error
    f = root / "src/usb/hcd.c"
    src = read_src(f)
    if "turned every USB error into a short frame" in src:
        print("[skip] usb_robustness: hcd.c already done")
    else:
        nl = "\r\n" if "\r\n" in src else "\n"
        old = "  urb->status = (int8_t)urb->result;"
        if src.count(old) != 1:
            sys.exit(f"FAIL {f}: expected one status overwrite, "
                     f"found {src.count(old)}")
        new = ("""  /* Only when the error branch above did not already flag one: it sets
     URB_XFERERR, and overwriting that with the OHCI condition code -- a
     small positive number -- turned every USB error into a short frame for
     callers that test `status < 0`. */
  if(urb->status >= 0)
    urb->status = (int8_t)urb->result;""").replace("\n", nl)
        f.write_text(src.replace(old, new, 1))
        print(f"[ok]   {f}: a flagged USB error survives to the caller")

    # 5b. bound the ComRAM wait
    edit("src/net/ieee80211.c", [
        ("\tdo {\n\t\tdisable_ohci_irq();\n\t\tfr = hcd_malloc(",
         "\t/* ComRAM is 4 KB. This loop has no CLR_WDT, so an exhausted pool\n"
         "\t   used to mean a 2-second watchdog reset; dropping one frame is a\n"
         "\t   better answer, and the `if(fr == NULL) return 0` below is\n"
         "\t   already written for it. */\n"
         "\tuint32_t comram_tries = 1000;\n"
         "\tdo {\n\t\tdisable_ohci_irq();\n\t\tfr = hcd_malloc(",
         "the ComRAM busy-loop"),
        ("\t} while((fr == NULL) && mayblock);",
         "\t} while((fr == NULL) && mayblock && --comram_tries);",
         "the end of the ComRAM busy-loop"),
    ], "comram_tries", "the ComRAM wait is bounded (1000 tries, then drop)")


def patch_wpa3_sae(root: Path) -> None:
    """Wire SAE into the driver so a rabbit can join a WPA3 network.

    The cryptography is in `crypto/` and tested on a build machine against
    OpenSSL; this is the part that has to live in the radio code. Four
    things had to be found out before any of it could be written, and each
    one changed the shape of the answer:

    **The password never reaches the driver.** The bytecode computes
    PBKDF2(password, ssid) itself and hands the C side a 32-byte PMK --
    `eapol_init()` is literally `memcpy(pmk, ieee80211_key, 32)`. SAE needs
    the password, not the PMK. It does pass through C exactly once, in
    `netPmk()` (src/vm/vnet.c), on its way into `mypassword_to_pmk`, so
    that is where it is captured. The alternative was reverse-engineering
    the config-sector offset out of the Metal bytecode.

    **The PMK hand-off is therefore trivial.** Once SAE has run, writing its
    PMK into `ieee80211_key` before `eapol_init()` makes the four-way
    handshake run unchanged, with the RT2573 still doing CCMP in hardware.
    That is the whole reason WPA3 is reachable on 2006 silicon at all.

    **The scan result cannot carry a SAE flag.** `scan_result.encryption`
    goes up to the VM, and `vinterp.c`'s OPnetAuth only calls `netAuth`
    when `(crypt & 0xF0) == encrypt` against the stored config byte. Set a
    new bit in the high nibble and the bytecode silently stops associating.
    So SAE-capable BSSIDs go in a small driver-side cache instead, and what
    the VM sees stays 0x4a.

    **It keeps its own cache rather than extending the cipher one.**
    `patches/wpa2-scanned-ciphers.patch` already caches per-BSSID data and
    the obvious move was to add a byte to it. But patches apply *after*
    apply-mods, so editing the lines around its context is how that patch
    stops applying -- quietly, with fuzz, or not at all. Forty bytes of RAM
    is cheaper than coupling the two.

    What is NOT here: BIP validation of received management frames. The
    RSN IE advertises MFPC, which is what an AP with MFPR=1 requires of us,
    so association works -- but an unprotected deauthenticate is still
    honoured, which is the attack 802.11w exists to stop. The code for it is
    written and tested (`crypto/cmac.c`, `bip_cmac_128`); what is missing is
    pulling the IGTK out of the four-way handshake's key data and checking
    the MMIE. Until that lands, this is WPA3's key exchange on WPA2's
    management-frame security. Said plainly because "supports WPA3" would
    imply otherwise.
    """
    staging = root.parent / "sae-crypto"
    if not staging.is_dir():
        sys.exit(f"FAIL wpa3_sae: {staging} is missing -- the Dockerfile "
                 f"stages the SAE crypto there, outside the nabgcc tree, so "
                 f"that modes without this step do not compile it")
    for name in ("p256", "sha256", "cmac", "sae"):
        src_c = staging / f"{name}.c"
        src_h = staging / f"{name}.h"
        if not src_c.is_file() or not src_h.is_file():
            sys.exit(f"FAIL wpa3_sae: {name}.c/.h missing from {staging}")
        (root / "src/net" / f"{name}.c").write_bytes(src_c.read_bytes())
        (root / "inc/net" / f"{name}.h").write_bytes(src_h.read_bytes())
    print(f"[ok]   {root}: SAE crypto copied into src/net and inc/net")

    def edit(rel, pairs, marker, label):
        f = root / rel
        src = read_src(f)
        if marker in src:
            print(f"[skip] wpa3_sae: {rel} already done")
            return
        nl = "\r\n" if "\r\n" in src else "\n"
        for old, new, what in pairs:
            o, n = old.replace("\n", nl), new.replace("\n", nl)
            if o not in src:
                sys.exit(f"FAIL {f}: cannot find {what} (upstream changed?)")
            src = src.replace(o, n, 1)
        f.write_text(src)
        print(f"[ok]   {f}: {label}")

    # -- the constants -----------------------------------------------------
    edit("inc/net/ieee80211.h", [
        ("#define IEEE80211_AUTH_ALG_SHARED\t0x0001",
         "#define IEEE80211_AUTH_ALG_SHARED\t0x0001\n"
         "#define IEEE80211_AUTH_ALG_SAE\t\t0x0003\n"
         "/* SAE carries its commit in sequence 1 and its confirm in 2, both\n"
         "   directions, which is why the sequence number alone says what a\n"
         "   frame is. */\n"
         "#define IEEE80211_SAE_COMMIT\t\t0x0001\n"
         "#define IEEE80211_SAE_CONFIRM\t\t0x0002",
         "the auth algorithm constants"),
        ("#define IEEE80211_AUTH_PSK         0x02",
         "#define IEEE80211_AUTH_PSK         0x02\n"
         "#define IEEE80211_AUTH_AKM_SAE     0x08",
         "the AKM suite constants"),
        ("enum {\n\tIEEE80211_AUTH_OPEN,\n\tIEEE80211_AUTH_SHARED,\n};",
         "enum {\n\tIEEE80211_AUTH_OPEN,\n\tIEEE80211_AUTH_SHARED,\n"
         "\tIEEE80211_AUTH_SAE,\n};",
         "the authmode enum"),
        ("void ieee80211_init(void);",
         "/* Captured on its way into PBKDF2: SAE needs the password itself,\n"
         "   and netPmk() is the only place it exists in C. */\n"
         "void ieee80211_sae_password(const uint8_t *password);\n"
         "void ieee80211_init(void);",
         "the password hook"),
    ], "IEEE80211_AUTH_ALG_SAE", "SAE constants and the password hook")

    # -- the passphrase, captured where it passes through C ----------------
    edit("src/vm/vnet.c", [
        ("void netPmk(uint8_t *ssid,uint8_t *key,uint8_t *buf)\n{\n"
         "  mypassword_to_pmk(key,ssid,strlen((char*)ssid),buf);\n}",
         "void netPmk(uint8_t *ssid,uint8_t *key,uint8_t *buf)\n{\n"
         "  /* The only point in the firmware where the password exists as\n"
         "     text: the bytecode hands it over once, here, to be turned\n"
         "     into a PMK. SAE cannot use the PMK -- it needs the password\n"
         "     -- so it is stashed before PBKDF2 eats it. */\n"
         "  ieee80211_sae_password(key);\n"
         "  mypassword_to_pmk(key,ssid,strlen((char*)ssid),buf);\n}",
         "netPmk"),
    ], "ieee80211_sae_password", "the password is captured for SAE")

    # -- the driver ------------------------------------------------------
    edit("src/net/ieee80211.c", [
        ('static const uint8_t ieee80211_wpa2_oui[IEEE80211_OUI_LEN] =\n{ 0x00, 0x0f, 0xac, 0x00 };\n',
         'static const uint8_t ieee80211_wpa2_oui[IEEE80211_OUI_LEN] =\n{ 0x00, 0x0f, 0xac, 0x00 };\n\n/* ------------------------------------------------------------------ SAE */\n\n#include "net/sae.h"\n\n/* BSSIDs seen advertising AKM suite 8. A second cache beside the one in\n   patches/wpa2-scanned-ciphers.patch, deliberately: that patch is applied\n   after apply-mods, and editing the lines around its context is how it\n   stops applying -- quietly, with fuzz, or not at all. 48 bytes of RAM is\n   cheaper than a coupling nobody would notice breaking. */\n#define IEEE80211_SAE_CACHE 8\nstatic uint8_t ieee80211_sae_bssids[IEEE80211_SAE_CACHE][IEEE80211_ADDR_LEN];\nstatic uint8_t ieee80211_sae_count;\n\n/* The password as text. See patch_wpa3_sae: this is the only place in the\n   firmware where it exists in that form. */\nstatic uint8_t ieee80211_sae_pw[64];\nstatic sae_ctx ieee80211_sae;\n\nvoid ieee80211_sae_password(const uint8_t *password)\n{\n  uint32_t i = 0;\n  while(password[i] && i < sizeof(ieee80211_sae_pw) - 1) {\n    ieee80211_sae_pw[i] = password[i];\n    i++;\n  }\n  ieee80211_sae_pw[i] = 0;\n}\n\nstatic void ieee80211_sae_remember(const uint8_t *bssid)\n{\n  uint8_t i;\n  for(i=0;i<ieee80211_sae_count;i++)\n    if(memcmp(ieee80211_sae_bssids[i], bssid, IEEE80211_ADDR_LEN) == 0) return;\n  if(ieee80211_sae_count < IEEE80211_SAE_CACHE)\n    memcpy(ieee80211_sae_bssids[ieee80211_sae_count++], bssid,\n           IEEE80211_ADDR_LEN);\n}\n\nstatic int32_t ieee80211_sae_known(const uint8_t *bssid)\n{\n  uint8_t i;\n  for(i=0;i<ieee80211_sae_count;i++)\n    if(memcmp(ieee80211_sae_bssids[i], bssid, IEEE80211_ADDR_LEN) == 0) return 1;\n  return 0;\n}\n\n/* The bodies are further down, next to rt2501_auth: they call\n   ieee80211_associate(), ieee80211_mask_to_rate() and the transmit path,\n   none of which is declared this early in the file. The data has to be up\n   here because the scan and the auth dispatch both reach it. */\nstatic int32_t ieee80211_sae_begin(void);\nstatic void ieee80211_input_sae_auth(uint16_t seq, uint16_t status,\n                                     uint8_t *body, uint32_t blen);\n',
         'the wpa2 OUI constant'),
        ('                    if(*(current+IEEE80211_OUI_LEN-1) == IEEE80211_AUTH_PSK)\n                      found = 1;\n',
         '                    if(*(current+IEEE80211_OUI_LEN-1) == IEEE80211_AUTH_PSK)\n                      found = 1;\n                    /* Suite 8 is SAE. Accepting it here is what makes a\n                       WPA3 network a candidate at all: without it the scan\n                       returns IEEE80211_CRYPT_UNSUPPORTED and the network\n                       is never offered to the bytecode. */\n                    if(*(current+IEEE80211_OUI_LEN-1) == IEEE80211_AUTH_AKM_SAE) {\n                      found = 1;\n                      ieee80211_sae_remember(scan_result.bssid);\n                    }\n',
         'the AKM suite loop'),
        ('void rt2501_auth(const uint8_t *ssid, const uint8_t *mac,\n',
         '\n/* --------------------------------------------------- SAE, continued */\n\n/* nab_rand() is the firmware\'s non-allocating PRNG, seeded from the\n   millisecond timer (patch_no_libc_malloc). SAE\'s secrecy rests on these\n   bytes, and an xorshift seeded from a timer is a weak place to rest it --\n   written down here rather than discovered later. It is what this chip has:\n   there is no entropy source on a 2006 Nabaztag. */\nstatic void ieee80211_sae_rng(uint8_t *out, uint32_t len, void *arg)\n{\n  uint32_t i;\n  (void)arg;\n  for(i=0;i<len;i++) out[i] = (uint8_t)(nab_rand() >> 11);\n}\n\n/* CLR_WDT lives in inc/common.h but expands to WDTCON, which this\n   file does not otherwise reach. */\n#include "ml674061.h"\n\n/* The watchdog fires at about 2.09 s and a scalar multiplication is the\n   longest thing this firmware ever computes. */\nstatic void ieee80211_sae_tick(void) { CLR_WDT; }\n\n/* An authentication frame with a body, which the stock sender cannot do:\n   its struct ends after algorithm, sequence and status. */\nstatic int32_t ieee80211_send_auth_body(uint16_t seq, const uint8_t *body,\n                                        uint32_t blen)\n{\n  struct {\n    TXD_STRUC txd;\n    struct ieee80211_frame header;\n    uint8_t auth[6];\n  } *auth;\n  uint8_t *write_ptr;\n  uint16_t duration;\n  uint32_t frame_length;\n\n  disable_ohci_irq();\n  auth = hcd_malloc(sizeof(*auth) + blen + 7, COMRAM, 15);\n  enable_ohci_irq();\n  if(auth == NULL) {\n    DBG_WIFI("SAE: no COMRAM for the auth frame"EOL);\n    return 0;\n  }\n\n  auth->header.i_fc[0] = IEEE80211_FC0_TYPE_MGT|IEEE80211_FC0_SUBTYPE_AUTH\n      |(IEEE80211_FC0_VERSION_0 << IEEE80211_FC0_VERSION_SHIFT);\n  auth->header.i_fc[1] = IEEE80211_FC1_DIR_NODS;\n  memcpy(auth->header.i_addr1, ieee80211_assoc_mac, IEEE80211_ADDR_LEN);\n  memcpy(auth->header.i_addr2, rt2501_mac, IEEE80211_ADDR_LEN);\n  memcpy(auth->header.i_addr3, ieee80211_assoc_bssid, IEEE80211_ADDR_LEN);\n  auth->header.i_seq[0] = 0;\n  auth->header.i_seq[1] = 0;\n\n  write_ptr = auth->auth;\n  *(write_ptr++) = (IEEE80211_AUTH_ALG_SAE & 0x00ff);\n  *(write_ptr++) = (IEEE80211_AUTH_ALG_SAE & 0xff00) >> 8;\n  *(write_ptr++) = (seq & 0x00ff);\n  *(write_ptr++) = (seq & 0xff00) >> 8;\n  *(write_ptr++) = (IEEE80211_STATUS_SUCCESS & 0x00ff);\n  *(write_ptr++) = (IEEE80211_STATUS_SUCCESS & 0xff00) >> 8;\n  memcpy(write_ptr, body, blen);\n  write_ptr += blen;\n\n  frame_length = sizeof(struct ieee80211_frame)\n                 + (uint32_t)(write_ptr - auth->auth);\n  duration = rt2501_txtime(frame_length,\n      ieee80211_mask_to_rate(ieee80211_lowest_txrate)) + IEEE80211_SIFS;\n  auth->header.i_dur[0] = (duration & 0x00ff);\n  auth->header.i_dur[1] = (duration & 0xff00) >> 8;\n\n  /* The argument order matters and bit me: Rate, Length, QueIdx,\n     PacketId. Length went in as 0 and the length ended up in QueIdx,\n     so the commit left with a zero-length descriptor and the AP never\n     saw a frame -- the rabbit sat in AUTH, retrying for ever. And the\n     rate wants mask_to_rt2501rate, not mask_to_rate: one is the\n     hardware encoding and the other is the 802.11 one. */\n  rt2501_make_tx_descriptor(&auth->txd,\n      RT2501_CIPHER_NONE,                                   /* CipherAlg */\n      0,                                                    /* KeyTable */\n      0,                                                    /* KeyIdx */\n      1,                                                    /* Ack */\n      0,                                                    /* Fragment */\n      0,                                                    /* InsTimestamp */\n      1,                                                    /* RetryMode */\n      0,                                                    /* Ifs */\n      ieee80211_mask_to_rt2501rate(ieee80211_lowest_txrate), /* Rate */\n      frame_length,                                         /* Length */\n      0,                                                    /* QueIdx */\n      0);                                                   /* PacketId */\n\n  disable_ohci_irq();\n  if(!rt2501_tx(auth, sizeof(TXD_STRUC) + frame_length)) {\n    DBG_WIFI("SAE: TX error"EOL);\n    enable_ohci_irq();\n    return 0;\n  }\n  enable_ohci_irq();\n  return 1;\n}\n\n/* Build the password element and send our commit. */\nstatic int32_t ieee80211_sae_begin(void)\n{\n  uint8_t commit[SAE_COMMIT_LEN];\n\n  if(ieee80211_sae_pw[0] == 0) {\n    DBG_WIFI("SAE: no password captured"EOL);\n    return 0;\n  }\n  /* Taking over from rt2501_auth means taking over everything it does\n     after the point we hook: it tells the autoresponder the AP rates\n     and switches the radio to the AP channel just before building its\n     descriptor. Skipping those sent the commit while the radio was\n     still hopping through the scan, and the emulator counted it under\n     "transmitted into the void" -- six commits, no reply, the rabbit\n     stuck in AUTH for ever. */\n  rt2501_write(rt2501_dev, RT2501_TXRX_CSR5,\n               ieee80211_mask_to_rt2501mask(ieee80211_assoc_rateset));\n  rt2501_switch_channel(ieee80211_assoc_channel);\n\n  DBG_WIFI("SAE: deriving the password element"EOL);\n  if(!sae_commit(&ieee80211_sae, rt2501_mac, ieee80211_assoc_bssid,\n                 (const char *)ieee80211_sae_pw,\n                 ieee80211_sae_rng, NULL, ieee80211_sae_tick)) {\n    DBG_WIFI("SAE: could not build a commit"EOL);\n    sae_clear(&ieee80211_sae);\n    return 0;\n  }\n  sae_commit_bytes(&ieee80211_sae, commit);\n  if(!ieee80211_send_auth_body(IEEE80211_SAE_COMMIT, commit, sizeof commit)) {\n    sae_clear(&ieee80211_sae);\n    return 0;\n  }\n  DBG_WIFI("SAE: commit sent"EOL);\n  return 1;\n}\n\n/* One received SAE authentication frame. */\nstatic void ieee80211_input_sae_auth(uint16_t seq, uint16_t status,\n                                     uint8_t *body, uint32_t blen)\n{\n  if(status != IEEE80211_STATUS_SUCCESS) {\n    sprintf(dbg_buffer, "SAE: the AP refused us, status %u"EOL, status);\n    DBG_WIFI(dbg_buffer);\n    sae_clear(&ieee80211_sae);\n    ieee80211_state = IEEE80211_S_IDLE;\n    return;\n  }\n\n  if(seq == IEEE80211_SAE_COMMIT) {\n    uint8_t confirm[SAE_CONFIRM_LEN];\n    if(!sae_peer_commit(&ieee80211_sae, body, blen, ieee80211_sae_tick)) {\n      DBG_WIFI("SAE: the AP\'s commit is unusable"EOL);\n      sae_clear(&ieee80211_sae);\n      ieee80211_state = IEEE80211_S_IDLE;\n      return;\n    }\n    sae_confirm_bytes(&ieee80211_sae, confirm);\n    if(!ieee80211_send_auth_body(IEEE80211_SAE_CONFIRM, confirm,\n                                 sizeof confirm)) {\n      sae_clear(&ieee80211_sae);\n      ieee80211_state = IEEE80211_S_IDLE;\n      return;\n    }\n    DBG_WIFI("SAE: keys derived, confirm sent"EOL);\n    return;\n  }\n\n  if(seq == IEEE80211_SAE_CONFIRM) {\n    if(!sae_peer_confirm(&ieee80211_sae, body, blen)) {\n      /* The one place SAE tells us the password is wrong. A failed confirm\n         must not be retried with the same scalar, so drop to idle and let\n         the bytecode start over. */\n      DBG_WIFI("SAE: the confirm did not verify -- wrong password?"EOL);\n      sae_clear(&ieee80211_sae);\n      ieee80211_state = IEEE80211_S_IDLE;\n      return;\n    }\n    /* The whole point: the four-way handshake reads its PMK out of\n       ieee80211_key, so handing SAE\'s PMK over is a memcpy and the rest of\n       the association is unchanged. */\n    memcpy(ieee80211_key, ieee80211_sae.pmk, EAPOL_MASTER_KEY_LENGTH);\n    sae_clear(&ieee80211_sae);\n    eapol_init();\n    DBG_WIFI("SAE: accepted, associating"EOL);\n    ieee80211_associate();\n    return;\n  }\n\n  sprintf(dbg_buffer, "SAE: unexpected sequence %u"EOL, seq);\n  DBG_WIFI(dbg_buffer);\n}\n\nvoid rt2501_auth(const uint8_t *ssid, const uint8_t *mac,\n',
         'rt2501_auth, for the SAE bodies'),
        ('\tDBG_WIFI("Sending auth request"EOL);\n',
         '\t/* A WPA3 network needs SAE before association, and SAE is an\n\t   authentication-frame exchange of its own -- so this returns here\n\t   rather than falling through to the open-auth frame below. */\n\tif(ieee80211_sae_known(bssid)\n\t   && (ieee80211_encryption & 0xF0) == IEEE80211_CRYPT_WPA2) {\n\t\tieee80211_authmode = IEEE80211_AUTH_SAE;\n\t\tieee80211_state = IEEE80211_S_AUTH;\n\t\tieee80211_timeout = IEEE80211_AUTH_TIMEOUT;\n\t\tif(ieee80211_sae_begin()) return;\n\t\t/* Could not start: an AP in transition mode still takes PSK, so\n\t\t   try the ordinary path rather than giving up on the network. */\n\t\tDBG_WIFI("SAE: falling back to open auth + PSK"EOL);\n\t\tieee80211_authmode = IEEE80211_AUTH_OPEN;\n\t\tieee80211_state = IEEE80211_S_IDLE;\n\t}\n\n\tDBG_WIFI("Sending auth request"EOL);\n',
         'the auth request send point'),
        ('\t\t\t\t\tif(ieee80211_authmode == IEEE80211_AUTH_OPEN) {\n',
         '\t\t\t\t\tif(ieee80211_authmode == IEEE80211_AUTH_SAE) {\n\t\t\t\t\t\tif(((frame_current[0] << 0)|(frame_current[1] << 8))\n\t\t\t\t\t\t   != IEEE80211_AUTH_ALG_SAE) {\n\t\t\t\t\t\t\tDBG_WIFI("SAE: the AP replied with another algorithm"EOL);\n\t\t\t\t\t\t\tsae_clear(&ieee80211_sae);\n\t\t\t\t\t\t\tieee80211_state = IEEE80211_S_IDLE;\n\t\t\t\t\t\t\tbreak;\n\t\t\t\t\t\t}\n\t\t\t\t\t\tieee80211_input_sae_auth(\n\t\t\t\t\t\t\t(frame_current[2] << 0)|(frame_current[3] << 8),\n\t\t\t\t\t\t\t(frame_current[4] << 0)|(frame_current[5] << 8),\n\t\t\t\t\t\t\t&frame_current[6],\n\t\t\t\t\t\t\tlength-sizeof(struct ieee80211_frame)-6);\n\t\t\t\t\t} else if(ieee80211_authmode == IEEE80211_AUTH_OPEN) {\n',
         'the received-auth dispatch'),
        ('      *(write_ptr-1) = IEEE80211_AUTH_PSK;\n          /* RSN Capability */\n      *(write_ptr++) = 0x00;\n      *(write_ptr++) = 0x00;\n',
         '      /* Suite 8 when we got here through SAE, 2 otherwise. */\n      *(write_ptr-1) = (ieee80211_authmode == IEEE80211_AUTH_SAE)\n                       ? IEEE80211_AUTH_AKM_SAE : IEEE80211_AUTH_PSK;\n          /* RSN Capability. MFPC (0x0080) is mandatory with WPA3: an AP\n             with MFPR set refuses a station that does not claim it. We\n             claim it and do not yet enforce it on received frames -- see\n             the note in patch_wpa3_sae about BIP. */\n      *(write_ptr++) = (ieee80211_authmode == IEEE80211_AUTH_SAE) ? 0x80 : 0x00;\n      *(write_ptr++) = 0x00;\n',
         'the transmitted RSN IE'),
    ], "ieee80211_sae_remember", "SAE wired into scan, auth and association")

    print("[ok]   wpa3_sae: crypto, password hook and driver in place")


def patch_extbss(root: Path) -> None:
    """Zero every .bss.* and move the two 4 KB scratch buffers to ExtRAM.

    RedoXyde/nabgcc `6303737` (Leonard, 2026-08-28), ported rather than
    reinvented, and credited: sys/ml67q4051.ld, src/vm/vaudio.c and
    src/vm/vlog.c are his change verbatim in substance. Proven on his
    hardware 2026-07-31 -- both key slots valid after association, zero
    cipher errors, and a broadcast ping answered.

    pacifox's #27 finding 1 and the root of finding 2, and the general form
    of #25 finding 1.

    What is wrong without it. `-fdata-sections` gives every variable its own
    `.bss.NAME` input section, and the linker script collects only `*(.bss)`
    and `*(COMMON)`. So 85 sections land past `__bss_end__` and the startup
    zero-loop never reaches them: the entire EAPOL state (pmk, ptk, gtk,
    nonces, replay counter), the scan cipher cache, `usbhost_init_status`,
    `rec_state`, `play_state` all boot holding whatever the SRAM held at
    power-on. Association was a lottery and every replug re-rolled it.

    Why it could not simply be bracketed. Collecting `.bss.*` lifts
    `__bss_end__` to 0x10003208, and init.s reserves 0x1680 bytes of stacks
    from the top of a 16 KB SRAM, so the stacks start at 0x10002980 -- below
    it. .bss then sits on the stacks, which is the 2026-06-20 brick. The way
    out is to stop storing 8 KB of pure scratch in a 16 KB SRAM: the audio
    FIFO and vlog's `buffer_temp` go to a NOLOAD `.extbss` section in
    external RAM, at 0xD0000000, below the USB pool that `inc/utils/mem.h`
    puts at 0xD0008000 and well below the VM heap at 0xD0010000. That brings
    `__bss_end__` down to about 0x10001464 and gives the user stack its full
    4 KB back, which is finding 1.

    The location counter is saved into `_intram_end` and restored after the
    ExtRAM section. This is the subtle part and it is Leonard's: an output
    section in another region drags the location counter into that region,
    which would put `__heap_start__` in ExtRAM while `__heap_end__` stayed in
    IntRAM -- a heap spanning two regions, and `_sbrk` handing out memory
    forever without reaching its limit. preflight.py's check_memory_landmarks
    fails on an inverted heap for this reason.

    Not zeroed, deliberately: `.extbss` is NOLOAD and nothing clears it.
    Both buffers are written before they are read -- the FIFO by
    audioFifoPlay's producer, `buffer_temp` by sysSave -- and external RAM is
    not even addressable until setup_ext_sram_rom() has run. Put anything in
    there that is read before it is written and it is the 2026-06-20 class of
    bug again, one region over.
    """
    ld = root / "sys/ml67q4051.ld"
    s = ld.read_text()
    if ".extbss" in s:
        print(f"[skip] extbss: already applied")
        return

    # The region gains write permission (it holds .bss now) and shrinks to
    # the 32 KB that is genuinely free: the USB pool starts at 0xD0008000
    # (EXTRAM_ADDR) and the VM heap at 0xD0010000 (SRAM_BASE). A 1024 KB
    # region would let a future section be placed straight through both.
    old_region = "  ExtRAM  (rx) : ORIGIN = 0xD0000000, LENGTH = 1024k\n"
    new_region = (
        "  /* 0xD0000000-0xD0007FFF free, used by .extbss below;\n"
        "     0xD0008000 USB/hcd pool (EXTRAM_ADDR, 32k);\n"
        "     0xD0010000 VM heap (SRAM_BASE / _vmem_heap). */\n"
        "  ExtRAM  (rwx) : ORIGIN = 0xD0000000, LENGTH = 32k\n"
    )

    old_bss = "    *(.bss)\n    *(COMMON)\n"
    new_bss = (
        "    *(.bss)\n"
        "    *(.bss.*)   /* -fdata-sections puts every variable in its own\n"
        "                   .bss.NAME section; without this pattern they are\n"
        "                   orphans PAST __bss_end__ that the startup\n"
        "                   zero-loop never reaches -- the EAPOL state,\n"
        "                   usbhost_init_status, rec_state, play_state. */\n"
        "    *(COMMON)\n"
    )

    old_stack = "  /* Stack */\n"
    new_stack = (
        "  /* Everything below is IntRAM arithmetic -- the stack and newlib\n"
        "     heap bounds come off the location counter -- so remember it\n"
        "     here and put it back after the ExtRAM section. An output\n"
        "     section in another region moves the counter into that region,\n"
        "     which would send __heap_start__ to ExtRAM while __heap_end__\n"
        "     stayed in IntRAM, and _sbrk would hand out memory forever\n"
        "     without ever reaching its limit. */\n"
        "  _intram_end = . ;\n"
        "\n"
        "  /* Scratch buffers too big for a 16 KB SRAM. NOLOAD and never\n"
        "     zeroed, so nothing here may be read before it is written. */\n"
        "  .extbss (NOLOAD):\n"
        "  {\n"
        "    *(.extbss)\n"
        "  } >ExtRAM\n"
        "\n"
        "  . = _intram_end ;\n"
        "\n"
        "  /* Stack */\n"
    )

    for old, new, what in (
        (old_region, new_region, "ExtRAM region"),
        (old_bss, new_bss, "*(.bss.*) collected"),
        (old_stack, new_stack, ".extbss section"),
    ):
        if old not in s:
            sys.exit(f"FAIL {ld}: cannot place {what} (upstream changed?)")
        s = s.replace(old, new, 1)
    ld.write_text(s)
    print(f"[ok]   {ld}: .bss.* collected, .extbss in ExtRAM, heap kept in IntRAM")

    attr = ' __attribute__((section(".extbss")))'
    for rel, old, label in (
        ("src/vm/vaudio.c",
         "uint8_t audioFifoPlay[AUDIO_FIFOPLAY];",
         "audioFifoPlay"),
        ("src/vm/vlog.c",
         "static uint8_t  buffer_temp[4096];",
         "buffer_temp"),
    ):
        f = root / rel
        src = read_src(f)
        if ".extbss" in src:
            print(f"[skip] extbss: {rel} already moved")
            continue
        if old not in src:
            sys.exit(f"FAIL {f}: cannot find {label} to move to .extbss")
        new = old[:-1] + attr + ";  /* see .extbss in ml67q4051.ld */"
        f.write_text(src.replace(old, new, 1))
        print(f"[ok]   {f}: {label} -> .extbss (4 KB out of IntRAM)")


def patch_no_libc_malloc(root: Path) -> None:
    """Stop the network stack reaching libc's malloc, which lands on live globals.

    pacifox's #27 finding 2. The chain: eapol.c's randbuffer() and two IV
    fields in ieee80211.c call libc rand(); rand() calls malloc(24) on its
    first call to hold its state; malloc goes to _sbrk, whose heap base is
    `__heap_start__ = __bss_end__ = 0x1000033c`; and the orphaned .bss.*
    sections that linker_keep deliberately leaves unzeroed start at exactly
    that address. The first block therefore lands on counter_timer, rec_state
    and play_state.

    His first suggestion was to initialise _sbrk's cursor explicitly, and
    taken alone it makes this worse rather than better: the cursor is a
    .bss.* variable itself, so today the block lands wherever stale SRAM
    points, which is sometimes harmless. Pin the cursor to NULL and the
    corruption becomes reliable. So the fix has to be either moving the heap
    base -- which the VM heap sits on top of, see the note of 2026-06-21 --
    or removing the allocation. The second is much the smaller change.

    Three steps, and only the first is the fix:

    - nab_rand(), an xorshift32 that allocates nothing, replaces libc rand()
      at its three call sites. They are the only callers: main.c's MALLOC is
      usbh_malloc, and the bytecode's rand goes through sysRand.
    - _sbrk returns (void *)-1 instead of 0 when the heap is full, so a
      future allocator that does get there fails the way newlib expects
      rather than being handed address zero.
    - its cursor goes in .data, so if anything ever does allocate, where it
      lands is at least repeatable. Belt and braces, not the fix.

    A side effect worth having: libc rand() with no srand() returns the same
    sequence on every boot, so every rabbit of this model produced the same
    EAPOL SNonce sequence from cold. nab_rand seeds itself from the
    millisecond timer, which is thousands of ticks in by the time the radio
    associates.

    HISTORY, kept because the sequence is the lesson. Applied on its own on
    2026-10-25 it built a brick, and preflight.py passed it. It is applied
    again now, after `extbss`, which is the precondition it was missing.

    Removing the libc rand() call sites removes the libc objects that pulled
    malloc in, and that changes the set of input objects the linker sees. The
    85 orphaned `.bss.*` sections then land *inside* the `.bss` output section
    instead of after it, because nothing in the linker script says where they
    go -- `linker_keep` brackets only `*(.bss)` and `*(COMMON)`. Against the
    control build:

                         control          with this step
        __bss_end__      0x1000034c       0x10003208
        __heap_start__   0x1000034c       0x10003208
        __heap_end__     0x10002888       0x10002888   (heap inverted)
        __stack_end__    0x10002988       0x10002988   (bss over the stack)
        zero-loop        12 bytes         11 968 bytes (orphans swallowed)

    The last line is the 2026-06-20 brick: the zero-loop clears the region the
    VM heap lives in, and the rabbit fetches its bytecode then stalls before
    locate.jsp. `preflight.py` passed this image. That is the part worth
    remembering -- the gate was extended afterwards with
    `check_memory_landmarks`, which now refuses it on all three symptoms, and
    the only reason the bad layout was caught at all is that the control build
    existed to compare against.

    The orphan placement had to stop being an accident first. `extbss` does
    that -- it collects `.bss.*` into `.bss` explicitly and moves 8 KB of
    scratch buffers to external RAM so the result still fits under the stacks
    -- and a step-list guard now refuses this step unless `extbss` is present.
    With the layout pinned, removing the rand() calls changes no addresses.

    Worth keeping even though `extbss` already moves the heap off the
    globals: libc rand() without srand() returns the same sequence on every
    boot, so every rabbit of this model produced an identical EAPOL SNonce
    sequence from cold. nab_rand seeds itself from the millisecond timer.
    """
    h = root / "inc/common.h"
    src = read_src(h)
    if "nab_rand" in src:
        print("[skip] no_libc_malloc: already applied")
        return
    nl = "\r\n" if "\r\n" in src else "\n"
    # Before the include guard's own #endif, which is the last one in the
    # file. NOT next to a GPIO macro: those live inside
    # `#if PCB_RELEASE == ...` branches, and a declaration dropped into the
    # wrong branch is invisible to the build that is running -- which is
    # exactly what happened, and the compiler only warned about an implicit
    # declaration before the link failed.
    head, sep, tail = src.rpartition("#endif")
    if not sep:
        sys.exit(f"FAIL {h}: no include guard to insert before (upstream changed?)")
    src = head + nl.join([
        "/* A pseudo-random word that allocates nothing. libc's rand() mallocs",
        "   its state on the first call, and this firmware's heap base is the",
        "   address of live globals -- see apply-mods.py. */",
        "uint32_t nab_rand(void);",
        "",
        sep,
    ]) + tail
    write_src(h, src)
    print(f"[ok]   {h}: nab_rand declared")

    c = root / "src/utils/sys.c"
    src = read_src(c)
    nl = "\r\n" if "\r\n" in src else "\n"
    old = nl.join([
        "    if ((heap + incr) >= (uint8_t *)&__heap_end__) {",
        "        return 0;",
        "    }",
    ])
    new = nl.join([
        "    if ((heap + incr) >= (uint8_t *)&__heap_end__) {",
        "        /* newlib reads 0 as a valid address. (void *)-1 is how a",
        "           failed _sbrk is spelled, and the difference is whether a",
        "           caller writes to address zero or gets NULL from malloc. */",
        "        return (void *)-1;",
        "    }",
    ])
    if old not in src:
        sys.exit(f"FAIL {c}: _sbrk's exhaustion branch not found (upstream changed?)")
    src = src.replace(old, new, 1)

    old = "    static uint8_t *heap = NULL;"
    new = nl.join([
        "    /* In .data, so the cursor is a known value at boot: it is itself",
        "       a .bss.* variable, which linker_keep leaves unzeroed. This is",
        "       not the fix -- nothing should reach here at all -- it only",
        "       makes the remaining path repeatable. */",
        "    static uint8_t *heap __attribute__((section(\".data\"))) = NULL;",
    ])
    if old not in src:
        sys.exit(f"FAIL {c}: _sbrk's cursor declaration not found (upstream changed?)")
    src = src.replace(old, new, 1)

    marker = "extern void *_sbrk(int32_t incr)"
    if marker not in src:
        sys.exit(f"FAIL {c}: _sbrk not found (upstream changed?)")
    src = src.replace(marker, nl.join([
        "/**",
        " * @brief A pseudo-random word that allocates nothing.",
        " *",
        " * xorshift32, seeded from the millisecond timer on first use. libc's",
        " * rand() mallocs its state, and _sbrk below hands out the address of",
        " * live globals; this exists so the network stack never goes there.",
        " *",
        " * It also stops every rabbit of this model producing the same EAPOL",
        " * SNonce sequence from cold, which rand() without srand() did.",
        " */",
        "uint32_t nab_rand(void)",
        "{",
        "    static uint32_t s __attribute__((section(\".data\"))) = 0;",
        "",
        "    if (s == 0)",
        "        s = counter_timer | 1u;   /* xorshift must never hold zero */",
        "    s ^= s << 13;",
        "    s ^= s >> 17;",
        "    s ^= s << 5;",
        "    return s;",
        "}",
        "",
        marker,
    ]), 1)
    # counter_timer is declared in utils/delay.h, and sys.c includes nothing
    # of the project: it is newlib's hook file. One source of truth beats a
    # second extern, so the header comes in rather than a copy of the line.
    inc = "#include <stdint.h>"
    if inc not in src:
        sys.exit(f"FAIL {c}: cannot find where to add the include (upstream changed?)")
    if "utils/delay.h" not in src:
        src = src.replace(inc, nl.join([
            inc,
            "",
            '#include "utils/delay.h"   /* counter_timer, which seeds nab_rand */',
        ]), 1)
    write_src(c, src)
    print(f"[ok]   {c}: nab_rand added, _sbrk fails properly")

    for rel, pairs in (
        ("src/net/eapol.c", [(["    buffer[i] = rand() & 0xFF;"],
                              ["    buffer[i] = nab_rand() & 0xFF;"])]),
        ("src/net/ieee80211.c", [(["\tauth->txd.Iv = rand() & 0x00ffffff;"],
                                  ["\tauth->txd.Iv = nab_rand() & 0x00ffffff;"]),
                                 (["        fr->txd.Iv = rand() & 0x00ffffff;"],
                                  ["        fr->txd.Iv = nab_rand() & 0x00ffffff;"])]),
    ):
        f = root / rel
        src = read_src(f)
        nl = "\r\n" if "\r\n" in src else "\n"
        for old_lines, new_lines in pairs:
            old = nl.join(old_lines)
            if old not in src:
                sys.exit(f"FAIL {f}: {old_lines[0].strip()!r} not found (upstream changed?)")
            src = src.replace(old, nl.join(new_lines), 1)
        write_src(f, src)
        print(f"[ok]   {f}: uses nab_rand")


def patch_bounded_waits(root: Path) -> None:
    """Stop three waits from feeding the watchdog for ever.

    pacifox's #27 finding 3 and #25's second half, same shape in both: a loop
    that waits for hardware and pets the watchdog while it waits. The watchdog
    is armed at about 2.09 s and exists precisely to recover a wedged rabbit;
    a wait that keeps clearing it turns every one of these into a permanent
    freeze instead of a reset. The defect is not the wait. It is the CLR_WDT
    inside it.

      - hal/audio.c's vlsi_read_sci and vlsi_write_sci:
        `while( !(INT_AUDIO_READ & INT_AUDIO_BIT) ) CLR_WDT;`
        If the VS1003 never raises DREQ -- a wedged codec, a corrupt stream --
        the rabbit stops here for good.
      - usb/rt2501usb.c's rt2501_setup: `while(1)` for BbpRfStatus with
        DelayMs(1000), and `while(1)` for BBP_R0 with DelayMs(500). DelayMs
        clears the watchdog in its own loop, so a radio that never reports
        ready hangs the boot.

    The two are fixed differently, because what is available differs.

    The RT2501 loops get a bound and `return 0`, which is what the function
    already does elsewhere and what its caller is written for: usbh_rt2501
    deletes the pipes and returns NULL, and the enumeration starts again. Ten
    attempts is ten seconds on the first loop and five on the second.

    The DREQ waits stop petting instead. A count of iterations, not a budget
    in milliseconds: vlsi_read_sci runs with interrupts masked in places --
    play_check wraps its drain in disable_ohci_irq -- and a deadline read from
    a clock that is not advancing would pet the watchdog for ever, which is
    the bug. So it pets a bounded number of times and then spins without
    petting, and the watchdog does its job about two seconds later. At a few
    tens of nanoseconds an iteration, 200,000 of them is several milliseconds
    of patience, which is far longer than any DREQ this chip raises in good
    health and far shorter than the watchdog's own patience.

    Not covered here, deliberately: ieee80211.c's transmit loop on hcd_malloc
    (#25). That one has no CLR_WDT, so the watchdog already catches it -- it
    only looked unrecoverable while #25's first half left the radio asleep
    after a reset, and cold_state fixed that.
    """
    c = root / "src/hal/audio.c"
    src = read_src(c)
    if "VLSI_DREQ_PETS" in src:
        print("[skip] bounded_waits: already applied")
        return
    nl = "\r\n" if "\r\n" in src else "\n"

    wait = "  while( !(INT_AUDIO_READ & INT_AUDIO_BIT) ) CLR_WDT;"
    bounded = nl.join([
        "  /* Pet the watchdog for a bounded while, then let it have us: a",
        "     codec that never raises DREQ used to freeze the rabbit for good",
        "     precisely because this loop kept the watchdog quiet. */",
        "  {",
        "    uint32_t dreq_pets = VLSI_DREQ_PETS;",
        "    while( !(INT_AUDIO_READ & INT_AUDIO_BIT) )",
        "      if(dreq_pets) { dreq_pets--; CLR_WDT; }",
        "  }",
    ])
    if src.count(wait) != 2:
        sys.exit(f"FAIL {c}: expected 2 DREQ waits, found {src.count(wait)}")
    src = src.replace(wait, bounded)

    # Above the doxygen block, not between it and the function: inserted
    # after the comment, doxygen attaches it to the macro instead.
    marker = nl.join(["/**",
                      " * @brief Read a register from the VLSI chip, using SPI"])
    if marker not in src:
        sys.exit(f"FAIL {c}: vlsi_read_sci's comment not found (upstream changed?)")
    src = src.replace(marker, nl.join([
        "/* How many times a DREQ wait may clear the watchdog before it stops.",
        "   Iterations rather than milliseconds, on purpose: see apply-mods.py. */",
        "#define VLSI_DREQ_PETS 200000u",
        "",
        marker,
    ]), 1)
    write_src(c, src)
    print(f"[ok]   {c}: the two DREQ waits stop feeding the watchdog")

    c = root / "src/usb/rt2501usb.c"
    src = read_src(c)
    nl = "\r\n" if "\r\n" in src else "\n"
    for name, old_lines, new_lines in (
        ("BbpRfStatus",
         ['  while(1) {',
          '    DBG_WIFI(".");',
          '    csr12.word = rt2501_read(rt2501_dev, RT2501_MAC_CSR12);',
          '    if(csr12.field.BbpRfStatus) break;',
          '    /* Force wake-up */',
          '    if(!rt2501_write(rt2501_dev, RT2501_MAC_CSR12, 0x4)) return 0;',
          '    DelayMs(1000);',
          '  }'],
         ['  {',
          '    /* Bounded: DelayMs clears the watchdog, so a radio that never',
          '       reports ready used to hang the boot with nothing to recover',
          '       it. Returning 0 makes the caller delete the pipes and start',
          '       the enumeration again. */',
          '    int32_t tries = RT2501_READY_TRIES;',
          '    while(1) {',
          '      DBG_WIFI(".");',
          '      csr12.word = rt2501_read(rt2501_dev, RT2501_MAC_CSR12);',
          '      if(csr12.field.BbpRfStatus) break;',
          '      if(--tries <= 0) {',
          '        DBG_WIFI("the radio never reported ready"EOL);',
          '        return 0;',
          '      }',
          '      /* Force wake-up */',
          '      if(!rt2501_write(rt2501_dev, RT2501_MAC_CSR12, 0x4)) return 0;',
          '      DelayMs(1000);',
          '    }',
          '  }']),
        ("BBP_R0",
         ['  while(1) {',
          '    if(rt2501_read_bbp(rt2501_dev, RT2501_BBP_R0) != 0) break;',
          '    DelayMs(500);',
          '    DBG_WIFI(".");',
          '  }'],
         ['  {',
          '    int32_t tries = RT2501_READY_TRIES;',
          '    while(1) {',
          '      if(rt2501_read_bbp(rt2501_dev, RT2501_BBP_R0) != 0) break;',
          '      if(--tries <= 0) {',
          '        DBG_WIFI("the BBP never came up"EOL);',
          '        return 0;',
          '      }',
          '      DelayMs(500);',
          '      DBG_WIFI(".");',
          '    }',
          '  }']),
    ):
        old = nl.join(old_lines)
        if old not in src:
            sys.exit(f"FAIL {c}: the {name} loop was not found (upstream changed?)")
        src = src.replace(old, nl.join(new_lines), 1)

    marker = "static int32_t rt2501_setup(void)"
    if marker not in src:
        sys.exit(f"FAIL {c}: rt2501_setup not found (upstream changed?)")
    src = src.replace(marker, nl.join([
        "/* Attempts before rt2501_setup gives up and lets the enumeration",
        "   start again: ten seconds on the first wait, five on the second. */",
        "#define RT2501_READY_TRIES 10",
        "",
        marker,
    ]), 1)
    write_src(c, src)
    print(f"[ok]   {c}: the two bring-up waits give up instead of hanging")


def patch_audio_halfduplex(root: Path) -> None:
    """Stop a capture to play a sound, and tell the bytecode when one is refused.

    The rabbit is half-duplex and says nothing about it. hal/audio.c's
    play_start() returns at once when rec_state is set, and rec_start() returns
    at once when play_state is set; both return void, and vinterp.c's
    OPplayStart and OPrecStart push a literal 0 whatever happened. So a sound
    pushed while the microphone is streaming is fetched over HTTP in full,
    answered 200, and never heard -- and the bytecode goes on waiting for a
    play callback that will not come. Reported by pacifox as #27 finding 4,
    measured in the emulator as #23 finding 5, where it leaves the rabbit mute
    until something stops the capture or it is unplugged.

    Two changes, and the first is the one that fixes it:

    - play_start() stops the capture instead of refusing. A sound is something
      the server asked for; a capture is a background state. It is also the
      policy the add-on already assumes -- its wake path stops the mic by hand
      before it speaks, with the comment "Half-duplex: stop the mic so the
      speaker is free". The firmware now agrees with it.
    - both return a status, and the two opcodes push it instead of 0. Nothing
      reads it yet, so on its own this fixes nothing; it is what a bytecode
      would need in order to stop waiting on a sound that never started.
      Success stays 0, so bytecode that does look at the value sees exactly
      what it saw before.

    rec_start keeps its guard the other way round: refusing to start a capture
    while a sound plays is the right call, and it is the direction a server can
    recover from, since it knows it asked.

    NAB_AUDIO_STATUS is defined so a host build -- the emulator's stubs -- can
    follow either shape and still measure the before and after.
    """
    def edit(rel, pairs, note):
        """Apply line-ending-safe edits to one file.

        read_src/write_src, and every multi-line anchor built from the
        file's own newline: all five of these sources are CRLF, and a
        plain read_text/write_text rewrites the whole file as LF. That
        turns a six-line change into a 2000-line diff nobody can review,
        and it is how `patches/wpa2-scanned-ciphers.patch` stopped
        applying once (#46).
        """
        f = root / rel
        src = read_src(f)
        nl = "\r\n" if "\r\n" in src else "\n"
        for old_lines, new_lines in pairs:
            old = nl.join(old_lines)
            if old not in src:
                sys.exit(f"FAIL {f}: anchor not found (upstream changed?)"
                         f"{nl}  {old_lines[0]!r}")
            src = src.replace(old, nl.join(new_lines), 1)
        write_src(f, src)
        print(f"[ok]   {f}: {note}")

    probe = read_src(root / "inc/hal/audio.h")
    if "NAB_AUDIO_STATUS" in probe:
        print("[skip] audio_halfduplex: already applied")
        return

    edit("inc/hal/audio.h", [
        (["void play_start(int32_t trytofeed);"],
         ["/* 0 if the sound started, non-zero if it could not. */",
          "int32_t play_start(int32_t trytofeed);"]),
        (["void rec_start(uint16_t sampling_frequency,uint16_t gain);"],
         ["/* 0 if the capture started, non-zero if a sound is playing. */",
          "int32_t rec_start(uint16_t sampling_frequency,uint16_t gain);"]),
        (["void play_check(int32_t nocb);"],
         ["/* Defined so a host build can follow either shape of the two",
          "   starts above and still measure the before and the after. */",
          "#define NAB_AUDIO_STATUS 1",
          "void play_check(int32_t nocb);"]),
    ], "play_start/rec_start return a status")

    edit("src/hal/audio.c", [
        (["void play_start(int32_t trytofeed)",
          "{",
          "  // Recording something, exit",
          "  if (rec_state)",
          "    return;",
          '  DBG_AUD("play_start"EOL);'],
         ["int32_t play_start(int32_t trytofeed)",
          "{",
          "  /* The speaker wins. This used to return here in silence, which",
          "     left a rabbit that had been asked to record mute for good:",
          "     every later sound was fetched, answered 200 and never played,",
          "     with nothing anywhere saying so. */",
          "  if (rec_state)",
          "  {",
          '    DBG_AUD("play_start: stopping the capture, the speaker wins"EOL);',
          "    rec_stop();",
          "  }",
          '  DBG_AUD("play_start"EOL);']),
        (["  vlsi_ampli(1); // Turn ampli ON",
          "  play_state=1;",
          "}"],
         ["  vlsi_ampli(1); // Turn ampli ON",
          "  play_state=1;",
          "  return 0;",
          "}"]),
        (["void rec_start(uint16_t sampling_frequency,uint16_t gain)",
          "{",
          "  // Playing something, exit",
          "  if (play_state)",
          "    return;"],
         ["int32_t rec_start(uint16_t sampling_frequency,uint16_t gain)",
          "{",
          "  /* A sound keeps the decoder: refusing here is right, and it is",
          "     the direction a server can recover from, since it knows it",
          "     asked. What was missing was saying so. */",
          "  if (play_state)",
          "    return 1;"]),
        (["  init_adpcm_encode(sampling_frequency,gain);",
          "  rec_state=1;  // Update state",
          "}"],
         ["  init_adpcm_encode(sampling_frequency,gain);",
          "  rec_state=1;  // Update state",
          "  return 0;",
          "}"]),
    ], "play_start stops a capture, both report what happened")

    edit("inc/vm/vaudio.h", [
        (["void audioPlayStart(int32_t freq,int32_t bps,int32_t stereo,int32_t trytofeed);"],
         ["int32_t audioPlayStart(int32_t freq,int32_t bps,int32_t stereo,int32_t trytofeed);"]),
    ], "audioPlayStart returns a status")

    edit("src/vm/vaudio.c", [
        (["void audioPlayStart(int32_t freq,int32_t bps,int32_t stereo,int32_t trytofeed)"],
         ["int32_t audioPlayStart(int32_t freq,int32_t bps,int32_t stereo,int32_t trytofeed)"]),
        (["  play_w=play_r=0;",
          "  play_start(trytofeed);",
          "}"],
         ["  play_w=play_r=0;",
          "  return play_start(trytofeed);",
          "}"]),
        (["  rec_start(freq,gain);",
          "  return 0;",
          "}"],
         ["  return rec_start((uint16_t)freq,(uint16_t)gain);",
          "}"]),
    ], "both starts hand their status up")

    edit("src/vm/vinterp.c", [
        (["          k=VALTOINT(VPULL());",
          "          VPUSH(INTTOVAL(0));",
          "          audioPlayStart(44100,16,2,k);"],
         ["          k=VALTOINT(VPULL());",
          "          /* The real status, not a literal 0: a sound refused",
          "             because the microphone has the decoder used to look",
          "             exactly like one that started. */",
          "          VPUSH(INTTOVAL(audioPlayStart(44100,16,2,k)));"]),
        (["          freq=VALTOINT(VPULL());",
          "          VPUSH(INTTOVAL(0));",
          "          audioRecStart(freq,gain);"],
         ["          freq=VALTOINT(VPULL());",
          "          VPUSH(INTTOVAL(audioRecStart(freq,gain)));"]),
    ], "OPplayStart/OPrecStart push what actually happened")


def patch_audio_fifo_data(root: Path) -> None:
    """Force the play-FIFO indices play_w/play_r into .data (init 0).

    With .bss left un-zeroed (see patch_linker_keep), these globals hold garbage
    at boot. audioPlayFetchByte() reads them BEFORE audioPlayStart() inits them,
    so a garbage index reads out of audioFifoPlay[] -> data abort (the original
    brick, triggered by the connection jingle). Putting them in .data (which the
    cstartup DOES copy/init from flash) makes the FIFO start empty, so the fetch
    returns "empty" instead of faulting. Surgical fix that preserves the VM heap
    the bytecode loader needs. Found 2026-06-21 via JTAG on the real rabbit.
    """
    p = root / "src/vm/vaudio.c"
    s = p.read_text()
    old = "int32_t play_w;\nint32_t play_r;"
    new = ('int32_t play_w __attribute__((section(".data")))=0;\n'
           'int32_t play_r __attribute__((section(".data")))=0;')
    if new in s:
        print(f"[skip] {p}: play_w/play_r already in .data")
        return
    if old not in s:
        sys.exit(f"FAIL {p}: play_w/play_r declarations not found (upstream changed?)")
    p.write_text(s.replace(old, new, 1))
    print(f"[ok]   {p}: play_w/play_r forced into .data (audio FIFO safe without .bss zero)")


def patch_eapol_replay(root: Path) -> None:
    """Commit the EAPOL replay counter after the MIC is verified, not before.

    `eapol_input()` compares the incoming counter with the local one and then
    copies it over — before any handler has checked the frame's MIC. So one
    malformed or stray EAPOL-Key frame carrying a high counter raises the
    local counter permanently, and every genuine frame afterwards fails the
    `<= 0` comparison. Group rekeys stop and the link is eventually lost.

    Reported by pacifox in ClaraVnk/nabaztag#26, finding 2. The order is
    wrong regardless of whether anyone has hit it: a value taken from an
    unauthenticated frame should not outlive that frame.

    The comparison stays where it is — rejecting a replay early is correct
    and cheap. Only the commit moves:

      * message 1/4 carries no MIC at all (the dispatcher routes to it only
        when `key_mic == 0`), so there is nothing to wait for and it commits
        on entry — exactly as safe as today;
      * message 3/4 commits after `MIC OK`, before it sets EAPOL_S_MSG3;
      * the group message commits after its own MIC check.

    `replay_pending` is written by the dispatcher before any handler runs, so
    it is never read stale.
    """
    p = root / "src/net/eapol.c"
    s = p.read_text()
    if "eapol_commit_replay" in s:
        print(f"[skip] {p}: replay counter already committed after the MIC")
        return

    old_commit = (
        '  /* Update local replay counter */\n'
        '  memcpy(replay_counter, fr->key_frame.replay_counter, EAPOL_RPC_LENGTH);\n'
        '  replay_active = 1;\n'
        '  DBG_WIFI("Replay counter OK"EOL);'
    )
    new_commit = (
        '  /* Remember it, but do NOT commit yet: nothing has verified this\n'
        '     frame\'s MIC. One stray frame with a high counter would otherwise\n'
        '     lock out every genuine one that follows. Each handler commits\n'
        '     after its own check. */\n'
        '  memcpy(replay_pending, fr->key_frame.replay_counter, EAPOL_RPC_LENGTH);\n'
        '  DBG_WIFI("Replay counter OK"EOL);'
    )
    if old_commit not in s:
        sys.exit(f"FAIL {p}: replay commit block not found (upstream changed?)")
    s = s.replace(old_commit, new_commit, 1)

    old_decl = "static uint8_t replay_counter[EAPOL_RPC_LENGTH];"
    if old_decl not in s:
        sys.exit(f"FAIL {p}: replay_counter declaration not found")
    s = s.replace(old_decl,
                  old_decl + "\nstatic uint8_t replay_pending[EAPOL_RPC_LENGTH];", 1)

    anchor = "static void eapol_input_msg1(uint8_t *frame, uint32_t length)"
    if anchor not in s:
        sys.exit(f"FAIL {p}: eapol_input_msg1 not found")
    helper = (
        "/* Accept the replay counter of the frame being handled. Called only\n"
        "   once a handler has verified that frame — see patch_eapol_replay. */\n"
        "static void eapol_commit_replay(void)\n"
        "{\n"
        "  memcpy(replay_counter, replay_pending, EAPOL_RPC_LENGTH);\n"
        "  replay_active = 1;\n"
        "}\n\n"
    )
    s = s.replace(anchor, helper + anchor, 1)
    s = s.replace(
        anchor + "\n{",
        anchor + "\n{\n  /* No MIC on message 1/4 — nothing to wait for. */\n"
        "  eapol_commit_replay();",
        1,
    )

    old_m3 = '  DBG_WIFI("MIC OK"EOL);\n\n  eapol_state = EAPOL_S_MSG3;'
    if old_m3 not in s:
        sys.exit(f"FAIL {p}: msg3 MIC-OK site not found")
    s = s.replace(old_m3,
                  '  DBG_WIFI("MIC OK"EOL);\n  eapol_commit_replay();\n\n'
                  '  eapol_state = EAPOL_S_MSG3;', 1)

    old_g = '  //DBG_WIFI("MIC OK"EOL);'
    if old_g not in s:
        sys.exit(f"FAIL {p}: group MIC-OK site not found")
    s = s.replace(old_g, old_g + "\n  eapol_commit_replay();", 1)

    p.write_text(s)
    print(f"[ok]   {p}: replay counter committed after the MIC, not before")
def patch_cold_state(root: Path) -> None:
    """Make the seven "have I already done this" guards survive a soft reset.

    `patch_linker_keep` leaves `.bss.*` orphans outside the range the startup
    loop zeroes — deliberately, to keep the VM heap out of it. A watchdog
    reset (`sysReboot()`) does not clear SRAM, so every one of those orphans
    carries its value from the previous lifetime into the next boot.

    That is fine for a buffer. It is not fine for a flag whose whole job is
    to say "this subsystem is already up", because on the boot after a soft
    reset the subsystem is NOT up and the flag says it is.

    pacifox found the first one in ClaraVnk/nabaztag#25:
    `usbhost_init_status` makes `usbhost_init()` return before `hcd_init()`,
    so the OHCI side is never programmed and the RT2501 is never enumerated —
    the orange belly that `releases/README.md` documents as normal and that
    only a power cycle clears.

    Reading the symbol table of a `lean` build shows it is one of seven. Only
    12 bytes are zeroed at startup (`__bss_start__` 0x10000330 →
    `__bss_end__` 0x1000033c); 90 symbols land outside, and these seven are
    read as guards before anything writes them:

        usbhost_init_status  0x10000fb4  usbhcore.c:60    hcd_init() skipped
        usbctrl_state        0x10000fb0  usbctrl.c:240    controller "active"
        rec_state            0x10000348  audio.c:323      play_start() returns
        play_state           0x10000349  audio.c:263      rec_start() returns
        eapol_state          0x1000054c  eapol.c:348      msg 1/4 rejected
        replay_active        0x10000528  eapol.c:905      every EAPOL frame
        ieee80211_state      0x10000688  ieee80211.c:1042 stale link state

    Four of them produce the power cycle independently. `eapol_state` stuck
    at EAPOL_S_RUN rejects the first message 1/4 of the next handshake, so
    the rabbit cannot rejoin WPA2. `rec_state` makes a rabbit that associates
    perfectly and never speaks again — which fits the reports that never fit
    the USB story.

    The initialisers are the values a cold boot gets today, so nothing
    changes for a rabbit that was unplugged: the enum starts at
    `EAPOL_S_MSG1` and `IEEE80211_S_IDLE`, both zero, which is what the
    zeroed `.bss` used to hand them. This only changes what a *soft* reset
    hands them.

    The fix is the one this file already uses for `play_w`/`play_r`
    (`patch_audio_fifo_data`): force them into `.data`, which cstartup DOES
    copy from flash on every boot. No code in `main()`, no ordering to get
    right, and it works for the `static` one too. Costs 16 bytes of flash.
    """
    data = '__attribute__((section(".data")))'
    edits = [
        ("src/usb/usbhcore.c",
         "uint8_t usbhost_init_status = 0;",
         f"uint8_t usbhost_init_status {data} = 0;"),
        ("src/usb/usbctrl.c",
         "uint32_t usbctrl_state = 0;",
         f"uint32_t usbctrl_state {data} = 0;"),
        ("src/hal/audio.c",
         "uint8_t play_state=0,\n        rec_state=0;",
         f"uint8_t play_state {data} =0;\n"
         f"uint8_t rec_state {data} =0;"),
        ("src/net/eapol.c",
         "eapol_state_t eapol_state;",
         f"eapol_state_t eapol_state {data} = EAPOL_S_MSG1;"),
        ("src/net/eapol.c",
         "static uint8_t replay_active;",
         f"static uint8_t replay_active {data} = 0;"),
        # The section goes on the *declaration* in the header, not on the
        # definition in the .c: `patches/wpa2-scanned-ciphers.patch` has
        # context a few lines below that definition, and editing it there
        # makes the patch stop applying. A section attribute on a prior
        # declaration carries to the tentative definition that follows.
        ("inc/net/ieee80211.h",
         "extern int32_t ieee80211_state;",
         f"extern int32_t ieee80211_state {data};"),
    ]
    done, skipped = 0, 0
    for rel, old, new in edits:
        p = root / rel
        s = p.read_text()
        if new in s:
            skipped += 1
            continue
        if old not in s:
            sys.exit(f"FAIL {p}: declaration not found (upstream changed?)\n  {old!r}")
        p.write_text(s.replace(old, new, 1))
        done += 1
    # Six edits for seven guards: play_state and rec_state share one
    # declaration upstream and the same replacement splits them in two.
    # Counting edits and calling them guards printed "6" for a patch whose
    # own docstring says seven, which is the kind of small lie that makes
    # someone re-read a working patch looking for the missing one.
    print(f"[ok]   cold state: {done} of {len(edits)} edit(s) applied, "
          f"covering seven guards"
          + (f" ({skipped} already there)" if skipped else ""))


def patch_gc_sections(root: Path) -> None:
    """Enable --gc-sections in the linker so dead code (the ~6 KB of
    newlib printf glue made unreachable by patch_makefile's DEBUG_*
    strip, plus other unused TweetNaCl entry points, plus dead helpers)
    actually gets dropped from the final image.

    REQUIRES the `patches/0001-ld-keep-vector-and-startup.patch` to be
    applied first (it wraps .intvec / .startup.* / .bytecode.* /
    .ramfunc in KEEP() so gc-sections can't prune them — those sections
    are referenced only by hardware, not by any C call chain).

    -ffunction-sections / -fdata-sections are already present upstream
    in CFLAGS, so we only need to add `-Wl,--gc-sections` to LDFLAGS.
    Idempotent: the check anchors on a LIVE (uncommented) flag, not
    just the substring (upstream ships a commented `#~ LDFLAGS +=
    -Wl,--gc-sections` line that a naive substring check would match).
    """
    p = root / "Makefile"
    s = p.read_text()
    has_live_gc = any(
        line.lstrip().startswith("LDFLAGS")
        and "--gc-sections" in line
        and not line.lstrip().startswith("#")
        for line in s.splitlines()
    )
    if has_live_gc:
        print(f"[skip] {p}: --gc-sections already live")
        return

    # Sanity: the KEEP patch must have landed on the linker script. If
    # KEEP isn't present we'd risk pruning the vector table → IRQ crash.
    ld = root / "sys/ml67q4051.ld"
    if "KEEP(*(.intvec))" not in ld.read_text():
        sys.exit(
            f"FAIL {p}: refusing to enable --gc-sections because "
            f"{ld} has no KEEP(*(.intvec)). Apply "
            f"patches/0001-ld-keep-vector-and-startup.patch first."
        )

    s = s.replace(
        "LDFLAGS += -Wl,-Map",
        "LDFLAGS += -Wl,--gc-sections\nLDFLAGS += -Wl,-Map",
        1,
    )
    p.write_text(s)
    print(f"[ok]   {p}: added -Wl,--gc-sections (KEEP-protected sections won't be pruned)")


def patch_bootloader(root: Path) -> None:
    """Modify httpflash in the Metal bootloader to require a valid Ed25519
    signature before calling flashFirmware. Unsigned or tampered .sim are
    refused with a red LED + the error page."""
    p = root / "mtl/boot/boot.0.0.0.13.mtl"
    s = p.read_text()
    if "verifySig" in s:
        print(f"[skip] {p}: verifySig already wired in")
        return

    # 1a. Add the sig marker constant right after the firmwarelimit one.
    marker_anchor = 'const firmwarelimit="-violet-";;\n'
    if marker_anchor not in s:
        sys.exit(f"FAIL {p}: firmwarelimit anchor not found")
    s = s.replace(
        marker_anchor,
        marker_anchor + 'const sigmarker="-sig-";;\n',
        1,
    )

    # 1b. Insert getsigreq helper right BEFORE getfirmware (which is right
    # AFTER getbinary, so forward-references are satisfied). The Metal
    # compiler is single-pass and doesn't allow forward references.
    getfirmware_anchor = 'fun getfirmware req=\n'
    if getfirmware_anchor not in s:
        sys.exit(f"FAIL {p}: getfirmware anchor not found")
    helper = (
        '// Extract the 64-byte Ed25519 signature appended to a signed .sim\n'
        '// (-violet-...payload...-violet--sig-<128 hex>-sig-). Returns nil if\n'
        '// the trailer is missing or malformed.\n'
        'fun getsigreq req=\n'
        '\tlet strstr req sigmarker 0 -> i0 in\n'
        '\tif i0!=nil then let i0+5->i1 in\n'
        '\tlet strstr req sigmarker i1 -> i2 in\n'
        '\tif i2!=nil then\n'
        '\tif i2-i1==128 then\n'
        '\tlet strnew 64 -> sig in\n'
        '\t(\n'
        '\t\tgetbinary sig req 0 i1 64;\n'
        '\t\tsig\n'
        '\t);;\n\n'
    )
    s = s.replace(getfirmware_anchor, helper + getfirmware_anchor, 1)

    # 2. Replace the body of httpflash to require sig verification.
    old_httpflash = (
        'fun httpflash req=\n'
        '\tSecholn "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFf";\n'
        '//\tSecholn req;\n'
        '\tlet getfirmware req -> firm in\n'
        '\tif firm!=nil then\n'
        '\t(\n'
        '//\t\tdump firm;\n'
        '\t\tSecholn "######### firmware found";\n'
        '\t\tsetleds 0xffffff;\n'
        '\t\tflashFirmware firm 0x13fb6754 0x0407FE58;\n'
        '\t\tnil\n'
        '\t)\n'
        '\telse\n'
        '\t(\n'
        '\t\tsetleds 0xff;\n'
        '\t\tpage_error\n'
        '\t);;\n'
    )
    new_httpflash = (
        'fun httpflash req=\n'
        '\tSecholn "httpflash: signed .sim required";\n'
        '\tlet getfirmware req -> firm in\n'
        '\tlet getsigreq req -> sig in\n'
        '\tif firm!=nil then\n'
        '\tif sig!=nil then\n'
        '\tif (verifySig firm sig)!=0 then\n'
        '\t(\n'
        '\t\tSecholn "######### sig OK, flashing";\n'
        '\t\tsetleds 0xffffff;\n'
        '\t\tflashFirmware firm 0x13fb6754 0x0407FE58;\n'
        '\t\tnil\n'
        '\t)\n'
        '\telse\n'
        '\t(\n'
        '\t\tSecholn "######### sig BAD, refusing";\n'
        '\t\tsetleds 0xff;\n'
        '\t\tpage_error\n'
        '\t);;\n'
    )
    if old_httpflash not in s:
        sys.exit(f"FAIL {p}: original httpflash body not matched. Did upstream change?")
    s = s.replace(old_httpflash, new_httpflash, 1)
    p.write_text(s)
    print(f"[ok]   {p}: httpflash now requires a valid Ed25519 signature")


def patch_wpa2_group_key(root: Path) -> None:
    """Make WPA2's group key actually work.

    Upstream nabgcc ships src/net/aes128.c as a stub — every function is
    `(void)` casts and nothing else — while eapol.c calls aes128_decrypt() to
    recover the group key of a CCMP network, under a comment reading "FIXME
    Actually do something". Nothing fails: the pairwise key is derived
    correctly, the MIC verifies, the rabbit associates and unicast works. But
    the group key installed into the radio is whatever was in memory, so the
    rabbit is deaf to every broadcast and multicast frame for the whole life
    of the association — and peers age it out of their ARP caches and re-ARP
    by broadcast into the void.

    On top of that, eapol_input_msg3() ignored the incoming key data, and WPA2
    delivers the GTK *in message 3*.

    Both the crypto (crypto/aes128.c, COPY'd by the Dockerfile) and the logic
    inserted here are uplg/nabgcc's, from its wpa23-gtk-r2 branch (MIT; text
    in upstream/UPLG-LICENSE). Taken rather than paraphrased, deliberately:
    the first attempt at this patch rewrote their logic from their header
    comment and put the msg3 install BEFORE the state transitions instead of
    after. That build did not associate.
    """
    p = root / "src/net/eapol.c"
    src = p.read_text(encoding="utf-8", errors="surrogateescape")
    if "eapol_install_msg3_gtk" in src:
        print("  eapol.c: already patched")
        return
    if '#include "net/aes128.h"' not in src:
        raise SystemExit("eapol.c no longer includes net/aes128.h — "
                         "aes_key_unwrap and rsn_find_gtk would not resolve")

    helper = '''
#define EAPOL_KEYDATA_MAX 128

/**
 * @brief Unwrap and install the GTK from message 3/4 Key Data (CCMP)
 *
 * From uplg/nabgcc (MIT), branch wpa23-gtk-r2. Key Data is AES-Key-Wrapped
 * (RFC 3394) under the KEK and holds an RSN KDE list from which the GTK KDE
 * is picked. Any failure leaves the group slot untouched and returns 0: a
 * wrong key installed silently is the failure this whole patch exists to end.
 */
static int32_t eapol_install_msg3_gtk(struct eapol_frame *fr_in,
                                      uint32_t length)
{
  uint8_t plain[EAPOL_KEYDATA_MAX];
  uint32_t wlen = (uint32_t)((fr_in->key_frame.key_data_length[0] << 8)
                             | fr_in->key_frame.key_data_length[1]);
  uint32_t glen = 0;
  uint8_t key_id = 0;

  /* key_data_length is attacker-controlled until checked against the frame
   * the radio actually delivered. */
  if((wlen < 24) || (wlen % 8) || (wlen > sizeof(plain)+8) ||
     (sizeof(struct eapol_frame) - EAPOL_RSN_LENGTH + wlen > length)) {
    DBG_WIFI("GTK: implausible key data length"EOL);
    return 0;
  }
  if(!aes_key_unwrap(ptk.s.kek, fr_in->key_frame.key_data, wlen, plain)) {
    DBG_WIFI("GTK: key unwrap integrity check failed"EOL);
    return 0;
  }
  if(!rsn_find_gtk(plain, wlen-8, gtk.s.ek, sizeof(gtk.s.ek),
                   &glen, &key_id)) {
    DBG_WIFI("GTK: no GTK KDE in key data"EOL);
    return 0;
  }
  if(key_id == 0) {
    /* Slot 0 carries the pairwise key; overwriting it would break unicast,
     * which is the one thing that currently works. */
    DBG_WIFI("GTK: refusing key id 0"EOL);
    return 0;
  }
  /* CCMP uses no MIC keys; pass the union\'s own fields. */
  rt2501_set_key(key_id, gtk.s.ek, gtk.s.mick.tx, gtk.s.mick.rx,
                 RT2501_CIPHER_AES);
  DBG_WIFI("GTK installed"EOL);
  return 1;
}
'''
    anchor = "static void eapol_input_msg3(uint8_t *frame, uint32_t length)"
    if anchor not in src:
        raise SystemExit("eapol.c: eapol_input_msg3 not found — upstream moved")
    src = src.replace(anchor, helper + "\n" + anchor, 1)

    # --- message 3 carries the GTK, and upstream dropped it ---------------
    # AFTER the state transitions, not before: the install does several
    # synchronous USB register writes, and doing them with the state machine
    # half-transitioned is what broke association on the first attempt.
    old3 = """  // FIXME Check length before cast ?
  (void)length;
  struct eapol_frame *fr_in = (struct eapol_frame *)frame;
  uint8_t old_mic[EAPOL_KEYMIC_LENGTH];"""
    if old3 not in src:
        raise SystemExit("eapol.c: eapol_input_msg3\'s preamble moved")
    src = src.replace(old3, """  struct eapol_frame *fr_in = (struct eapol_frame *)frame;
  uint8_t old_mic[EAPOL_KEYMIC_LENGTH];""", 1)

    marker = """  eapol_state = EAPOL_S_GROUP;
  ieee80211_state = IEEE80211_S_RUN;
  ieee80211_timeout = IEEE80211_RUN_TIMEOUT;
}"""
    if marker not in src:
        raise SystemExit("eapol.c: the end of eapol_input_msg3 moved")
    src = src.replace(marker, """  eapol_state = EAPOL_S_GROUP;
  ieee80211_state = IEEE80211_S_RUN;
  ieee80211_timeout = IEEE80211_RUN_TIMEOUT;

  /* WPA2 sends the GTK here, inside AES-Key-Wrapped Key Data, not in a
     separate group-key message. A supplicant that ignores it never installs
     one at all. */
  if((ieee80211_encryption&0x0F) == IEEE80211_CIPHER_CCMP)
    eapol_install_msg3_gtk(fr_in, length);
}""", 1)

    # --- the group rekey: replace the stub call ---------------------------
    old_ccmp = """    case IEEE80211_CIPHER_CCMP: // Decrypt CCMP/AES
      {
        // FIXME Actually do something...
        memcpy(&key[0], ptk.s.kek, 16);
        aes128_init(&aes, key, 16); // 16 or 128 ?"""
    if old_ccmp not in src:
        raise SystemExit("eapol.c: the CCMP stub call is not where it was")
    start = src.index(old_ccmp)
    stop = src.index("      break;", start) + len("      break;")
    src = src[:start] + """    case IEEE80211_CIPHER_CCMP:
      /* Same wrapped-and-KDE-encoded layout as message 3/4, so the same code
         handles a rotation. A failure here must NOT be swallowed: announcing
         RUN with a stale group key is what made the radio go silently deaf
         between rekeys. */
      if(!eapol_install_msg3_gtk(fr_in, length)) {
        DBG_WIFI("GTK rotation failed, dropping the link"EOL);
        eapol_state = EAPOL_S_MSG1;
        ieee80211_state = IEEE80211_S_IDLE;
        return;
      }
      break;""" + src[stop:]

    # the group handler declared a context for the stub it no longer calls
    src = src.replace("  struct aes128_context aes;\n", "", 1)
    # and discarded `length`, which the install needs to bound its reads
    src = src.replace("""static void eapol_input_group_msg1(uint8_t *frame, uint32_t length)
{
  // FIXME Check length before cast ?
  (void)length;
""", """static void eapol_input_group_msg1(uint8_t *frame, uint32_t length)
{
""", 1)

    p.write_text(src, encoding="utf-8", errors="surrogateescape")
    print("  eapol.c: WPA2 group key wired (msg3 + rekey), uplg's logic")


def patch_eapol_group_ack(root: Path) -> None:
    """Accept a group rekey that follows the standard.

    eapol_input()'s dispatch sends a frame to eapol_input_group_msg1() only
    when Key Type is 0, Key MIC is 1, Secure is 1 **and Key Ack is 1**. That
    last one is wrong: IEEE 802.11-2016 table 12-8 gives Key Ack = 0 for group
    key message 1. The bit exists to say "I expect a reply in this pairwise
    exchange", and a group rekey is not one.

    So an access point that follows the standard has every rekey silently
    dropped — the frame matches none of the three branches and lands in
    `DBG_WIFI("Drop EAPOL frame...")`. The rabbit stays associated and keeps
    the group key it was given at association, which means it goes deaf to
    broadcast and multicast traffic from the first rotation onwards. Most
    access points rotate hourly. Peers then age it out of their ARP caches and
    re-ARP by broadcast into the void, which is the same symptom the GTK work
    was about and which survived it.

    Measured in firmware-arm/emulator, not guessed: `./rabbit
    scenarios/wpa2-rekey.txt` logs "Drop EAPOL frame..." twice and zero rekeys
    acknowledged, and the same run with --rekey-ack (the non-standard bit set)
    installs both keys. Reported by pacifox in issue #26.
    """
    p = root / "src/net/eapol.c"
    src = p.read_text(encoding="utf-8", errors="surrogateescape")
    if "group key message 1 carries Key Ack 0" in src:
        print("  eapol.c: group rekey already accepts Key Ack 0")
        return

    needle = (
        "  else if((fr->key_frame.key_info.key_type == 0) &&\n"
        "      //(fr->key_frame.key_info.key_index != 0) &&\n"
        "      (fr->key_frame.key_info.key_ack == 1) &&\n"
    )
    if needle not in src:
        sys.exit("apply-mods: eapol.c group-key dispatch is not what we expected")

    replacement = (
        "  else if((fr->key_frame.key_info.key_type == 0) &&\n"
        "      //(fr->key_frame.key_info.key_index != 0) &&\n"
        "      /* No Key Ack test: group key message 1 carries Key Ack 0.\n"
        "       * IEEE 802.11-2016 table 12-8. Requiring 1 here dropped every\n"
        "       * rekey from a standards-following AP, and the rabbit kept the\n"
        "       * group key it was handed at association -- deaf to broadcast\n"
        "       * and multicast from the first rotation onwards. */\n"
    )
    src = src.replace(needle, replacement, 1)
    p.write_text(src, encoding="utf-8", errors="surrogateescape")
    print("  eapol.c: group rekey now accepts the standard's Key Ack 0")


def patch_group_cipher(root: Path) -> None:
    """Install the group key under the group cipher, not the pairwise one.

    `ieee80211_associate()` carries both ciphers in the low nibble of
    `ieee80211_encryption` while it builds the RSN element, and then throws the
    group one away:

        ieee80211_encryption = (ieee80211_encryption&0xF0)
                             | ((ieee80211_encryption&0x03)<<1);

    From then on nothing knows what the group cipher was, and
    `eapol_install_msg3_gtk()` installs the GTK as AES unconditionally. On a
    network whose group cipher is TKIP and whose pairwise cipher is CCMP --
    which is what a mixed-mode access point offers -- the radio is handed the
    group key with the wrong algorithm and decrypts every broadcast and
    multicast frame to rubbish. The rabbit is deaf to them for the whole life
    of the association, which looks exactly like the group-rekey bug and is a
    different cause.

    Measured, not reasoned about. firmware-arm/emulator,
    scenarios/wpa2-mixed-gtk.txt, against an AP advertising group TKIP and
    pairwise CCMP:

        the rabbit asks for RSN version 1, group TKIP, 1 pairwise, first CCMP
        radio: key 0 installed, cipher 4      <- pairwise, CCMP, right
        radio: key 1 installed, cipher 4      <- group, should be 3 (TKIP)

    The fix keeps the group cipher in a variable of its own rather than trying
    to make one nibble carry two things, and the install site reads it.
    Reported by pacifox in issue #26, finding 3.
    """
    hdr = root / "inc/net/ieee80211.h"
    src = read_src(hdr)
    if "ieee80211_group_cipher" not in src:
        needle = "extern uint8_t ieee80211_encryption;"
        if needle not in src:
            sys.exit("apply-mods: ieee80211.h has no ieee80211_encryption declaration")
        src = src.replace(
            needle,
            needle + "\n/* The group cipher suite, kept because ieee80211_associate() discards it\n"
            " * from ieee80211_encryption's low nibble. IEEE80211_CIPHER_TKIP or\n"
            " * IEEE80211_CIPHER_CCMP; 0 when there is no group key. */\n"
            "extern uint8_t ieee80211_group_cipher;",
            1,
        )
        write_src(hdr, src)
        print("  ieee80211.h: declared ieee80211_group_cipher")

    p = root / "src/net/ieee80211.c"
    src = read_src(p)
    if "ieee80211_group_cipher = " not in src:
        needle = "uint8_t ieee80211_encryption;"
        if needle not in src:
            sys.exit("apply-mods: ieee80211.c has no ieee80211_encryption definition")
        src = src.replace(needle, needle + "\nuint8_t ieee80211_group_cipher;", 1)

        # Capture it where the RSN element is built, which is the last moment
        # the information still exists.
        anchor = ("      /* Final line for this hack, reset \"normal\" cipher using "
                  "pairwise cipher 0x03 = 0b0011 */")
        if anchor not in src:
            sys.exit("apply-mods: ieee80211.c's RSN-element hack is not where we left it")
        src = src.replace(
            anchor,
            "      /* Keep the group cipher before the line below destroys it: it is\n"
            "       * the same value this function just wrote into the RSN element,\n"
            "       * and eapol.c needs it to install the GTK. */\n"
            "      ieee80211_group_cipher = (ieee80211_encryption & 0x0C) >> 1;\n"
            + anchor,
            1,
        )
        write_src(p, src)
        print("  ieee80211.c: the group cipher is kept at association")

    p = root / "src/net/eapol.c"
    src = p.read_text(encoding="utf-8", errors="surrogateescape")
    if "ieee80211_group_cipher == IEEE80211_CIPHER_TKIP" in src:
        print("  eapol.c: the GTK already follows the group cipher")
        return
    needle = ("  rt2501_set_key(key_id, gtk.s.ek, gtk.s.mick.tx, gtk.s.mick.rx,\n"
              "                 RT2501_CIPHER_AES);")
    if needle not in src:
        sys.exit("apply-mods: eapol.c's GTK install is not where we left it "
                 "(run wpa2_gtk first)")
    src = src.replace(
        needle,
        "  /* The GROUP cipher, which is not always the pairwise one: a mixed-mode\n"
        "   * AP offers group TKIP with pairwise CCMP, and installing the group key\n"
        "   * as AES there makes the radio decrypt every broadcast frame to\n"
        "   * rubbish. See issue #26 finding 3. */\n"
        "  rt2501_set_key(key_id, gtk.s.ek, gtk.s.mick.tx, gtk.s.mick.rx,\n"
        "                 (ieee80211_group_cipher == IEEE80211_CIPHER_TKIP)\n"
        "                   ? RT2501_CIPHER_TKIP : RT2501_CIPHER_AES);",
        1,
    )
    p.write_text(src, encoding="utf-8", errors="surrogateescape")
    print("  eapol.c: the GTK is installed under the group cipher")


def patch_eapol_ptk_rekey(root: Path) -> None:
    """Answer a fresh four-way handshake on a live association.

    eapol_input_msg1() returns early unless eapol_state is EAPOL_S_MSG1 or
    EAPOL_S_MSG3. After a completed handshake it is EAPOL_S_GROUP, and after
    the first group message EAPOL_S_RUN -- so an access point that rekeys the
    pairwise key periodically (hostapd's `wpa_ptk_rekey`, commonly an hour)
    sends message 1, gets "Inappropriate message" and silence, gives up, and
    deauthenticates. The rabbit then reconnects from scratch, every rekey
    interval, for ever.

    Measured in firmware-arm/emulator, scenarios/wpa2-ptk-rekey.txt:

        AP: starting a fresh four-way handshake on a live association
        AP: EAPOL 1/4, ANonce c0..df
        FAIL 1 pairwise rekey(s) ignored

    The fix accepts message 1 from the two running states as well, which
    restarts the exchange from EAPOL_S_MSG1 as the handler already does.

    One thing this does NOT fix, and pacifox flagged it when he reported it
    (issue #26, finding 1): the pairwise key is installed immediately after
    message 2 rather than after message 3. 802.11i has the supplicant install
    it after message 3, so the existing code is already early -- during a
    rekey that means the radio switches to the new PTK while the access point
    may still be sending under the old one, and a lost message 3 leaves the
    two ends on different keys. Doing it properly means deferring the install,
    which is a larger change and is not this one. What this step buys is that
    a rekey completes at all instead of costing a deauthentication; the
    remaining window is narrow and was already there.
    """
    p = root / "src/net/eapol.c"
    src = p.read_text(encoding="utf-8", errors="surrogateescape")
    # The marker has to be unique, and the obvious one is not:
    # "EAPOL_S_GROUP) && (eapol_state != EAPOL_S_RUN)" already appears in
    # eapol_input_group_msg1's own guard, so using it made this step believe
    # it had already run and do nothing -- a check that passes for the wrong
    # reason, which is the only kind worth worrying about.
    if "hostapd's wpa_ptk_rekey" in src:
        print("  eapol.c: message 1 is already accepted while running")
        return

    needle = (
        "  /* EAPOL_S_MSG3 can happen if the AP did not receive our reply */\n"
        "  if((eapol_state != EAPOL_S_MSG1) && (eapol_state != EAPOL_S_MSG3)) {"
    )
    if needle not in src:
        sys.exit("apply-mods: eapol_input_msg1's state guard is not where we left it")

    src = src.replace(
        needle,
        "  /* EAPOL_S_MSG3 can happen if the AP did not receive our reply, and\n"
        "   * EAPOL_S_GROUP / EAPOL_S_RUN when the AP rekeys the pairwise key on a\n"
        "   * live association -- hostapd's wpa_ptk_rekey. Refusing those two cost\n"
        "   * a deauthentication every rekey interval. See issue #26 finding 1. */\n"
        "  if((eapol_state != EAPOL_S_MSG1) && (eapol_state != EAPOL_S_MSG3)\n"
        "     && (eapol_state != EAPOL_S_GROUP) && (eapol_state != EAPOL_S_RUN)) {",
        1,
    )
    p.write_text(src, encoding="utf-8", errors="surrogateescape")
    print("  eapol.c: message 1 is accepted while running, so a rekey completes")


def patch_vm_bounds(root: Path) -> None:
    """Make the interpreter's store bounds-check actually check.

    Three stores guard themselves with

        if ((i>=0)||(i<VSIZE(p))) VSTORE(p,i,v);

    which is true for every i. OPfetch two dozen lines above gets the same
    test right -- `if ((i<0)||(i>=VSIZE(p))) VSTACKSET(0,NIL);` -- so this is
    a transcription slip rather than a difference of opinion, and it has been
    there since 2006. An out-of-range index writes into whatever heap object
    sits next door: a string's bytes, another table's header, or the GC's own
    bookkeeping. The bytecode a warren serves decides the index, so a bad
    bytecode corrupts the VM silently instead of being refused.

    Also: vmemAllocTab() computes `wsize = HEADER_LENGTH + size` with no lower
    bound, and OPtabnew takes its size from the stack. A negative size moves
    _vmem_heapindex *backwards* over live objects. Clamped here, at the
    opcode, rather than in vmem.c, because every other caller passes a
    constant from the bytecode stream and has nothing to clamp.

    Reported by pacifox in issue #27, finding 6. Only reachable through
    bytecode that gets an index wrong -- which is every bytecode under
    development, including the ones in this repository.
    """
    p = root / "src/vm/vinterp.c"
    src = read_src(p)

    if "/* (i>=0)||(i<VSIZE(p)) was true for every i" in src:
        print("  vinterp.c: the store bounds-check is already a conjunction")
        return

    bad = "if ((i>=0)||(i<VSIZE(p))) VSTORE(p,i,v);"
    n = src.count(bad)
    if n != 3:
        sys.exit(f"FAIL {p}: expected 3 inverted store guards, found {n}")
    # These sources are CRLF, so a multi-line anchor written with "\n" matches
    # nothing. Build them from the file's own line ending instead -- the third
    # time this has cost a confusing failure in this file.
    nl = "\r\n" if "\r\n" in src else "\n"

    good = nl.join([
        "/* (i>=0)||(i<VSIZE(p)) was true for every i, so this stored out",
        "               * of range into the next heap object. OPfetch has the test",
        "               * the right way round. See issue #27 finding 6. */",
        "              if ((i>=0)&&(i<VSIZE(p))) VSTORE(p,i,v);",
    ])
    src = src.replace(bad, good)

    # OPtabnew: a negative size walks the heap index backwards.
    needle = nl.join([
        "            int32_t p,i,v0;",
        "            v=VALTOINT(v);",
        "            p=VMALLOC(v);",
    ])
    if needle not in src:
        sys.exit(f"FAIL {p}: OPtabnew's allocation is not where we left it")
    src = src.replace(
        needle,
        nl.join([
            "            int32_t p,i,v0;",
            "            v=VALTOINT(v);",
            "            /* vmemAllocTab has no lower bound on size, and a negative",
            "             * one moves _vmem_heapindex backwards over live objects. */",
            "            if (v<0) v=0;",
            "            p=VMALLOC(v);",
        ]),
        1,
    )
    write_src(p, src)
    print("  vinterp.c: 3 store guards fixed, and a negative table size clamped")


def patch_mdns(root: Path) -> None:
    """Inject the boot-mods/mdns.mtl announcer into boot.0.0.0.13.mtl and
    wire mdns_boot_tick into the boot loop's !master branch.

    The injection point is just after the firmwarelimit/sigmarker block
    (top of file scope, before any function that uses udpsend) so all
    referenced primitives (strnew/strset/udpsend/netip/time) are already
    in scope. mdns_boot_tick is called from `fun loop` inside the
    `!master` branch right after wifiRun / boot_leds — those checks
    gate on netip / wifi being up, which is exactly when mDNS becomes
    meaningful.
    """
    p = root / "mtl/boot/boot.0.0.0.13.mtl"
    s = p.read_text()
    if "mdns_boot_tick" in s:
        print(f"[skip] {p}: mdns already wired in")
        return

    # 1. Inject the mDNS source. It defines top-level consts + vars + funs
    # so it must come AFTER the constants block but BEFORE `fun loop`
    # (which calls mdns_boot_tick).
    src_path = Path(__file__).resolve().parent / "boot-mods" / "mdns.mtl"
    if not src_path.is_file():
        sys.exit(f"FAIL {p}: missing boot-mods/mdns.mtl at {src_path}")
    mdns_body = src_path.read_text()

    loop_anchor = 'fun loop=\n'
    if loop_anchor not in s:
        sys.exit(f"FAIL {p}: `fun loop=` anchor not found")
    s = s.replace(loop_anchor, mdns_body + "\n" + loop_anchor, 1)

    # 2. Hook mdns_boot_tick into the !master branch of `fun loop`.
    # Original head (vanilla wpa2 HEAD):
    #     fun loop=
    #         if !master then
    #         (
    #             if !wavrunning then
    #             (
    #                 wifiRun;
    #                 boot_leds;
    #                 boot_loop
    #             )
    old_branch = (
        '\t\tif !wavrunning then\n'
        '\t\t(\n'
        '\t\t\twifiRun;\n'
        '\t\t\tboot_leds;\n'
        '\t\t\tboot_loop\n'
        '\t\t)\n'
    )
    new_branch = (
        '\t\tif !wavrunning then\n'
        '\t\t(\n'
        '\t\t\twifiRun;\n'
        '\t\t\tboot_leds;\n'
        '\t\t\tmdns_boot_tick;\n'
        '\t\t\tboot_loop\n'
        '\t\t)\n'
    )
    if old_branch not in s:
        sys.exit(f"FAIL {p}: `fun loop` !master branch not matched. Did upstream change?")
    s = s.replace(old_branch, new_branch, 1)

    p.write_text(s)
    print(f"[ok]   {p}: mdns announcer wired + tick added to boot loop")


# ---------------------------------------------------------------------------
# Phase 8 — move config-portal HTML pages from Metal globals to C-side ROM.
# ---------------------------------------------------------------------------

# Renderer C source: walks a ROM page byte-by-byte, substituting `<MARKER>`
# tokens against a Metal list-of-pairs supplied by the caller. Two-pass:
# measure, then allocate, then fill. Uses only vmem.h primitives so it
# survives any future VM heap layout change.
_ROM_PAGES_C = r'''
/*
 * rom_pages.c — Phase 8: HTML config pages live in flash ROM as const
 * char[]; the Metal bytecode just calls `pageRender idx replacements`.
 *
 * Generated by apply-mods.py (patch_rom_pages). Do NOT edit by hand —
 * any changes here will be wiped on the next build.
 */
#include <stdint.h>
#include <string.h>
#include "vm/vbc.h"
#include "vm/vmem.h"
#include "vm/rom_pages_data.h"  /* rom_page_data[], rom_page_size[], ROM_NPAGES */

/* If the byte at page[start]=='<', sniff a marker [A-Z0-9_-]+ then '>'.
 * Returns the byte length INCLUDING brackets (>=2), or 0 if not a marker. */
static int32_t sniff_marker(const uint8_t *page, int32_t page_size, int32_t start)
{
    int32_t i = start + 1;
    if (i >= page_size || !((page[i] >= 'A' && page[i] <= 'Z'))) return 0;
    while (i < page_size && page[i] != '>')
    {
        uint8_t c = page[i];
        int ok = (c >= 'A' && c <= 'Z') ||
                 (c >= '0' && c <= '9') ||
                 c == '_' || c == '-';
        if (!ok) return 0;
        i++;
    }
    if (i >= page_size) return 0;
    return (i + 1) - start;
}

/* Walk Metal list (chain of cons-cells, each holding a 2-tuple [key val]).
 * `reps` is tagged. Returns the tagged value of the matching pair's val,
 * or NIL if no match.
 *
 * mark+mark_size is the marker WITH brackets (e.g. "<MAC>"). Keys in the
 * replacements list are expected to include the brackets too — matching
 * the existing pagefill convention. */
static int32_t lookup(int32_t reps, const uint8_t *mark, int32_t mark_size)
{
    while (reps != NIL)
    {
        int32_t cell = VALTOPNT(reps);
        int32_t pair_val = VFETCH(cell, 0);
        if (pair_val != NIL)
        {
            int32_t pair = VALTOPNT(pair_val);
            int32_t key_val = VFETCH(pair, 0);
            if (key_val != NIL)
            {
                int32_t key = VALTOPNT(key_val);
                if ((int32_t)VSIZEBIN(key) == mark_size &&
                    memcmp(VSTARTBIN(key), mark, mark_size) == 0)
                    return VFETCH(pair, 1);
            }
        }
        reps = VFETCH(cell, 1);
    }
    return NIL;
}

/* Two-pass renderer: pass 1 measures output, pass 2 allocates + fills. */
int32_t rom_page_render(int32_t idx, int32_t reps)
{
    if (idx < 0 || idx >= ROM_NPAGES) return NIL;
    const uint8_t *page = (const uint8_t *)rom_page_data[idx];
    int32_t page_size = rom_page_size[idx];

    /* Pass 1 — measure. */
    int32_t out_size = 0;
    int32_t i = 0;
    while (i < page_size)
    {
        if (page[i] == '<')
        {
            int32_t mlen = sniff_marker(page, page_size, i);
            if (mlen > 0)
            {
                int32_t val = lookup(reps, page + i, mlen);
                if (val != NIL)
                {
                    int32_t vp = VALTOPNT(val);
                    out_size += VSIZEBIN(vp);
                    i += mlen;
                    continue;
                }
                /* unmatched marker — copy through as-is */
            }
        }
        out_size++;
        i++;
    }

    /* Pass 2 — allocate + fill. */
    int32_t out = VMALLOCBIN(out_size);
    if (out < 0) return NIL;
    uint8_t *dst = VSTARTBIN(out);
    int32_t o = 0;
    i = 0;
    while (i < page_size)
    {
        if (page[i] == '<')
        {
            int32_t mlen = sniff_marker(page, page_size, i);
            if (mlen > 0)
            {
                int32_t val = lookup(reps, page + i, mlen);
                if (val != NIL)
                {
                    int32_t vp = VALTOPNT(val);
                    int32_t vs = VSIZEBIN(vp);
                    memcpy(dst + o, VSTARTBIN(vp), vs);
                    o += vs;
                    i += mlen;
                    continue;
                }
            }
        }
        dst[o++] = page[i++];
    }
    return out;
}
'''


def _c_escape_string(s: bytes) -> str:
    """Encode bytes as a C string literal body (without the surrounding
    quotes). Splits long strings into multiple adjacent literals so we
    don't hit any compiler line-length limits."""
    out_lines: list[str] = []
    line = []
    line_len = 0
    for b in s:
        if b == ord('"'):
            tok = '\\"'
        elif b == ord('\\'):
            tok = '\\\\'
        elif b == ord('\n'):
            tok = '\\n'
        elif b == ord('\r'):
            tok = '\\r'
        elif b == ord('\t'):
            tok = '\\t'
        elif 32 <= b < 127:
            tok = chr(b)
        else:
            tok = f'\\x{b:02x}"\n"'  # use string-end + new-literal to anchor hex
        line.append(tok)
        line_len += len(tok)
        if line_len >= 80 and tok != '\\':
            out_lines.append(''.join(line))
            line = []
            line_len = 0
    if line:
        out_lines.append(''.join(line))
    return '"\n"'.join(out_lines)


def patch_rom_pages(root: Path) -> None:
    """Phase 8 — move config portal HTML from Metal globals to C-side ROM
    + replace the 3 Metal callsites with `pageRender idx [...replacements]`.

    Produces (all in-tree):
      - src/vm/rom_pages.c          new C file (renderer + extern data)
      - inc/vm/rom_pages_data.h     generated; const char[] arrays
      - Makefile                    + rom_pages.o in OBJS
      - inc/vm/vbc.h                + #define OPpageRender 153 (×2 copies)
      - src/vm/vinterp.c            + case OPpageRender dispatch
      - mtl/mtl_linux/.../stdlib_core.cpp  + pageRender builtin (NBcore++)
      - mtl/boot/boot.0.0.0.13.mtl  blank page_*; rewrite httpindex/done/u

    Mutually exclusive with modernize_pages — phase8-rom is the modernized
    pages, just stored in ROM instead of Metal globals. The HTML source
    files (firmware-arm/pages/*.html) are reused as-is.
    """
    boot = root / "mtl/boot/boot.0.0.0.13.mtl"
    s = boot.read_text()
    if "pageRender" in s:
        print(f"[skip] phase8-rom: pageRender already wired in {boot}")
        return
    if "/* pages-modernized */" in s:
        sys.exit(
            f"FAIL phase8-rom: modernize_pages step already ran. Phase 8 "
            f"and modernize_pages are mutually exclusive — pick one in the "
            f"mode's step list."
        )

    pages_dir = Path(__file__).resolve().parent / "pages"
    pages = [
        ("page_a", pages_dir / "page_a.html"),       # rom_page_0
        ("page_done", pages_dir / "page_done.html"), # rom_page_1
        ("page_u", pages_dir / "page_u.html"),       # rom_page_2
        ("page_error", pages_dir / "page_error.html"),# rom_page_3
    ]
    for name, path in pages:
        if not path.is_file():
            sys.exit(f"FAIL phase8-rom: missing {path}")

    # 1. Generate rom_pages_data.h
    inc_dir = root / "inc/vm"
    inc_dir.mkdir(parents=True, exist_ok=True)
    rom_h = inc_dir / "rom_pages_data.h"
    parts = ["/* Generated by apply-mods.py (patch_rom_pages). DO NOT EDIT. */\n"]
    parts.append("#ifndef ROM_PAGES_DATA_H\n#define ROM_PAGES_DATA_H\n\n")
    parts.append("#include <stdint.h>\n\n")
    for i, (name, path) in enumerate(pages):
        raw = path.read_bytes()
        escaped = _c_escape_string(raw)
        parts.append(f"/* {name} — {len(raw)} bytes */\n")
        parts.append(f'static const char rom_page_{i}_data[] = \n"{escaped}";\n\n')
    parts.append("static const char *const rom_page_data[] = {\n")
    for i, _ in enumerate(pages):
        parts.append(f"    rom_page_{i}_data,\n")
    parts.append("};\n\n")
    parts.append("static const int32_t rom_page_size[] = {\n")
    for i, (_, path) in enumerate(pages):
        parts.append(f"    sizeof(rom_page_{i}_data) - 1,  /* {path.name} */\n")
    parts.append("};\n\n")
    parts.append(f"#define ROM_NPAGES {len(pages)}\n\n")
    parts.append("#endif\n")
    rom_h.write_text("".join(parts))
    print(f"[ok]   wrote {rom_h} ({rom_h.stat().st_size} B)")

    # 2. Write rom_pages.c
    rom_c = root / "src/vm/rom_pages.c"
    rom_c.write_text(_ROM_PAGES_C)
    print(f"[ok]   wrote {rom_c} ({rom_c.stat().st_size} B)")

    # 3. Add OPpageRender to BOTH vbc.h copies (after OPverifySig).
    vbc_targets = [
        root / "inc/vm/vbc.h",
        root / "mtl/mtl_linux/inc/vm/vbc.h",
    ]
    for p in vbc_targets:
        s2 = p.read_text()
        if "OPpageRender" in s2:
            print(f"[skip] {p}: OPpageRender already present")
            continue
        if "OPverifySig 152" not in s2:
            sys.exit(f"FAIL {p}: OPverifySig anchor missing (run vbc step first)")
        s2 = s2.replace(
            "#define OPverifySig 152\n",
            "#define OPverifySig 152\n#define OPpageRender 153\n",
            1,
        )
        p.write_text(s2)
        print(f"[ok]   {p}: added OPpageRender 153")

    # 4. Add OPpageRender dispatch case to vinterp.c.
    vinterp = root / "src/vm/vinterp.c"
    s2 = vinterp.read_text()
    if "OPpageRender" in s2:
        print(f"[skip] {vinterp}: case already present")
    else:
        # Splice after the OPverifySig case (added by patch_vinterp_c earlier).
        anchor = (
            '      case OPverifySig:\n'
        )
        if anchor not in s2:
            sys.exit(f"FAIL {vinterp}: OPverifySig case missing (run vinterp step first)")
        # Find the end of the OPverifySig case — its `break;`.
        idx = s2.find(anchor)
        # walk forward looking for `        break;\n` at brace-depth 1 (relative)
        scan = idx + len(anchor)
        depth = 0
        while scan < len(s2):
            if s2[scan] == '{':
                depth += 1
            elif s2[scan] == '}':
                depth -= 1
            if depth == 0 and s2[scan:scan+13] == "        break":
                # find end of this line
                end_of_break = s2.find("\n", scan) + 1
                addition = (
                    '      case OPpageRender:\n'
                    '        {\n'
                    '          extern int32_t rom_page_render(int32_t,int32_t);\n'
                    '          int32_t reps=VPULL();\n'
                    '          int32_t idx=VALTOINT(VSTACKGET(0));\n'
                    '          int32_t result=rom_page_render(idx,reps);\n'
                    '          VSTACKSET(0,(result==NIL)?NIL:PNTTOVAL(result));\n'
                    '        }\n'
                    '        break;\n'
                )
                s2 = s2[:end_of_break] + addition + s2[end_of_break:]
                vinterp.write_text(s2)
                print(f"[ok]   {vinterp}: added OPpageRender dispatch case")
                break
            scan += 1
        else:
            sys.exit(f"FAIL {vinterp}: could not locate OPverifySig break")

    # 5. Register pageRender in stdlib_core.cpp (after verifySig).
    stdlib = root / "mtl/mtl_linux/src/vcomp/stdlib_core.cpp"
    s2 = stdlib.read_text()
    if '"pageRender"' in s2:
        print(f"[skip] {stdlib}: pageRender already registered")
    else:
        # NBcore was bumped to 120 by patch_stdlib_core. Bump to 121.
        if "#define NBcore 120" not in s2:
            sys.exit(f"FAIL {stdlib}: NBcore 120 anchor missing (run stdlib step first)")
        s2 = s2.replace("#define NBcore 120", "#define NBcore 121", 1)

        # Each array gets an entry as /*18*/.
        edits = [
            (
                '/*17*/"verifySig"\n};',
                '/*17*/"verifySig",\n/*18*/"pageRender"\n};',
            ),
            (
                '/*17*/OPverifySig,\n};',
                '/*17*/OPverifySig,\n/*18*/OPpageRender,\n};',
            ),
            (
                '/*17*/2\n};',
                '/*17*/2,\n/*18*/2\n};',
            ),
            (
                '/*17*/"fun[S S]I"\n};',
                '/*17*/"fun[S S]I",\n/*18*/"fun[I list[S S]]S"\n};',
            ),
        ]
        ok = 0
        for old, new in edits:
            if old in s2:
                s2 = s2.replace(old, new, 1)
                ok += 1
        if ok != len(edits):
            sys.exit(f"FAIL {stdlib}: only {ok}/{len(edits)} arrays patched")
        stdlib.write_text(s2)
        print(f"[ok]   {stdlib}: registered pageRender (NBcore 120→121)")

    # 6. No Makefile patch needed — `C_FILES += $(wildcard src/**/*.c)`
    #    already picks up our new src/vm/rom_pages.c automatically.

    # 7. Rewrite boot.0.0.0.13.mtl:
    #    a. Blank the 4 page globals (their data now lives in C-side ROM).
    #    b. Replace `fun httpdone = page_done;;` → `pageRender 1 nil`
    #    c. Replace `fun httpupgrade = page_u;;` → `pageRender 2 nil`
    #    d. Rewrite `httpindex` body: drop the `pagefill … page_a` form,
    #       call `pageRender 0 [...replacements...]` directly.
    #    e. Replace `page_error` ref in httpflash with `pageRender 3 nil`.
    _rewrite_boot_for_rom_pages(boot)
    print(f"[ok]   {boot}: phase8-rom rewrite applied")


def _blank_global(src: str, name: str) -> tuple[str, int]:
    """Replace `var <name>=<expr>;;<EOL>` with `var <name>;;<EOL>`. Robust
    to multi-line values, CRLF or LF, trailing whitespace."""
    head = f"var {name}="
    start = src.find(head)
    if start < 0:
        return src, 0
    eol = "\r\n" if "\r\n" in src else "\n"
    i = start + len(head)
    n = len(src)
    while i < n - 1:
        if src[i:i+2] == ";;":
            j = i + 2
            while j < n and src[j] in " \t":
                j += 1
            if j < n and src[j] in "\r\n":
                # consume just ONE line-terminator (CRLF or LF), preserving
                # any subsequent blank lines verbatim.
                if src[j:j+2] == "\r\n":
                    j += 2
                else:
                    j += 1
                end = j
                orig = end - start
                new_block = f"var {name};;{eol}"
                return src[:start] + new_block + src[end:], orig
        i += 1
    return src, 0


def _rewrite_boot_for_rom_pages(boot: Path) -> None:
    s = boot.read_bytes().decode("latin-1")
    # Detect line endings — patch_bootloader (which runs before us) does a
    # naive read_text/write_text that collapses CRLF→LF on Linux, so we
    # may see EITHER. Build anchors using the file's actual EOL.
    eol = "\r\n" if "\r\n" in s else "\n"

    # a. blank the 4 page globals
    for name in ("page_a", "page_done", "page_u", "page_error"):
        s, _ = _blank_global(s, name)

    def must_replace(old: str, new: str, what: str) -> None:
        nonlocal s
        if old not in s:
            sys.exit(f"FAIL phase8-rom: {what} anchor not matched")
        s = s.replace(old, new, 1)

    # b. httpdone
    must_replace(
        f"fun httpdone={eol}\tpage_done;;",
        f"fun httpdone={eol}\tpageRender 1 nil;;",
        "httpdone body",
    )
    # c. httpupgrade
    must_replace(
        f"fun httpupgrade={eol}\tpage_u;;",
        f"fun httpupgrade={eol}\tpageRender 2 nil;;",
        "httpupgrade body",
    )
    # d. httpindex
    must_replace(
        f"\tstrcatlist pagefill{eol}",
        f"\tpageRender 0{eol}",
        "strcatlist pagefill",
    )
    must_replace(
        f"\t\tpage_a{eol}\t;;",
        f"\t;;",
        "trailing page_a arg",
    )
    # e. page_error usage in httpflash else branch.
    tail = f"\t\tpage_error{eol}\t)"
    if tail in s:
        s = s.replace(tail, f"\t\tpageRender 3 nil{eol}\t)", 1)

    boot.write_bytes(s.encode("latin-1"))


def _wav_header(freq: int, channel: int, bps: int) -> bytes:
    """Reproduce mkwav's byte output exactly — see boot.0.0.0.13.mtl L2203.

    Note: bps is emitted as a 4-byte field (itobin4) here, not the
    standard 2-byte WAV field. We preserve the Metal-original layout so
    downstream consumers (the wav player on the rabbit) see the same
    bytes they always saw.
    """
    import struct
    c = (
        b"WAVEfmt "
        + struct.pack("<I", 0x12)
        + struct.pack("<H", 1)
        + struct.pack("<H", channel)
        + struct.pack("<I", freq)
        + struct.pack("<I", freq * channel * bps // 8)
        + struct.pack("<H", channel * bps // 8)
        + struct.pack("<I", bps)
        + b"data"
        + struct.pack("<I", 0)
    )
    return b"RIFF" + struct.pack("<I", len(c)) + c


def _bytes_to_metal_literal(b: bytes) -> str:
    """Encode a byte string as a Metal `"..."` literal, using `\\$xx` hex
    escapes for non-printable bytes / quotes / backslashes."""
    parts = []
    for ch in b:
        if 32 <= ch < 127 and ch not in (ord('"'), ord('\\')):
            parts.append(chr(ch))
        else:
            parts.append(f"\\${ch:02x}")
    return '"' + ''.join(parts) + '"'


def patch_strip_dump(root: Path) -> None:
    """Replace the boot.mtl debug-trace functions `dump s` and `dumpscan l0`
    with identity (`s` / `l0`). Both are passthrough Secho-based hex
    dumpers — they print to UART then return their argument unchanged.
    With DEBUG_VM/MAIN disabled by patch_makefile (and the UART pad
    unwired on the production rabbit), nobody reads that output, so we
    just delete the bodies and keep the passthrough semantics.

    Saves ~300 B of boot bytecode. Caller sites stay 1:1 valid.
    """
    boot = root / "mtl/boot/boot.0.0.0.13.mtl"
    s = boot.read_bytes().decode("latin-1")
    if "/* dump-stripped */" in s:
        print(f"[skip] {boot}: dump/dumpscan already stripped")
        return
    eol = "\r\n" if "\r\n" in s else "\n"

    def replace_fun(src: str, head: str, identity_arg: str) -> str:
        start = src.find(head)
        if start < 0:
            sys.exit(f"FAIL strip_dump: `{head}` definition not found")
        i = start + len(head)
        n = len(src)
        end = None
        while i < n - 1:
            if src[i:i+2] == ";;":
                j = i + 2
                while j < n and src[j] in " \t":
                    j += 1
                if j < n and src[j] in "\r\n":
                    if src[j:j+2] == "\r\n":
                        j += 2
                    else:
                        j += 1
                    end = j
                    break
            i += 1
        if end is None:
            sys.exit(f"FAIL strip_dump: could not delimit `{head}` body")
        replacement = f"{head}{identity_arg};;{eol}"
        return src[:start] + replacement + src[end:]

    s = replace_fun(s, "fun dump s=", "s")
    s = replace_fun(s, "fun dumpscan l0=", "l0")
    s = f"/* dump-stripped */{eol}" + s
    boot.write_bytes(s.encode("latin-1"))
    print(f"[ok]   {boot}: stripped dump + dumpscan to identity passthrough")


def patch_prune_orphans(root: Path) -> None:
    """Delete fun defs that are unreachable after the rom_pages rewrite.

    `pagefill` and `listreplacestr` were the Metal-side templating
    machinery for the HTML pages: pagefill walked a list of `[marker
    val]` pairs and applied listreplacestr per marker. After rom_pages
    rerouted every callsite (httpindex/httpdone/httpupgrade) to the
    C-side `pageRender` opcode, both funs lost every external caller.

    The Metal compiler doesn't do dead-code elimination, so they
    survive in the bytecode unless we delete the source.

    Idempotent + load-bearing: verifies each target has *exactly* the
    expected reference pattern (def + self-recursion only) before
    deleting; if any unexpected caller has appeared, abort rather than
    silently breaking the build.
    """
    boot = root / "mtl/boot/boot.0.0.0.13.mtl"
    s = boot.read_bytes().decode("latin-1")
    if "/* orphans-pruned */" in s:
        print(f"[skip] {boot}: orphan funs already pruned")
        return
    if "/* pages-modernized */" in s or "pageRender" not in s:
        sys.exit(
            "FAIL prune_orphans: requires rom_pages to have run first "
            "(the funs are only orphan after pageRender takes over)"
        )

    eol = "\r\n" if "\r\n" in s else "\n"

    def find_block(src: str, head: str) -> tuple[int, int]:
        start = src.find(head)
        if start < 0:
            return -1, -1
        i = start + len(head)
        n = len(src)
        while i < n - 1:
            if src[i:i+2] == ";;":
                j = i + 2
                while j < n and src[j] in " \t":
                    j += 1
                if j < n and src[j] in "\r\n":
                    if src[j:j+2] == "\r\n":
                        j += 2
                    else:
                        j += 1
                    return start, j
            i += 1
        return -1, -1

    # 1. Verify expected orphans: each name's only refs are inside its own
    # body (def line + N self-recursive calls). External callers = 0.
    targets = [
        # name              expected_refs   reason for being orphan
        ("pagefill",        2),  # def + 1 self; only caller was httpindex (gone)
        ("listreplacestr",  4),  # def + 2 self + 1 from pagefill (gone)
        ("wifiConnected",   1),  # def only; never referenced (stillborn helper)
        ("listnth",         2),  # def + 1 self; never wired in
        ("itoanil",         1),  # def only; never wired in
        ("unregudp",        1),  # def only; never wired in
    ]
    for name, expected in targets:
        n_refs = len(re.findall(rf"\b{name}\b", s))
        if n_refs != expected:
            sys.exit(
                f"FAIL prune_orphans: `{name}` has {n_refs} refs, expected "
                f"{expected}. Did upstream change or did an earlier step "
                f"leave a caller behind?"
            )

    # 2. Delete each fun body in order. The recursive-call cleanup order
    # matters only across funs that call each other (pagefill →
    # listreplacestr); within-orphans deletion order is arbitrary.
    fun_heads = [
        "fun pagefill l p=",
        "fun listreplacestr l key val=",
        "fun wifiConnected= ",  # bare-args form (no args)
        "fun listnth l i=",
        "fun itoanil l=",
        "fun unregudp port=",
    ]
    for head in fun_heads:
        start, end = find_block(s, head)
        if start < 0:
            # Try without trailing space (wifiConnected variant).
            start, end = find_block(s, head.rstrip())
        if start < 0:
            sys.exit(f"FAIL prune_orphans: couldn't delimit `{head!r}`")
        s = s[:start] + s[end:]

    s = f"/* orphans-pruned */{eol}" + s
    boot.write_bytes(s.encode("latin-1"))
    print(f"[ok]   {boot}: pruned {len(fun_heads)} orphan funs")


def patch_inline_dump_calls(root: Path) -> None:
    """After patch_strip_dump reduced `dump`/`dumpscan` to identity
    (`fun dump s = s;;`, `fun dumpscan l0 = l0;;`), every callsite
    still spends a `push-arg + OPexec + return` cycle for nothing.
    Delete the keyword at every callsite, then delete the now-dead
    function definitions. Net per call: ~5 B saved.

    Requires patch_strip_dump to have run first (the deletions assume
    the funs are already identity, otherwise we'd silently drop a
    side effect).
    """
    boot = root / "mtl/boot/boot.0.0.0.13.mtl"
    s = boot.read_bytes().decode("latin-1")
    if "/* dump-inlined */" in s:
        print(f"[skip] {boot}: dump callsites already inlined")
        return
    if "/* dump-stripped */" not in s:
        sys.exit("FAIL inline_dump_calls: requires strip_dump to run first")

    eol = "\r\n" if "\r\n" in s else "\n"

    # 1. Delete `dump ` and `dumpscan ` keywords from code. These are
    #    1-arg functions reduced to identity by patch_strip_dump, so
    #    deleting the keyword is a no-op semantically.
    pat = re.compile(r"\b(?:dumpscan|dump)\b[ \t]+")
    n_call = 0
    def _sub(_m):
        nonlocal n_call
        n_call += 1
        return ""
    s = pat.sub(_sub, s)

    # 2. Delete the now-dead `fun dump s=s;;` and `fun dumpscan l0=l0;;`
    #    definitions. We re-locate the exact form patch_strip_dump wrote
    #    (no leading whitespace per its `replacement = f"{head}{identity_arg};;{eol}"`).
    for head, body in (("fun dump s=", "s"), ("fun dumpscan l0=", "l0")):
        target = f"{head}{body};;{eol}"
        if target in s:
            s = s.replace(target, "", 1)
    s = f"/* dump-inlined */{eol}" + s
    boot.write_bytes(s.encode("latin-1"))
    print(f"[ok]   {boot}: inlined {n_call} dump/dumpscan callsites + dropped defs")


def patch_strip_echo_opcodes(root: Path) -> None:
    """Delete every dynamic-arg `Secho`/`Secholn`/`Iecho`/`Iecholn` opcode
    keyword from boot.mtl. Confirmed against upstream vinterp.c: all four
    opcodes are pure stack passthroughs (they call logSecho/logIecho on
    `VSTACKGET(0)` but never `VPULL`/`VSTACKSET`, so the top-of-stack
    value flows through unchanged). With the UART pad unwired, the side
    effect is invisible, and deleting the keyword leaves the argument
    expression in place — zero behaviour change for any caller using the
    return value (`let Iecholn EXPR -> X in ...` becomes `let EXPR -> X
    in ...`; `Iecholn rssi;` becomes `rssi;` which the sequence drop
    swallows just the same).

    Saves ~5 B per callsite (opcode + arg push folded into the argument's
    own emission).
    """
    boot = root / "mtl/boot/boot.0.0.0.13.mtl"
    s = boot.read_bytes().decode("latin-1")
    if "/* echo-opcodes-stripped */" in s:
        print(f"[skip] {boot}: echo opcodes already stripped")
        return

    # Match `Secho`, `Secholn`, `Iecho`, or `Iecholn` as a whole word
    # (word boundary) followed by at least one space/tab. The argument
    # expression that comes next stays untouched.
    pat = re.compile(r"\b(?:Secholn|Secho|Iecholn|Iecho)\b[ \t]+")
    n = 0
    def _sub(_m):
        nonlocal n
        n += 1
        return ""
    s = pat.sub(_sub, s)
    eol = "\r\n" if "\r\n" in s else "\n"
    s = f"/* echo-opcodes-stripped */{eol}" + s
    boot.write_bytes(s.encode("latin-1"))
    print(f"[ok]   {boot}: dropped {n} echo-opcode keywords (Secho/Iecho/{{ln}})")


def patch_strip_echo_funs(root: Path) -> None:
    """Reduce the user-defined echo helpers (`MACecho`, `SEQecho`, `IPecho`)
    to identity. Each was a `print bytes to UART, then return src`
    construct; with UART unwired and the print side stripped of its
    constant strings already, the for-loop body is dead weight while
    the `src` return value IS observed by callers (`netSend ... (MACecho
    mac 0 1) ...`, `set t.ackT = SEQecho ...`, etc.). Reducing each to
    `src;;` preserves the return-value contract every caller relies on.

    Saves ~120 B across the 3 fun bodies + their callees (Secho/Iecho
    dynamic-arg calls that drop out as dead code).
    """
    boot = root / "mtl/boot/boot.0.0.0.13.mtl"
    s = boot.read_bytes().decode("latin-1")
    if "/* echo-funs-identity */" in s:
        print(f"[skip] {boot}: echo helper funs already identity")
        return
    eol = "\r\n" if "\r\n" in s else "\n"

    n_replaced = 0
    for head in (
        "fun MACecho src i0 ln=",
        "fun SEQecho src i0 ln=",
        "fun IPecho src i0 ln=",
    ):
        start = s.find(head)
        if start < 0:
            sys.exit(f"FAIL strip_echo_funs: `{head}` not found")
        i = start + len(head)
        n = len(s)
        end = None
        while i < n - 1:
            if s[i:i+2] == ";;":
                j = i + 2
                while j < n and s[j] in " \t":
                    j += 1
                if j < n and s[j] in "\r\n":
                    if s[j:j+2] == "\r\n":
                        j += 2
                    else:
                        j += 1
                    end = j
                    break
            i += 1
        if end is None:
            sys.exit(f"FAIL strip_echo_funs: could not delimit `{head}` body")
        s = s[:start] + f"{head}src;;{eol}" + s[end:]
        n_replaced += 1
    s = f"/* echo-funs-identity */{eol}" + s
    boot.write_bytes(s.encode("latin-1"))
    print(f"[ok]   {boot}: collapsed {n_replaced} echo helpers to identity")


def patch_strip_secho(root: Path) -> None:
    """Strip all `Secho "<literal>"` and `Secholn "<literal>"` calls — the
    UART pad is unwired on the production rabbit and DEBUG_VM/AUDIO/MAIN
    are already off, so the output goes nowhere. Replace each call with
    `nil` (a valid Metal expression) so the surrounding statement / body
    structure stays valid.

    Saves ~1 KB across the bytecode (97 callsites × ~6 B opcodes +
    deduped string globals).
    """
    boot = root / "mtl/boot/boot.0.0.0.13.mtl"
    s = boot.read_bytes().decode("latin-1")
    if "/* secho-stripped */" in s:
        print(f"[skip] {boot}: Secho calls already stripped")
        return

    # Match `Secho` or `Secholn` followed by whitespace then a Metal string
    # literal. Metal strings: " ... " with \\ to escape, \" for embedded
    # quote. We DON'T want to match inside other strings; risk-mitigation
    # is that all big string globals (page_a/done/u/error) are already
    # gone by the time this step runs, so the few remaining inline `var`
    # values are tiny and the regex is safe enough in practice.
    pat = re.compile(
        r'\bSecho(?:ln)?\s+"(?:[^"\\]|\\.)*"',
        re.DOTALL,
    )
    n = 0
    def _sub(m):
        nonlocal n
        n += 1
        return "nil"
    s = pat.sub(_sub, s)

    eol = "\r\n" if "\r\n" in s else "\n"
    s = f"/* secho-stripped */{eol}" + s
    boot.write_bytes(s.encode("latin-1"))
    print(f"[ok]   {boot}: stripped {n} Secho/Secholn string-literal calls")


def patch_inline_mkwav(root: Path) -> None:
    """Replace `(mkwav 8000 1 16)::nil` with the precomputed 46-byte WAV
    header literal, then delete the `fun mkwav freq channel bps=...`
    definition. Boot.mtl has exactly ONE mkwav callsite (at fifotest
    init) with fixed args, so this is a pure refactor — no behavior
    change, just no longer paying ~520 B of bytecode for a value we
    already know at build time.
    """
    boot = root / "mtl/boot/boot.0.0.0.13.mtl"
    s = boot.read_bytes().decode("latin-1")
    if "/* mkwav-inlined */" in s:
        print(f"[skip] {boot}: mkwav already inlined")
        return

    eol = "\r\n" if "\r\n" in s else "\n"
    wav_literal = _bytes_to_metal_literal(_wav_header(8000, 1, 16))

    # 1. Substitute the single call site.
    call_anchor = "(mkwav 8000 1 16)"
    if call_anchor not in s:
        sys.exit(f"FAIL inline_mkwav: callsite {call_anchor!r} not found")
    s = s.replace(call_anchor, wav_literal, 1)

    # 2. Delete the `fun mkwav freq channel bps= ... ;;` definition.
    # The body ends at the FIRST `;;` followed by line break — same rule as
    # _blank_global. Replace the entire block with the marker comment so the
    # skip-guard works on re-runs.
    head = "fun mkwav freq channel bps="
    start = s.find(head)
    if start < 0:
        sys.exit(f"FAIL inline_mkwav: 'fun mkwav' definition not found")
    i = start + len(head)
    n = len(s)
    end = None
    while i < n - 1:
        if s[i:i+2] == ";;":
            j = i + 2
            while j < n and s[j] in " \t":
                j += 1
            if j < n and s[j] in "\r\n":
                if s[j:j+2] == "\r\n":
                    j += 2
                else:
                    j += 1
                end = j
                break
        i += 1
    if end is None:
        sys.exit(f"FAIL inline_mkwav: could not delimit `fun mkwav` body")
    marker = f"// mkwav-inlined: precomputed 46-byte WAV header above{eol}"
    s = s[:start] + marker + s[end:]

    s = f"/* mkwav-inlined */{eol}" + s
    boot.write_bytes(s.encode("latin-1"))
    print(f"[ok]   {boot}: inlined mkwav (deleted fun + substituted callsite)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="Path to the nabgcc checkout root")
    ap.add_argument(
        "--mode",
        choices=("full", "minimal", "signed-stock", "pages-only", "max", "mdns-only", "lean", "lean-wpa3", "lean-nomdns", "phase8-rom"),
        default="full",
        help=(
            "full         = everything (verify-enforced bootloader + modernized UI). "
            "minimal      = opcode + crypto only; boot.mtl stays byte-identical to "
            "vanilla wpa2. The minimal build is our rescue 'parachute' — "
            "almost the same firmware that was already flashed safely by hand, "
            "plus the verifySig opcode for future OTAs. "
            "signed-stock = minimal + httpflash signature gate, NO page changes. "
            "Use after minimal proves the C-side is sane, to verify the "
            "bootloader patch in isolation. "
            "pages-only   = minimal + modernized UI, NO bootloader patch. "
            "Use to verify the page rewrite in isolation. "
            "lean-wpa3    = lean plus the WPA3-SAE client. A separate mode "
            "on purpose: `lean` is what every rabbit on the warren runs, "
            "and an experimental key exchange does not belong in it.\n"
            "lean-nomdns  = lean minus the mDNS announcer/resolver, nothing "
            "else changed. Diagnostic build for the connection drops "
            "reported on lean; needs working DNS to find its server. "
            "max          = full + mdns announcer; the everything-bag for a "
            "fresh flash. "
            "mdns-only    = minimal + mdns; isolation bisect for the mdns add. "
            "lean         = max + --gc-sections (drops dead printf glue, "
            "~6-10 KB saved). Requires the KEEP() linker patch."
        ),
    )
    ap.add_argument(
        "--steps",
        default=None,
        help="Override the default step set chosen by --mode.",
    )
    args = ap.parse_args()
    root = Path(args.root).resolve()
    if not (root / "Makefile").is_file():
        sys.exit(f"not a nabgcc root: {root} (Makefile missing)")

    # linker_keep is in ALL modes by default: it's idempotent + harmless
    # (KEEP() is a no-op when --gc-sections is off) + future-proofs against
    # someone enabling gc-sections elsewhere. Only mode `lean` adds the
    # gc_sections step that actually USES the protection.
    default_steps = {
        "full": "vbc,vinterp,stdlib,strip_tweetnacl,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,wpa2_gtk,eapol_group_ack,group_cipher,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,extbss,no_libc_malloc,linker_keep",
        # Minimal omits modernize_pages + bootloader — boot.mtl untouched, so
        # the rabbit's flash + config-mode behavior is byte-equivalent to the
        # vanilla wpa2 branch HEAD. Only the C side gains the verifySig opcode
        # (dormant until called).
        "minimal": "vbc,vinterp,stdlib,strip_tweetnacl,audio_fifo_data,audio_halfduplex,bounded_waits,makefile,wpa2_gtk,eapol_group_ack,group_cipher,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,extbss,no_libc_malloc,linker_keep,gc_sections",
        # Bisection helpers — same C-side as minimal, only ONE boot.mtl mod
        # added. Use them when full has bricked the rabbit to pinpoint which
        # of the two boot.mtl patches is the culprit.
        "signed-stock": "vbc,vinterp,stdlib,strip_tweetnacl,audio_fifo_data,audio_halfduplex,bounded_waits,makefile,bootloader,wpa2_gtk,eapol_group_ack,group_cipher,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,extbss,no_libc_malloc,linker_keep",
        "pages-only":   "vbc,vinterp,stdlib,strip_tweetnacl,audio_fifo_data,audio_halfduplex,bounded_waits,makefile,modernize_pages,wpa2_gtk,eapol_group_ack,group_cipher,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,extbss,no_libc_malloc,linker_keep",
        # max = full + mdns. The everything-bag for a fresh flash.
        # NOTE: mdns is NEW and untested on hardware as of 2026-05-31
        # — flash mdns-only first as a less-invasive smoke test.
        "max":       "vbc,vinterp,stdlib,strip_tweetnacl,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,extbss,no_libc_malloc,linker_keep",
        # mdns-only: vanilla boot.mtl + just the mDNS announcer. Use to
        # smoke-test the mdns add without entangling with bootloader/pages.
        "mdns-only": "vbc,vinterp,stdlib,strip_tweetnacl,audio_fifo_data,audio_halfduplex,bounded_waits,makefile,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,extbss,no_libc_malloc,linker_keep",
        # lean: max + gc-sections. Drops the dead newlib printf glue
        # (~6 KB) that DEBUG_*-stripping made unreachable but the
        # linker still kept (no DCE). Safe because linker_keep protects
        # .intvec / .startup.* / .ramfunc / .bytecode.*.
        "lean":      "vbc,vinterp,stdlib,strip_tweetnacl,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,extbss,no_libc_malloc,linker_keep,gc_sections",
        "lean-wpa3": "vbc,vinterp,stdlib,strip_tweetnacl,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,extbss,no_libc_malloc,wpa3_sae,linker_keep,gc_sections",
        # lean-nomdns: `lean` with the mDNS announcer/resolver removed and
        # NOTHING else changed. Diagnostic build for the connection drops
        # reported on `lean`: mdns is the only new *network* feature in that
        # mode, so flashing this isolates it from the rest of the stack.
        # A rabbit on this build needs working DNS to find its server.
        "lean-nomdns": "vbc,vinterp,stdlib,strip_tweetnacl,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,wpa2_gtk,eapol_group_ack,group_cipher,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,extbss,no_libc_malloc,linker_keep,gc_sections",
        # phase8-rom: instead of modernize_pages (which stores pages as
        # Metal globals), use rom_pages — pages live in C-side const ROM
        # and are rendered via the new OPpageRender opcode. Measures the
        # flash budget trade-off. Mutually exclusive with modernize_pages.
        "phase8-rom": "vbc,vinterp,stdlib,strip_tweetnacl,audio_fifo_data,audio_halfduplex,bounded_waits,makefile,bootloader,rom_pages,prune_orphans,inline_mkwav,strip_dump,inline_dump_calls,strip_echo_funs,strip_secho,strip_echo_opcodes,wpa2_gtk,eapol_group_ack,group_cipher,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,extbss,no_libc_malloc,linker_keep,gc_sections",
    }
    steps_str = args.steps if args.steps else default_steps[args.mode]
    steps = steps_str.split(",")
    print(f"=== apply-mods.py mode={args.mode} steps={steps_str} ===")

    # linker_keep deliberately leaves *(.bss.*) out of the zero-loop, because
    # bracketing it costs the whole VM heap on 16 KB of SRAM (see the note of
    # 2026-06-21). The price is that every -fdata-sections global starts as
    # garbage, and two of them -- play_w and play_r -- are read before they
    # are written. That is the 2026-06-20 brick: an out-of-bounds load, a Data
    # Abort, and a dead-loop in the vector, triggered by the connection
    # jingle. audio_fifo_data is the fix and the two steps belong together.
    #
    # They came apart in practice: `minimal` -- the image RECOVERY.md calls
    # the parachute -- shipped on 2026-10-20 with linker_keep and gc_sections
    # and without audio_fifo_data, and so did signed-stock, pages-only, max,
    # mdns-only and phase8-rom. pacifox caught it with preflight.py before
    # anyone flashed one (#45). A comment would not have caught it; this does.
    if "linker_keep" in steps and "audio_fifo_data" not in steps:
        sys.exit(
            "FAIL step list: linker_keep without audio_fifo_data leaves play_w "
            "and play_r uninitialised in a .bss.* section the zero-loop does "
            "not reach. Reading one of them is the 2026-06-20 brick. Add "
            "audio_fifo_data, or drop linker_keep."
        )
    # Same shape, same reason, and this is the precondition that was missing
    # on 2026-10-25: no_libc_malloc changes which objects get linked, and
    # without an explicit home for .bss.* the linker then places 85 orphan
    # sections inside .bss, on top of the stacks. extbss gives them that
    # home. With it the step is safe; without it, it builds a brick that
    # preflight passed. Order, not merit.
    # wpa3_sae needs nab_rand() for its scalars, which no_libc_malloc
    # provides, and it needs .bss zeroed at boot: a sae_ctx that starts with
    # stale SRAM is a context whose state field may already read
    # SAE_ACCEPTED. On an image before extbss it would have been an
    # un-zeroed .bss.* orphan, which is usbhost_init_status all over again.
    if "wpa3_sae" in steps:
        missing = [d for d in ("no_libc_malloc", "extbss") if d not in steps]
        if missing:
            sys.exit(
                "FAIL step list: wpa3_sae needs " + ", ".join(missing) +
                ". nab_rand() comes from no_libc_malloc and is what SAE's "
                "secrecy rests on; extbss is what zeroes the SAE context at "
                "boot. Without either, this builds a WPA3 client that is "
                "worse than no WPA3 client."
            )

    if "no_libc_malloc" in steps and "extbss" not in steps:
        sys.exit(
            "FAIL step list: no_libc_malloc changes the linked object set, so "
            "the 85 orphaned .bss.* sections move inside .bss and land on the "
            "stacks that init.s reserves -- the 2026-06-20 brick. Add extbss, "
            "which places .bss.* explicitly and moves 8 KB of scratch to "
            "external RAM. See patch_no_libc_malloc's docstring."
        )
    fns = {
        "eapol_replay": patch_eapol_replay,
        "cold_state": patch_cold_state,
        "vbc": patch_vbc_h,
        "vinterp": patch_vinterp_c,
        "stdlib": patch_stdlib_core,
        "strip_tweetnacl": strip_tweetnacl,
        "modernize_pages": modernize_pages,
        "makefile": patch_makefile,
        "bootloader": patch_bootloader,
        "mdns": patch_mdns,
        "wpa2_gtk": patch_wpa2_group_key,
        "eapol_group_ack": patch_eapol_group_ack,
        "group_cipher": patch_group_cipher,
        "eapol_ptk_rekey": patch_eapol_ptk_rekey,
        "vm_bounds": patch_vm_bounds,
        "linker_keep": patch_linker_keep,
        "gc_sections": patch_gc_sections,
        "audio_fifo_data": patch_audio_fifo_data,
        "audio_halfduplex": patch_audio_halfduplex,
        "bounded_waits": patch_bounded_waits,
        "wpa3_sae": patch_wpa3_sae,
        "usb_robustness": patch_usb_robustness,
        "extbss": patch_extbss,
        "no_libc_malloc": patch_no_libc_malloc,
        "audio_ampli": patch_audio_ampli,
        "rom_pages": patch_rom_pages,
        "inline_mkwav": patch_inline_mkwav,
        "strip_dump": patch_strip_dump,
        "strip_secho": patch_strip_secho,
        "strip_echo_funs": patch_strip_echo_funs,
        "strip_echo_opcodes": patch_strip_echo_opcodes,
        "inline_dump_calls": patch_inline_dump_calls,
        "prune_orphans": patch_prune_orphans,
    }
    for step in steps:
        step = step.strip()
        if not step:
            continue
        fn = fns.get(step)
        if not fn:
            sys.exit(f"unknown step: {step}")
        print(f"--- step: {step} ---")
        fn(root)
    print("--- all steps OK ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
