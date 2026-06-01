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
    not wired up on Kevin's rabbit anyway.
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
        choices=("full", "minimal", "signed-stock", "pages-only", "max", "mdns-only", "lean", "phase8-rom"),
        default="full",
        help=(
            "full         = everything (verify-enforced bootloader + modernized UI). "
            "minimal      = opcode + crypto only; boot.mtl stays byte-identical to "
            "vanilla wpa2. The minimal build is our rescue 'parachute' — "
            "almost the same firmware Kevin's sysadmin already flashed safely, "
            "plus the verifySig opcode for future OTAs. "
            "signed-stock = minimal + httpflash signature gate, NO page changes. "
            "Use after minimal proves the C-side is sane, to verify the "
            "bootloader patch in isolation. "
            "pages-only   = minimal + modernized UI, NO bootloader patch. "
            "Use to verify the page rewrite in isolation. "
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
        "full": "vbc,vinterp,stdlib,strip_tweetnacl,modernize_pages,makefile,bootloader,linker_keep",
        # Minimal omits modernize_pages + bootloader — boot.mtl untouched, so
        # the rabbit's flash + config-mode behavior is byte-equivalent to the
        # vanilla wpa2 branch HEAD. Only the C side gains the verifySig opcode
        # (dormant until called).
        "minimal": "vbc,vinterp,stdlib,strip_tweetnacl,makefile,linker_keep",
        # Bisection helpers — same C-side as minimal, only ONE boot.mtl mod
        # added. Use them when full has bricked the rabbit to pinpoint which
        # of the two boot.mtl patches is the culprit.
        "signed-stock": "vbc,vinterp,stdlib,strip_tweetnacl,makefile,bootloader,linker_keep",
        "pages-only":   "vbc,vinterp,stdlib,strip_tweetnacl,makefile,modernize_pages,linker_keep",
        # max = full + mdns. The everything-bag for a fresh flash.
        # NOTE: mdns is NEW and untested on hardware as of 2026-05-31
        # — flash mdns-only first as a less-invasive smoke test.
        "max":       "vbc,vinterp,stdlib,strip_tweetnacl,modernize_pages,makefile,bootloader,mdns,linker_keep",
        # mdns-only: vanilla boot.mtl + just the mDNS announcer. Use to
        # smoke-test the mdns add without entangling with bootloader/pages.
        "mdns-only": "vbc,vinterp,stdlib,strip_tweetnacl,makefile,mdns,linker_keep",
        # lean: max + gc-sections. Drops the dead newlib printf glue
        # (~6 KB) that DEBUG_*-stripping made unreachable but the
        # linker still kept (no DCE). Safe because linker_keep protects
        # .intvec / .startup.* / .ramfunc / .bytecode.*.
        "lean":      "vbc,vinterp,stdlib,strip_tweetnacl,modernize_pages,makefile,bootloader,mdns,linker_keep,gc_sections",
        # phase8-rom: instead of modernize_pages (which stores pages as
        # Metal globals), use rom_pages — pages live in C-side const ROM
        # and are rendered via the new OPpageRender opcode. Measures the
        # flash budget trade-off. Mutually exclusive with modernize_pages.
        "phase8-rom": "vbc,vinterp,stdlib,strip_tweetnacl,makefile,bootloader,rom_pages,prune_orphans,inline_mkwav,strip_dump,inline_dump_calls,strip_echo_funs,strip_secho,strip_echo_opcodes,linker_keep,gc_sections",
    }
    steps_str = args.steps if args.steps else default_steps[args.mode]
    steps = steps_str.split(",")
    print(f"=== apply-mods.py mode={args.mode} steps={steps_str} ===")
    fns = {
        "vbc": patch_vbc_h,
        "vinterp": patch_vinterp_c,
        "stdlib": patch_stdlib_core,
        "strip_tweetnacl": strip_tweetnacl,
        "modernize_pages": modernize_pages,
        "makefile": patch_makefile,
        "bootloader": patch_bootloader,
        "mdns": patch_mdns,
        "linker_keep": patch_linker_keep,
        "gc_sections": patch_gc_sections,
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
