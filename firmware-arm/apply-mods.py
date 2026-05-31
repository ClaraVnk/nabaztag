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

    HISTORICAL NOTE: an earlier version of this function also tried to
    enable --gc-sections via LDFLAGS — but the upstream `ml67q4051.ld`
    linker script has NO `KEEP()` directives around `.intvec` /
    `.startup.*` / `.ramfunc`, so --gc-sections is liable to prune the
    ARM7 interrupt vector table (only referenced by hardware, not by C).
    The old idempotency check `if "gc-sections" not in s` also
    accidentally matched the commented `#~ LDFLAGS += -Wl,--gc-sections`
    in upstream, so on a fresh clone the gc-sections flag was never
    actually added — saved us from shipping a vector-table-pruned build.
    Leaving gc-sections OUT until someone adds KEEP() to the linker
    script.
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
    # (where mdns_boot_tick is called). Anchor on `fun pagefill l p=`
    # which lives between the consts and loop in every variant.
    mdns_src = (root.parent / "boot-mods" / "mdns.mtl")
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="Path to the nabgcc checkout root")
    ap.add_argument(
        "--mode",
        choices=("full", "minimal", "signed-stock", "pages-only", "max", "mdns-only"),
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
            "mdns-only    = minimal + mdns; isolation bisect for the mdns add."
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

    default_steps = {
        "full": "vbc,vinterp,stdlib,strip_tweetnacl,modernize_pages,makefile,bootloader",
        # Minimal omits modernize_pages + bootloader — boot.mtl untouched, so
        # the rabbit's flash + config-mode behavior is byte-equivalent to the
        # vanilla wpa2 branch HEAD. Only the C side gains the verifySig opcode
        # (dormant until called).
        "minimal": "vbc,vinterp,stdlib,strip_tweetnacl,makefile",
        # Bisection helpers — same C-side as minimal, only ONE boot.mtl mod
        # added. Use them when full has bricked the rabbit to pinpoint which
        # of the two boot.mtl patches is the culprit.
        "signed-stock": "vbc,vinterp,stdlib,strip_tweetnacl,makefile,bootloader",
        "pages-only":   "vbc,vinterp,stdlib,strip_tweetnacl,makefile,modernize_pages",
        # max = full + mdns. The everything-bag for a fresh flash.
        # NOTE: mdns is NEW and untested on hardware as of 2026-05-31
        # — flash mdns-only first as a less-invasive smoke test.
        "max":       "vbc,vinterp,stdlib,strip_tweetnacl,modernize_pages,makefile,bootloader,mdns",
        # mdns-only: vanilla boot.mtl + just the mDNS announcer. Use to
        # smoke-test the mdns add without entangling with bootloader/pages.
        "mdns-only": "vbc,vinterp,stdlib,strip_tweetnacl,makefile,mdns",
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
