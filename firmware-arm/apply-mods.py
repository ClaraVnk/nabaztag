#!/usr/bin/env python3
"""Apply our custom modifications to a freshly cloned nabgcc tree.

Runs inside the build container after `git clone`. Idempotent: every step
checks first whether the change is already applied (so repeated runs are
safe, and "make clean" doesn't require re-cloning).

The script is intentionally chatty so the Docker build log shows what
landed.
"""

import argparse
import datetime
import os
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


def read_flat(path: Path) -> tuple[str, str]:
    """A source's text with LF endings, plus the ending it actually uses.

    Some of nabgcc's files are CRLF in the pristine tree and others are LF,
    and a step that anchors on "\n" quietly matches the second kind and not
    the first. That is how patch_wpa3_timing came to depend on another step
    having rewritten its file first, and how patch_flash_verify failed on
    mem.c, which is CRLF throughout. Flatten, patch, restore.
    """
    t = read_src(path)
    if "\r\n" in t:
        return t.replace("\r\n", "\n"), "\r\n"
    return t, "\n"


def write_flat(path: Path, text: str, nl: str) -> None:
    write_src(path, text if nl == "\n" else text.replace("\n", nl))


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
        '          /* Both operands stay on the stack until the verifier has\n'
        '           * run. The scratch allocation below can run the GC, and the\n'
        '           * GC compacts: every index taken before it is stale after\n'
        '           * it. The first version pulled sig off the stack and read\n'
        '           * payload through its old index, and the emulator showed\n'
        '           * what that does (2026-11-08, naboot-stable.signed.sim,\n'
        '           * 107,704-byte payload): the third collection of the OTA\n'
        '           * callback ran inside this allocation, VSIZEBIN(payload)\n'
        '           * then read -1,728,528, and nab_verify_sig called memcpy\n'
        '           * with it -- a segfault there, a data abort and a reset on\n'
        '           * the ARM7, with nothing written and no red light. */\n'
        '          int32_t sig=VALTOPNT(VSTACKGET(0));\n'
        '          int32_t payload=VALTOPNT(VSTACKGET(1));\n'
        '          int32_t ok=0;\n'
        '          nab_verify_calls++;\n'
        '          if ((payload!=NIL) && (sig!=NIL) && (VSIZEBIN(sig)==64))\n'
        '          {\n'
        '            /* payload+64 of scratch, on top of the 215 KB HTTP body and\n'
        '             * the payload itself: about 430 KB live at once for an OTA.\n'
        '             * When this allocation fails, ok stays 0 and the bytecode\n'
        '             * reads that as a BAD SIGNATURE -- a refusal for want of\n'
        '             * memory, indistinguishable from a forgery. Counted apart,\n'
        '             * because on 2026-11-07 that distinction was the whole\n'
        '             * question and nothing could answer it. */\n'
        '            int32_t scratch=VMALLOCBIN(VSIZEBIN(payload)+64);\n'
        '            if (scratch>=0)\n'
        '            {\n'
        '              /* Re-read after the allocation: see above. */\n'
        '              sig=VALTOPNT(VSTACKGET(0));\n'
        '              payload=VALTOPNT(VSTACKGET(1));\n'
        '              ok = nab_verify_sig(\n'
        '                  (const uint8_t*)VSTARTBIN(payload), VSIZEBIN(payload),\n'
        '                  (const uint8_t*)VSTARTBIN(sig),\n'
        '                  (void*)VSTARTBIN(scratch));\n'
        '            }\n'
        '            else\n'
        '              nab_verify_nomem++;\n'
        '          }\n'
        '          else nab_verify_badargs++;\n'
        '          if (ok)\n'
        '          {\n'
        '            nab_verify_ok++;\n'
        '            /* adoption/flash: remember the image this signature covers,\n'
        '               so that OPflashFirmware will flash only it and nothing\n'
        '               else. SHA-512 via crypto_hash, whose block loop already\n'
        '               feeds the watchdog (patch_verify_wdt). */\n'
        '            crypto_hash_sha512_tweet(nab_flash_fp,(const uint8_t*)VSTARTBIN(payload),VSIZEBIN(payload));\n'
        '            nab_flash_armed=1;\n'
        '          }\n'
        '          (void)VPULL();\n'
        '          VSTACKSET(0, INTTOVAL(ok));\n'
        '        }\n'
        '        break;\n'
    )
    if needle not in s:
        sys.exit(f"FAIL {p}: OPsndVol case anchor not found")
    s = s.replace(needle, needle + addition, 1)

    # Add the include at the top of the include block.
    inc_anchor = '#include "vbc.h"'
    # Four words of .bss so a refusal can be told apart from a forgery. The
    # opcode answers the bytecode with a bare 0 for "no" and the bytecode has
    # no else, so on 2026-11-07 an OTA that refused a perfectly good image
    # looked exactly like one that refused a tampered one -- and there was no
    # way to ask which. Read them over JTAG with `nm -S` on the resident ELF:
    #   calls 0            -> the bytecode never got as far as verifySig
    #   calls 1, badargs 1 -> payload or sig was nil, or the sig was not 64 B
    #   calls 1, nomem 1   -> the scratch allocation failed; NOT a bad signature
    #   calls 1, ok 0      -> the signature really does not match
    counters = ('int32_t nab_verify_calls;\n'
                'int32_t nab_verify_ok;\n'
                'int32_t nab_verify_nomem;\n'
                'int32_t nab_verify_badargs;\n'
                '/* adoption/flash: the image a verifySig accepted (SHA-512), and\n'
                '   whether one has been. OPflashFirmware flashes only a match, so a\n'
                '   runtime bytecode cannot hand junk or an unsigned image to the\n'
                '   flash -- the signature gate (fwota.mtl) is no longer the only one. */\n'
                'int32_t nab_flash_armed;\n'
                'uint8_t nab_flash_fp[64];\n'
                'extern int crypto_hash_sha512_tweet(unsigned char *,const unsigned char *,unsigned long long);\n'
                '/* 1 iff `img` is byte-for-byte the image the last verifySig accepted.\n'
                '   crypto_hash (SHA-512) feeds the watchdog as verifySig does; the\n'
                '   compare is constant time. OPflashFirmware gates on this. */\n'
                'static int nab_fw_verified(const unsigned char *img, int len)\n'
                '{\n'
                '  unsigned char fp[64]; int i, d = 0;\n'
                '  if (!nab_flash_armed) return 0;\n'
                '  crypto_hash_sha512_tweet(fp, img, (unsigned long long)len);\n'
                '  for (i = 0; i < 64; i++) d |= fp[i] ^ nab_flash_fp[i];\n'
                '  return d == 0;\n'
                '}\n')
    if '#include "nab_sig.h"' not in s:
        if inc_anchor in s:
            s = s.replace(inc_anchor, inc_anchor + '\n#include "nab_sig.h"', 1)
        else:
            s = '#include "nab_sig.h"\n' + s
    # Separately, and whichever branch placed the include: the counters have to
    # be defined or the file does not compile. The first version of this put
    # them in one branch only, the other branch is the one that runs, and the
    # dry run caught it.
    if 'nab_verify_calls' in s and 'int32_t nab_verify_calls;' not in s:
        s = s.replace('#include "nab_sig.h"\n',
                      '#include "nab_sig.h"\n\n' + counters, 1)

    # OPflashFirmware: flash only the image the last verifySig accepted, byte
    # for byte. The two magic words stay the "are you sure" they always were;
    # the signature binding is added to the same guard, in C, so a runtime
    # bytecode that skips fwota.mtl cannot reach the flash with junk. The guard
    # line is stock (patch_flash_verify rewrites the body below it, not this
    # line), so this composes whether or not flash_verify runs.
    guard_old = ("          if ((firmware!=NIL)&&(v==0x13fb6754)&&(w==0x0407FE58))\n")
    guard_new = ("          if ((firmware!=NIL)&&(v==0x13fb6754)&&(w==0x0407FE58)\n"
                 "              &&nab_fw_verified((const unsigned char*)VSTARTBIN(firmware),VSIZEBIN(firmware)))\n")
    if s.count(guard_old) != 1:
        sys.exit(f"FAIL {p}: OPflashFirmware's guard is not the shape this patch expects ({s.count(guard_old)})")
    s = s.replace(guard_old, guard_new, 1)

    p.write_text(s)
    print(f"[ok]   {p}: OPverifySig + nab_sig.h; flashFirmware bound to a verified image")


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


def patch_sign_open_aliasing(root: Path) -> None:
    """Let crypto_sign_open be called with one buffer, which is how we call it.

    `nab_sig.c` composes [sig || payload] in the scratch buffer and passes it
    as BOTH the input and the output of crypto_sign_open, on the stated
    grounds that "only the sig prefix gets overwritten by the message bytes".
    That is wrong, and it is the reason `verifySig` has never once returned 1:

        FOR(i,n) m[i] = sm[i];
        FOR(i,32) m[i+32] = pk[i];     <-- the public key lands on sm[32..63]
        crypto_hash_sha512_tweet(h,m,n);
        reduce(h);
        scalarmult(p,q,h);
        scalarbase(q,sm + 32);         <-- and this needs sm[32..63]

    With m == sm the second half of the signature is gone by the time
    scalarbase reads it, so the check fails for every key, every message and
    every length. Proven offline 2026-11-07: TweetNaCl could not verify its
    own signature over a five-byte message through that call, and succeeded
    immediately with two distinct buffers.

    So no signed .sim has ever installed on this hardware -- not over OTA, not
    through the signature-gated httpflash -- which is the whole of
    ClaraVnk/nabaztag's five months of flashes that wrote nothing.

    The fix keeps the one-buffer API, because the alternative is a second
    buffer the size of the image (107 KB) on a device that has little to
    spare. Thirty-two bytes of stack hold the half of the signature that
    scalarbase needs. Upstream is untouched on disk; this patches the copy the
    Dockerfile puts in the build tree, so the change is visible here rather
    than hidden in a vendored file.
    """
    p = root / "src/crypto/tweetnacl.c"
    t = p.read_text()
    if "/* alias-safe */" in t:
        print(f"[skip] {p}: crypto_sign_open is already alias-safe")
        return

    a = """  FOR(i,n) m[i] = sm[i];
  FOR(i,32) m[i+32] = pk[i];"""
    if t.count(a) != 1:
        sys.exit(f"FAIL {p}: crypto_sign_open is not the shape this patch expects")
    t = t.replace(a, """  /* alias-safe: callers pass m == sm, and the line below drops the public
     key on sm[32..63], which scalarbase still has to read. Keep it. */
  u8 sig2[32];
  FOR(i,32) sig2[i] = sm[32+i];
  FOR(i,n) m[i] = sm[i];
  FOR(i,32) m[i+32] = pk[i];""", 1)

    b = "  scalarbase(q,sm + 32);"
    if t.count(b) != 1:
        sys.exit(f"FAIL {p}: the scalarbase call is not where it was")
    t = t.replace(b, "  scalarbase(q,sig2);", 1)

    p.write_text(t)
    print(f"[ok]   {p}: crypto_sign_open survives m == sm")



def patch_verify_wdt(root: Path) -> None:
    """Feed the watchdog from inside the signature check.

    verifySig is one opcode, and the interpreter's CLR_WDT runs once per
    opcode (vinterp.c:86). Nothing else feeds the watchdog -- no interrupt
    handler does -- so the whole of crypto_sign_open has to finish inside one
    2.09 s window, or the chip resets with the middle belly light still blue.

    It cannot. Measured 2026-11-08 under qemu-arm, with nab_sig.c and this
    tweetnacl.c compiled with the firmware's own flags (-O1 -mthumb
    -mcpu=arm7tdmi) and given naboot-stable's 107,704-byte image: the
    verification executes 247,618,040 instructions. At the APB clock the
    watchdog is derived from (32 MHz, main.c wdt_start), that is 7.7 s even
    at one cycle per instruction, which an ARM7TDMI running from flash does
    not get near. So an OTA on any image without this patch dies in
    verifySig a little over two seconds after the middle light turns blue,
    exactly as a stale heap index does (see the vinterp step), and the two
    cannot be told apart from the belly.

    The work is in four loops: SHA-512 over the image, 842 blocks of 128
    bytes; two scalar multiplications of 256 steps each; and the two field
    inversions around them, 254 and 251 steps. One tick per iteration is
    1,860 ticks; the longest stretch without one is 812,960 instructions,
    the two 107 KB copies before the first (qemu-arm, same build) -- 25 ms
    at 32 MHz and one cycle per instruction, 0.1 s at four. The tick is nab_verify_tick() in nab_sig.c, which is
    CLR_WDT on the hardware and nothing in the emulator.
    """
    p = root / "src/crypto/tweetnacl.c"
    t = p.read_text()
    # The marker is the first tick itself, not the bare name: x25519_wdt
    # declares nab_verify_tick too, and when it ran first this step took the
    # declaration for its own work and skipped. Every adoption image of
    # 2026-11-08 shipped so -- two ticks instead of four, and an OTA that the
    # watchdog reset in the middle of verifySig, on hardware only (the
    # emulator has no watchdog).
    if "nab_verify_tick();\n    FOR(i,16) w[i] = dl64(m + 8 * i);" in t:
        print(f"[skip] {p}: the verifier already feeds the watchdog")
        return

    a = "  while (n >= 128) {\n    FOR(i,16) w[i] = dl64(m + 8 * i);\n"
    if t.count(a) != 1:
        sys.exit(f"FAIL {p}: crypto_hashblocks' block loop is not the shape this patch expects")
    t = t.replace(a, "  while (n >= 128) {\n    nab_verify_tick();\n"
                     "    FOR(i,16) w[i] = dl64(m + 8 * i);\n", 1)

    b = "  for (i = 255;i >= 0;--i) {\n    u8 b = (s[i/8]>>(i&7))&1;\n"
    if t.count(b) != 1:
        sys.exit(f"FAIL {p}: scalarmult's bit loop is not the shape this patch expects")
    t = t.replace(b, "  for (i = 255;i >= 0;--i) {\n    nab_verify_tick();\n"
                     "    u8 b = (s[i/8]>>(i&7))&1;\n", 1)

    # The two field inversions: unpackneg's pow2523 before the first hash
    # block and pack's inv25519 after the last scalar step. Measured the same
    # way, without these two: 11,715,152 instructions before the first tick.
    for name, loop in (("inv25519", "  for(a=253;a>=0;a--) {\n    S(c,c);\n"),
                       ("pow2523",  "  for(a=250;a>=0;a--) {\n    S(c,c);\n")):
        if t.count(loop) != 1:
            sys.exit(f"FAIL {p}: {name}'s loop is not the shape this patch expects")
        head, tail = loop.split("\n", 1)
        t = t.replace(loop, head + "\n    nab_verify_tick();\n" + tail, 1)

    inc = '#include "tweetnacl.h"\n'
    if inc not in t:
        sys.exit(f"FAIL {p}: no tweetnacl.h include to hang the declaration on")
    if "extern void nab_verify_tick(void);" not in t:
        t = t.replace(inc, inc + "/* The watchdog, from nab_sig.c; see patch_verify_wdt. */\n"
                                 "extern void nab_verify_tick(void);\n", 1)
    p.write_text(t)
    print(f"[ok]   {p}: the signature check feeds the watchdog 1,860 times")


def patch_x25519_wdt(root: Path) -> None:
    """Keep X25519 and make its ladder feed the watchdog; add nab_kx.

    The adoption key exchange (crypto/nab_kx.c) runs two X25519 scalar
    multiplications on the rabbit, once per adoption. TweetNaCl's ladder is
    63.6 M instructions with nothing feeding the watchdog (tools/cost,
    2026-11-08): 2-8 s against a 2.09 s window, so it would reset the rabbit
    every time. One nab_verify_tick() per ladder step makes that 255 ticks,
    ~250 k instructions apart; the inversion after it is inv25519, which
    verify_wdt already ticks.

    strip_tweetnacl drops crypto_scalarmult unless this step has marked the
    file first, so this must come before it in a mode's list.

    nab_kx.c and its header go into src/net and inc/net beside the SAE
    crypto, whose sha256 they use. Tests: firmware-arm/tests, `make kx`.
    """
    p = root / "src/crypto/tweetnacl.c"
    t = p.read_text()
    if "/* nab: keep crypto_scalarmult */" in t:
        print(f"[skip] {p}: X25519 already kept and ticked")
        return
    if "/* tweetnacl-stripped */" in t:
        sys.exit(f"FAIL {p}: strip_tweetnacl ran first and removed X25519 -- "
                 "put x25519_wdt before it")
    a = "  for(i=254;i>=0;--i) {\n    r=(z[i>>3]>>(i&7))&1;\n"
    if t.count(a) != 1:
        sys.exit(f"FAIL {p}: crypto_scalarmult's ladder is not the shape this patch expects")
    t = t.replace(a, "  for(i=254;i>=0;--i) {\n    nab_verify_tick();\n"
                     "    r=(z[i>>3]>>(i&7))&1;\n", 1)
    inc = '#include "tweetnacl.h"\n'
    if inc not in t:
        sys.exit(f"FAIL {p}: no tweetnacl.h include to hang the declaration on")
    t = t.replace(inc, inc + "/* nab: keep crypto_scalarmult */\n"
                             "extern void nab_verify_tick(void);\n", 1)
    p.write_text(t)
    here = _staged_crypto(root)
    (root / "src/net").mkdir(parents=True, exist_ok=True)
    (root / "inc/net").mkdir(parents=True, exist_ok=True)
    (root / "src/net/nab_kx.c").write_text((here / "nab_kx.c").read_text())
    (root / "inc/net/nab_kx.h").write_text((here / "nab_kx.h").read_text())
    print(f"[ok]   {p}: X25519 kept, its ladder feeds the watchdog; nab_kx added")


def _staged_crypto(root: Path) -> Path:
    """Where the build containers stage our crypto sources (see Dockerfile:
    /build/sae-crypto, outside the nabgcc tree); the repo's crypto/ when
    apply-mods runs from a checkout, as tests/Makefile does."""
    staging = root.parent / "sae-crypto"
    if (staging / "nab_kx.c").exists():
        return staging
    return Path(__file__).resolve().parent / "crypto"


def add_core_opcode(root: Path, name: str, sig: str, nargs: int, case: str) -> None:
    """Register one more core opcode, numbered after whatever is there.

    The earlier opcode steps hard-code their number and their /*N*/ slot,
    which is why rom_pages and wpa3_boot_pw both claimed 153. This takes
    the next free number in vbc.h and the next slot in each of the four
    stdlib_core.cpp arrays, so steps can stack in any mode.
    """
    op = "OP" + name
    nums = []
    for q in (root / "inc/vm/vbc.h", root / "mtl/mtl_linux/inc/vm/vbc.h"):
        t = q.read_text()
        if f"#define {op} " in t:
            nums.append(int(re.search(rf"#define {op} (\d+)", t).group(1)))
            continue
        n = max(int(m) for m in re.findall(r"#define OP\w+ (\d+)", t)) + 1
        last = list(re.finditer(r"#define OP\w+ \d+\n", t))[-1]
        t = t[:last.end()] + f"#define {op} {n}\n" + t[last.end():]
        q.write_text(t)
        nums.append(n)
    if nums[0] != nums[1]:
        sys.exit(f"FAIL vbc.h: the two copies disagree on {op} ({nums})")

    q = root / "src/vm/vinterp.c"
    t = q.read_text()
    if f"case {op}:" not in t:
        needle = "      case OPverifySig:\n"
        if needle not in t:
            sys.exit(f"FAIL {q}: OPverifySig case missing -- run the vinterp step first")
        t = t.replace(needle, case + needle, 1)
        q.write_text(t)

    q = root / "mtl/mtl_linux/src/vcomp/stdlib_core.cpp"
    t = q.read_text()
    if f'"{name}"' not in t:
        nb = re.search(r"#define NBcore (\d+)", t)
        t = t[:nb.start(1)] + str(int(nb.group(1)) + 1) + t[nb.end(1):]
        tails = list(re.finditer(r"/\*(\d+)\*/([^\n]*)\n\};", t))
        top = max(int(m.group(1)) for m in tails)
        tails = [m for m in tails if int(m.group(1)) == top]
        if len(tails) != 4:
            sys.exit(f"FAIL {q}: expected four arrays ending at /*{top}*/, found {len(tails)}")
        values = [f'"{name}"', f"{op},", str(nargs), f'"{sig}"']
        for m, v in sorted(zip(tails, values), key=lambda mv: -mv[0].start()):
            last = m.group(2)
            joined = last if last.endswith(",") else last + ","
            t = (t[:m.start(2)] + joined + f"\n/*{top + 1}*/{v}\n" + "};" + t[m.end():])
        q.write_text(t)
    print(f"[ok]   core opcode {name} = {nums[0]}, {sig}")


def patch_adoption_kx(root: Path) -> None:
    """The bootloader's side of the adoption key exchange: three opcodes.

    See ADOPTION-KEY.md for the protocol and why the bootloader, not the
    runtime, owns it. Requires x25519_wdt (nab_kx.c) and wpa3_sae (SHA-256,
    and the random stream ieee80211_rng_bytes exposes).

      kxBegin            -> the rabbit's public key (32 bytes); the private
                            key stays in C
      kxFinish warrenpub -> key (32) followed by the five colours (5), or nil
      kxUse key          -> 1: the key the HMAC and TLS opcodes will use
      kxCheck data mac   -> 1 if mac is HMAC-SHA-256(key, data)

    All three refuse once OPbytecode has run -- the bootloader has handed
    over to the warren's runtime -- and from then on `load` returns zeros
    where the key is stored (bytes 224 to 256) and `save` refuses to write
    there, whatever filename the opcode names: there is one flash page, and
    sysLoad/sysSave ignore the name, so keying the guard on "conf.bin" (as
    the first version did) let a runtime bytecode read the key back under
    any other name, and write it under any name at all -- the runtime that
    runs these opcodes is the warren's, not ours. Not 208: the runtime
    keeps its XMPP password there (main.mtl, 208-220), and hiding or
    overwriting it made a rabbit re-register on hardware, 2026-11-08.
    """
    if not (root / "src/net/nab_kx.c").exists():
        sys.exit("FAIL adoption_kx: nab_kx.c missing -- x25519_wdt must run first")
    ie = root / "src/net/ieee80211.c"
    t = read_src(ie)
    if "ieee80211_sae_rng" not in t:
        sys.exit(f"FAIL {ie}: adoption_kx needs wpa3_sae's random stream")
    if "ieee80211_rng_bytes" not in t:
        nl = "\r\n" if "\r\n" in t else "\n"
        t += (nl + "/* adoption_kx: the same stream for the adoption key. */" + nl
              + "void ieee80211_rng_bytes(uint8_t *out, uint32_t len)" + nl
              + "{" + nl + "  ieee80211_sae_rng(out, len, NULL);" + nl + "}" + nl)
        write_src(ie, t)
    here = _staged_crypto(root)
    (root / "src/net/nab_kx_vm.c").write_text((here / "nab_kx_vm.c").read_text())

    decl = ("int nab_kx_vm_begin(uint8_t *pub);\n"
            "int nab_kx_vm_finish(const uint8_t *wpub, uint8_t *key, uint8_t *check);\n"
            "int nab_kx_vm_use(const uint8_t *key);\n"
            "int nab_kx_vm_check(const uint8_t *data, uint32_t len, const uint8_t *mac);\n"
            "extern uint8_t nab_kx_latched;\n")
    q = root / "src/vm/vinterp.c"
    t = q.read_text()
    if "nab_kx_vm_begin" not in t:
        if "#include <string.h>\n" not in t:
            sys.exit(f"FAIL {q}: no <string.h> include to hang the declarations on")
        t = t.replace("#include <string.h>\n", "#include <string.h>\n" + decl, 1)
        q.write_text(t)

    add_core_opcode(root, "kxBegin", "fun[]S", 0,
        "      case OPkxBegin:\n"
        "        {\n"
        "          /* Two to eight seconds of X25519; the ladder feeds the watchdog. */\n"
        "          uint8_t pub[32];\n"
        "          if (nab_kx_vm_begin(pub)) VPUSH(PNTTOVAL(VMALLOCSTR((char*)pub,32)));\n"
        "          else VPUSH(NIL);\n"
        "        }\n"
        "        break;\n")
    add_core_opcode(root, "kxFinish", "fun[S]S", 1,
        "      case OPkxFinish:\n"
        "        {\n"
        "          int32_t w=VALTOPNT(VSTACKGET(0));\n"
        "          uint8_t out[37];\n"
        "          int32_t r=NIL;\n"
        "          if (w!=NIL && VSIZEBIN(w)==32)\n"
        "          {\n"
        "            uint8_t wpub[32];\n"
        "            memcpy(wpub,VSTARTBIN(w),32);   /* VMALLOCSTR may move the heap */\n"
        "            if (nab_kx_vm_finish(wpub,out,out+32)) r=PNTTOVAL(VMALLOCSTR((char*)out,37));\n"
        "            memset(out,0,sizeof out);\n"
        "          }\n"
        "          VSTACKSET(0,r);\n"
        "        }\n"
        "        break;\n")
    add_core_opcode(root, "kxUse", "fun[S]I", 1,
        "      case OPkxUse:\n"
        "        {\n"
        "          int32_t k=VALTOPNT(VSTACKGET(0));\n"
        "          int32_t ok=0;\n"
        "          if (k!=NIL && VSIZEBIN(k)==32) ok=nab_kx_vm_use((const uint8_t*)VSTARTBIN(k));\n"
        "          VSTACKSET(0,INTTOVAL(ok));\n"
        "        }\n"
        "        break;\n")

    add_core_opcode(root, "kxCheck", "fun[S S]I", 2,
        "      case OPkxCheck:\n"
        "        {\n"
        "          /* HMAC-SHA-256 of the bytecode under the kxUse key; ~1 s,\n"
        "             ticking the watchdog every 4 KB. Says yes or no only. */\n"
        "          int32_t m=VALTOPNT(VPULL());\n"
        "          int32_t d=VALTOPNT(VSTACKGET(0));\n"
        "          int32_t ok=0;\n"
        "          if (d!=NIL && m!=NIL && VSIZEBIN(m)==32)\n"
        "            ok=nab_kx_vm_check((const uint8_t*)VSTARTBIN(d),VSIZEBIN(d),(const uint8_t*)VSTARTBIN(m));\n"
        "          VSTACKSET(0,INTTOVAL(ok));\n"
        "        }\n"
        "        break;\n")

    # The latch, at the hand-over, and the hiding in `load`.
    t = q.read_text()
    a = '        DBG_VM(EOL"#################### OPbytecode"EOL);\n'
    if "nab_kx_latched=1" not in t:
        if t.count(a) != 1:
            sys.exit(f"FAIL {q}: OPbytecode is not the shape adoption_kx expects")
        t = t.replace(a, a + "        nab_kx_latched=1;   /* adoption_kx: the runtime is the warren's */\n", 1)
    b = ("            len=sysLoad(VSTARTBIN(p),index,VSIZEBIN(p),VSTARTBIN(q),start,len);\n")
    if "whatever filename the load names" not in t:
        if t.count(b) != 1:
            sys.exit(f"FAIL {q}: OPload is not the shape adoption_kx expects")
        t = t.replace(b, b + (
            "            /* adoption_kx: after the hand-over the key is not readable,\n"
            "               whatever filename the load names -- one flash page, and no\n"
            "               runtime load has reason to read bytes 224..256. */\n"
            "            if (nab_kx_latched && len>0)\n"
            "            {\n"
            "              int32_t i;\n"
            "              for (i=0;i<len;i++)\n"
            "                if (start+i>=224 && start+i<257) VSTARTBIN(p)[index+i]=0;\n"
            "            }\n"), 1)
    sv = ("            len=sysSave(VSTARTBIN(p),index,VSIZEBIN(p),VSTARTBIN(q),start,len);\n")
    if "the key region is not writable" not in t:
        if t.count(sv) != 1:
            sys.exit(f"FAIL {q}: OPsave is not the shape adoption_kx expects")
        t = t.replace(sv, (
            "            /* adoption_kx: after the hand-over the key region is not\n"
            "               writable, whatever filename -- no runtime save touches\n"
            "               224..256, so a bytecode cannot overwrite the key or forge\n"
            "               the confirmed flag. */\n"
            "            if (nab_kx_latched && len>0 && start<257 && start+len>224)\n"
            "              len=0;\n"
            "            else\n"
            "              len=sysSave(VSTARTBIN(p),index,VSIZEBIN(p),VSTARTBIN(q),start,len);\n"), 1)
    q.write_text(t)
    print(f"[ok]   adoption_kx: kxBegin/kxFinish/kxUse, latched at OPbytecode, key hidden from load and save")


def patch_tls_psk(root: Path) -> None:
    """TLS 1.3 with the adoption key on the XMPP connection: five opcodes.

    See crypto/nab_tls.h and ADOPTION-KEY.md. Requires adoption_kx (the key,
    nab_kx_vm.c) and wpa3_sae (SHA-256 and cmac.c's AES block, which GCM
    runs on). The TCP/IP stack and the XMPP client are Metal bytecode, so
    the seam is where that code reads and writes its socket
    (bytecode/patch_tls.py); records, keys and secrets stay in C.

      tlsStart id   -> the ClientHello record for PSK identity `id` (the
                       MAC as 12 hex digits), nil without a key
      tlsIn data    -> feed bytes from the socket (or nil), then the
                       plaintext of the next decrypted application record,
                       nil when none is complete; loop until nil
      tlsOut data   -> records for `data`; with nil, the handshake output
                       and alerts waiting to be written, or nil
      tlsState      -> -1 no key, 0 idle, 1 handshake, 2 ready, 3 failed,
                       4 closed
      tlsClose      -> the close_notify record, nil unless ready

    Memory: 7 KB of `.extbss` for the receive buffer (one record of
    max_fragment_length 4096 plus one TCP segment), the handshake
    reassembly and the state; nothing on the 16 KB internal SRAM. Time:
    AES-128-GCM at 849 k instructions per KB (tools/cost), so a 4 KB
    record is ~3.4 M instructions inside one opcode; the GCM loop feeds the
    watchdog every 16 blocks.
    """
    if not (root / "src/net/nab_kx_vm.c").exists():
        sys.exit("FAIL tls_psk: nab_kx_vm.c missing -- adoption_kx must run first")
    if not (root / "src/net/cmac.c").exists():
        sys.exit("FAIL tls_psk: cmac.c missing -- wpa3_sae must run first")
    here = _staged_crypto(root)
    (root / "src/net/nab_tls.c").write_text((here / "nab_tls.c").read_text())
    (root / "src/net/nab_tls_vm.c").write_text((here / "nab_tls_vm.c").read_text())
    (root / "inc/net/nab_tls.h").write_text((here / "nab_tls.h").read_text())

    decl = ("int nab_tls_vm_state(void);\n"
            "int nab_tls_vm_start(const uint8_t *id, uint32_t idlen, uint8_t *out, uint32_t cap);\n"
            "int nab_tls_in(const uint8_t *data, uint32_t len);\n"
            "int nab_tls_next(void);\n"
            "void nab_tls_take(uint8_t *dst);\n"
            "int nab_tls_pending(uint8_t *out, uint32_t cap);\n"
            "uint32_t nab_tls_out_size(uint32_t len);\n"
            "int nab_tls_out(const uint8_t *in, uint32_t len, uint8_t *out, uint32_t cap);\n"
            "int nab_tls_close(uint8_t *out, uint32_t cap);\n")
    q = root / "src/vm/vinterp.c"
    t = q.read_text()
    if "nab_tls_vm_start" not in t:
        if "#include <string.h>\n" not in t:
            sys.exit(f"FAIL {q}: no <string.h> include to hang the declarations on")
        t = t.replace("#include <string.h>\n", "#include <string.h>\n" + decl, 1)
        q.write_text(t)

    add_core_opcode(root, "tlsStart", "fun[S]S", 1,
        "      case OPtlsStart:\n"
        "        {\n"
        "          /* The ClientHello: 97 bytes plus the identity. */\n"
        "          int32_t id=VALTOPNT(VSTACKGET(0));\n"
        "          uint8_t ch[160];\n"
        "          int32_t n=0;\n"
        "          if (id!=NIL && VSIZEBIN(id)<=32) n=nab_tls_vm_start((const uint8_t*)VSTARTBIN(id),VSIZEBIN(id),ch,sizeof ch);\n"
        "          VSTACKSET(0,n>0?PNTTOVAL(VMALLOCSTR((char*)ch,n)):NIL);\n"
        "        }\n"
        "        break;\n")
    add_core_opcode(root, "tlsIn", "fun[S]S", 1,
        "      case OPtlsIn:\n"
        "        {\n"
        "          /* Feed the segment, then hand back one decrypted record. The\n"
        "             buffer holds a record and a whole segment, so what a\n"
        "             drained buffer does not take is a segment larger than the\n"
        "             MSS, which this stack never produces: dropped. */\n"
        "          int32_t d=VALTOPNT(VSTACKGET(0));\n"
        "          int32_t r;\n"
        "          if (d!=NIL && VSIZEBIN(d)>0) nab_tls_in((const uint8_t*)VSTARTBIN(d),VSIZEBIN(d));\n"
        "          r=nab_tls_next();\n"
        "          if (r>=0)\n"
        "          {\n"
        "            int32_t p=VMALLOCBIN(r);\n"
        "            if (p!=NIL) nab_tls_take(VSTARTBIN(p));\n"
        "            VSTACKSET(0,p!=NIL?PNTTOVAL(p):NIL);\n"
        "          }\n"
        "          else VSTACKSET(0,NIL);\n"
        "        }\n"
        "        break;\n")
    add_core_opcode(root, "tlsOut", "fun[S]S", 1,
        "      case OPtlsOut:\n"
        "        {\n"
        "          int32_t d=VALTOPNT(VSTACKGET(0));\n"
        "          int32_t r=NIL;\n"
        "          if (d==NIL)\n"
        "          {\n"
        "            uint8_t pend[64];\n"
        "            int32_t n=nab_tls_pending(pend,sizeof pend);\n"
        "            if (n>0) r=PNTTOVAL(VMALLOCSTR((char*)pend,n));\n"
        "          }\n"
        "          else\n"
        "          {\n"
        "            uint32_t size=nab_tls_out_size(VSIZEBIN(d));\n"
        "            int32_t p=VMALLOCBIN(size);   /* may move the heap: re-read d */\n"
        "            d=VALTOPNT(VSTACKGET(0));\n"
        "            if (p!=NIL && nab_tls_out((const uint8_t*)VSTARTBIN(d),VSIZEBIN(d),VSTARTBIN(p),size)>0) r=PNTTOVAL(p);\n"
        "          }\n"
        "          VSTACKSET(0,r);\n"
        "        }\n"
        "        break;\n")
    add_core_opcode(root, "tlsState", "fun[]I", 0,
        "      case OPtlsState:\n"
        "        VPUSH(INTTOVAL(nab_tls_vm_state()));\n"
        "        break;\n")
    add_core_opcode(root, "tlsClose", "fun[]S", 0,
        "      case OPtlsClose:\n"
        "        {\n"
        "          uint8_t a[32];\n"
        "          int32_t n=nab_tls_close(a,sizeof a);\n"
        "          VPUSH(n>0?PNTTOVAL(VMALLOCSTR((char*)a,n)):NIL);\n"
        "        }\n"
        "        break;\n")
    print("[ok]   tls_psk: tlsStart/tlsIn/tlsOut/tlsState/tlsClose on the adoption key")


def patch_tls_certs(root: Path) -> None:
    """TLS 1.3 with a server's certificate: the rabbit fetches HTTPS itself.

    2026-11-12, for Naboot 1.2.0.0. Until now a podcast reached the rabbit
    through the warren, which fetched it over HTTPS and relayed it in the
    clear or inside the session: the warren's bandwidth and processor, for
    every rabbit listening. The rabbit now opens the host itself. nab_tls.c
    gets its second session (NAB_TLS_CERTS): X25519, ChaCha20-Poly1305 then
    AES-128-GCM, the certificate chain checked up to the root the warren
    sends with the order to play (nab_https_vm.c), the name, the dates,
    CertificateVerify. Proven on a host against openssl s_server
    (tests/tls_cert_interop.py), 16 KB records included.

    Opcodes, all on session 1 and giving session 0 back:
      httpsStart host now roots -> ClientHello, or nil
      httpsIn segment           -> one decrypted record, or nil
      httpsOut data|nil         -> records for data; with nil, what is pending
      httpsState                -> nab_tls state
      httpsClose                -> close_notify

    Memory: the 16 KB-record receive buffer and the chain reassembly (26 KB)
    do not fit beside .extbss, so they get .extbss2, 32 KB taken from the
    BOTTOM of the VM heap: the heap starts at 0xD0018000 and is 32 KB
    shorter (768 KB). Its top, where the VM stack lives, does not move, so
    no RAM is used that the rabbit did not use before. Requires tls_psk.
    """
    if not (root / "src/net/nab_tls.c").exists():
        sys.exit("FAIL tls_certs: tls_psk must run first")
    here = _staged_crypto(root)
    for f in ("nab_bn.c", "nab_rsa.c", "nab_ecdsa.c", "nab_x509.c", "nab_sha384.c",
              "chachapoly.c", "nab_https_vm.c", "nab_tls.c"):
        (root / "src/net" / f).write_text((here / f).read_text())
    for f in ("nab_pki.h", "chachapoly.h"):
        (root / "inc/net" / f).write_text((here / f).read_text())
    h = (here / "nab_tls.h").read_text()
    guard = "#ifndef NAB_TLS_H\n"
    if h.count(guard) != 1:
        sys.exit("FAIL tls_certs: nab_tls.h guard not found")
    (root / "inc/net/nab_tls.h").write_text(h.replace(
        guard, "/* tls_certs (apply-mods.py) */\n#define NAB_TLS_CERTS 1\n#define NAB_TLS_EXTBSS2 1\n" + guard, 1))

    # The VM heap moves up by 32 KB; its end, and the stack there, stay.
    q = root / "inc/vm/vmem.h"
    t = q.read_text()
    if "0xD0018000" not in t:
        for old, new in (("#define VMEM_LENGTH\t            (1024*200)",
                          "#define VMEM_LENGTH\t            (1024*192)   /* tls_certs: 32 KB to .extbss2 */"),
                         ("#define _vmem_heap               ((int32_t*)(0xD0010000))",
                          "#define _vmem_heap               ((int32_t*)(0xD0018000))   /* tls_certs */")):
            if t.count(old) != 1:
                sys.exit(f"FAIL {q}: {old!r} not found once")
            t = t.replace(old, new, 1)
        q.write_text(t)
    q = root / "inc/utils/mem.h"
    t = q.read_text()
    old = "#define SRAM_BASE   (0xD0010000)"
    if old in t:
        q.write_text(t.replace(old, "#define SRAM_BASE   (0xD0018000)   /* tls_certs */", 1))

    ld = root / "sys/ml67q4051.ld"
    s = ld.read_text()
    if ".extbss2" not in s:
        a = "  ExtRAM  (rwx) : ORIGIN = 0xD0000000, LENGTH = 32k\n"
        if s.count(a) != 1:
            sys.exit(f"FAIL {ld}: extbss must run first")
        s = s.replace(a, a + "  /* tls_certs: the bottom 32 KB of what was the VM heap */\n"
                             "  ExtRAM2 (rwx) : ORIGIN = 0xD0010000, LENGTH = 32k\n", 1)
        b = "SECTIONS\n{\n"
        if s.count(b) != 1:
            sys.exit(f"FAIL {ld}: no SECTIONS block")
        s = s.replace(b, b + "  .extbss2 (NOLOAD) :\n  {\n    *(.extbss2)\n    *(.extbss2.*)\n"
                             "  } >ExtRAM2\n  __vm_heap_origin__ = 0xD0018000;\n\n", 1)
        ld.write_text(s)

    decl = ("int nab_https_vm_start(const uint8_t *host, uint32_t hostlen, const uint8_t *now, uint32_t nowlen,\n"
            "                       const uint8_t *der, uint32_t derlen, uint8_t *out, uint32_t cap);\n"
            "int nab_https_vm_in(const uint8_t *data, uint32_t len);\n"
            "void nab_https_vm_take(uint8_t *dst);\n"
            "int nab_https_vm_pending(uint8_t *out, uint32_t cap);\n"
            "int nab_https_vm_out(const uint8_t *in, uint32_t len, uint8_t *out, uint32_t cap);\n"
            "int nab_https_vm_state(void);\n"
            "int nab_https_vm_close(uint8_t *out, uint32_t cap);\n")
    q = root / "src/vm/vinterp.c"
    t = q.read_text()
    if "nab_https_vm_start" not in t:
        t = t.replace("#include <string.h>\n", "#include <string.h>\n" + decl, 1)
        q.write_text(t)
    add_core_opcode(root, "httpsStart", "fun[S S S]S", 3,
        "      case OPhttpsStart:\n"
        "        {\n"
        "          /* host, \"YYYYMMDDHHMMSS\", the roots (DER, end to end) */\n"
        "          int32_t r=VALTOPNT(VPULL());\n"
        "          int32_t w=VALTOPNT(VPULL());\n"
        "          int32_t h=VALTOPNT(VSTACKGET(0));\n"
        "          uint8_t ch[192];\n"
        "          int32_t n=0;\n"
        "          if (h!=NIL && w!=NIL && r!=NIL)\n"
        "            n=nab_https_vm_start((const uint8_t*)VSTARTBIN(h),VSIZEBIN(h),(const uint8_t*)VSTARTBIN(w),VSIZEBIN(w),\n"
        "                                 (const uint8_t*)VSTARTBIN(r),VSIZEBIN(r),ch,sizeof ch);\n"
        "          VSTACKSET(0,n>0?PNTTOVAL(VMALLOCSTR((char*)ch,n)):NIL);\n"
        "        }\n"
        "        break;\n")
    add_core_opcode(root, "httpsIn", "fun[S]S", 1,
        "      case OPhttpsIn:\n"
        "        {\n"
        "          int32_t d=VALTOPNT(VSTACKGET(0));\n"
        "          int32_t r=nab_https_vm_in(d!=NIL?(const uint8_t*)VSTARTBIN(d):0,d!=NIL?VSIZEBIN(d):0);\n"
        "          if (r>=0)\n"
        "          {\n"
        "            int32_t p=VMALLOCBIN(r);\n"
        "            if (p!=NIL) nab_https_vm_take(VSTARTBIN(p));\n"
        "            VSTACKSET(0,p!=NIL?PNTTOVAL(p):NIL);\n"
        "          }\n"
        "          else VSTACKSET(0,NIL);\n"
        "        }\n"
        "        break;\n")
    add_core_opcode(root, "httpsOut", "fun[S]S", 1,
        "      case OPhttpsOut:\n"
        "        {\n"
        "          int32_t d=VALTOPNT(VSTACKGET(0));\n"
        "          int32_t r=NIL;\n"
        "          if (d==NIL)\n"
        "          {\n"
        "            uint8_t pend[64];\n"
        "            int32_t n=nab_https_vm_pending(pend,sizeof pend);\n"
        "            if (n>0) r=PNTTOVAL(VMALLOCSTR((char*)pend,n));\n"
        "          }\n"
        "          else\n"
        "          {\n"
        "            uint32_t size=nab_tls_out_size(VSIZEBIN(d));\n"
        "            int32_t p=VMALLOCBIN(size);   /* may move the heap: re-read d */\n"
        "            d=VALTOPNT(VSTACKGET(0));\n"
        "            if (p!=NIL && nab_https_vm_out((const uint8_t*)VSTARTBIN(d),VSIZEBIN(d),VSTARTBIN(p),size)>0) r=PNTTOVAL(p);\n"
        "          }\n"
        "          VSTACKSET(0,r);\n"
        "        }\n"
        "        break;\n")
    add_core_opcode(root, "httpsState", "fun[]I", 0,
        "      case OPhttpsState:\n"
        "        VPUSH(INTTOVAL(nab_https_vm_state()));\n"
        "        break;\n")
    add_core_opcode(root, "httpsClose", "fun[]S", 0,
        "      case OPhttpsClose:\n"
        "        {\n"
        "          uint8_t a[32];\n"
        "          int32_t n=nab_https_vm_close(a,sizeof a);\n"
        "          VPUSH(n>0?PNTTOVAL(VMALLOCSTR((char*)a,n)):NIL);\n"
        "        }\n"
        "        break;\n")
    print("[ok]   tls_certs: httpsStart/In/Out/State/Close, .extbss2 at 0xD0010000, VM heap from 0xD0018000")


def patch_adoption_boot(root: Path) -> None:
    """The adoption flow in the bootloader: boot-mods/adoption.mtl.

    Requires adoption_kx (the opcodes) and bootloader. The configuration
    sector grows from 208 to 262 bytes: key at 224, flag at 256 ('K' key,
    'C' confirmed), five colours at 257 -- past the runtime's XMPP password
    at 208-220. The bytecode fetch waits for
    kx_ready; the boot LEDs give way to the colours while they are shown.
    """
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t = read_src(q)
    if "kx_step" in t:
        print(f"[skip] {q}: adoption flow already there")
        return
    if "kxBegin" not in (root / "mtl/mtl_linux/src/vcomp/stdlib_core.cpp").read_text():
        sys.exit(f"FAIL {q}: adoption_kx must run first")
    nl = "\r\n" if "\r\n" in t else "\n"
    body = (Path(__file__).resolve().parent / "boot-mods" / "adoption.mtl").read_text()
    edits = [
        ("const CONF_LENGTH=208;;", "const CONF_LENGTH=262;;", "CONF_LENGTH"),
        ("fun getbytecode res=" + nl, body.replace("\n", nl) + nl + "fun getbytecode res=" + nl, "fun getbytecode"),
        ("\tlet getbytecode res -> bc in" + nl + "\tif bc!=nil then" + nl,
         "\tlet getbytecode res -> bc in" + nl + "\tif bc!=nil && kx_accept res bc then" + nl,
         "the bytecode acceptance"),
        ('\t\tif wifiReady then set httpboot=httprequest "GET" boot_url url nil #_bootcbhttp HTTP_NORMAL;' + nl,
         "\t\tif wifiReady then kx_step url;" + nl
         + '\t\tif wifiReady && kx_ready then set httpboot=httprequest "GET" boot_url url nil #_bootcbhttp HTTP_NORMAL;' + nl,
         "the bytecode request"),
        ("\t\t\tboot_leds;" + nl, "\t\t\tif kx_showing then kx_leds else boot_leds;" + nl, "boot_leds in loop"),
        ("\tif wifitry!=nil && time-wifitry>BOOT_WIFITIMEOUT && httpboot==nil then",
         "\tif wifitry!=nil && time-wifitry>BOOT_WIFITIMEOUT && httpboot==nil && !kx_busy then",
         "the Wi-Fi give-up test"),
    ]
    for a, b, what in edits:
        if t.count(a) != 1:
            sys.exit(f"FAIL {q}: expected one {what}, found {t.count(a)}")
        t = t.replace(a, b, 1)
    write_src(q, t)
    print(f"[ok]   {q}: adoption flow in boot_loop, conf 246 bytes")


def patch_crash_report(root: Path) -> None:
    """Put the reset counters in the boot URL, so the warren sees them.

    persist_counters and reset_reason keep, in .extbss, who reset the chip
    and how far the last OTA got -- but only a JTAG read could see them, and
    owners have no JTAG. On 2026-11-08 an OTA failed twice on hardware
    ("the flash wrote nothing") while the emulator passed; the counters
    (verify calls 6, ok 4, reason 0) named the watchdog, read over JTAG.
    Every rabbit now says it in its boot URL instead:

        &r=<reason>.<resets>.<verify calls>.<verify ok>.<flash calls>.<flash errors>

    reason as in patch_reset_reason (0 none/watchdog, 1 VM out of memory, 2
    bytecode reboot, 3 flash writer, 4 other). The counters are cleared at
    power-up, so after a replug it reads r=0.0.0.0.0.0.

    One core opcode, resetInfo -> that string; the bootloader's boot_url
    appends it. Requires persist_counters and reset_reason.
    """
    q = root / "src/vm/vinterp.c"
    t = q.read_text()
    if "nab_crash_report" not in t:
        decl = ("extern int32_t nab_reset_reason, nab_reset_count, nab_verify_calls,\n"
                "  nab_verify_ok, nab_flash_calls, nab_flash_error;\n"
                "/* crash_report: r=reason.resets.verify.verify_ok.flash.flash_error */\n"
                "static int nab_crash_report(char *out)\n{\n"
                "  int32_t v[6]; int i, n = 0;\n"
                "  v[0]=nab_reset_reason; v[1]=nab_reset_count; v[2]=nab_verify_calls;\n"
                "  v[3]=nab_verify_ok; v[4]=nab_flash_calls; v[5]=nab_flash_error;\n"
                "  for (i = 0; i < 6; i++) {\n"
                "    uint32_t x = (uint32_t)v[i]; char tmp[10]; int k = 0;\n"
                "    if (i) out[n++] = '.';\n"
                "    do { tmp[k++] = (char)('0' + x % 10); x /= 10; } while (x && k < 10);\n"
                "    while (k) out[n++] = tmp[--k];\n"
                "  }\n"
                "  return n;\n}\n")
        if "#include <string.h>\n" not in t:
            sys.exit(f"FAIL {q}: no <string.h> include to hang the report on")
        t = t.replace("#include <string.h>\n", "#include <string.h>\n" + decl, 1)
        q.write_text(t)
    add_core_opcode(root, "resetInfo", "fun[]S", 0,
        "      case OPresetInfo:\n"
        "        {\n"
        "          char out[6 * 11];\n"
        "          int n = nab_crash_report(out);\n"
        "          VPUSH(PNTTOVAL(VMALLOCSTR(out, n)));\n"
        "        }\n"
        "        break;\n")
    b = root / "mtl/boot/boot.0.0.0.13.mtl"
    u = read_src(b)
    if "resetInfo" not in u:
        m = re.search(r"(fun boot_url url =\r?\n[^\n]*?)::nil;;", u)
        if not m:
            sys.exit(f"FAIL {b}: boot_url is not the shape crash_report expects")
        u = u[:m.end(1)] + '::"&r="::resetInfo' + u[m.end(1):]
        write_src(b, u)
    print("[ok]   crash_report: &r=reason.resets.verify.ok.flash.errors in the boot URL")


def patch_rfic_report(root: Path) -> None:
    """Say which radio chip the rabbit has: `&f=<RfIcType>` in the boot URL.

    rt2501usb.c carries register tables and channel code for four RF chips
    (RF5225, RF5226, RF2528, RF2527), about 1.1 KB, and the Nabaztag:tag's
    RT2571W module should only ever have the RF2528. Should: the value comes
    from the dongle's own EEPROM, per rabbit, and a wrong guess is a rabbit
    with no Wi-Fi. So the fleet says it first (2026-11-12), and the tables
    go only once every rabbit has.

    No new opcode: crash_report's resetInfo already builds a string for
    boot_url, so it ends with `&f=<n>` too, and the warren reads it as one
    more parameter (terrier: kept out of the boot marks, it is hardware,
    not the image). Requires crash_report.
    """
    q = root / "src/usb/rt2501usb.c"
    t, nl = read_flat(q)
    if "rt2501_rfic(void)" not in t:
        a = "static EEPROM_ANTENNA_STRUC rt2501_Antenna;\n"
        if t.count(a) != 1:
            sys.exit(f"FAIL {q}: rt2501_Antenna is not declared where rfic_report expects")
        t = t.replace(a, a + "/* rfic_report (apply-mods.py): the RF chip, for the boot URL. */\n"
                         "uint8_t rt2501_rfic(void) { return rt2501_Antenna.field.RfIcType; }\n", 1)
        write_flat(q, t, nl)
    q = root / "src/vm/vinterp.c"
    t = q.read_text()
    if "rt2501_rfic" in t:
        print(f"[skip] {q}: the boot URL already says the RF chip")
        return
    a = "  return n;\n}\n"
    i = t.find("static int nab_crash_report(char *out)")
    j = t.find(a, i)
    if i < 0 or j < 0:
        sys.exit(f"FAIL {q}: rfic_report needs crash_report first")
    t = t[:j] + ("  {\n    /* rfic_report: &f=<RfIcType>, decimal */\n"
                 "    extern uint8_t rt2501_rfic(void);\n"
                 "    uint32_t f = rt2501_rfic();\n"
                 "    out[n++] = '&'; out[n++] = 'f'; out[n++] = '=';\n"
                 "    if (f >= 100) out[n++] = (char)('0' + f / 100);\n"
                 "    if (f >= 10) out[n++] = (char)('0' + f / 10 % 10);\n"
                 "    out[n++] = (char)('0' + f % 10);\n  }\n") + t[j:]
    if t.count("char out[6 * 11];") != 1:
        sys.exit(f"FAIL {q}: resetInfo's buffer is not where rfic_report expects it")
    t = t.replace("char out[6 * 11];", "char out[6 * 11 + 8];   /* + &f=255 */", 1)
    q.write_text(t)
    print("[ok]   rfic_report: &f=<RF chip> after the reset report in the boot URL")


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
    if "/* nab: keep crypto_scalarmult */" in s:
        unused.remove("crypto_scalarmult")   # x25519_wdt: the adoption exchange
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

    It does not drop the printf family by itself, though: the lean-wpa3
    .map of 2026-11-08 still had sprintf, nano-svfprintf, vfprintf_i and
    nano malloc/free/realloc linked, 4 316 bytes, pulled by the sprintf
    calls in uart.c and by sprintf(dbg_buffer, ...) calls that sit outside
    the DEBUG_* blocks. uart_no_sprintf and strip_dbg_sprintf are the steps
    that finish the job (5 680 bytes together, lean-wpa3-slim).
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
                # A file can hold both conventions at once: eapol.c is CRLF,
                # and the block an earlier step inserted into it is LF. Try
                # the raw form before giving up, and match the surrounding
                # text when replacing so the file does not get more mixed
                # than it already is.
                if old in src:
                    o, n = old, new
                else:
                    sys.exit(f"FAIL {f}: cannot find {what} "
                             f"(upstream changed?)")
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
     callers that test `status < 0`.

     The guard tests for that flag and NOT for `status >= 0`, which is the
     shape this was written in first. Every URB is set to URB_PENDING (-1)
     when it is submitted (hcd.c:494, :649), so while the transfer is in
     flight `status` is negative by construction: `status >= 0` is false at
     every completion, the transfer count was never stored, `status` stayed
     -1, and every caller read a successful transfer as an error. Measured
     over JTAG on 2026-11-07, the first time this patch ever ran on hardware:
     65 receive errors and not one good frame, which is what condemned the
     radio. URB_XFERERR is assigned nowhere else in this function, so the
     flag is the honest question to ask. */
  if(urb->status != URB_XFERERR)
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

    That last paragraph was right about everything except the conclusion,
    and the error cost ten days. `netPmk()` runs when the password is
    **typed**, not when the rabbit boots: on every boot afterwards the
    bootloader reads the cached PMK and never calls it, so the hook below
    captures nothing and SAE never starts. The config-sector offset turned
    out to be no mystery either -- `boot.0.0.0.13.mtl:211` names it. The
    passphrase is now handed over at association time by
    `patch_wpa3_boot_password`, which this step requires and which is where
    the whole story is written down. The hook here stays: it is what makes
    SAE work on the very association that follows a config change.

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
         "#define IEEE80211_SAE_CONFIRM\t\t0x0002\n/* Not a refusal: the AP wants its anti-clogging token echoed. */\n#define IEEE80211_STATUS_ANTI_CLOGGING\t76",
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
         "/* 802.11w. eapol.c hands over the key data it has just\n"
         "   unwrapped; the management-frame path asks whether a frame\n"
         "   may act on us. */\n"
         "void ieee80211_install_igtk(const uint8_t *keydata, uint32_t len);\n"
         "void ieee80211_forget_igtk(void);\n"
         "int32_t ieee80211_mgmt_ok(const uint8_t *frame, uint32_t length);\n"
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
         'static const uint8_t ieee80211_wpa2_oui[IEEE80211_OUI_LEN] =\n{ 0x00, 0x0f, 0xac, 0x00 };\n\n/* ------------------------------------------------------------------ SAE */\n\n#include "net/sae.h"\n#include "net/sha256.h"\n\n/* BSSIDs seen advertising AKM suite 8. A second cache beside the one in\n   patches/wpa2-scanned-ciphers.patch, deliberately: that patch is applied\n   after apply-mods, and editing the lines around its context is how it\n   stops applying -- quietly, with fuzz, or not at all. 48 bytes of RAM is\n   cheaper than a coupling nobody would notice breaking. */\n#define IEEE80211_SAE_CACHE 8\nstatic uint8_t ieee80211_sae_bssids[IEEE80211_SAE_CACHE][IEEE80211_ADDR_LEN];\nstatic uint8_t ieee80211_sae_count;\n\n/* The password as text. See patch_wpa3_sae: this is the only place in the\n   firmware where it exists in that form. */\nstatic uint8_t ieee80211_sae_pw[64];\nstatic sae_ctx ieee80211_sae;\n/* How many anti-clogging tokens this attempt has echoed. Bounded, or\n   an AP that always asks becomes an endless exchange. */\nstatic uint8_t ieee80211_sae_tokens;\n\nvoid ieee80211_sae_password(const uint8_t *password)\n{\n  uint32_t i = 0;\n  while(password[i] && i < sizeof(ieee80211_sae_pw) - 1) {\n    ieee80211_sae_pw[i] = password[i];\n    i++;\n  }\n  ieee80211_sae_pw[i] = 0;\n}\n\n/* Full, the oldest entry goes: on hardware the eight slots were taken by\n   the neighbourhood\'s WPA3 BSSIDs, and a network seen ninth would never\n   have been tried with SAE. */\nstatic void ieee80211_sae_remember(const uint8_t *bssid)\n{\n  uint8_t i;\n  for(i=0;i<ieee80211_sae_count;i++)\n    if(memcmp(ieee80211_sae_bssids[i], bssid, IEEE80211_ADDR_LEN) == 0) return;\n  if(ieee80211_sae_count == IEEE80211_SAE_CACHE) {\n    memmove(ieee80211_sae_bssids[0], ieee80211_sae_bssids[1],\n            (IEEE80211_SAE_CACHE - 1) * IEEE80211_ADDR_LEN);\n    ieee80211_sae_count--;\n  }\n  memcpy(ieee80211_sae_bssids[ieee80211_sae_count++], bssid,\n         IEEE80211_ADDR_LEN);\n}\n\n/* An AP that refused SAE, or never answered it, is tried with PSK next\n   time. Nothing else ever empties this list: on hardware, 2026-11-08, an\n   SSID switched back from WPA2/WPA3 to WPA2-only kept the rabbit\n   sending commits it could not answer until it was unplugged. The next\n   scan puts the BSSID back if it still advertises AKM 8. */\nstatic void ieee80211_sae_forget(const uint8_t *bssid)\n{\n  uint8_t i;\n  for(i=0;i<ieee80211_sae_count;i++)\n    if(memcmp(ieee80211_sae_bssids[i], bssid, IEEE80211_ADDR_LEN) == 0) {\n      memmove(ieee80211_sae_bssids[i], ieee80211_sae_bssids[i + 1],\n              (ieee80211_sae_count - i - 1) * IEEE80211_ADDR_LEN);\n      ieee80211_sae_count--;\n      return;\n    }\n}\n\nstatic int32_t ieee80211_sae_known(const uint8_t *bssid)\n{\n  uint8_t i;\n  for(i=0;i<ieee80211_sae_count;i++)\n    if(memcmp(ieee80211_sae_bssids[i], bssid, IEEE80211_ADDR_LEN) == 0) return 1;\n  return 0;\n}\n\n/* The bodies are further down, next to rt2501_auth: they call\n   ieee80211_associate(), ieee80211_mask_to_rate() and the transmit path,\n   none of which is declared this early in the file. The data has to be up\n   here because the scan and the auth dispatch both reach it. */\nstatic int32_t ieee80211_sae_begin(void);\nstatic void ieee80211_input_sae_auth(uint16_t seq, uint16_t status,\n                                     uint8_t *body, uint32_t blen);\n',
         'the wpa2 OUI constant'),
        ('                    if(*(current+IEEE80211_OUI_LEN-1) == IEEE80211_AUTH_PSK)\n                      found = 1;\n',
         '                    if(*(current+IEEE80211_OUI_LEN-1) == IEEE80211_AUTH_PSK)\n                      found = 1;\n                    /* Suite 8 is SAE. Accepting it here is what makes a\n                       WPA3 network a candidate at all: without it the scan\n                       returns IEEE80211_CRYPT_UNSUPPORTED and the network\n                       is never offered to the bytecode. */\n                    if(*(current+IEEE80211_OUI_LEN-1) == IEEE80211_AUTH_AKM_SAE) {\n                      found = 1;\n                      ieee80211_sae_remember(scan_result.bssid);\n                    }\n',
         'the AKM suite loop'),
        ('void rt2501_auth(const uint8_t *ssid, const uint8_t *mac,\n',
         '\n/* --------------------------------------------------- SAE, continued */\n\n/* Where SAE\'s secrecy actually comes from, and it deserves plain words.\n\n   There is no entropy source on a 2006 Nabaztag: no ring oscillator, no\n   hardware RNG, nothing. nab_rand() is an xorshift seeded from the\n   millisecond timer, and a timer value at the moment of association is\n   guessable within a wide window by anybody who watched the rabbit boot.\n\n   So this stirs in everything the chip knows that an attacker does not\n   control all of: the MAC address (fixed, but it personalises the stream so\n   two rabbits never produce the same scalars), the free-running timer\n   sampled afresh on every call, and the received signal strength, whose low\n   bits are thermal noise from the radio and are the one genuinely\n   unpredictable input available. Mixed through SHA-256 rather than XORed,\n   so a predictable input cannot cancel an unpredictable one, and chained\n   through `carry` so no two calls can repeat.\n\n   Honest summary: better than a timer alone, still not a hardware RNG. An\n   attacker who can predict the timer to the millisecond and knows the RSSI\n   the radio reported is left with the noise floor. */\nstatic void ieee80211_sae_rng(uint8_t *out, uint32_t len, void *arg)\n{\n  static uint8_t carry[SHA256_DIGEST];\n  uint8_t pool[SHA256_DIGEST], mix[SHA256_DIGEST + 18];\n  uint32_t done = 0, n;\n\n  (void)arg;\n  while(done < len) {\n    uint32_t t = counter_timer;\n    uint32_t r = (uint32_t)rt2501_rssi_average();\n    uint32_t x = nab_rand();\n    n = 0;\n    memcpy(mix + n, carry, SHA256_DIGEST);  n += SHA256_DIGEST;\n    memcpy(mix + n, rt2501_mac, 6);         n += 6;\n    memcpy(mix + n, &t, 4);                 n += 4;\n    memcpy(mix + n, &r, 4);                 n += 4;\n    memcpy(mix + n, &x, 4);                 n += 4;\n    sha256(pool, mix, n);\n    memcpy(carry, pool, SHA256_DIGEST);\n    n = len - done;\n    if(n > SHA256_DIGEST) n = SHA256_DIGEST;\n    memcpy(out + done, pool, n);\n    done += n;\n  }\n  memset(pool, 0, sizeof pool);\n  memset(mix, 0, sizeof mix);\n}\n\n/* CLR_WDT lives in inc/common.h but expands to WDTCON, which this\n   file does not otherwise reach. */\n#include "ml674061.h"\n\n/* The watchdog fires at about 2.09 s and a scalar multiplication is the\n   longest thing this firmware ever computes. */\nstatic void ieee80211_sae_tick(void) { CLR_WDT; }\n\n/* An authentication frame with a body, which the stock sender cannot do:\n   its struct ends after algorithm, sequence and status. */\nstatic int32_t ieee80211_send_auth_body(uint16_t seq, const uint8_t *body,\n                                        uint32_t blen)\n{\n  struct {\n    TXD_STRUC txd;\n    struct ieee80211_frame header;\n    uint8_t auth[6];\n  } *auth;\n  uint8_t *write_ptr;\n  uint16_t duration;\n  uint32_t frame_length;\n\n  disable_ohci_irq();\n  auth = hcd_malloc(sizeof(*auth) + blen + 7, COMRAM, 15);\n  enable_ohci_irq();\n  if(auth == NULL) {\n    DBG_WIFI("SAE: no COMRAM for the auth frame"EOL);\n    return 0;\n  }\n\n  auth->header.i_fc[0] = IEEE80211_FC0_TYPE_MGT|IEEE80211_FC0_SUBTYPE_AUTH\n      |(IEEE80211_FC0_VERSION_0 << IEEE80211_FC0_VERSION_SHIFT);\n  auth->header.i_fc[1] = IEEE80211_FC1_DIR_NODS;\n  memcpy(auth->header.i_addr1, ieee80211_assoc_mac, IEEE80211_ADDR_LEN);\n  memcpy(auth->header.i_addr2, rt2501_mac, IEEE80211_ADDR_LEN);\n  memcpy(auth->header.i_addr3, ieee80211_assoc_bssid, IEEE80211_ADDR_LEN);\n  auth->header.i_seq[0] = 0;\n  auth->header.i_seq[1] = 0;\n\n  write_ptr = auth->auth;\n  *(write_ptr++) = (IEEE80211_AUTH_ALG_SAE & 0x00ff);\n  *(write_ptr++) = (IEEE80211_AUTH_ALG_SAE & 0xff00) >> 8;\n  *(write_ptr++) = (seq & 0x00ff);\n  *(write_ptr++) = (seq & 0xff00) >> 8;\n  *(write_ptr++) = (IEEE80211_STATUS_SUCCESS & 0x00ff);\n  *(write_ptr++) = (IEEE80211_STATUS_SUCCESS & 0xff00) >> 8;\n  memcpy(write_ptr, body, blen);\n  write_ptr += blen;\n\n  frame_length = sizeof(struct ieee80211_frame)\n                 + (uint32_t)(write_ptr - auth->auth);\n  duration = rt2501_txtime(frame_length,\n      ieee80211_mask_to_rate(ieee80211_lowest_txrate)) + IEEE80211_SIFS;\n  auth->header.i_dur[0] = (duration & 0x00ff);\n  auth->header.i_dur[1] = (duration & 0xff00) >> 8;\n\n  /* The argument order matters and bit me: Rate, Length, QueIdx,\n     PacketId. Length went in as 0 and the length ended up in QueIdx,\n     so the commit left with a zero-length descriptor and the AP never\n     saw a frame -- the rabbit sat in AUTH, retrying for ever. And the\n     rate wants mask_to_rt2501rate, not mask_to_rate: one is the\n     hardware encoding and the other is the 802.11 one. */\n  rt2501_make_tx_descriptor(&auth->txd,\n      RT2501_CIPHER_NONE,                                   /* CipherAlg */\n      0,                                                    /* KeyTable */\n      0,                                                    /* KeyIdx */\n      1,                                                    /* Ack */\n      0,                                                    /* Fragment */\n      0,                                                    /* InsTimestamp */\n      1,                                                    /* RetryMode */\n      0,                                                    /* Ifs */\n      ieee80211_mask_to_rt2501rate(ieee80211_lowest_txrate), /* Rate */\n      frame_length,                                         /* Length */\n      0,                                                    /* QueIdx */\n      0);                                                   /* PacketId */\n\n  disable_ohci_irq();\n  if(!rt2501_tx(auth, sizeof(TXD_STRUC) + frame_length)) {\n    DBG_WIFI("SAE: TX error"EOL);\n    enable_ohci_irq();\n    return 0;\n  }\n  enable_ohci_irq();\n  return 1;\n}\n\n/* Build the password element and send our commit. */\nstatic int32_t ieee80211_sae_begin(void)\n{\n  uint8_t commit[SAE_COMMIT_LEN];\n\n  if(ieee80211_sae_pw[0] == 0) {\n    DBG_WIFI("SAE: no password captured"EOL);\n    return 0;\n  }\n  /* Taking over from rt2501_auth means taking over everything it does\n     after the point we hook: it tells the autoresponder the AP rates\n     and switches the radio to the AP channel just before building its\n     descriptor. Skipping those sent the commit while the radio was\n     still hopping through the scan, and the emulator counted it under\n     "transmitted into the void" -- six commits, no reply, the rabbit\n     stuck in AUTH for ever. */\n  rt2501_write(rt2501_dev, RT2501_TXRX_CSR5,\n               ieee80211_mask_to_rt2501mask(ieee80211_assoc_rateset));\n  rt2501_switch_channel(ieee80211_assoc_channel);\n\n  ieee80211_sae_tokens = 0;\n  DBG_WIFI("SAE: deriving the password element"EOL);\n  if(!sae_commit(&ieee80211_sae, rt2501_mac, ieee80211_assoc_bssid,\n                 (const char *)ieee80211_sae_pw,\n                 ieee80211_sae_rng, NULL, ieee80211_sae_tick)) {\n    DBG_WIFI("SAE: could not build a commit"EOL);\n    sae_clear(&ieee80211_sae);\n    return 0;\n  }\n  sae_commit_bytes(&ieee80211_sae, commit);\n  if(!ieee80211_send_auth_body(IEEE80211_SAE_COMMIT, commit, sizeof commit)) {\n    sae_clear(&ieee80211_sae);\n    return 0;\n  }\n  DBG_WIFI("SAE: commit sent"EOL);\n  return 1;\n}\n\n/* One received SAE authentication frame. */\nstatic void ieee80211_input_sae_auth(uint16_t seq, uint16_t status,\n                                     uint8_t *body, uint32_t blen)\n{\n  /* Status 76 is not a refusal. An access point under load answers a commit\n     with an anti-clogging token and expects the same commit back with that\n     token echoed -- its defence against being made to do elliptic-curve\n     arithmetic for strangers. Treating it as a refusal is how a client fails\n     to associate with a busy AP and reports "wrong password".\n\n     The token\'s framing differs between editions of the standard: some\n     senders put the group in front of it, some do not. Both shapes are\n     accepted. Guessing wrong costs nothing that was not already lost -- the\n     retry fails, which is exactly what happened before this existed. */\n  if(status == IEEE80211_STATUS_ANTI_CLOGGING && seq == IEEE80211_SAE_COMMIT) {\n    uint8_t retry[SAE_COMMIT_TOKEN_LEN];\n    const uint8_t *tok = body;\n    uint32_t tlen = blen, n;\n\n    if(++ieee80211_sae_tokens > 3) {\n      DBG_WIFI("SAE: the AP keeps asking for a token, giving up"EOL);\n      sae_clear(&ieee80211_sae);\n      ieee80211_state = IEEE80211_S_IDLE;\n      return;\n    }\n    if(tlen >= 2 && tok[0] == 19 && tok[1] == 0) { tok += 2; tlen -= 2; }\n    if(tlen == 0 || tlen > SAE_TOKEN_MAX) {\n      sprintf(dbg_buffer, "SAE: a token of %lu bytes is not usable"EOL,\n              (unsigned long)tlen);\n      DBG_WIFI(dbg_buffer);\n      sae_clear(&ieee80211_sae);\n      ieee80211_state = IEEE80211_S_IDLE;\n      return;\n    }\n    /* The same scalar and element go back out: only the token is new. */\n    n = sae_commit_bytes_token(&ieee80211_sae, retry, tok, tlen);\n    if(!ieee80211_send_auth_body(IEEE80211_SAE_COMMIT, retry, n)) {\n      sae_clear(&ieee80211_sae);\n      ieee80211_state = IEEE80211_S_IDLE;\n      return;\n    }\n    sprintf(dbg_buffer, "SAE: commit resent with a %lu-byte token"EOL,\n            (unsigned long)tlen);\n    DBG_WIFI(dbg_buffer);\n    ieee80211_timeout = IEEE80211_AUTH_TIMEOUT;\n    return;\n  }\n\n  if(status != IEEE80211_STATUS_SUCCESS) {\n    sprintf(dbg_buffer, "SAE: the AP refused us, status %u"EOL, status);\n    DBG_WIFI(dbg_buffer);\n    ieee80211_sae_forget(ieee80211_assoc_bssid);\n    sae_clear(&ieee80211_sae);\n    ieee80211_state = IEEE80211_S_IDLE;\n    return;\n  }\n\n  if(seq == IEEE80211_SAE_COMMIT) {\n    uint8_t confirm[SAE_CONFIRM_LEN];\n    if(!sae_peer_commit(&ieee80211_sae, body, blen, ieee80211_sae_tick)) {\n      DBG_WIFI("SAE: the AP\'s commit is unusable"EOL);\n      sae_clear(&ieee80211_sae);\n      ieee80211_state = IEEE80211_S_IDLE;\n      return;\n    }\n    sae_confirm_bytes(&ieee80211_sae, confirm);\n    if(!ieee80211_send_auth_body(IEEE80211_SAE_CONFIRM, confirm,\n                                 sizeof confirm)) {\n      sae_clear(&ieee80211_sae);\n      ieee80211_state = IEEE80211_S_IDLE;\n      return;\n    }\n    DBG_WIFI("SAE: keys derived, confirm sent"EOL);\n    return;\n  }\n\n  if(seq == IEEE80211_SAE_CONFIRM) {\n    if(!sae_peer_confirm(&ieee80211_sae, body, blen)) {\n      /* The one place SAE tells us the password is wrong. A failed confirm\n         must not be retried with the same scalar, so drop to idle and let\n         the bytecode start over. */\n      DBG_WIFI("SAE: the confirm did not verify -- wrong password?"EOL);\n      sae_clear(&ieee80211_sae);\n      ieee80211_state = IEEE80211_S_IDLE;\n      return;\n    }\n    /* The whole point: the four-way handshake reads its PMK out of\n       ieee80211_key, so handing SAE\'s PMK over is a memcpy and the rest of\n       the association is unchanged. */\n    memcpy(ieee80211_key, ieee80211_sae.pmk, EAPOL_MASTER_KEY_LENGTH);\n    sae_clear(&ieee80211_sae);\n    eapol_init();\n    DBG_WIFI("SAE: accepted, associating"EOL);\n    ieee80211_associate();\n    return;\n  }\n\n  sprintf(dbg_buffer, "SAE: unexpected sequence %u"EOL, seq);\n  DBG_WIFI(dbg_buffer);\n}\n\n/* --------------------------------------------------------------- 802.11w */\n\n#include "net/cmac.h"\n\n/* The integrity group key, and the last packet number seen under it. Until\n   an AP sends one, ieee80211_igtk_set stays 0 and management frames are\n   accepted as before -- which is WPA2 behaviour and is what every rabbit\n   on a WPA2 network still gets. */\nstatic uint8_t ieee80211_igtk[16];\nstatic uint8_t ieee80211_igtk_ipn[6];\nstatic uint8_t ieee80211_igtk_id;\nstatic uint8_t ieee80211_igtk_set;\n/* Diagnostics for dcc_telemetry: management frames refused here, and\n   how many of the deauth/disassoc seen carried the Protected bit. */\nuint8_t ieee80211_pmf_dropped, ieee80211_pmf_protbit;\n\nvoid ieee80211_install_igtk(const uint8_t *keydata, uint32_t len)\n{\n  uint8_t id = 0, ipn[6], key[16];\n\n  if(!rsn_find_igtk(keydata, len, &id, ipn, key)) return;\n  memcpy(ieee80211_igtk, key, sizeof ieee80211_igtk);\n  memcpy(ieee80211_igtk_ipn, ipn, sizeof ieee80211_igtk_ipn);\n  ieee80211_igtk_id = id;\n  ieee80211_igtk_set = 1;\n  memset(key, 0, sizeof key);\n  sprintf(dbg_buffer, "IGTK installed, key id %u"EOL, id);\n  DBG_WIFI(dbg_buffer);\n}\n\nvoid ieee80211_forget_igtk(void)\n{\n  memset(ieee80211_igtk, 0, sizeof ieee80211_igtk);\n  memset(ieee80211_igtk_ipn, 0, sizeof ieee80211_igtk_ipn);\n  ieee80211_igtk_set = 0;\n}\n\n/*\n * Is this robust management frame allowed to act on us?\n *\n * Without an IGTK: yes, always. That is WPA2, and nothing changes for a\n * rabbit on a WPA2 network.\n *\n * With one: the frame must carry an MMIE whose MIC verifies under the IGTK,\n * and whose packet number has not been seen. This is the whole point of\n * WPA3 in daily use -- without it anybody within radio range can knock the\n * rabbit off its network with a forged deauthenticate, which is a two-line\n * script, and with it they cannot.\n *\n * `frame` is the 802.11 header; `length` covers header and body.\n */\nint32_t ieee80211_mgmt_ok(const uint8_t *frame, uint32_t length)\n{\n  const uint8_t *body;\n  uint8_t copy[256], mic[8], *mmie;\n  uint32_t blen;\n  int32_t i, newer = 0;\n\n  if(!ieee80211_igtk_set) return 1;\n  if(frame[1] & 0x40) ieee80211_pmf_protbit++;\n  if(length < sizeof(struct ieee80211_frame)) return 0;\n\n  body = frame + sizeof(struct ieee80211_frame);\n  blen = length - sizeof(struct ieee80211_frame);\n\n  /* The MMIE is the last element: id 76, length 16, then KeyID(2),\n     IPN(6), MIC(8). */\n  if(blen < 18 || blen > sizeof(copy)) {\n    /* Too short to carry an MMIE at all, which is what a forged frame\n       looks like: an attacker has no key to sign one with. */\n    DBG_WIFI("802.11w: unsigned management frame, dropped"EOL);\n    ieee80211_pmf_dropped++;\n    return 0;\n  }\n  memcpy(copy, body, blen);\n  mmie = copy + blen - 18;\n  if(mmie[0] != 76 || mmie[1] != 16) {\n    DBG_WIFI("802.11w: unprotected management frame, dropped"EOL);\n    ieee80211_pmf_dropped++;\n    return 0;\n  }\n  if((uint8_t)(mmie[2] | (mmie[3] << 8)) != ieee80211_igtk_id) {\n    DBG_WIFI("802.11w: MMIE names another key, dropped"EOL);\n    ieee80211_pmf_dropped++;\n    return 0;\n  }\n\n  /* The packet number is a 48-bit little-endian counter; anything not\n     strictly greater than the last one is a replay. */\n  for(i = 5; i >= 0; i--) {\n    if(mmie[4 + i] > ieee80211_igtk_ipn[i]) { newer = 1; break; }\n    if(mmie[4 + i] < ieee80211_igtk_ipn[i]) { newer = 0; break; }\n  }\n  if(!newer) {\n    DBG_WIFI("802.11w: replayed packet number, dropped"EOL);\n    ieee80211_pmf_dropped++;\n    return 0;\n  }\n\n  memcpy(mic, mmie + 10, 8);\n  memset(mmie + 10, 0, 8);\n  {\n    uint8_t want[8];\n    bip_cmac_128(want, ieee80211_igtk, frame, copy, blen);\n    for(i = 0; i < 8; i++)\n      if(want[i] != mic[i]) {\n        DBG_WIFI("802.11w: bad MIC, dropped"EOL);\n        ieee80211_pmf_dropped++;\n        return 0;\n      }\n  }\n\n  memcpy(ieee80211_igtk_ipn, mmie + 4, 6);\n  return 1;\n}\n\nvoid rt2501_auth(const uint8_t *ssid, const uint8_t *mac,\n',
         'rt2501_auth, for the SAE bodies'),
        ('\tDBG_WIFI("Sending auth request"EOL);\n',
         '\t/* A WPA3 network needs SAE before association, and SAE is an\n\t   authentication-frame exchange of its own -- so this returns here\n\t   rather than falling through to the open-auth frame below. */\n\tif(ieee80211_sae_known(bssid)\n\t   && (ieee80211_encryption & 0xF0) == IEEE80211_CRYPT_WPA2) {\n\t\tieee80211_authmode = IEEE80211_AUTH_SAE;\n\t\tieee80211_state = IEEE80211_S_AUTH;\n\t\tieee80211_timeout = IEEE80211_AUTH_TIMEOUT;\n\t\tif(ieee80211_sae_begin()) return;\n\t\t/* Could not start: an AP in transition mode still takes PSK, so\n\t\t   try the ordinary path rather than giving up on the network. */\n\t\tDBG_WIFI("SAE: falling back to open auth + PSK"EOL);\n\t\tieee80211_authmode = IEEE80211_AUTH_OPEN;\n\t\tieee80211_state = IEEE80211_S_IDLE;\n\t}\n\n\tDBG_WIFI("Sending auth request"EOL);\n',
         'the auth request send point'),
        ('\t\t\t\t\tif(ieee80211_authmode == IEEE80211_AUTH_OPEN) {\n',
         '\t\t\t\t\tif(ieee80211_authmode == IEEE80211_AUTH_SAE) {\n\t\t\t\t\t\tif(((frame_current[0] << 0)|(frame_current[1] << 8))\n\t\t\t\t\t\t   != IEEE80211_AUTH_ALG_SAE) {\n\t\t\t\t\t\t\tDBG_WIFI("SAE: the AP replied with another algorithm"EOL);\n\t\t\t\t\t\t\t/* What a WPA2-only AP answers a commit with. */\n\t\t\t\t\t\t\tieee80211_sae_forget(ieee80211_assoc_bssid);\n\t\t\t\t\t\t\tsae_clear(&ieee80211_sae);\n\t\t\t\t\t\t\tieee80211_state = IEEE80211_S_IDLE;\n\t\t\t\t\t\t\tbreak;\n\t\t\t\t\t\t}\n\t\t\t\t\t\tieee80211_input_sae_auth(\n\t\t\t\t\t\t\t(frame_current[2] << 0)|(frame_current[3] << 8),\n\t\t\t\t\t\t\t(frame_current[4] << 0)|(frame_current[5] << 8),\n\t\t\t\t\t\t\t&frame_current[6],\n\t\t\t\t\t\t\tlength-sizeof(struct ieee80211_frame)-6);\n\t\t\t\t\t} else if(ieee80211_authmode == IEEE80211_AUTH_OPEN) {\n',
         'the received-auth dispatch'),
        ('      *(write_ptr-1) = IEEE80211_AUTH_PSK;\n          /* RSN Capability */\n      *(write_ptr++) = 0x00;\n      *(write_ptr++) = 0x00;\n',
         '      /* Suite 8 when we got here through SAE, 2 otherwise. */\n      *(write_ptr-1) = (ieee80211_authmode == IEEE80211_AUTH_SAE)\n                       ? IEEE80211_AUTH_AKM_SAE : IEEE80211_AUTH_PSK;\n          /* RSN Capability. MFPC (0x0080) is mandatory with WPA3: an AP\n             with MFPR set refuses a station that does not claim it. We\n             claim it and do not yet enforce it on received frames -- see\n             the note in patch_wpa3_sae about BIP. */\n      *(write_ptr++) = (ieee80211_authmode == IEEE80211_AUTH_SAE) ? 0x80 : 0x00;\n      *(write_ptr++) = 0x00;\n',
         'the transmitted RSN IE'),
        ('\t\tcase IEEE80211_FC0_SUBTYPE_DEAUTH:\n      DBG_WIFI("Deauth"EOL);\n',
         '\t\tcase IEEE80211_FC0_SUBTYPE_DEAUTH:\n      DBG_WIFI("Deauth"EOL);\n\t\t\t/* 802.11w: once an IGTK is installed, a deauthenticate or a\n\t\t\t   disassociate (which falls through to here) has to prove it\n\t\t\t   came from the access point. Without one this returns 1 and\n\t\t\t   nothing changes -- which is what a WPA2 rabbit still gets. */\n\t\t\tif(!ieee80211_mgmt_ok((const uint8_t *)fr, length)) break;\n',
         'the deauth and disassoc handler'),
        ('\t\tif(ieee80211_timeout == 0)\n\t\t\tieee80211_state = IEEE80211_S_IDLE;\n',
         '\t\tif(ieee80211_timeout == 0) {\n'
         '\t\t\t/* SAE that timed out in AUTH: an AP that ignores commits, as a\n'
         '\t\t\t   WPA2-only one may. PSK next time; see ieee80211_sae_forget. */\n'
         '\t\t\tif(ieee80211_state == IEEE80211_S_AUTH\n'
         '\t\t\t   && ieee80211_authmode == IEEE80211_AUTH_SAE)\n'
         '\t\t\t\tieee80211_sae_forget(ieee80211_assoc_bssid);\n'
         '\t\t\tieee80211_state = IEEE80211_S_IDLE;\n'
         '\t\t}\n',
         'the managed-mode timeout'),
    ], "ieee80211_sae_remember", "SAE wired into scan, auth and association")

    # The IGTK rides in the key data the GTK step already unwraps, so the
    # extraction belongs there rather than in a second unwrap of our own.
    # This edits text that patch_wpa2_gtk inserted, which is why the guard
    # above requires it: both are apply-mods steps and the order is fixed,
    # unlike the patches/ directory, which is applied afterwards.
    edit("src/net/eapol.c", [
        ('  DBG_WIFI("GTK installed"EOL);',
         '  /* The IGTK travels in the same key data when the AP protects\n'
         '   * management frames, and this is where that data is already\n'
         '   * unwrapped and its integrity already checked. Anchored on this\n'
         '   * line rather than on the rt2501_set_key() above it, which\n'
         '   * patch_group_cipher rewrites -- an anchor on text another step\n'
         '   * owns breaks the day that step changes. */\n'
         '  ieee80211_install_igtk(plain, wlen-8);\n'
         '  DBG_WIFI("GTK installed"EOL);',
         "the GTK installer, for the IGTK beside it"),
    ], "ieee80211_install_igtk", "the IGTK is installed from message 3")

    print("[ok]   wpa3_sae: crypto, password hook and driver in place")


def patch_wpa3_boot_password(root: Path) -> None:
    """Hand SAE the passphrase the config sector already holds.

    `patch_wpa3_sae` captures the password in `netPmk()`, on the grounds that
    it is the only place it passes through C -- and notes that the
    alternative was "reverse-engineering the config-sector offset out of the
    Metal bytecode". That reasoning had one hole, and a WPA3-only UniFi found
    it on the first try: **netPmk runs when the password is typed, not when
    the rabbit boots.**

    `boot.0.0.0.13.mtl:1867` computes the PMK once, in the config-form
    handler, and caches it. On every boot after that, `wifiAuth` reads the
    cached PMK and hands THAT to netAuth:

        let if crypt==WEP then wifiwepkey confGetWifikey0
            else if crypt==WPA || crypt==WPA2 then confGetWifipmk -> key in

    So `ieee80211_sae_pw` is empty, `ieee80211_sae_begin()` returns 0 at its
    first line, and the SAE branch falls through to open auth + PSK --
    silently, because that fallback exists for transition-mode APs. On a
    WPA3-only network the rabbit simply never authenticates: the first belly
    light blinks orange for ever. Observed on hardware 2026-11-06, and the
    emulator could not have caught it, because its harness calls
    `ieee80211_sae_password()` itself in place of the bytecode.

    No offence to the original reasoning: the offset was the hard part and it
    is no longer a mystery. `boot.0.0.0.13.mtl:211` says
    `const CONF_WIFIKEY0=92;;  //64`, the config form writes the passphrase
    there for every security mode (`v=='n'` at line 1814), and
    `confGetWifikey0` reads it back. Nothing new is stored, nothing has to be
    re-typed in config mode, and no passphrase lands in flash that was not
    already there.

    What is added is one opcode to carry it across, exactly as verifySig was:
    `netSaePw <passphrase>`, called from wifiAuth just before netAuth. A new
    opcode rather than calling netPmk again, because netPmk means PBKDF2 --
    4096 iterations of HMAC-SHA1, the step the config handler lights the
    whole belly for -- and paying that on every association to obtain a
    string we already have in flash would be an odd trade.
    """
    # 1. The opcode number, in both copies of vbc.h.
    for q in (root / "inc/vm/vbc.h", root / "mtl/mtl_linux/inc/vm/vbc.h"):
        t = q.read_text()
        if "OPnetSaePw" in t:
            print(f"[skip] {q}: OPnetSaePw already present")
            continue
        a = "#define OPverifySig 152\n"
        if a not in t:
            sys.exit(f"FAIL {q}: OPverifySig anchor missing — run the vbc step first")
        q.write_text(t.replace(a, a + "#define OPnetSaePw 153\n", 1))
        print(f"[ok]   {q}: added OPnetSaePw 153")

    # 2. The dispatch case. One argument, a bin; returns 1 if it took it.
    q = root / "src/vm/vinterp.c"
    t = q.read_text()
    if "OPnetSaePw" not in t:
        needle = "      case OPverifySig:\n"
        if needle not in t:
            sys.exit(f"FAIL {q}: OPverifySig case missing — run the vinterp step first")
        case = (
            "      case OPnetSaePw:\n"
            "        {\n"
            "          /* The passphrase, from the config sector, for SAE. The\n"
            "             bytecode owns this string; sae_password() copies it. */\n"
            "          int32_t pw=VALTOPNT(VSTACKGET(0));\n"
            "          int32_t ok=0;\n"
            "          if (pw!=NIL)\n"
            "          {\n"
            "            ieee80211_sae_password((const uint8_t*)VSTARTBIN(pw));\n"
            "            ok=1;\n"
            "          }\n"
            "          VSTACKSET(0, INTTOVAL(ok));\n"
            "        }\n"
            "        break;\n"
        )
        t = t.replace(needle, case + needle, 1)
        if '#include "net/ieee80211.h"' not in t:
            t = t.replace('#include "vbc.h"', '#include "vbc.h"\n#include "net/ieee80211.h"', 1)
        q.write_text(t)
        print(f"[ok]   {q}: added OPnetSaePw case")
    else:
        print(f"[skip] {q}: case already present")

    # 3. The compiler's stdlib tables. NBcore caps the loop that walks them,
    #    so a new entry without the bump is a label the compiler cannot see.
    q = root / "mtl/mtl_linux/src/vcomp/stdlib_core.cpp"
    t = q.read_text()
    if '"netSaePw"' in t:
        print(f"[skip] {q}: netSaePw already registered")
    else:
        nb = "#define NBcore 120"
        if nb not in t:
            sys.exit(f"FAIL {q}: NBcore 120 not found — run the stdlib step first")
        t = t.replace(nb, "#define NBcore 121", 1)
        edits = [
            ('/*17*/"verifySig"\n};',   '/*17*/"verifySig",\n/*18*/"netSaePw"\n};'),
            ('/*17*/OPverifySig,\n};',  '/*17*/OPverifySig,\n/*18*/OPnetSaePw,\n};'),
            ('/*17*/2\n};',             '/*17*/2,\n/*18*/1\n};'),
            ('/*17*/"fun[S S]I"\n};',   '/*17*/"fun[S S]I",\n/*18*/"fun[S]I"\n};'),
        ]
        for old, newtext in edits:
            if old not in t:
                sys.exit(f"FAIL {q}: could not splice {old!r} — the verifySig step must run first")
            t = t.replace(old, newtext, 1)
        q.write_text(t)
        print(f"[ok]   {q}: registered netSaePw in all 4 stdlib arrays (NBcore 121)")

    # 4. The call, in the bootloader, where the association is actually made.
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t = read_src(q)
    if "netSaePw" in t:
        print(f"[skip] {q}: wifiAuth already hands the passphrase over")
        return
    # The pristine file is CRLF; patch_bootloader flattens it to LF through
    # write_text, which is the only reason an LF anchor ever matched. The
    # emulator's step list has no bootloader step, and there this exited
    # with "found 0" on 2026-11-08.
    nl = "\r\n" if "\r\n" in t else "\n"
    a = "\t\t\tnetAuth hd wifiscans Iecholn auth crypt key;" + nl
    if t.count(a) != 1:
        sys.exit(f"FAIL {q}: expected exactly one netAuth call in wifiAuth, found {t.count(a)}")
    call = (
        "\t\t\t// SAE needs the passphrase, and `key` above is the cached PMK.\n"
        "\t\t\t// Harmless on a WPA2 network: nothing reads it unless the scan\n"
        "\t\t\t// saw AKM suite 8 on this BSSID.\n"
        "\t\t\tif crypt==IEEE80211_CRYPT_WPA || crypt==IEEE80211_CRYPT_WPA2\n"
        "\t\t\tthen netSaePw confGetWifikey0;\n"
    ).replace("\n", nl)
    write_src(q, t.replace(a, call + a, 1))
    print(f"[ok]   {q}: wifiAuth hands the stored passphrase to SAE")


def patch_wpa3_timing(root: Path) -> None:
    """Measure what a SAE commit costs on the real chip, and say so out loud.

    #65 measured what P-256 costs in **flash** -- 5 452 bytes -- and nobody
    has ever measured what it costs in **milliseconds** on an ARM7TDMI at
    32.768 MHz. That number decides a live question: hostapd retransmits a
    SAE commit after about a second and gives up not long after, so if
    deriving the password element and doing one scalar multiplication takes
    several seconds here, this firmware cannot complete an exchange with any
    access point, and no amount of cryptographic correctness will save it.
    It would not be a crypto bug or a network bug. It would be too slow.

    The only honest way to get the number is to take it on the hardware, so
    that is what this does: `netSaeMs <passphrase>` runs one real commit,
    timed with `counter_timer` (1 ms per tick), and returns the elapsed
    milliseconds. The bootloader appends it to its `bc.jsp` query string as
    `&t=`, where the warren's own HTTP log records it for free -- no new
    endpoint, no new protocol, and it arrives over WPA2 where the rabbit can
    still reach a server.

    This is a diagnostic build, which is why it is a mode of its own. It adds
    one commit's worth of delay to every boot, and a rabbit that nobody is
    measuring should not pay that.

    Two details worth keeping. The probe gets its own `sae_ctx` in .bss
    rather than sharing the live one, so a measurement cannot disturb an
    association in flight. And it passes the rabbit's own MAC as both peers:
    the derivation sorts the two addresses and then does exactly the same
    arithmetic either way, so the timing is honest even though the commit
    itself is meaningless.
    """
    # 1. The opcode number.
    for q in (root / "inc/vm/vbc.h", root / "mtl/mtl_linux/inc/vm/vbc.h"):
        t = q.read_text()
        if "OPnetSaeMs" in t:
            print(f"[skip] {q}: OPnetSaeMs already present")
            continue
        a = "#define OPnetSaePw 153\n"
        if a not in t:
            sys.exit(f"FAIL {q}: OPnetSaePw anchor missing — wpa3_boot_pw must run first")
        q.write_text(t.replace(a, a + "#define OPnetSaeMs 154\n", 1))
        print(f"[ok]   {q}: added OPnetSaeMs 154")

    # 2. The timed commit itself, beside the rest of the SAE code.
    q = root / "src/net/ieee80211.c"
    # read_src/write_src, not read_text/write_text: this file is 2 250 CRLF
    # lines and the plain helpers rewrite every one of them to LF, which
    # changes the whole file and stops wpa2-scanned-ciphers.patch applying at
    # its very first hunk. Cost: two builds.
    t = read_src(q)
    if "ieee80211_sae_time_commit" not in t:
        # Appended at the END of the file, deliberately. patches/ apply AFTER
        # apply-mods, and wpa2-scanned-ciphers.patch anchors its first hunk
        # around line 50 -- the first version of this step inserted here
        # beside ieee80211_sae_remember and the patch stopped applying, which
        # is the failure patch_wpa3_sae's own docstring warns about. Nothing
        # below needs to be early: ieee80211_sae_rng and ieee80211_sae_tick
        # are static and defined further up, and this only has to compile.
        if "static void ieee80211_sae_remember(const uint8_t *bssid)" not in t:
            sys.exit(f"FAIL {q}: SAE block not found — wpa3_sae must run first")
        fn = (
            "/* One commit, timed, for the diagnostic build. Its own context so a\n"
            "   measurement cannot disturb a real association, and the rabbit's own\n"
            "   MAC on both sides -- the derivation sorts the pair and then does the\n"
            "   same work regardless, so the number is honest. */\n"
            "static sae_ctx ieee80211_sae_probe;\n"
            "\n"
            "uint32_t ieee80211_sae_time_commit(const uint8_t *password)\n"
            "{\n"
            "  uint32_t t0, t1;\n"
            "\n"
            "  if(password == NULL || password[0] == 0) return 0;\n"
            "  t0 = counter_timer;\n"
            "  if(!sae_commit(&ieee80211_sae_probe, rt2501_mac, rt2501_mac,\n"
            "                 (const char *)password,\n"
            "                 ieee80211_sae_rng, NULL, ieee80211_sae_tick)) {\n"
            "    sae_clear(&ieee80211_sae_probe);\n"
            "    return 0;\n"
            "  }\n"
            "  t1 = counter_timer;\n"
            "  sae_clear(&ieee80211_sae_probe);\n"
            "  sprintf(dbg_buffer, \"SAE: a commit costs %lu ms here\"EOL,\n"
            "          (unsigned long)(t1 - t0));\n"
            "  DBG_WIFI(dbg_buffer);\n"
            "  return t1 - t0;\n"
            "}\n"
        )
        # ...and the block we append has to speak the file's own line endings.
        nl = "\r\n" if "\r\n" in t else "\n"
        if nl == "\r\n":
            fn = fn.replace("\n", "\r\n")
        write_src(q, t.rstrip("\r\n") + nl + nl + fn)
        print(f"[ok]   {q}: ieee80211_sae_time_commit added")
    else:
        print(f"[skip] {q}: already there")

    # 3. Its declaration.
    q = root / "inc/net/ieee80211.h"
    t = read_src(q)
    if "ieee80211_sae_time_commit" not in t:
        # No trailing newline in the needle: this header is CRLF in the
        # pristine tree, and read_src preserves that. A "\n" here only
        # matched because another step had already rewritten the file
        # through write_text and flattened it -- an order dependency that
        # held in the shipped mode and broke the moment the emulator built
        # a shorter step list.
        a = "void ieee80211_sae_password(const uint8_t *password);"
        if a not in t:
            sys.exit(f"FAIL {q}: the password hook is missing — wpa3_sae must run first")
        nl = "\r\n" if "\r\n" in t else "\n"
        t = t.replace(a, a + nl +
                      "/* Diagnostic: how long one commit takes on this chip, in ms. */" + nl +
                      "uint32_t ieee80211_sae_time_commit(const uint8_t *password);", 1)
        write_src(q, t)
        print(f"[ok]   {q}: declared")

    # 4. The dispatch case.
    q = root / "src/vm/vinterp.c"
    t = q.read_text()
    if "OPnetSaeMs" not in t:
        needle = "      case OPnetSaePw:\n"
        if needle not in t:
            sys.exit(f"FAIL {q}: OPnetSaePw case missing — wpa3_boot_pw must run first")
        case = (
            "      case OPnetSaeMs:\n"
            "        {\n"
            "          /* Times one real SAE commit. Seconds, possibly. */\n"
            "          int32_t pw=VALTOPNT(VSTACKGET(0));\n"
            "          int32_t ms=0;\n"
            "          if (pw!=NIL)\n"
            "            ms=(int32_t)ieee80211_sae_time_commit((const uint8_t*)VSTARTBIN(pw));\n"
            "          VSTACKSET(0, INTTOVAL(ms));\n"
            "        }\n"
            "        break;\n"
        )
        t = t.replace(needle, case + needle, 1)
        q.write_text(t)
        print(f"[ok]   {q}: added OPnetSaeMs case")

    # 5. The compiler tables.
    q = root / "mtl/mtl_linux/src/vcomp/stdlib_core.cpp"
    t = q.read_text()
    if '"netSaeMs"' in t:
        print(f"[skip] {q}: netSaeMs already registered")
    else:
        nb = "#define NBcore 121"
        if nb not in t:
            sys.exit(f"FAIL {q}: NBcore 121 not found — wpa3_boot_pw must run first")
        t = t.replace(nb, "#define NBcore 122", 1)
        for old, newtext in [
            ('/*18*/"netSaePw"\n};',  '/*18*/"netSaePw",\n/*19*/"netSaeMs"\n};'),
            ('/*18*/OPnetSaePw,\n};', '/*18*/OPnetSaePw,\n/*19*/OPnetSaeMs,\n};'),
            ('/*18*/1\n};',           '/*18*/1,\n/*19*/1\n};'),
            ('/*18*/"fun[S]I"\n};',   '/*18*/"fun[S]I",\n/*19*/"fun[S]I"\n};'),
        ]:
            if old not in t:
                sys.exit(f"FAIL {q}: could not splice {old!r}")
            t = t.replace(old, newtext, 1)
        q.write_text(t)
        print(f"[ok]   {q}: registered netSaeMs (NBcore 122)")

    # 6. The measurement reaches us in the boot URL, where the warren's HTTP
    #    log already records every query string.
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t = read_src(q)
    if "netSaeMs" in t:
        print(f"[skip] {q}: boot_url already carries the measurement")
        return
    a = '::"&h="::(itoa HARDWARE)::nil;;'
    if t.count(a) != 1:
        sys.exit(f"FAIL {q}: expected one boot_url tail, found {t.count(a)}")
    write_src(q, t.replace(
        a, '::"&h="::(itoa HARDWARE)::"&t="'
           '::(itoa netSaeMs confGetWifikey0)::nil;;', 1))
    print(f"[ok]   {q}: boot_url reports the commit cost as &t=")


def patch_eapol_msg2_ie(root: Path) -> None:
    """Message 2 of the four-way handshake repeats the association's RSN IE.

    hostapd compares the RSN IE it finds in message 2 with the one from the
    (re)association request, byte for byte (`wpa_compare_rsn_ie`), and
    disconnects the station with reason 17 on any difference. The stock
    supplicant writes the *pairwise* cipher into the group-cipher slot of
    that IE, because by then `ieee80211_associate()` has thrown the group
    cipher away (patch_group_cipher tells that story). On a CCMP-only
    network the two happen to be equal and nobody notices. On a mixed-mode
    network the association says group TKIP and message 2 says group CCMP:

        association: 30140100000fac02 0100000fac04 0100000fac02 0000
        message 2:   30140100000fac04 0100000fac04 0100000fac02 0000

    Measured in the emulator on 2026-11-08, the day its access point
    started comparing the two the way hostapd does
    (scenarios/wpa2-mixed-gtk.txt). `ieee80211_group_cipher` already holds
    the right byte, so this is one assignment.
    """
    q = root / "src/net/eapol.c"
    t = read_src(q)
    if "key_data+7)  = ieee80211_group_cipher" in t:
        print(f"[skip] {q}: message 2 already carries the group cipher")
        return
    if "ieee80211_group_cipher" not in read_src(root / "inc/net/ieee80211.h"):
        sys.exit(f"FAIL {q}: ieee80211_group_cipher is missing -- group_cipher must run first")
    nl = "\r\n" if "\r\n" in t else "\n"
    a = "    *(fr_out.llc_eapol.key_frame.key_data+7)  = (ieee80211_encryption&0x0F);" + nl
    if t.count(a) != 1:
        sys.exit(f"FAIL {q}: the message-2 group-cipher byte is not where expected")
    b = ("    /* The GROUP cipher, kept by patch_group_cipher: on a mixed-mode AP the" + nl +
         "       association request says TKIP here and this byte said CCMP, and" + nl +
         "       hostapd compares the two IEs and disconnects (reason 17). */" + nl +
         "    *(fr_out.llc_eapol.key_frame.key_data+7)  = ieee80211_group_cipher;" + nl)
    write_src(q, t.replace(a, b, 1))
    print(f"[ok]   {q}: message 2 names the group cipher the association named")


def patch_wpa3_eapol_akm(root: Path) -> None:
    """Run the four-way handshake on SAE's key schedule, not WPA2-PSK's.

    802.11-2020 12.7.3 and Tables 12-10/12-11: when the negotiated AKM is
    00-0F-AC:8 the EAPOL-Key descriptor version is 0, the PTK is
    KDF-SHA-256(PMK, "Pairwise key expansion", ...) and the MIC is
    AES-128-CMAC under the KCK -- hostapd's wpa_eapol_key_mic(),
    WPA_KEY_INFO_TYPE_AKM_DEFINED + WPA_KEY_MGMT_SAE: "EAPOL-Key MIC using
    AES-CMAC (AKM-defined - SAE)". HMAC-SHA-256 is SAE-EXT-KEY's (AKM 24).

    This step first shipped with HMAC-SHA-256-128 for the MIC, and the
    emulator's access point had been written to agree. On hardware,
    2026-11-08, Nabazprout on a UniFi WPA2/WPA3 transition network ran SAE
    to the end, associated, sent message 2 and waited: JTAG read
    ieee80211_state EAPOL with eapol_state stuck on message 3, then IDLE and
    a new scan, for ever. The access point drops a message 2 whose MIC it
    cannot verify without a word.

    The PTK's length is an input of KDF-SHA-256 (it is hashed into every
    block), unlike the SHA-1 PRF's: hostapd derives KCK + KEK + TK = 48
    bytes for CCMP. The stock buffer is 64, sized for TKIP's MIC keys, and
    asking the KDF for 64 gives a different KCK altogether. Same hardware
    symptom, same silence; the emulator's access point asked for 64 too.
    The stock supplicant knows versions 1 and 2 only -- HMAC-MD5 with the
    SHA-1 PRF, HMAC-SHA-1 with the SHA-1 PRF -- so after a successful SAE
    exchange it answered message 1 with a message 2 whose MIC no real
    access point can verify, and whose RSN IE still said PSK and no MFPC
    although the association request had said SAE and MFPC. Either one is
    enough for hostapd to drop the station; the second it even logs.

    The emulator could not see it: its access point answered a SAE
    authentication with descriptor version 2 and never compared the IEs,
    so `scenarios/wpa3-sae.txt` reached RUN on WPA2's handshake. Made to
    behave like hostapd on 2026-11-08, the same scenario stopped at:

        EAPOL 2/4 received, PTK derived (..., descriptor version 0)
        the RSN IE in 2/4 is not the one from the association request
          association: 30140100000fac040100000fac040100000fac08 8000
          message 2:   30140100000fac040100000fac040100000fac02 0000

    and with this step it reaches RUN again, this time on the schedule a
    real WPA3 access point runs. Everything here keys off
    `ieee80211_authmode == IEEE80211_AUTH_SAE`, which rt2501_auth sets when
    it starts SAE and resets on the next association, so a WPA2 network
    gets exactly the bytes it got before.
    """
    q = root / "src/net/eapol.c"
    t = read_src(q)
    if "eapol_akm_sae" in t:
        print(f"[skip] {q}: the AKM-defined schedule is already there")
        return
    if "IEEE80211_AUTH_SAE" not in read_src(root / "inc/net/ieee80211.h"):
        sys.exit(f"FAIL {q}: IEEE80211_AUTH_SAE is missing -- wpa3_sae must run first")
    nl = "\r\n" if "\r\n" in t else "\n"

    def need(anchor, what, count=1):
        a = anchor.replace("\n", nl)
        if t.count(a) != count:
            sys.exit(f"FAIL {q}: expected {count} of {what}, found {t.count(a)}")
        return a

    # 1. SHA-256, which the wpa3_sae step put in inc/net.
    a = need('#include "net/aes128.h"\n', "the aes128 include")
    t = t.replace(a, a + '#include "net/sha256.h"\n#include "net/cmac.h"'.replace("\n", nl) + nl, 1)

    # 2. The three decisions, in one place, before compute_ptk() needs them.
    a = need("static const uint8_t ptk_prefix[] = {\n", "the PTK label")
    helpers = (
        "/* 802.11-2020 12.7.3, Tables 12-10 and 12-11. With AKM 00-0F-AC:8 the\n"
        "   EAPOL-Key descriptor version is 0 and the schedule is the AKM's own:\n"
        "   KDF-SHA-256 for the PTK, AES-128-CMAC for the MIC (hostapd,\n"
        "   wpa_eapol_key_mic: AKM-defined, SAE).\n"
        "   Versions 1 and 2 stay what they were for every other network. */\n"
        "static int eapol_akm_sae(void)\n"
        "{\n"
        "  return ieee80211_authmode == IEEE80211_AUTH_SAE;\n"
        "}\n"
        "\n"
        "static uint8_t eapol_desc_ver(void)\n"
        "{\n"
        "  return eapol_akm_sae() ? 0 : 2;\n"
        "}\n"
        "\n"
        "/* In place of hmac_sha1 at every MIC site: the callers keep the first\n"
        "   16 of the 20 bytes they are handed, which is the whole CMAC tag. */\n"
        "static void eapol_mic_sha(const uint8_t *key, uint32_t key_len,\n"
        "                          const uint8_t *data, uint32_t data_len,\n"
        "                          uint8_t *mac)\n"
        "{\n"
        "  if(eapol_akm_sae()) {\n"
        "    (void)key_len;\n"
        "    aes_cmac(mac, key, data, data_len);\n"
        "    memset(mac + CMAC_TAG, 0, 20 - CMAC_TAG);\n"
        "    return;\n"
        "  }\n"
        "  hmac_sha1(key, key_len, data, data_len, mac);\n"
        "}\n"
        "\n"
    ).replace("\n", nl)
    t = t.replace(a, helpers + a, 1)

    # 3. The PTK.
    a = need(
        "  prf(pmk, EAPOL_MASTER_KEY_LENGTH,\n"
        "      ptk_prefix, sizeof(ptk_prefix),\n"
        "      concatenation, position,\n"
        "      ptk, length);\n"
        "}\n", "the PTK derivation")
    b = (
        "  if(eapol_akm_sae()) {\n"
        "    /* Same concatenation, the AKM's KDF over it -- and no NUL after the\n"
        "       label: that byte belongs to the SHA-1 PRF alone. The KDF hashes\n"
        "       its output length into every block, so it must be hostapd's\n"
        "       KCK + KEK + TK = 48 bytes for CCMP (wpa_pmk_to_ptk), not the\n"
        "       64-byte TKIP buffer the SHA-1 PRF could fill without harm. */\n"
        "    memset(ptk, 0, length);\n"
        "    sha256_kdf(ptk, EAPOL_MICK_LENGTH + EAPOL_EK_LENGTH + 16,\n"
        "               pmk, EAPOL_MASTER_KEY_LENGTH,\n"
        "               \"Pairwise key expansion\", concatenation, position);\n"
        "    return;\n"
        "  }\n"
        "  prf(pmk, EAPOL_MASTER_KEY_LENGTH,\n"
        "      ptk_prefix, sizeof(ptk_prefix),\n"
        "      concatenation, position,\n"
        "      ptk, length);\n"
        "}\n").replace("\n", nl)
    t = t.replace(a, b, 1)

    # 4. The MICs: five sites, all keyed on the KCK.
    a = need("hmac_sha1(ptk.s.kck, EAPOL_MICK_LENGTH,", "the KCK MIC call", 5)
    t = t.replace(a, "eapol_mic_sha(ptk.s.kck, EAPOL_MICK_LENGTH,")

    # 5. The descriptor version we put on the wire: three sites.
    a = need("key_info.key_desc_ver = 2;", "the transmitted descriptor version", 3)
    t = t.replace(a, "key_info.key_desc_ver = eapol_desc_ver();")

    # 6. ...and the one the AP puts on message 1.
    a = need(
        "      case 0x02:\n"
        "        ieee80211_encryption |= IEEE80211_CIPHER_CCMP;\n", "the message-1 version switch")
    b = (
        "      case 0x00:\n"
        "        /* AKM-defined (SAE): CCMP, on the schedule eapol_akm_sae()\n"
        "           selects. */\n"
        "        ieee80211_encryption |= IEEE80211_CIPHER_CCMP;\n"
        "        break;\n").replace("\n", nl) + a
    t = t.replace(a, b, 1)

    # 7. Message 2's RSN IE says what the association request said.
    a = need("    *(fr_out.llc_eapol.key_frame.key_data+13) = (ieee80211_encryption&0x0F);\n",
             "the message-2 pairwise byte")
    b = a + (
        "    if(eapol_akm_sae()) {\n"
        "      /* AKM 8 and MFPC, as the association request has them: hostapd\n"
        "         compares the two IEs and disconnects on any difference. */\n"
        "      *(fr_out.llc_eapol.key_frame.key_data+19) = IEEE80211_AUTH_AKM_SAE;\n"
        "      *(fr_out.llc_eapol.key_frame.key_data+20) = 0x80;\n"
        "    }\n").replace("\n", nl)
    t = t.replace(a, b, 1)

    write_src(q, t)
    print(f"[ok]   {q}: the handshake runs on SAE's own key schedule")
def patch_entropy_pool(root: Path) -> None:
    """One entropy pool behind every secret, and a gate before the first key.

    What it replaces. `ieee80211_sae_rng` was SHA-256 over the millisecond
    timer, the RSSI average and `nab_rand()` -- an xorshift seeded from the
    same timer. Every input is a function of the moment of boot, which
    anyone in radio range knows to within seconds, so from one captured
    SAE commit the whole state is an offline search of about 2^46 hashes;
    a hit yields the password element (an offline dictionary attack on the
    passphrase, the thing SAE exists to prevent), the session PMK, and --
    because `nab_kx_vm_begin` and `nab_tls_vm_start` drew from the same
    stream -- the X25519 adoption private key whose public half goes out in
    clear, and with it the HMAC and TLS keys for the rabbit's lifetime.
    The EAPOL SNonce came from `nab_rand()` alone. The 2026-11-08 audit's
    S1 and its SNonce finding.

    What it is now: crypto/nab_rng.c, a SHA-256 accumulator (see its header
    for the design and the credit arithmetic). Fed from

      - every received frame, in ieee80211_input(): arrival millisecond,
        RSSI, sequence number and first body bytes (2 bits of credit each);
      - the power-up contents of `buffer_temp`, 4 KB of NOLOAD external RAM,
        and a 32-byte carry in the same RAM rewritten at every draw, so a
        soft reset does not restart the stream (hashed in at main());
      - a 32-byte seed in the configuration sector at offset 4064, written
        ONCE, on the first boot whose pool reaches 128 bits of credit, and
        read on every boot after (128 bits of credit). Once, because the
        ML67Q4051's flash is rated for 100 program cycles and that sector
        holds the Wi-Fi password: a write per boot is a rabbit that forgets
        its network inside a year. Two boots still never start the same:
        the carry and the SRAM noise differ, and so do the frames.

    Who draws what. SAE's rand/mask and the adoption private key are gated:
    `ieee80211_sae_begin` returns 0 until nab_rng_ready() and the boot
    bytecode tries again (it already retries a failed association);
    `kxBegin` returns nil and adoption.mtl asks again on its next pass for
    up to KX_WAIT seconds. The SNonce and the TLS client random are fresh
    but not secret, and take ungated bytes rather than hold up a WPA2
    association or downgrade a TLS connection.

    Measured in the emulator (scenarios/wpa3-sae.txt): the pool is ready
    before the first commit on a cold boot with no seed -- the scan alone
    hears more than 64 frames -- and tools/rng-boots.sh shows two boots on
    identical timers draw different scalars and different key bytes.

    Needs wpa3_sae (SHA-256, the SAE block it edits) and extbss (the
    .extbss section the carry and buffer_temp live in). Requires that the
    staged crypto carries nab_rng.c/.h.
    """
    here = _staged_crypto(root)
    for name, dst in (("nab_rng.c", "src/net/nab_rng.c"),
                      ("nab_rng.h", "inc/net/nab_rng.h")):
        src = here / name
        if not src.is_file():
            sys.exit(f"FAIL entropy_pool: {src} missing from the staged crypto")
        (root / dst).write_bytes(src.read_bytes())
    print(f"[ok]   {root}: nab_rng.c/.h copied into src/net and inc/net")

    # -- the driver: the pool replaces the SAE stream, every frame feeds it,
    #    the first commit waits for it, and the seed is written once --------
    q = root / "src/net/ieee80211.c"
    t, nl = read_flat(q)
    if "nab_rng_frame(" in t:
        print(f"[skip] entropy_pool: {q} already done")
    else:
        if "#include \"net/sha256.h\"\n" not in t:
            sys.exit(f"FAIL {q}: no SAE block -- wpa3_sae must run first")
        t = t.replace("#include \"net/sha256.h\"\n",
                      "#include \"net/sha256.h\"\n#include \"net/nab_rng.h\"\n", 1)

        a = t.find("/* Where SAE's secrecy actually comes from")
        b = t.find("static void ieee80211_sae_rng(uint8_t *out, uint32_t len, void *arg)")
        if a < 0 or b < 0 or b < a:
            sys.exit(f"FAIL {q}: ieee80211_sae_rng is not where wpa3_sae put it")
        e = t.find("\n}\n", b)
        if e < 0:
            sys.exit(f"FAIL {q}: ieee80211_sae_rng has no end")
        e += len("\n}\n")
        t = t[:a] + (
            "/* SAE's randomness comes from the entropy pool -- nab_rng.h says what\n"
            "   feeds it and why the timer alone was not enough. The callback keeps\n"
            "   SAE's signature; readiness is checked before the commit is built,\n"
            "   in ieee80211_sae_begin, because this cannot say no. */\n"
            "static void ieee80211_sae_rng(uint8_t *out, uint32_t len, void *arg)\n"
            "{\n"
            "  (void)arg;\n"
            "  nab_rng_bytes(out, len);\n"
            "}\n"
            "\n"
            "/* The seed in the configuration sector, written once: see nab_rng.h.\n"
            "   The same call sysSave makes, interrupts off for the sector erase,\n"
            "   with the same 4 KB scratch buffer. */\n"
            "extern uint8_t buffer_temp[4096];\n"
            "static void ieee80211_rng_persist(void)\n"
            "{\n"
            "  uint8_t seed[NAB_RNG_SEED_LEN];\n"
            "  if(!nab_rng_seed_to_store(seed)) return;\n"
            "  __disable_interrupt();\n"
            "  write_uc_flash(NAB_RNG_SEED_OFF, seed, NAB_RNG_SEED_LEN, buffer_temp);\n"
            "  __enable_interrupt();\n"
            "  memset(seed, 0, sizeof seed);\n"
            "  DBG_WIFI(\"RNG: seed stored in the configuration sector\"EOL);\n"
            "}\n"
            "\n"
            "/* Secret key material for the adoption exchange (nab_kx_vm.c): 0 until\n"
            "   the pool is ready, and the bootloader asks again. */\n"
            "int ieee80211_rng_key_bytes(uint8_t *out, uint32_t len)\n"
            "{\n"
            "  if(!nab_rng_key_bytes(out, len)) return 0;\n"
            "  ieee80211_rng_persist();\n"
            "  return 1;\n"
            "}\n"
            "\n"
            "/* Fresh, not secret: the TLS client random (nab_tls_vm.c). */\n"
            "void ieee80211_rng_bytes(uint8_t *out, uint32_t len)\n"
            "{\n"
            "  nab_rng_bytes(out, len);\n"
            "}\n"
        ) + t[e:]

        gate_at = "  ieee80211_sae_tokens = 0;\n"
        if t.count(gate_at) != 1:
            sys.exit(f"FAIL {q}: ieee80211_sae_begin is not the shape entropy_pool expects")
        t = t.replace(gate_at, gate_at + (
            "  /* No commit from a pool that has not earned it: the scalars that\n"
            "     leave in it are what an observer would search. The radio is on\n"
            "     the AP's channel now, so wait here and listen: its beacons alone\n"
            "     are 20 bits of credit a second, and the frames arrive from the\n"
            "     USB interrupt during DelayMs exactly as they do during a scan.\n"
            "     This happens once in a rabbit's life -- the seed stored right\n"
            "     after makes every later boot ready at init (nab_rng.h). */\n"
            "  if(!nab_rng_ready()) {\n"
            "    uint32_t t0 = counter_timer;\n"
            "    DBG_WIFI(\"SAE: the entropy pool is not ready, listening first\"EOL);\n"
            "    while(!nab_rng_ready()\n"
            "          && (uint32_t)(counter_timer - t0) < IEEE80211_RNG_WAIT_MS)\n"
            "      DelayMs(100);\n"
            "    if(!nab_rng_ready()) {\n"
            "      /* An AP whose beacons we cannot hear for this long is not one\n"
            "         we would associate with anyway. Back to IDLE for a retry --\n"
            "         rt2501_auth reads IDLE as \"not now\", never as \"try PSK\". */\n"
            "      DBG_WIFI(\"SAE: still no credit, no commit; retrying later\"EOL);\n"
            "      ieee80211_state = IEEE80211_S_IDLE;\n"
            "      return 0;\n"
            "    }\n"
            "  }\n"
            "  ieee80211_rng_persist();\n"), 1)
        t = t.replace("#include \"net/nab_rng.h\"\n",
                      "#include \"net/nab_rng.h\"\n"
                      "/* How long the first commit may listen for the pool's credit. Within\n"
                      "   the bootloader's 20 s Wi-Fi budget after a 4.9 s scan. */\n"
                      "#define IEEE80211_RNG_WAIT_MS 8000\n", 1)

        hook = ("\t\tif(ieee80211_sae_begin()) return;\n"
                "\t\t/* Could not start: an AP in transition mode still takes PSK, so\n")
        if t.count(hook) != 1:
            sys.exit(f"FAIL {q}: the SAE hook in rt2501_auth is not where wpa3_sae put it")
        t = t.replace(hook,
                      "\t\tif(ieee80211_sae_begin()) return;\n"
                      "\t\t/* entropy_pool: a pool still short of credit went to IDLE for a\n"
                      "\t\t   retry. That is \"not yet\", not a reason to try PSK. */\n"
                      "\t\tif(ieee80211_state == IEEE80211_S_IDLE) return;\n"
                      "\t\t/* Could not start: an AP in transition mode still takes PSK, so\n", 1)

        feed_at = ("\tstruct ieee80211_frame_min *frame_min;\n\n"
                   "\tif(ieee80211_state == IEEE80211_S_IDLE) return;\n")
        if t.count(feed_at) != 1:
            sys.exit(f"FAIL {q}: ieee80211_input's prologue not found")
        t = t.replace(feed_at,
                      "\tstruct ieee80211_frame_min *frame_min;\n\n"
                      "\t/* Every frame feeds the entropy pool, whatever becomes of it. */\n"
                      "\tnab_rng_frame(frame, length, rssi, counter_timer);\n\n"
                      "\tif(ieee80211_state == IEEE80211_S_IDLE) return;\n", 1)
        write_flat(q, t, nl)
        print(f"[ok]   {q}: SAE and the adoption key draw from the pool, every frame "
              "feeds it, the commit waits for it, the seed is written once")

    # -- main(): the pool starts from the retained RAM and the stored seed --
    q = root / "src/main.c"
    t, nl = read_flat(q)
    if "nab_rng_init" in t:
        print(f"[skip] entropy_pool: src/main.c already starts the pool")
    else:
        anchor = '  DBG_MAIN("vmemInit"EOL);'
        if anchor not in t:
            sys.exit(f"FAIL {q}: no vmemInit call to start the pool before")
        t = t.replace('#include "utils/mem.h"\n',
                      '#include "utils/mem.h"\n#include "net/nab_rng.h"\n', 1)
        t = t.replace(anchor, (
            "  /* The entropy pool (nab_rng.h): the power-up contents of the 4 KB\n"
            "   * scratch buffer in external RAM, the carry beside it, and the seed\n"
            "   * the configuration sector may hold. Before anything can draw. */\n"
            "  {\n"
            "    extern uint8_t buffer_temp[4096];\n"
            "    uint8_t seed[NAB_RNG_SEED_LEN];\n"
            "    read_uc_flash(NAB_RNG_SEED_OFF, seed, NAB_RNG_SEED_LEN);\n"
            "    nab_rng_init(buffer_temp, 4096, seed);\n"
            "    memset(seed, 0, sizeof seed);\n"
            "  }\n\n") + anchor, 1)
        write_flat(q, t, nl)
        print(f"[ok]   {q}: the pool starts before vmemInit")

    # -- vlog.c: the scratch buffer is shared with the driver ---------------
    q = root / "src/vm/vlog.c"
    t, nl = read_flat(q)
    old = 'static uint8_t  buffer_temp[4096] __attribute__((section(".extbss")));'
    if "shared with ieee80211_rng_persist" in t:
        print(f"[skip] entropy_pool: buffer_temp already shared")
    elif old not in t:
        sys.exit(f"FAIL {q}: buffer_temp is not in .extbss -- extbss must run first")
    else:
        t = t.replace(old, 'uint8_t buffer_temp[4096] __attribute__((section(".extbss")));  /* shared with ieee80211_rng_persist */', 1)
        write_flat(q, t, nl)
        print(f"[ok]   {q}: buffer_temp shared with the driver's seed write")

    # -- eapol.c: the SNonce ------------------------------------------------
    q = root / "src/net/eapol.c"
    t, nl = read_flat(q)
    if "nab_rng_bytes(buffer, length)" in t:
        print(f"[skip] entropy_pool: the SNonce already comes from the pool")
    else:
        old = ("static void randbuffer(uint8_t *buffer, uint32_t length)\n"
               "{\n"
               "  uint32_t i;\n"
               "  for(i=0;i<length;i++)\n"
               "    buffer[i] = nab_rand() & 0xFF;\n"
               "}\n")
        if old not in t:
            sys.exit(f"FAIL {q}: randbuffer is not the shape no_libc_malloc left it "
                     "(entropy_pool must run after no_libc_malloc)")
        t = t.replace(old, (
            "static void randbuffer(uint8_t *buffer, uint32_t length)\n"
            "{\n"
            "  /* The SNonce: fresh from the entropy pool, ungated -- it is the\n"
            "     supplicant's contribution to the PTK's freshness, not a secret,\n"
            "     and a WPA2 association should not wait for credit. */\n"
            "  nab_rng_bytes(buffer, length);\n"
            "}\n"), 1)
        t = t.replace('#include "net/eapol.h"\n',
                      '#include "net/eapol.h"\n#include "net/nab_rng.h"\n', 1)
        write_flat(q, t, nl)
        print(f"[ok]   {q}: the SNonce comes from the pool")


def patch_rx_protected(root: Path) -> None:
    """Drop plaintext data frames once the link is protected.

    The receive filter in rt2501_rx_callback passed up any frame the radio
    reported as unencrypted (`CipherAlg == NONE`), and ieee80211_input_data
    never looked at the Protected bit: with a pairwise key installed, a
    data frame forged from the AP's address, in clear, from anyone in radio
    range with no knowledge of the password, was accepted and handed to the
    IP stack -- or, as EAPOL, to the handshake. The FragAttacks class
    (CVE-2020-26140/26143/26144) and the 2026-11-08 audit's F1; it was also
    the way in for F2 (a forged message 1 replacing the PTK) and F3.

    The rule, as mac80211 applies it: once the four-way handshake has
    completed -- message 3 verified, message 4 sent, `eapol_secure` set --
    every data frame must carry the Protected bit, except null-data, which
    has nothing to encrypt. EAPOL is no exception: before that point
    messages 1 and 3 arrive in clear as the standard says and are let
    through; after it the AP encrypts everything, a group rekey and a
    pairwise rekey included (wpa2-rekey and wpa2-ptk-rekey still pass).
    `eapol_init` clears the flag, so a new association starts open again.

    Below the driver, a second check the emulator cannot see: a data frame
    whose Protected bit is set but that the radio reports as `CipherAlg ==
    NONE` was not decrypted (no key matched) and still carries its IV and
    ciphertext; it is dropped there rather than parsed as if it were plain.

    `ieee80211_plain_dropped` counts what the gate refused, for the
    emulator's `expect-plain-dropped` and for telemetry.
    """
    q = root / "inc/net/eapol.h"
    t, nl = read_flat(q)
    if "eapol_secure" not in t:
        a = "extern eapol_state_t eapol_state;\n"
        if a not in t:
            sys.exit(f"FAIL {q}: eapol_state declaration not found")
        t = t.replace(a, a + "/* 1 from message 4 sent until the next eapol_init: the AP encrypts\n"
                              "   everything from here on, so a plaintext data frame is a forgery. */\n"
                              "extern uint8_t eapol_secure;\n", 1)
        write_flat(q, t, nl)
        print(f"[ok]   {q}: eapol_secure declared")

    q = root / "src/net/eapol.c"
    t, nl = read_flat(q)
    if "eapol_secure" in t:
        print(f"[skip] rx_protected: {q} already done")
    else:
        # cold_state has moved eapol_state into .data with an initialiser by
        # the time this runs; the flag goes beside it either way, in .data
        # too, so it is 0 at boot whatever the zero-loop covers.
        m = re.search(r"^eapol_state_t eapol_state[^;]*;\n", t, re.M)
        if not m:
            sys.exit(f"FAIL {q}: eapol_state's definition not found")
        t = t[:m.end()] + 'uint8_t eapol_secure __attribute__((section(".data"))) = 0;\n' + t[m.end():]
        for old, new, what in (
            ("void eapol_init(void)\n{\n  eapol_state = EAPOL_S_MSG1;\n",
             "void eapol_init(void)\n{\n  eapol_state = EAPOL_S_MSG1;\n  eapol_secure = 0;\n",
             "eapol_init"),
            ("  eapol_state = EAPOL_S_GROUP;\n",
             "  eapol_state = EAPOL_S_GROUP;\n"
             "  eapol_secure = 1;   /* rx_protected: from here on, no plaintext data */\n",
             "the end of message 3"),
        ):
            if t.count(old) != 1:
                sys.exit(f"FAIL {q}: expected one {what}, found {t.count(old)}")
            t = t.replace(old, new, 1)
        write_flat(q, t, nl)
        print(f"[ok]   {q}: eapol_secure set after message 4, cleared by eapol_init")

    q = root / "src/net/ieee80211.c"
    t, nl = read_flat(q)
    if "ieee80211_plain_dropped" in t:
        print(f"[skip] rx_protected: {q} already done")
    else:
        if '#include "net/eapol.h"\n' not in t:
            sys.exit(f"FAIL {q}: eapol.h not included (upstream changed?)")
        decl_at = "static void ieee80211_input_data(uint8_t *frame, uint32_t length, int16_t rssi)\n"
        if t.count(decl_at) != 1:
            sys.exit(f"FAIL {q}: ieee80211_input_data not found")
        t = t.replace(decl_at,
                      "/* Data frames refused for arriving in clear on a protected link. */\n"
                      "uint8_t ieee80211_plain_dropped;\n\n" + decl_at, 1)
        gate_at = ("\tif(length < sizeof(struct ieee80211_frame)) return;\n"
                   "\tfr = (void *)frame;\n")
        if t.count(gate_at) != 1:
            sys.exit(f"FAIL {q}: ieee80211_input_data's prologue not found")
        t = t.replace(gate_at, gate_at + (
            "\n"
            "\t/* rx_protected: with the pairwise key in use, a data frame that is\n"
            "\t   not protected did not come from the AP, whatever its addresses say.\n"
            "\t   Null-data is the one frame with nothing to encrypt. */\n"
            "\tif(eapol_secure\n"
            "\t   && ((fr->header.i_fc[0] & IEEE80211_FC0_SUBTYPE_MASK)\n"
            "\t       != IEEE80211_FC0_SUBTYPE_NODATA)\n"
            "\t   && !(fr->header.i_fc[1] & IEEE80211_FC1_PROTECTED)) {\n"
            "\t\tieee80211_plain_dropped++;\n"
            "\t\tDBG_WIFI(\"Plaintext data frame on a protected link, dropped\"EOL);\n"
            "\t\treturn;\n"
            "\t}\n"), 1)
        write_flat(q, t, nl)
        print(f"[ok]   {q}: plaintext data frames dropped once the link is protected")

    q = root / "src/usb/rt2501usb.c"
    t, nl = read_flat(q)
    if "rx_protected" in t:
        print(f"[skip] rx_protected: {q} already done")
    else:
        old = ("    if(!rxd->Crc && ((rxd->CipherAlg == RT2501_CIPHER_NONE) || (rxd->CipherErr == 0))) {\n"
               "      ieee80211_input(rt2501_frame+sizeof(RXD_STRUC),\n")
        if t.count(old) != 1:
            sys.exit(f"FAIL {q}: the receive filter is not the shape rx_protected expects")
        t = t.replace(old, (
            "    /* rx_protected: a data frame with the Protected bit set that the\n"
            "       radio did not decrypt (CipherAlg NONE: no key matched) still\n"
            "       carries its IV and ciphertext. Nothing above can use it, and\n"
            "       ieee80211_input_data would read the IV as an LLC header. */\n"
            "    if(!rxd->Crc && ((rxd->CipherAlg == RT2501_CIPHER_NONE) || (rxd->CipherErr == 0))\n"
            "       && !(rxd->CipherAlg == RT2501_CIPHER_NONE && rxd->DataByteCnt >= 2\n"
            "            && (rt2501_frame[sizeof(RXD_STRUC)] & IEEE80211_FC0_TYPE_MASK) == IEEE80211_FC0_TYPE_DATA\n"
            "            && (rt2501_frame[sizeof(RXD_STRUC) + 1] & IEEE80211_FC1_PROTECTED))) {\n"
            "      ieee80211_input(rt2501_frame+sizeof(RXD_STRUC),\n"), 1)
        write_flat(q, t, nl)
        print(f"[ok]   {q}: an undecrypted protected data frame stops at the radio")


def patch_eapol_ptk_install(root: Path) -> None:
    """Install the pairwise key after message 3, not after message 2.

    The stock supplicant derives the PTK in eapol_input_msg1(), answers with
    message 2 and installs the new key into the radio right there -- before
    anything from the AP has been authenticated. 802.11i has the supplicant
    install after message 3, whose MIC proves the AP holds the same PTK.
    pacifox flagged the ordering in issue #26 and the 2026-11-08 audit made
    it a finding (F2): message 1 carries no MIC, so once eapol_ptk_rekey let
    message 1 through on a live link, one forged message 1 replaced the live
    PTK and committed its replay counter, and the link was dead until the
    next association. rx_protected closed the plaintext way in; this closes
    the hole itself, whatever the way in.

    It also makes a real rekey work on hardware. With the key swapped at
    message 2, the AP's message 3 -- still encrypted under the old PTK, the
    AP installs the new one only after message 4 -- arrived as a cipher
    error and the rekey failed; hostapd then retries three times and
    deauthenticates. The emulator could not see that: its radio has no
    cipher. It can now (expect-ptk-synced).

    What changes in eapol.c:

      * message 1 derives into `ptk_new` and leaves `ptk`, the live key,
        alone; message 2 is MIC'd with the candidate's KCK and echoes the
        counter of the frame it answers (`replay_pending`) rather than
        committing it -- the commit moved to message 3, after the MIC;
      * message 3's MIC is checked with the candidate's KCK; on MIC OK the
        candidate becomes `ptk`, message 4 goes out (still in clear, as
        ieee80211_send has it while eapol_state is MSG3), and only then is
        the key handed to the radio, with ptk_tsc reset;
      * a retransmitted message 3 (state already GROUP) re-sends message 4
        and does NOT reinstall: reinstalling the same PTK resets the packet
        counters, which is KRACK's pairwise case (CVE-2017-13077);
      * a message 1 on a live link (state GROUP or RUN) no longer drops the
        state to MSG1: `eapol_rekey` marks the rekey and the state stays,
        so the uplink stays encrypted under the live key (ieee80211_send
        sends in clear only in MSG1/MSG3) and a group message is still
        accepted meanwhile. Message 3 ends the rekey; a forged message 1
        that no message 3 follows leaves the session exactly as it was.

    Needs eapol_replay (replay_pending, eapol_commit_replay) and
    eapol_ptk_rekey (message 1 accepted while running -- without it this is
    moot) before it; wpa2_gtk for the message 3 shape it anchors on.
    """
    p = root / "src/net/eapol.c"
    t, nl = read_flat(p)
    if "ptk_new" in t:
        print(f"[skip] eapol_ptk_install: {p} already installs after message 3")
        return
    for need, what in (("replay_pending", "eapol_replay"),
                       ("hostapd's wpa_ptk_rekey", "eapol_ptk_rekey"),
                       ("eapol_install_msg3_gtk", "wpa2_gtk")):
        if need not in t:
            sys.exit(f"FAIL {p}: eapol_ptk_install needs {what} applied first")

    def once(old, new, what):
        nonlocal t
        if t.count(old) != 1:
            sys.exit(f"FAIL {p}: expected exactly one {what}, found {t.count(old)}")
        t = t.replace(old, new, 1)

    once("} ptk;  // Pairwise Transient Key",
         "} ptk, ptk_new;  // Pairwise Transient Key; ptk_new: derived at\n"
         "                 // message 1, live only once message 3 verified\n"
         "                 // (eapol_ptk_install)\n"
         "/* 1 from a message 1 received on a live link until the message 3 that\n"
         "   completes the rekey: the state stays GROUP/RUN meanwhile. */\n"
         "static uint8_t eapol_rekey;",
         "ptk declaration")
    once("void eapol_init(void)\n{\n  eapol_state = EAPOL_S_MSG1;\n",
         "void eapol_init(void)\n{\n  eapol_state = EAPOL_S_MSG1;\n"
         "  eapol_rekey = 0;   /* eapol_ptk_install */\n",
         "eapol_init")
    once("  eapol_state = EAPOL_S_MSG1;\n"
         "\n"
         "  if((ieee80211_encryption&0x0F) == 0x00)\n",
         "  /* On a live link this is a rekey (eapol_ptk_install): the live key\n"
         "     stays installed and the state stays where it is, so the uplink\n"
         "     stays encrypted and a group message is still taken; message 3\n"
         "     ends it. A message 1 nobody follows up changes nothing. */\n"
         "  if(eapol_state == EAPOL_S_GROUP || eapol_state == EAPOL_S_RUN)\n"
         "    eapol_rekey = 1;\n"
         "  else\n"
         "    eapol_state = EAPOL_S_MSG1;\n"
         "\n"
         "  if((ieee80211_encryption&0x0F) == 0x00)\n",
         "message 1 state")
    once("  /* EAPOL_S_GROUP can happen if the AP did not receive our reply */\n"
         "  if((eapol_state != EAPOL_S_MSG3) && (eapol_state != EAPOL_S_GROUP)) {\n",
         "  /* EAPOL_S_GROUP can happen if the AP did not receive our reply, and\n"
         "     RUN during a rekey (eapol_ptk_install). */\n"
         "  if((eapol_state != EAPOL_S_MSG3) && (eapol_state != EAPOL_S_GROUP)\n"
         "     && !(eapol_rekey && eapol_state == EAPOL_S_RUN)) {\n",
         "message 3 state guard")
    # --- message 1: derive a candidate, commit nothing -------------------
    once("  memcpy(ptk.b, ptk_buffer, EAPOL_PTK_LENGTH);",
         "  memcpy(ptk_new.b, ptk_buffer, EAPOL_PTK_LENGTH);",
         "PTK copy in message 1")
    once('    sprintf(dbg_buffer, "%02x", ptk.b[i]);',
         '    sprintf(dbg_buffer, "%02x", ptk_new.b[i]);',
         "PTK dump in message 1")
    once("    eapol_mic_sha(ptk.s.kck, EAPOL_MICK_LENGTH,\n"
         "              (uint8_t *)&fr_out+LLC_LENGTH,\n"
         "              sizeof(struct eapol_frame)+ rsn_size-LLC_LENGTH,",
         "    eapol_mic_sha(ptk_new.s.kck, EAPOL_MICK_LENGTH,\n"
         "              (uint8_t *)&fr_out+LLC_LENGTH,\n"
         "              sizeof(struct eapol_frame)+ rsn_size-LLC_LENGTH,",
         "message 2 SHA MIC")
    once("    hmac_md5(ptk.s.kck, EAPOL_MICK_LENGTH,\n"
         "              (uint8_t *)&fr_out+LLC_LENGTH, sizeof(struct eapol_frame)+\n"
         "              rsn_size-LLC_LENGTH, fr_out.llc_eapol.key_frame.key_mic);",
         "    hmac_md5(ptk_new.s.kck, EAPOL_MICK_LENGTH,\n"
         "              (uint8_t *)&fr_out+LLC_LENGTH, sizeof(struct eapol_frame)+\n"
         "              rsn_size-LLC_LENGTH, fr_out.llc_eapol.key_frame.key_mic);",
         "message 2 MD5 MIC")
    once("  /* No MIC on message 1/4 — nothing to wait for. */\n"
         "  eapol_commit_replay();\n",
         "  /* No MIC on message 1/4, so nothing it carries is trusted yet: the\n"
         "     replay counter is echoed in message 2 and committed at message 3\n"
         "     (eapol_ptk_install). */\n",
         "message 1 replay commit")
    once("  memcpy(fr_out.llc_eapol.key_frame.replay_counter,\n"
         "                                    replay_counter, EAPOL_RPC_LENGTH);\n"
         "  memcpy(fr_out.llc_eapol.key_frame.key_nonce, snonce, EAPOL_NONCE_LENGTH);",
         "  memcpy(fr_out.llc_eapol.key_frame.replay_counter,\n"
         "                                    replay_pending, EAPOL_RPC_LENGTH);\n"
         "  memcpy(fr_out.llc_eapol.key_frame.key_nonce, snonce, EAPOL_NONCE_LENGTH);",
         "message 2 replay counter")
    once("  /* Install pairwise encryption and MIC keys */\n"
         "  if((ieee80211_encryption&0x0F) == IEEE80211_CIPHER_CCMP)  // FIXME\n"
         "  {\n"
         "    rt2501_set_key(0, ptk.s.tk, ptk.s.mick.tx, ptk.s.mick.rx, RT2501_CIPHER_AES);\n"
         "  }\n"
         "  else\n"
         "  {\n"
         "    rt2501_set_key(0, ptk.s.tk, ptk.s.mick.tx, ptk.s.mick.rx, RT2501_CIPHER_TKIP);\n"
         "  }\n"
         "  memset(ptk_tsc, 0, EAPOL_TSC_LENGTH);\n"
         "\n"
         "  eapol_state = EAPOL_S_MSG3;\n"
         "}",
         "  /* Not installed here. The radio keeps the live key, if any, until\n"
         "     message 3 proves the AP derived the same one (eapol_ptk_install).\n"
         "     During a rekey the state stays where it was, too. */\n"
         "\n"
         "  if(!eapol_rekey) eapol_state = EAPOL_S_MSG3;\n"
         "}",
         "PTK install in message 1")
    # --- message 3: verify with the candidate, then make it live ----------
    once("  uint8_t old_mic[EAPOL_KEYMIC_LENGTH];\n"
         "  struct {\n"
         "    struct eapol_frame llc_eapol;\n"
         "  } fr_out;",
         "  uint8_t old_mic[EAPOL_KEYMIC_LENGTH];\n"
         "  int32_t install_ptk;   /* eapol_ptk_install */\n"
         "  struct {\n"
         "    struct eapol_frame llc_eapol;\n"
         "  } fr_out;",
         "message 3 locals")
    once("     eapol_mic_sha(ptk.s.kck, EAPOL_MICK_LENGTH,\n"
         "     frame+LLC_LENGTH,\n",
         "     eapol_mic_sha(ptk_new.s.kck, EAPOL_MICK_LENGTH,\n"
         "     frame+LLC_LENGTH,\n",
         "message 3 SHA MIC check")
    once("    hmac_md5(ptk.s.kck, EAPOL_MICK_LENGTH,\n"
         "     frame+LLC_LENGTH,\n",
         "    hmac_md5(ptk_new.s.kck, EAPOL_MICK_LENGTH,\n"
         "     frame+LLC_LENGTH,\n",
         "message 3 MD5 MIC check")
    once('  DBG_WIFI("MIC OK"EOL);\n'
         "  eapol_commit_replay();\n"
         "\n"
         "  eapol_state = EAPOL_S_MSG3;",
         '  DBG_WIFI("MIC OK"EOL);\n'
         "  eapol_commit_replay();\n"
         "\n"
         "  /* The AP holds the key message 1 derived: it is the live one from\n"
         "     here. Handed to the radio after message 4 below, and only on the\n"
         "     first message 3 -- a retransmission (state already GROUP) must\n"
         "     not reinstall it, which would reset the packet counters. */\n"
         "  install_ptk = (eapol_state == EAPOL_S_MSG3) || eapol_rekey;\n"
         "  eapol_rekey = 0;\n"
         "  memcpy(ptk.b, ptk_new.b, EAPOL_PTK_LENGTH);\n"
         "\n"
         "  eapol_state = EAPOL_S_MSG3;",
         "message 3 MIC OK")
    once('  //DBG_WIFI("Response sent"EOL);\n'
         "\n"
         "  eapol_state = EAPOL_S_GROUP;",
         '  //DBG_WIFI("Response sent"EOL);\n'
         "\n"
         "  /* Install pairwise encryption and MIC keys: after message 4 went out\n"
         "     in clear, as the standard orders it (eapol_ptk_install). */\n"
         "  if(install_ptk) {\n"
         "    if((ieee80211_encryption&0x0F) == IEEE80211_CIPHER_CCMP)\n"
         "      rt2501_set_key(0, ptk.s.tk, ptk.s.mick.tx, ptk.s.mick.rx, RT2501_CIPHER_AES);\n"
         "    else\n"
         "      rt2501_set_key(0, ptk.s.tk, ptk.s.mick.tx, ptk.s.mick.rx, RT2501_CIPHER_TKIP);\n"
         "    memset(ptk_tsc, 0, EAPOL_TSC_LENGTH);\n"
         '    DBG_WIFI("PTK installed"EOL);\n'
         "  }\n"
         "\n"
         "  eapol_state = EAPOL_S_GROUP;",
         "message 4 sent")
    write_flat(p, t, nl)
    print(f"[ok]   {p}: the PTK is installed after message 3, not message 2")


def patch_eapol_mic_len(root: Path) -> None:
    """Bound the EAPOL MIC span by the frame the radio delivered.

    eapol_input_msg3() and eapol_input_group_msg1() compute the MIC over
    `body_length + 4` bytes, with body_length read out of the frame itself:
    up to 65 539 bytes from a receive buffer of a few hundred. eapol_input()
    only checks that the frame is at least a key frame long. The 2026-11-08
    audit's F3: one frame with a large body_length reads 64 KB past the
    receive buffer -- a wrong MIC if that memory exists, a data abort and
    the watchdog if it does not. The dispatcher drops the frame before any
    MIC check as soon as the declared body does not fit in `length`, or is
    shorter than a key frame. `eapol_bad_length` counts them, for the
    emulator (expect-eapol-badlen) and for telemetry.
    """
    p = root / "src/net/eapol.c"
    t, nl = read_flat(p)
    if "eapol_mic_span" in t:
        print(f"[skip] eapol_mic_len: {p} already bounds the MIC span")
        return
    anchor = "static void eapol_input_msg1(uint8_t *frame, uint32_t length)"
    if t.count(anchor) != 1:
        sys.exit(f"FAIL {p}: eapol_input_msg1 not found")
    helper = (
        "/* eapol_mic_len: the bytes the MIC covers, from the frame's own length\n"
        "   field -- which is attacker data until it is checked against what the\n"
        "   radio delivered. 0 when it does not fit, or is shorter than a key\n"
        "   frame; the caller drops the frame before any MIC is computed. */\n"
        "uint8_t eapol_bad_length;\n"
        "static int32_t eapol_mic_span(const struct eapol_frame *fr, uint32_t length,\n"
        "                              uint32_t *span)\n"
        "{\n"
        "  uint32_t body = ((uint32_t)fr->body_length[0] << 8) | fr->body_length[1];\n"
        "  if(body < sizeof(struct eapol_key_frame) - EAPOL_RSN_LENGTH\n"
        "     || LLC_LENGTH + 4 + body > length) {\n"
        "    eapol_bad_length++;\n"
        '    DBG_WIFI("EAPOL body length does not fit the frame, dropped"EOL);\n'
        "    return 0;\n"
        "  }\n"
        "  *span = body + 4;\n"
        "  return 1;\n"
        "}\n\n"
    )
    t = t.replace(anchor, helper + anchor, 1)
    old = "  uint8_t old_mic[EAPOL_KEYMIC_LENGTH];\n"
    if t.count(old) != 2:
        sys.exit(f"FAIL {p}: expected old_mic in message 3 and the group handler, found {t.count(old)}")
    t = t.replace(old, old + "  uint32_t mic_span;      /* eapol_mic_len */\n")
    old = "  /* Check MIC */\n  memcpy(old_mic, fr_in->key_frame.key_mic, EAPOL_KEYMIC_LENGTH);\n"
    if t.count(old) != 2:
        sys.exit(f"FAIL {p}: expected two MIC checks, found {t.count(old)}")
    t = t.replace(old, "  if(!eapol_mic_span(fr_in, length, &mic_span)) return;\n" + old)
    old = "((fr_in->body_length[0] << 8)|fr_in->body_length[1])+4,\n"
    if t.count(old) != 4:
        sys.exit(f"FAIL {p}: expected four MIC spans from the frame, found {t.count(old)}")
    t = t.replace(old, "mic_span,\n")
    write_flat(p, t, nl)
    print(f"[ok]   {p}: the MIC span is bounded by the received length")


def patch_gtk_no_reinstall(root: Path) -> None:
    """Do not reinstall a group key the radio already holds.

    A retransmitted message 3 (the AP did not hear message 4) and a repeated
    group message both run rt2501_set_key() again with the key the radio
    already has. Loading a key resets the receive sequence counter that
    goes with it on every Ralink part documented, so the frames an attacker
    recorded under that key replay: KRACK's group-key case, CVE-2017-13080,
    the 2026-11-08 audit's F4. The IGTK travels in the same key data and
    ieee80211_install_igtk() took its IPN from the frame each time, which
    is the same reset for management frames.

    So eapol.c remembers the key id and bytes it last installed and skips
    the radio when they are unchanged (eapol_init forgets them: a new
    association starts clean), and ieee80211_install_igtk() keeps the IPN
    it has when the IGTK it is handed is the one it holds. A changed key
    installs as before: the hourly rotation (wpa2-rekey) still goes
    through; what does not is the same key twice.
    """
    p = root / "src/net/eapol.c"
    t, nl = read_flat(p)
    if "gtk_live_id" in t:
        print(f"[skip] gtk_no_reinstall: {p} already skips an unchanged GTK")
    else:
        def once(old, new, what):
            nonlocal t
            if t.count(old) != 1:
                sys.exit(f"FAIL {p}: expected exactly one {what}, found {t.count(old)}")
            t = t.replace(old, new, 1)
        once("} gtk;  // Group Transient Key",
             "} gtk;  // Group Transient Key\n"
             "/* The group key the radio holds (gtk_no_reinstall): key id, 0 for\n"
             "   none, and the bytes. Loading the same key again would reset its\n"
             "   replay counter -- CVE-2017-13080. */\n"
             "static uint8_t gtk_live_id;\n"
             "static uint8_t gtk_live[32];",
             "gtk declaration")
        once("void eapol_init(void)\n{\n  eapol_state = EAPOL_S_MSG1;\n",
             "void eapol_init(void)\n{\n  eapol_state = EAPOL_S_MSG1;\n"
             "  gtk_live_id = 0;   /* gtk_no_reinstall: a new association, no key */\n",
             "eapol_init")
        once("  rt2501_set_key(key_id, gtk.s.ek, gtk.s.mick.tx, gtk.s.mick.rx,\n"
             "                 (ieee80211_group_cipher == IEEE80211_CIPHER_TKIP)\n"
             "                   ? RT2501_CIPHER_TKIP : RT2501_CIPHER_AES);\n",
             "  if(gtk_live_id == key_id\n"
             "     && memcmp(gtk_live, gtk.s.ek, sizeof(gtk.s.ek)) == 0) {\n"
             "    /* The key the radio already holds, retransmitted: leave the\n"
             "       radio's replay counter alone (gtk_no_reinstall). */\n"
             '    DBG_WIFI("GTK unchanged, not reinstalled"EOL);\n'
             "  } else {\n"
             "  rt2501_set_key(key_id, gtk.s.ek, gtk.s.mick.tx, gtk.s.mick.rx,\n"
             "                 (ieee80211_group_cipher == IEEE80211_CIPHER_TKIP)\n"
             "                   ? RT2501_CIPHER_TKIP : RT2501_CIPHER_AES);\n"
             "  gtk_live_id = key_id;\n"
             "  memcpy(gtk_live, gtk.s.ek, sizeof(gtk.s.ek));\n"
             "  }\n",
             "CCMP group key install")
        once("        if(fr_in->key_frame.key_info.key_index != 0)\n"
             "            rt2501_set_key(fr_in->key_frame.key_info.key_index,\n"
             "                           gtk.s.ek, gtk.s.mick.tx, gtk.s.mick.rx, RT2501_CIPHER_TKIP);\n",
             "        if(fr_in->key_frame.key_info.key_index != 0\n"
             "           && !(gtk_live_id == fr_in->key_frame.key_info.key_index\n"
             "                && memcmp(gtk_live, gtk.b, sizeof(gtk.b)) == 0)) {\n"
             "            rt2501_set_key(fr_in->key_frame.key_info.key_index,\n"
             "                           gtk.s.ek, gtk.s.mick.tx, gtk.s.mick.rx, RT2501_CIPHER_TKIP);\n"
             "            gtk_live_id = fr_in->key_frame.key_info.key_index;\n"
             "            memcpy(gtk_live, gtk.b, sizeof(gtk.b));\n"
             "        }\n",
             "TKIP group key install")
        write_flat(p, t, nl)
        print(f"[ok]   {p}: an unchanged group key is not reinstalled")

    q = root / "src/net/ieee80211.c"
    t, nl = read_flat(q)
    if "gtk_no_reinstall" in t:
        print(f"[skip] gtk_no_reinstall: {q} already keeps the IPN for an unchanged IGTK")
        return
    old = ("  if(!rsn_find_igtk(keydata, len, &id, ipn, key)) return;\n"
           "  memcpy(ieee80211_igtk, key, sizeof ieee80211_igtk);\n")
    if t.count(old) != 1:
        sys.exit(f"FAIL {q}: ieee80211_install_igtk is not the shape wpa3_sae left it")
    t = t.replace(old,
                  "  if(!rsn_find_igtk(keydata, len, &id, ipn, key)) return;\n"
                  "  if(ieee80211_igtk_set && ieee80211_igtk_id == id\n"
                  "     && memcmp(ieee80211_igtk, key, sizeof ieee80211_igtk) == 0) {\n"
                  "    /* gtk_no_reinstall: the IGTK we hold, retransmitted. Taking its\n"
                  "       IPN again would reopen the replay window. */\n"
                  "    memset(key, 0, sizeof key);\n"
                  "    return;\n"
                  "  }\n"
                  "  memcpy(ieee80211_igtk, key, sizeof ieee80211_igtk);\n", 1)
    write_flat(q, t, nl)
    print(f"[ok]   {q}: an unchanged IGTK keeps its packet number")


def patch_no_tkip_pairwise(root: Path) -> None:
    """Refuse TKIP as the pairwise cipher.

    TKIP is RC4 with a per-packet key mix and a 64-bit MIC; it has been
    broken since 2008 (Beck-Tews, CVE-2008-5230 class) and withdrawn from
    the standard since 802.11-2012. This firmware accepted it wherever the
    AP offered it, and ieee80211_associate() builds WPA (version 1)
    requests with TKIP as the unicast cipher whatever the AP advertised, so
    a WPA1 network was a TKIP network. The 2026-11-08 audit's F8.

    Refused, in rt2501_auth: a WPA2 network whose only pairwise cipher is
    TKIP, and any WPA1 network. The rabbit goes back to IDLE and the
    bootloader retries, as for any network it cannot join; the bytecode's
    own OPnetAuth check still filters most of these before they get here.
    Mixed-mode APs -- TKIP as the GROUP cipher with CCMP pairwise, the
    2006-era compromise -- are still joined (wpa2-mixed-gtk): the group
    cipher is the AP's to choose and refusing it would refuse the network.
    eapol_input_msg1()'s own "switch to TKIP" on key descriptor version 1
    is closed the same way, for a network that reached it with no cipher
    bit set.
    """
    q = root / "src/net/ieee80211.c"
    t, nl = read_flat(q)
    if "no_tkip_pairwise" in t:
        print(f"[skip] no_tkip_pairwise: {q} already refuses pairwise TKIP")
    else:
        old = ("\tieee80211_encryption = encryption;\n"
               "\tswitch(ieee80211_encryption&0xF0) {\n"
               "\t\tcase IEEE80211_CRYPT_NONE:\n"
               "\t\t\tieee80211_authmode = IEEE80211_AUTH_OPEN;\n")
        if t.count(old) != 1:
            sys.exit(f"FAIL {q}: rt2501_auth's encryption switch not found")
        t = t.replace(old,
                      "\tieee80211_encryption = encryption;\n"
                      "\t/* no_tkip_pairwise: the scan's low bits say which pairwise ciphers\n"
                      "\t   the AP offers (0x01 TKIP, 0x02 CCMP; ieee80211_associate picks\n"
                      "\t   CCMP when both). TKIP alone, or WPA version 1 -- which this\n"
                      "\t   firmware only ever negotiates as TKIP -- is a network we do not\n"
                      "\t   join. IDLE, and the bootloader retries as for any refusal. */\n"
                      "\tif((ieee80211_encryption&0xF0) == IEEE80211_CRYPT_WPA\n"
                      "\t   || ((ieee80211_encryption&0xF0) == IEEE80211_CRYPT_WPA2\n"
                      "\t       && (ieee80211_encryption&0x03) == 0x01)) {\n"
                      "\t\tDBG_WIFI(\"TKIP is the only pairwise cipher offered; refusing the network\"EOL);\n"
                      "\t\tieee80211_tkip_refused++;\n"
                      "\t\treturn;\n"
                      "\t}\n"
                      "\tswitch(ieee80211_encryption&0xF0) {\n"
                      "\t\tcase IEEE80211_CRYPT_NONE:\n"
                      "\t\t\tieee80211_authmode = IEEE80211_AUTH_OPEN;\n", 1)
        anchor = "void rt2501_auth(const uint8_t *ssid, const uint8_t *mac,"
        if t.count(anchor) != 1:
            sys.exit(f"FAIL {q}: rt2501_auth not found")
        t = t.replace(anchor,
                      "/* Networks refused for offering TKIP as the pairwise cipher\n"
                      "   (no_tkip_pairwise); for the emulator and telemetry. */\n"
                      "uint8_t ieee80211_tkip_refused;\n\n" + anchor, 1)
        write_flat(q, t, nl)
        print(f"[ok]   {q}: a TKIP-only or WPA1 network is refused")

    p = root / "src/net/eapol.c"
    t, nl = read_flat(p)
    if "no_tkip_pairwise" in t:
        print(f"[skip] no_tkip_pairwise: {p} already refuses the TKIP switch")
        return
    old = ("      case 0x01:\n"
           "        ieee80211_encryption |= IEEE80211_CIPHER_TKIP;\n"
           '        DBG_WIFI("AP Uses TKIP so... Switch to TKIP"EOL);\n'
           "        break;\n")
    if t.count(old) != 1:
        sys.exit(f"FAIL {p}: message 1's TKIP switch not found")
    t = t.replace(old,
                  "      case 0x01:\n"
                  "        /* no_tkip_pairwise: a key descriptor that means TKIP on a\n"
                  "           network that declared no cipher. Not answered. */\n"
                  '        DBG_WIFI("AP wants pairwise TKIP; refused"EOL);\n'
                  "        ieee80211_state = IEEE80211_S_IDLE;\n"
                  "        return;\n", 1)
    write_flat(p, t, nl)
    print(f"[ok]   {p}: message 1 no longer switches the link to TKIP")


def patch_sae_required(root: Path) -> None:
    """Once a network has authenticated with SAE, never join it with PSK.

    wpa3_sae chooses SAE for a BSSID seen advertising AKM 8, PSK otherwise;
    and ieee80211_sae_forget() demotes a BSSID whose SAE attempt is refused
    or unanswered. Both decisions rest on unauthenticated frames. An access
    point with the real SSID, another BSSID and WPA2-PSK only is joined
    with the PBKDF2 PMK, and message 2 of that handshake is what an offline
    dictionary attack on the passphrase needs -- the attack SAE exists to
    prevent. One forged refusal from the real BSSID does the same for the
    next attempt. Dragonblood's downgrade, CVE-2019-9494 family; the
    2026-11-08 audit's S2.

    The remedy is memory, as wpa_supplicant's: once SAE has succeeded on a
    network -- the AP's confirm verified, which proves it holds the password
    -- that network requires SAE from then on, and an AP of that SSID that
    offers none is refused. The memory has to survive a power cycle or it
    protects nothing, so it is in the configuration sector, next to the
    entropy pool's seed: 32 bytes at 4032, SHA-256 of a label, the SSID and
    the passphrase. Bound to both, so a reconfiguration to another network
    (or another passphrase) is not locked by the old one, and stored as a
    hash so the sector holds nothing it did not already hold.

    Flash is the constraint: the sector is rated for 100 program cycles and
    holds the Wi-Fi password. So the record is written once per network,
    only after a successful SAE, only when it is not already there, and
    from the main loop (ieee80211_timer, as rt2501_timer runs it), never
    from the receive path. The way out of the lock -- an owner who
    reprovisions the SSID from WPA3 to WPA2-only, which is what happened on
    2026-11-08 -- is config mode: a configuration save (sysSave covering
    the Wi-Fi fields, which is physical presence at the rabbit) clears the
    record, one extra sector write, and only when a record is there. Until
    then a rabbit whose network went back to WPA2 stays orange, and that
    is the trade: scenarios/wpa3-downgrade.txt now expects it.

    Needs wpa3_sae (the SAE hook and SHA-256), entropy_pool (buffer_temp
    shared with the driver, write_uc_flash in ieee80211.c) before it.
    """
    q = root / "src/net/ieee80211.c"
    t, nl = read_flat(q)
    if "ieee80211_sae_required" in t:
        print(f"[skip] sae_required: {q} already remembers SAE networks")
    else:
        for need, what in (("static int32_t ieee80211_sae_known", "wpa3_sae"),
                           ("ieee80211_rng_persist", "entropy_pool")):
            if need not in t:
                sys.exit(f"FAIL {q}: sae_required needs {what} applied first")

        def once(old, new, what):
            nonlocal t
            if t.count(old) != 1:
                sys.exit(f"FAIL {q}: expected exactly one {what}, found {t.count(old)}")
            t = t.replace(old, new, 1)

        once("/* The bodies are further down, next to rt2501_auth:",
             "/* sae_required: a network that has authenticated with SAE requires it\n"
             "   from then on. The record is 32 bytes in the configuration sector,\n"
             "   SHA-256(label, SSID, passphrase): present and matching means\n"
             "   \"required\". Written once, from the main loop; cleared by a\n"
             "   configuration save (ieee80211_sae_release, from sysSave). */\n"
             "#define IEEE80211_SAE_REQ_OFF 4032\n"
             "#define IEEE80211_SAE_REQ_LEN 32\n"
             "static uint8_t ieee80211_sae_req_pending;\n"
             "uint8_t ieee80211_sae_refused;   /* PSK refused on a SAE network */\n"
             "extern uint8_t buffer_temp[4096];\n"
             "static void ieee80211_sae_req_tag(uint8_t out[IEEE80211_SAE_REQ_LEN])\n"
             "{\n"
             "  sha256_ctx c;\n"
             "  sha256_init(&c);\n"
             "  sha256_update(&c, (const uint8_t *)\"nabaztag sae required\", 21);\n"
             "  sha256_update(&c, ieee80211_assoc_ssid,\n"
             "                strlen((const char *)ieee80211_assoc_ssid));\n"
             "  sha256_update(&c, (const uint8_t *)\"\", 1);\n"
             "  sha256_update(&c, ieee80211_sae_pw, strlen((const char *)ieee80211_sae_pw));\n"
             "  sha256_final(&c, out);\n"
             "}\n"
             "static int32_t ieee80211_sae_required(void)\n"
             "{\n"
             "  uint8_t have[IEEE80211_SAE_REQ_LEN], want[IEEE80211_SAE_REQ_LEN];\n"
             "  if(ieee80211_sae_pw[0] == 0) return 0;\n"
             "  read_uc_flash(IEEE80211_SAE_REQ_OFF, have, IEEE80211_SAE_REQ_LEN);\n"
             "  ieee80211_sae_req_tag(want);\n"
             "  return memcmp(have, want, IEEE80211_SAE_REQ_LEN) == 0;\n"
             "}\n"
             "/* Main-loop context only (ieee80211_timer): a sector erase with\n"
             "   interrupts off is not for the receive path. */\n"
             "static void ieee80211_sae_req_store(void)\n"
             "{\n"
             "  uint8_t tag[IEEE80211_SAE_REQ_LEN];\n"
             "  if(!ieee80211_sae_req_pending) return;\n"
             "  ieee80211_sae_req_pending = 0;\n"
             "  if(ieee80211_sae_pw[0] == 0 || ieee80211_sae_required()) return;\n"
             "  ieee80211_sae_req_tag(tag);\n"
             "  __disable_interrupt();\n"
             "  write_uc_flash(IEEE80211_SAE_REQ_OFF, tag, IEEE80211_SAE_REQ_LEN, buffer_temp);\n"
             "  __enable_interrupt();\n"
             "  DBG_WIFI(\"SAE: this network requires SAE from now on (stored)\"EOL);\n"
             "}\n"
             "/* A configuration save: the owner is at the rabbit, the lock goes. */\n"
             "void ieee80211_sae_release(void)\n"
             "{\n"
             "  static const uint8_t zero[IEEE80211_SAE_REQ_LEN];\n"
             "  uint8_t have[IEEE80211_SAE_REQ_LEN];\n"
             "  read_uc_flash(IEEE80211_SAE_REQ_OFF, have, IEEE80211_SAE_REQ_LEN);\n"
             "  if(memcmp(have, zero, IEEE80211_SAE_REQ_LEN) == 0) return;\n"
             "  __disable_interrupt();\n"
             "  write_uc_flash(IEEE80211_SAE_REQ_OFF, (uint8_t *)zero, IEEE80211_SAE_REQ_LEN, buffer_temp);\n"
             "  __enable_interrupt();\n"
             "  DBG_WIFI(\"SAE: requirement cleared by a configuration save\"EOL);\n"
             "}\n\n"
             "/* The bodies are further down, next to rt2501_auth:",
             "SAE cache tail")
        once('    DBG_WIFI("SAE: accepted, associating"EOL);\n'
             "    ieee80211_associate();",
             '    DBG_WIFI("SAE: accepted, associating"EOL);\n'
             "    ieee80211_sae_req_pending = 1;   /* sae_required: stored from the main loop */\n"
             "    ieee80211_associate();",
             "SAE accepted")
        once("\tif(ieee80211_sae_known(bssid)\n"
             "\t   && (ieee80211_encryption & 0xF0) == IEEE80211_CRYPT_WPA2) {\n",
             "\t{\n"
             "\tint32_t sae_req = ieee80211_sae_required();\n"
             "\t/* sae_required: a network that has done SAE is not joined any other\n"
             "\t   way, whatever this AP advertises. */\n"
             "\tif(sae_req && (ieee80211_encryption & 0xF0) != IEEE80211_CRYPT_WPA2) {\n"
             "\t\tDBG_WIFI(\"SAE: this network requires SAE and the AP offers none; refused\"EOL);\n"
             "\t\tieee80211_sae_refused++;\n"
             "\t\treturn;\n"
             "\t}\n"
             "\t/* ...and an AP of it that advertised no SAE is a PSK attempt refused. */\n"
             "\tif(sae_req && !ieee80211_sae_known(bssid)) ieee80211_sae_refused++;\n"
             "\tif((ieee80211_sae_known(bssid) || sae_req)\n"
             "\t   && (ieee80211_encryption & 0xF0) == IEEE80211_CRYPT_WPA2) {\n",
             "SAE hook in rt2501_auth")
        once('\t\tDBG_WIFI("SAE: falling back to open auth + PSK"EOL);\n'
             "\t\tieee80211_authmode = IEEE80211_AUTH_OPEN;\n"
             "\t\tieee80211_state = IEEE80211_S_IDLE;\n"
             "\t}\n",
             "\t\tif(sae_req) {\n"
             "\t\t\tDBG_WIFI(\"SAE: required on this network, not falling back to PSK\"EOL);\n"
             "\t\t\tieee80211_sae_refused++;\n"
             "\t\t\tieee80211_authmode = IEEE80211_AUTH_OPEN;\n"
             "\t\t\tieee80211_state = IEEE80211_S_IDLE;\n"
             "\t\t\treturn;\n"
             "\t\t}\n"
             '\t\tDBG_WIFI("SAE: falling back to open auth + PSK"EOL);\n'
             "\t\tieee80211_authmode = IEEE80211_AUTH_OPEN;\n"
             "\t\tieee80211_state = IEEE80211_S_IDLE;\n"
             "\t}\n"
             "\t}\n",
             "PSK fallback")
        # Inside ieee80211_timer's managed branch: main-loop context (main.c
        # calls rt2501_timer from its loop), and text neither wpa3_sae nor
        # link_liveness anchors on.
        once("\tif(ieee80211_mode == IEEE80211_M_MANAGED) {\n"
             "\t\t/* Managed mode */\n"
             "\t\tif(ieee80211_timeout > 0) ieee80211_timeout--;\n",
             "\tif(ieee80211_mode == IEEE80211_M_MANAGED) {\n"
             "\t\t/* Managed mode */\n"
             "\t\tieee80211_sae_req_store();   /* sae_required: main-loop context */\n"
             "\t\tif(ieee80211_timeout > 0) ieee80211_timeout--;\n",
             "ieee80211_timer's managed branch")
        write_flat(q, t, nl)
        print(f"[ok]   {q}: a network that did SAE requires SAE, remembered in the config sector")

    v = root / "src/vm/vlog.c"
    t, nl = read_flat(v)
    if "ieee80211_sae_release" in t:
        print(f"[skip] sae_required: {v} already clears the record on a configuration save")
        return
    old = ("  __disable_interrupt();\n"
           "  write_uc_flash(j,(uint8_t*)dst,len,buffer_temp);\n"
           "  __enable_interrupt();\n"
           "  return len;\n")
    if t.count(old) != 1:
        sys.exit(f"FAIL {v}: sysSave's flash write not found")
    t = t.replace(old,
                  "  __disable_interrupt();\n"
                  "  write_uc_flash(j,(uint8_t*)dst,len,buffer_temp);\n"
                  "  __enable_interrupt();\n"
                  "  /* sae_required: a save that covers the Wi-Fi fields (offset 92, the\n"
                  "     208-byte page the bootloader's config mode writes) is the owner at\n"
                  "     the rabbit; the \"SAE required\" record goes with it. */\n"
                  "  if(j <= 92 && j + len >= 208) ieee80211_sae_release();\n"
                  "  return len;\n", 1)
    anchor = "// pour le firmware, le \"fichier\" ouvert est toujours l'eeprom\nint32_t sysSave("
    if t.count(anchor) != 1:
        sys.exit(f"FAIL {v}: sysSave not found")
    t = t.replace(anchor, "void ieee80211_sae_release(void);   /* sae_required, ieee80211.c */\n" + anchor, 1)
    write_flat(v, t, nl)
    print(f"[ok]   {v}: a configuration save clears the SAE requirement")


def patch_wpa3_h2e(root: Path) -> None:
    """SAE hash-to-element, when the access point offers it.

    `wpa3_sae` hunts and pecks (802.11-2020 12.4.4.2.2), the only password
    element derivation an AP from before 2019 knows. An AP configured for
    H2E only -- hostapd's `sae_pwe=1`, and the one setting a UniFi
    controller does not show -- does not refuse such a commit: hostapd's
    handle_auth_sae() drops it on the floor (`sae_status_success()` false,
    `goto remove_sta`, no reply), and the rabbit times out in AUTH, over and
    over. H2E is also the derivation without the Dragonblood timing leak,
    which is why wpa_supplicant has used it whenever offered since 2.10.

    So: H2E when the AP's RSN Extension element (ID 244) sets bit 5,
    hunting and pecking otherwise, exactly wpa_supplicant's `sae_pwe=2`.
    The arithmetic is in crypto/sae.c (`sae_derive_pt`, `sae_commit_pt`,
    `p256_sswu`) and is checked against IEEE 802.11-2020 Annex J.10 and
    RFC 9380 J.1.1 in tests/; this step is the station-side plumbing:

    - The scan remembers, per BSSID, whether the RSNXE advertised H2E,
      beside the AKM-8 flag the SAE cache already keeps (one byte per
      entry, 8 bytes).
    - PT depends on the SSID and the password only, so it is derived once
      and kept: 64 bytes for the point, 8 for a tag of the (SSID, password)
      it belongs to, 1 for validity -- 73 bytes of .bss. A rabbit that
      reassociates pays one scalar multiplication for its PWE, not the
      whole derivation. The tag is SHA-256(ssid || 0 || password) cut to
      8 bytes: a change of either on the config page invalidates it.
    - The commit goes out with status 126 (SAE_HASH_TO_ELEMENT) and the
      AP's commit is expected back with 126; a 0 there is a mismatch and is
      refused, as wpa_supplicant refuses it (sme.c, "Unexpected use of
      status code 0 in SAE commit when H2E was expected"). The confirm
      keeps status 0.
    - An anti-clogging token travels in a container element (255, len, 93)
      after the commit element with H2E, between group and scalar
      without; both the AP's request and our echo follow that.
    - The association request carries the RSNXE (f4 01 20) when and only
      when H2E was used. hostapd with `sae_pwe=2` refuses a station that
      advertises H2E and then hunts and pecks ("indicates support for SAE
      H2E, but did not use it", ieee802_11.c), and it compares the RSNXE
      of message 2 of the four-way handshake with the association's
      (wpa_auth.c, "RSNXE from (Re)AssocReq did not match"), so message 2
      repeats it. EAPOL_RSN_LENGTH grows from 24 to 28 to hold it.

    The AKM stays 00-0F-AC:8 and the handshake stays on `wpa3_eapol_akm`'s
    schedule: H2E changes how the PWE is found, nothing after it.
    """
    def edit(rel, pairs, marker, label):
        f = root / rel
        src = read_src(f)
        if marker in src:
            print(f"[skip] wpa3_h2e: {rel} already done")
            return
        nl = "\r\n" if "\r\n" in src else "\n"
        for old, new, what in pairs:
            o, n = old.replace("\n", nl), new.replace("\n", nl)
            if src.count(o) != 1:
                sys.exit(f"FAIL {f}: expected exactly one {what}, found {src.count(o)} (upstream changed?)")
            src = src.replace(o, n, 1)
        write_src(f, src)
        print(f"[ok]   {f}: {label}")

    t = read_src(root / "src/net/ieee80211.c")
    if "ieee80211_sae_remember" not in t:
        sys.exit("FAIL wpa3_h2e: the SAE driver block is missing -- wpa3_sae must run first")
    if "ieee80211_sae_forget" not in t:
        sys.exit("FAIL wpa3_h2e: the SAE cache has no forget -- this step edits the cache sae_required left")

    edit("inc/net/ieee80211.h", [
        ("void ieee80211_sae_password(const uint8_t *password);",
         "void ieee80211_sae_password(const uint8_t *password);\n"
         "/* 1 while the current association used SAE hash-to-element: the\n"
         "   RSNXE then goes in the association request and in EAPOL 2/4. */\n"
         "int32_t ieee80211_sae_h2e_in_use(void);",
         "the password hook"),
    ], "ieee80211_sae_h2e_in_use", "the H2E accessor")

    edit("src/net/ieee80211.c", [
        # 1. The cache: a flags byte per BSSID.
        ("static uint8_t ieee80211_sae_bssids[IEEE80211_SAE_CACHE][IEEE80211_ADDR_LEN];\n"
         "static uint8_t ieee80211_sae_count;\n",
         "static uint8_t ieee80211_sae_bssids[IEEE80211_SAE_CACHE][IEEE80211_ADDR_LEN];\n"
         "static uint8_t ieee80211_sae_count;\n"
         "/* Per entry: bit 0, the RSN element offered AKM 8; bit 1, the RSN\n"
         "   Extension element (ID 244) set bit 5, SAE hash-to-element. The two\n"
         "   elements arrive separately, so each sets its own bit. */\n"
         "#define IEEE80211_SAE_F_SAE 0x01\n"
         "#define IEEE80211_SAE_F_H2E 0x02\n"
         "static uint8_t ieee80211_sae_flags[IEEE80211_SAE_CACHE];\n"
         "/* 1 while the association in progress used H2E (see patch_wpa3_h2e). */\n"
         "static uint8_t ieee80211_sae_h2e_used;\n"
         "/* The password token PT (802.11-2020 12.4.4.2.3), derived once per\n"
         "   (SSID, password) and kept: 64 bytes of point, 8 of tag, 1 of\n"
         "   validity. Deriving it costs about a scalar multiplication; reusing\n"
         "   it costs nothing. */\n"
         "static p256_point ieee80211_sae_pt;\n"
         "static uint8_t ieee80211_sae_pt_tag[8];\n"
         "static uint8_t ieee80211_sae_pt_valid;\n",
         "the SAE cache"),
                # The three cache functions wpa3_sae and sae_required left: remember
        # (evicting the oldest when full), forget, known. Each keeps the
        # flags byte beside its BSSID.
        ("static void ieee80211_sae_remember(const uint8_t *bssid)\n{\n  uint8_t i;\n  for(i=0;i<ieee80211_sae_count;i++)\n    if(memcmp(ieee80211_sae_bssids[i], bssid, IEEE80211_ADDR_LEN) == 0) return;\n  if(ieee80211_sae_count == IEEE80211_SAE_CACHE) {\n    memmove(ieee80211_sae_bssids[0], ieee80211_sae_bssids[1],\n            (IEEE80211_SAE_CACHE - 1) * IEEE80211_ADDR_LEN);\n    ieee80211_sae_count--;\n  }\n  memcpy(ieee80211_sae_bssids[ieee80211_sae_count++], bssid,\n         IEEE80211_ADDR_LEN);\n}\n",
         "static void ieee80211_sae_remember_flag(const uint8_t *bssid, uint8_t flag)\n{\n  uint8_t i;\n  for(i=0;i<ieee80211_sae_count;i++)\n    if(memcmp(ieee80211_sae_bssids[i], bssid, IEEE80211_ADDR_LEN) == 0) {\n      ieee80211_sae_flags[i] |= flag;\n      return;\n    }\n  if(ieee80211_sae_count == IEEE80211_SAE_CACHE) {\n    memmove(ieee80211_sae_bssids[0], ieee80211_sae_bssids[1],\n            (IEEE80211_SAE_CACHE - 1) * IEEE80211_ADDR_LEN);\n    memmove(ieee80211_sae_flags, ieee80211_sae_flags + 1, IEEE80211_SAE_CACHE - 1);\n    ieee80211_sae_count--;\n  }\n  memcpy(ieee80211_sae_bssids[ieee80211_sae_count], bssid,\n         IEEE80211_ADDR_LEN);\n  ieee80211_sae_flags[ieee80211_sae_count++] = flag;\n}\n\nstatic void ieee80211_sae_remember(const uint8_t *bssid)\n{\n  ieee80211_sae_remember_flag(bssid, IEEE80211_SAE_F_SAE);\n}\n",
         "the SAE cache's remember"),
        ("      memmove(ieee80211_sae_bssids[i], ieee80211_sae_bssids[i + 1],\n              (ieee80211_sae_count - i - 1) * IEEE80211_ADDR_LEN);\n      ieee80211_sae_count--;\n      return;\n",
         "      memmove(ieee80211_sae_bssids[i], ieee80211_sae_bssids[i + 1],\n              (ieee80211_sae_count - i - 1) * IEEE80211_ADDR_LEN);\n      memmove(ieee80211_sae_flags + i, ieee80211_sae_flags + i + 1,\n              ieee80211_sae_count - i - 1);\n      ieee80211_sae_count--;\n      return;\n",
         "the SAE cache's forget"),
        ("static int32_t ieee80211_sae_known(const uint8_t *bssid)\n{\n  uint8_t i;\n  for(i=0;i<ieee80211_sae_count;i++)\n    if(memcmp(ieee80211_sae_bssids[i], bssid, IEEE80211_ADDR_LEN) == 0) return 1;\n  return 0;\n}\n",
         "static int32_t ieee80211_sae_flag(const uint8_t *bssid, uint8_t flag)\n{\n  uint8_t i;\n  for(i=0;i<ieee80211_sae_count;i++)\n    if(memcmp(ieee80211_sae_bssids[i], bssid, IEEE80211_ADDR_LEN) == 0)\n      return (ieee80211_sae_flags[i] & flag) != 0;\n  return 0;\n}\n\n/* Known for SAE at all: the RSN element offered AKM 8. An entry the RSNXE\n   alone created says no here, and the network is tried with PSK. */\nstatic int32_t ieee80211_sae_known(const uint8_t *bssid)\n{\n  return ieee80211_sae_flag(bssid, IEEE80211_SAE_F_SAE);\n}\n\nint32_t ieee80211_sae_h2e_in_use(void)\n{\n  return ieee80211_sae_h2e_used;\n}\n",
         "the SAE cache's known"),
        # 2. The scan: the RSN Extension element.
        ("\t\t\t\t\t\t\tcase IEEE80211_ELEMID_VENDOR:\n              {\n\t\t\t\t\t\t\t\t/* Check for WPA */",
         "\t\t\t\t\t\t\tcase 244:\n"
         "\t\t\t\t\t\t\t\t/* The RSN Extension element (9.4.2.241): one capability octet,\n"
         "\t\t\t\t\t\t\t\t   bit 5 = SAE hash-to-element. The AP's answer to whether it\n"
         "\t\t\t\t\t\t\t\t   wants H2E; hostapd sends it for sae_pwe=1 and 2. */\n"
         "\t\t\t\t\t\t\t\tif(frame_current[1] >= 1 && (frame_current[2] & SAE_RSNXE_H2E_BIT))\n"
         "\t\t\t\t\t\t\t\t\tieee80211_sae_remember_flag(scan_result.bssid, IEEE80211_SAE_F_H2E);\n"
         "\t\t\t\t\t\t\t\tbreak;\n"
         "\t\t\t\t\t\t\tcase IEEE80211_ELEMID_VENDOR:\n              {\n\t\t\t\t\t\t\t\t/* Check for WPA */",
         "the vendor element case of the scan"),
        # 3. The auth frame builder takes a status code.
        ("static int32_t ieee80211_send_auth_body(uint16_t seq, const uint8_t *body,\n                                        uint32_t blen)\n{\n",
         "static int32_t ieee80211_send_auth_body(uint16_t seq, uint16_t status,\n                                        const uint8_t *body, uint32_t blen)\n{\n",
         "the auth body sender's definition"),
        ("  *(write_ptr++) = (IEEE80211_STATUS_SUCCESS & 0x00ff);\n  *(write_ptr++) = (IEEE80211_STATUS_SUCCESS & 0xff00) >> 8;\n  memcpy(write_ptr, body, blen);\n",
         "  *(write_ptr++) = (status & 0x00ff);\n  *(write_ptr++) = (status & 0xff00) >> 8;\n  memcpy(write_ptr, body, blen);\n",
         "the status bytes of the auth body"),
        # 4. The commit: H2E if the AP offered it.
        ("  DBG_WIFI(\"SAE: deriving the password element\"EOL);\n  if(!sae_commit(&ieee80211_sae, rt2501_mac, ieee80211_assoc_bssid,\n                 (const char *)ieee80211_sae_pw,\n                 ieee80211_sae_rng, NULL, ieee80211_sae_tick)) {\n    DBG_WIFI(\"SAE: could not build a commit\"EOL);\n    sae_clear(&ieee80211_sae);\n    return 0;\n  }\n  sae_commit_bytes(&ieee80211_sae, commit);\n  if(!ieee80211_send_auth_body(IEEE80211_SAE_COMMIT, commit, sizeof commit)) {\n",
         "  ieee80211_sae_h2e_used = 0;\n"
         "  if(ieee80211_sae_flag(ieee80211_assoc_bssid, IEEE80211_SAE_F_H2E)) {\n"
         "    /* Hash-to-element (12.4.4.2.3), because the AP's RSNXE asked for\n"
         "       it. PT is kept across associations; the tag says whether the\n"
         "       one we hold is for this SSID and password. */\n"
         "    uint8_t tag[SHA256_DIGEST], mix[IEEE80211_SSID_MAXLEN + 1 + sizeof ieee80211_sae_pw];\n"
         "    uint32_t n = strlen((char*)ieee80211_assoc_ssid);\n"
         "    memcpy(mix, ieee80211_assoc_ssid, n);\n"
         "    mix[n++] = 0;\n"
         "    memcpy(mix + n, ieee80211_sae_pw, strlen((char*)ieee80211_sae_pw));\n"
         "    n += strlen((char*)ieee80211_sae_pw);\n"
         "    sha256(tag, mix, n);\n"
         "    memset(mix, 0, sizeof mix);\n"
         "    if(!ieee80211_sae_pt_valid || memcmp(tag, ieee80211_sae_pt_tag, sizeof ieee80211_sae_pt_tag) != 0) {\n"
         "      DBG_WIFI(\"SAE: deriving the password token (H2E)\"EOL);\n"
         "      ieee80211_sae_pt_valid = 0;\n"
         "      if(!sae_derive_pt(&ieee80211_sae_pt, ieee80211_assoc_ssid,\n"
         "                        strlen((char*)ieee80211_assoc_ssid),\n"
         "                        (const char *)ieee80211_sae_pw, NULL,\n"
         "                        ieee80211_sae_tick)) {\n"
         "        DBG_WIFI(\"SAE: could not derive PT\"EOL);\n"
         "        return 0;\n"
         "      }\n"
         "      memcpy(ieee80211_sae_pt_tag, tag, sizeof ieee80211_sae_pt_tag);\n"
         "      ieee80211_sae_pt_valid = 1;\n"
         "    }\n"
         "    DBG_WIFI(\"SAE: deriving the password element from PT (H2E)\"EOL);\n"
         "    if(!sae_commit_pt(&ieee80211_sae, rt2501_mac, ieee80211_assoc_bssid,\n"
         "                      &ieee80211_sae_pt,\n"
         "                      ieee80211_sae_rng, NULL, ieee80211_sae_tick)) {\n"
         "      DBG_WIFI(\"SAE: could not build an H2E commit\"EOL);\n"
         "      sae_clear(&ieee80211_sae);\n"
         "      return 0;\n"
         "    }\n"
         "    ieee80211_sae_h2e_used = 1;\n"
         "  } else {\n"
         "    DBG_WIFI(\"SAE: deriving the password element (hunting and pecking)\"EOL);\n"
         "    if(!sae_commit(&ieee80211_sae, rt2501_mac, ieee80211_assoc_bssid,\n"
         "                   (const char *)ieee80211_sae_pw,\n"
         "                   ieee80211_sae_rng, NULL, ieee80211_sae_tick)) {\n"
         "      DBG_WIFI(\"SAE: could not build a commit\"EOL);\n"
         "      sae_clear(&ieee80211_sae);\n"
         "      return 0;\n"
         "    }\n"
         "  }\n"
         "  sae_commit_bytes(&ieee80211_sae, commit);\n"
         "  /* Status 126 says \"this commit is H2E\" (Table 9-50); 0 says hunted and\n"
         "     pecked. hostapd drops a commit whose status does not match its\n"
         "     sae_pwe without a reply. */\n"
         "  if(!ieee80211_send_auth_body(IEEE80211_SAE_COMMIT,\n"
         "                               ieee80211_sae.h2e ? SAE_STATUS_H2E : IEEE80211_STATUS_SUCCESS,\n"
         "                               commit, sizeof commit)) {\n",
         "the commit"),
        # 5. The token: in a container with H2E.
        ("    if(tlen >= 2 && tok[0] == 19 && tok[1] == 0) { tok += 2; tlen -= 2; }\n",
         "    if(tlen >= 2 && tok[0] == 19 && tok[1] == 0) { tok += 2; tlen -= 2; }\n"
         "    /* With H2E the token comes in an Anti-Clogging Token Container\n"
         "       element (255, length, 93, token) and goes back in one. */\n"
         "    if(ieee80211_sae.h2e && tlen >= 3 && tok[0] == SAE_EID_EXTENSION\n"
         "       && tok[2] == SAE_EID_EXT_TOKEN && tok[1] >= 1\n"
         "       && (uint32_t)tok[1] - 1 <= tlen - 3) {\n"
         "      tlen = (uint32_t)tok[1] - 1;\n"
         "      tok += 3;\n"
         "    }\n",
         "the token's group prefix"),
        ("    if(!ieee80211_send_auth_body(IEEE80211_SAE_COMMIT, retry, n)) {\n",
         "    if(!ieee80211_send_auth_body(IEEE80211_SAE_COMMIT,\n"
         "                                 ieee80211_sae.h2e ? SAE_STATUS_H2E : IEEE80211_STATUS_SUCCESS,\n"
         "                                 retry, n)) {\n",
         "the token retry"),
        # 6. The AP's commit must answer in kind.
        ("  if(status != IEEE80211_STATUS_SUCCESS) {\n    sprintf(dbg_buffer, \"SAE: the AP refused us, status %u\"EOL, status);\n",
         "  if(seq == IEEE80211_SAE_COMMIT && ieee80211_sae.h2e\n"
         "     && status == SAE_STATUS_H2E) {\n"
         "    /* 126 on the AP's commit is the H2E handshake going to plan. */\n"
         "  } else if(status != IEEE80211_STATUS_SUCCESS\n"
         "            || (seq == IEEE80211_SAE_COMMIT && ieee80211_sae.h2e)) {\n"
         "    /* ...and a 0 there when we sent 126 is an AP that did not do what\n"
         "       it advertised; wpa_supplicant refuses that too (sme.c). */\n"
         "    sprintf(dbg_buffer, \"SAE: the AP refused us, status %u\"EOL, status);\n",
         "the refusal"),
        ("    if(!ieee80211_send_auth_body(IEEE80211_SAE_CONFIRM, confirm,\n                                 sizeof confirm)) {\n",
         "    if(!ieee80211_send_auth_body(IEEE80211_SAE_CONFIRM, IEEE80211_STATUS_SUCCESS,\n                                 confirm, sizeof confirm)) {\n",
         "the confirm"),
        # 7. The association request: the RSNXE, when H2E was used.
        ("\t\t+2+0x16];//+4];\n\t} *assoc;",
         "\t\t+2+0x16\n\t\t+SAE_RSNXE_LEN];//+4];\n\t} *assoc;",
         "the association frame's size"),
        ("      *(write_ptr++) = (ieee80211_authmode == IEEE80211_AUTH_SAE) ? 0x80 : 0x00;\n      *(write_ptr++) = 0x00;\n",
         "      *(write_ptr++) = (ieee80211_authmode == IEEE80211_AUTH_SAE) ? 0x80 : 0x00;\n      *(write_ptr++) = 0x00;\n"
         "      /* The RSN Extension element, when and only when this association\n"
         "         used H2E: hostapd with sae_pwe=2 refuses a station that claims\n"
         "         H2E here and hunted and pecked, and message 2 of the handshake\n"
         "         must repeat whatever is sent here. */\n"
         "      if(ieee80211_authmode == IEEE80211_AUTH_SAE && ieee80211_sae_h2e_used) {\n"
         "        *(write_ptr++) = SAE_RSNXE_ID;\n"
         "        *(write_ptr++) = 1;\n"
         "        *(write_ptr++) = SAE_RSNXE_H2E_BIT;\n"
         "      }\n",
         "the RSN capabilities of the association request"),
    ], "ieee80211_sae_h2e_used", "SAE picks H2E when the AP offers it")

    # Message 2 repeats the RSNXE. EAPOL_RSN_LENGTH is the key_data member's
    # size: 22 bytes of RSN IE plus 3 of RSNXE no longer fit in 24.
    edit("inc/net/eapol.h", [
        ("#define EAPOL_RSN_LENGTH          24\n",
         "/* 28, not 24: the RSN IE (22) and, after SAE H2E, the RSNXE (3). */\n"
         "#define EAPOL_RSN_LENGTH          28\n",
         "EAPOL_RSN_LENGTH"),
    ], "EAPOL_RSN_LENGTH          28", "room for the RSNXE in key data")

    edit("src/net/eapol.c", [
        ("  uint16_t rsn_size = sizeof(wpa_rsn);\n  if((ieee80211_encryption&0xF0) == IEEE80211_CRYPT_WPA2)\n    rsn_size = sizeof(wpa2_rsn);\n",
         "  uint16_t rsn_size = sizeof(wpa_rsn);\n  if((ieee80211_encryption&0xF0) == IEEE80211_CRYPT_WPA2)\n    rsn_size = sizeof(wpa2_rsn);\n"
         "  /* After SAE H2E the association request carried an RSNXE, and\n"
         "     hostapd deauthenticates if message 2 does not repeat it. */\n"
         "  if(ieee80211_authmode == IEEE80211_AUTH_SAE && ieee80211_sae_h2e_in_use())\n"
         "    rsn_size += SAE_RSNXE_LEN;\n",
         "the key data size"),
        ("    memcpy(fr_out.llc_eapol.key_frame.key_data, wpa2_rsn, rsn_size);\n",
         "    memcpy(fr_out.llc_eapol.key_frame.key_data, wpa2_rsn, sizeof(wpa2_rsn));\n",
         "the RSN IE copy"),
        ("    *(fr_out.llc_eapol.key_frame.key_data+13) = (ieee80211_encryption&0x0F);\n",
         "    *(fr_out.llc_eapol.key_frame.key_data+13) = (ieee80211_encryption&0x0F);\n"
         "    if(ieee80211_authmode == IEEE80211_AUTH_SAE && ieee80211_sae_h2e_in_use()) {\n"
         "      /* The RSNXE, as the association request had it (f4 01 20). */\n"
         "      *(fr_out.llc_eapol.key_frame.key_data+sizeof(wpa2_rsn))   = SAE_RSNXE_ID;\n"
         "      *(fr_out.llc_eapol.key_frame.key_data+sizeof(wpa2_rsn)+1) = 1;\n"
         "      *(fr_out.llc_eapol.key_frame.key_data+sizeof(wpa2_rsn)+2) = SAE_RSNXE_H2E_BIT;\n"
         "    }\n",
         "the message-2 pairwise byte"),
        # The frame the stock code sends is LLC + 4 + body_length bytes, and
        # its body_length counts the key_data member once more than the key
        # data it holds; the trailer below is what makes the two agree. It
        # has to be as long as key_data, or a 25-byte key data reads past
        # the struct -- and the arithmetic of the send has to stay the
        # stock one: with EAPOL_RSN_LENGTH on both sides it did not, and
        # every WPA2 scenario stopped at EAPOL on 2026-11-08.
        ("    uint8_t rsn[sizeof(wpa_rsn)];\n  } fr_out;\n  uint8_t ptk_buffer[80];\n",
         "    uint8_t rsn[EAPOL_RSN_LENGTH];\n  } fr_out;\n  uint8_t ptk_buffer[80];\n",
         "the message-2 trailer"),
        ("  rt2501_send((uint8_t *)&fr_out, sizeof(fr_out)-(sizeof(wpa_rsn)-rsn_size), ieee80211_assoc_mac, 1, 1);\n",
         "  rt2501_send((uint8_t *)&fr_out, sizeof(fr_out)-(EAPOL_RSN_LENGTH-rsn_size), ieee80211_assoc_mac, 1, 1);\n",
         "the message-2 send"),
    ], "SAE_RSNXE_LEN", "message 2 repeats the RSNXE")

    # eapol.c reaches SAE_RSNXE_* through net/sae.h.
    q = root / "src/net/eapol.c"
    t = read_src(q)
    if '#include "net/sae.h"' not in t:
        nl = "\r\n" if "\r\n" in t else "\n"
        a = '#include "net/sha256.h"' + nl
        if a not in t:
            sys.exit(f"FAIL {q}: the sha256 include is missing -- wpa3_eapol_akm must run first")
        write_src(q, t.replace(a, a + '#include "net/sae.h"' + nl, 1))
        print(f"[ok]   {q}: includes net/sae.h")

    print("[ok]   wpa3_h2e: hash-to-element on offer, hunting and pecking otherwise")


def patch_boot_tls(root: Path) -> None:
    """bc.jsp over TLS 1.3 with the adoption key: boot-mods/boot_tls.mtl.

    For a rabbit whose owner confirmed its key (flag 'C' at 256), the
    bootloader asks for its bytecode on the warren's TLS port, 5223, where
    the Terrier answers HTTP as well as XMPP (one listener, the first bytes
    after the handshake decide). The TLS context is free at boot: the
    runtime's XMPP starts after the bytecode runs, with its own handshake.

    One attempt per boot, then plain bc.jsp: a warren without the HTTP side
    of the TLS port, or a handshake that fails, costs one BOOT_HTTPTIMEOUT
    and the rabbit boots as before. The bytecode carries its HMAC either
    way, so a forced fallback loses privacy, not integrity -- and the
    warren learns from where the boot came: only a TLS bc.jsp is proof of
    the key.

    Requires adoption_boot (the flag, kx_ready, httpboot's call site) and
    tls_psk (the opcodes).
    """
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t = read_src(q)
    if "boottls_request" in t:
        print(f"[skip] {q}: bc.jsp already goes over TLS when it can")
        return
    if "kx_ready" not in t:
        sys.exit(f"FAIL {q}: adoption_boot must run first")
    if "tlsStart" not in (root / "mtl/mtl_linux/src/vcomp/stdlib_core.cpp").read_text():
        sys.exit(f"FAIL {q}: tls_psk must run first")
    nl = "\r\n" if "\r\n" in t else "\n"
    body = (Path(__file__).resolve().parent / "boot-mods" / "boot_tls.mtl").read_text()
    edits = [
        ("fun boot_url url =" + nl, body.replace("\n", nl) + nl + "fun boot_url url =" + nl, "fun boot_url"),
        ('\t\tif wifiReady && kx_ready then set httpboot=httprequest "GET" boot_url url nil #_bootcbhttp HTTP_NORMAL;' + nl,
         '\t\tif wifiReady && kx_ready then set httpboot=if boottls_wanted then boottls_request boot_url url #_bootcbhttp'
         ' else httprequest "GET" boot_url url nil #_bootcbhttp HTTP_NORMAL;' + nl,
         "the bytecode request"),
    ]
    for a, b, what in edits:
        if t.count(a) != 1:
            sys.exit(f"FAIL {q}: expected one {what}, found {t.count(a)}")
        t = t.replace(a, b, 1)
    write_src(q, t)
    print(f"[ok]   {q}: bc.jsp over TLS 1.3 for a confirmed key, one attempt per boot")


def patch_firmware_version(root: Path) -> None:
    """Say a version other than 0.0.0.13 in the boot URL and the setup page.

    The firmware version is four bytes in the bootloader --
    `const FIRMWARE="\0\0\0\13";;` -- rendered by `webip` as "0.0.0.13" in
    `/vl/bc.jsp?v=` and in two places on the config page. Measured on
    2026-11-08, nothing compares it: not the bootloader, not the Violet
    runtime bytecode, not the warren (which stores it as `fw_version` and
    keeps it out of the boot marks -- terrier tests/test_firmware_1_0_0_0.py),
    not the Home Assistant add-on (which ignores the query string). So the
    bump is safe, and it is cosmetic: the build mark `&z=`, not `v=`, is what
    tells one image from another.

    NAB_FIRMWARE_VERSION, default 1.0.0.0, four integers 0..255. In no mode
    yet: the plan is to ship it with the first release after WPA3. Never in
    `minimal`, whose bootloader must stay byte-identical to vanilla wpa2 --
    the guard ties this step to `bootloader`, which minimal does not carry.
    """
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t = read_src(q)
    want = os.environ.get("NAB_FIRMWARE_VERSION") or "1.0.0.0"
    parts = want.split(".")
    if len(parts) != 4 or not all(x.isdigit() and 0 <= int(x) <= 255 for x in parts):
        sys.exit(f"FAIL {q}: NAB_FIRMWARE_VERSION must be four integers "
                 f"0..255, got {want!r}")
    # Metal string escapes are decimal: "\13" is the byte 13.
    lit = '"' + "".join("\\" + str(int(x)) for x in parts) + '"'
    old = 'const FIRMWARE="\\0\\0\\0\\13";;'
    new = f"const FIRMWARE={lit};;"
    if new in t:
        print(f"[skip] {q}: FIRMWARE already {want}")
        return
    if t.count(old) != 1:
        sys.exit(f"FAIL {q}: the FIRMWARE constant is not the stock 0.0.0.13 "
                 f"line this step expects (found {t.count(old)})")
    write_src(q, t.replace(old, new, 1))
    print(f"[ok]   {q}: FIRMWARE {want} (boot URL v= and the setup page)")


def patch_factory_server(root: Path) -> None:
    """After /reset, point the rabbit at the Terrier, not at Violet's server.

    `confReset` (the config page's reset, any URI starting with `r`) copies
    `conf0` over the configuration sector, and Violet's conf0 starts with
    the server `r.nabaztag.com/vl`. On 2026-11-14 a second-hand rabbit wiped
    that way breathed for the first time in its new home -- on a stranger's
    OpenJabNab that answers at r.nabaztag.com today ("ping nabaztag.local"),
    key exchange included, while the Terrier never saw it. Every rabbit an
    owner resets goes there unless they retype the server.

    The field is 41 bytes, NUL-terminated, padded with '-': the new name
    keeps the same 41, so every offset after it (CONF_NETDHCP=41, ...) is
    untouched. NAB_FACTORY_SERVER overrides it for another warren.

    And the config page offers the same server in place of Violet's, or of
    an empty field (Clara, the same day: "pré-remplir l'adresse du
    terrier"): a rabbit flashed without a reset still holds its last
    server, and the page showed r.nabaztag.com for the owner to save again.
    Any other address -- a home warren -- is shown as it is.
    """
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t, nl = read_flat(q)
    want = os.environ.get("NAB_FACTORY_SERVER") or "terrier.cyberloutre.fr/vl"
    if not re.fullmatch(r"[A-Za-z0-9.:/-]{1,40}", want):
        sys.exit(f"FAIL {q}: NAB_FACTORY_SERVER must be 1..40 plain characters, got {want!r}")
    old = '"r.nabaztag.com/vl\\0-----------------------\\\n'
    new = '"' + want + "\\0" + "-" * (40 - len(want)) + '\\\n'
    if new in t:
        print(f"[skip] {q}: factory server already {want}")
        return
    if t.count(old) != 1:
        sys.exit(f"FAIL {q}: conf0 does not start with Violet's server line as "
                 f"this step expects (found {t.count(old)})")
    t = t.replace(old, new, 1)
    page_old = '\t\t\t["<SERVER>" filterweb server]::\n'
    page_new = ('\t\t\t["<SERVER>" filterweb (if (!strcmp server "r.nabaztag.com/vl") || '
                f'(strlen server)==0 then "{want}" else server)]::\n')
    if t.count(page_old) != 1:
        sys.exit(f"FAIL {q}: the config page's <SERVER> line is not the one "
                 f"this step expects (found {t.count(page_old)})")
    t = t.replace(page_old, page_new, 1)
    write_flat(q, t, nl)
    print(f"[ok]   {q}: factory server {want} (what /reset leaves, what the page offers)")


def patch_boot_marker(root: Path) -> None:
    """Make the running build say so in its own boot URL, with no opcode.

    Four characters of literal string appended to `boot_url`, and nothing
    else: no new opcode, no call, no arithmetic. It exists to answer one
    question a flash cannot otherwise answer -- is the bytecode region of
    the flash actually the bytecode we handed it? -- because every other
    marker we have depends on code that could itself be the thing failing.

    Pair it with `lean-marker`, which is `lean` and this step. The size
    matters: on 2026-11-07 every image that had ever provably flashed was
    about 107 KB, and no 117 KB WPA3 image had ever been shown to run, so
    the marker needs to exist at the small size too or it answers nothing.
    """
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t = read_src(q)
    # The stamp build.sh passes in: the minute the image was built, so no two
    # builds say the same thing. Without it the marker was a literal 1, which
    # proved an image was new but could not say WHICH new image -- and the
    # whole point is telling one build from another.
    mark = os.environ.get("NAB_BUILD_MARK") or "1"
    if not re.fullmatch(r"[0-9A-Za-z._-]{1,16}", mark):
        sys.exit(f"FAIL {q}: NAB_BUILD_MARK must be 1-16 URL-safe characters, "
                 f"got {mark!r}")
    if '"&z=' in t:
        print(f"[skip] {q}: boot_url already carries the marker")
        return
    a = '::"&h="::(itoa HARDWARE)'
    if t.count(a) != 1:
        sys.exit(f"FAIL {q}: expected one boot_url tail, found {t.count(a)}")
    # `&s=1` rides along: it says this image's signature verification works,
    # which the warren needs because a rabbit whose verifier is broken is
    # served a bytecode whose OTA path does not ask it to verify at all --
    # leaning on the server's own check instead. That is a real reduction in
    # defence, so it ends by itself: a rabbit that reports s=1 gets the normal,
    # gated bytecode again, per rabbit, with nobody having to remember.
    #
    # A literal and not a timestamp, deliberately: build marks are UTC, the
    # warren's logs are local, and a comparison between the two is the kind of
    # thing that eats an evening. It joins fw_boot_marks for free, that being
    # "everything beyond v, m, l, p and h".
    #
    # It lives here and not in sign_open_alias because `minimal` carries the
    # alias fix but must keep a bootloader byte-identical to vanilla wpa2 --
    # that is the whole promise of the rescue build. A minimal rabbit is
    # therefore served the recovery bytecode indefinitely, which is safe: it
    # has no signature gate of its own either way.
    write_src(q, t.replace(a, a + f'::"&z={mark}"::"&s=1"', 1))
    print(f"[ok]   {q}: boot_url carries &z={mark} and &s=1")



def patch_flash_verify(root: Path) -> None:
    """Make a flash that wrote nothing say so, instead of rebooting proudly.

    This is the most expensive bug this project has had. `write_uc_flash_sec`
    issues an erase, spins on FLACON until the controller reports done, then
    programs four bytes at a time and spins again -- and checks nothing. The
    author's own `write_ok` variable is commented out two lines above. So a
    sector that refuses to erase, a part that never starts the command, a
    worn cell that will not take a bit: all of it is invisible. `flash_uc`
    then trips the watchdog on purpose, the rabbit reboots, and the belly runs
    the whole triumphant sequence -- white LEDs, reset, orange, green -- while
    the firmware on the chip is the one that was already there.

    A Nabaztag ran a firmware from June 2026 for five months that way, through
    four flashes that all "succeeded", including two the owner watched to the
    end. It took a JTAG dump to find out, and the dump also found a bit in
    sector 0 that reads 0x41 on one pass and 0x43 on the next.

    So, three things, all in the same spirit: never claim more than was
    verified.

    - Both FLACON spins get a bound. Unbounded, a part that never answers
      hangs a rabbit with no diagnosis at all -- which is worse than a wrong
      answer, because nothing is on screen and nothing is in the log.
    - The erase is verified: an erased sector reads 0xFF everywhere, and if it
      does not, there is no point programming over it.
    - Every programmed word is read back. This is the one that matters: it is
      the only thing that can tell a flash that worked from a flash that did
      not, and it costs one read per word on a part that already spends
      milliseconds per command.

    The failure then travels all the way up -- `write_uc_flash_sec` returns a
    status, `flash_uc` refuses to trip the watchdog and returns the address
    that failed, `sysFlash` passes it on, `OPflashFirmware` pushes nil, and
    `httpflash` turns the belly red and serves the error page. The rabbit
    keeps the firmware it had, which it was going to keep anyway, and this
    time it admits it.
    """
    q = root / "src/utils/mem.c"
    t, nl = read_flat(q)
    if "flash_verify_word" in t:
        print(f"[skip] {q}: the flash writer already verifies itself")
        return

    a = "void write_uc_flash_sec(uint32_t address, uint8_t *data,"
    if t.count(a) != 1:
        sys.exit(f"FAIL {q}: expected one write_uc_flash_sec definition")
    t = t.replace(a, "int32_t write_uc_flash_sec(uint32_t address, uint8_t *data,", 1)

    # A bound on the completion spin. 2^20 reads of a peripheral register is
    # far longer than any command this part takes and still finite.
    a = """  //Wait for the completion of the command
  while((get_value(FLACON) & (0x04|0x8|0x2))!= 0x2);
//-----------------------------"""
    if a not in t:
        sys.exit(f"FAIL {q}: the erase completion spin is not where it was")
    t = t.replace(a, """  //Wait for the completion of the command, but not forever
  {
    uint32_t spin=0;
    while((get_value(FLACON) & (0x04|0x8|0x2))!= 0x2)
      if(++spin > 0x100000) return 1;   /* erase never completed */
  }

  //----------------------------------------------------------------
  // An erased sector reads 0xFF everywhere. If it does not, there is
  // nothing to be gained by programming on top of it.
  //----------------------------------------------------------------
  for(cmpt_int=0; cmpt_int<4096; cmpt_int++)
    if(read_char[cmpt_int] != 0xFF) return 2;   /* sector did not erase */
//-----------------------------""", 1)

    a = """\t  //Wait for the completion of the command
\t  while((get_value(FLACON) & (0x04|0x8|0x2))!= 0x2);
  }"""
    if a not in t:
        sys.exit(f"FAIL {q}: the programming completion spin is not where it was")
    t = t.replace(a, """\t  //Wait for the completion of the command, but not forever
    {
      uint32_t spin=0;
      while((get_value(FLACON) & (0x04|0x8|0x2))!= 0x2)
        if(++spin > 0x100000) return 3;   /* word never programmed */
    }

    //--------------------------------------------------------------
    // flash_verify_word: read the four bytes back. This is the whole
    // point of the patch -- without it a write that did nothing is
    // indistinguishable from one that worked.
    //--------------------------------------------------------------
    if(read_char[cmpt_int]   != temp[cmpt_int]   ||
       read_char[cmpt_int+1] != temp[cmpt_int+1] ||
       read_char[cmpt_int+2] != temp[cmpt_int+2] ||
       read_char[cmpt_int+3] != temp[cmpt_int+3]) return 4;
  }""", 1)

    a = """  //Start watchdog clock
  clr_bit(TBGCON,0x80);
}"""
    if t.count(a) != 1:
        sys.exit(f"FAIL {q}: expected one watchdog restart at the end of the sector writer")
    t = t.replace(a, """  //Start watchdog clock
  clr_bit(TBGCON,0x80);
  return 0;
}""", 1)

    a = """__attribute__ ((section(".ramfunc"))) void flash_uc(uint8_t *data, int32_t nb_byte, uint8_t *temp)
{
  int32_t address=0;
  while(nb_byte>0)
  {
    write_uc_flash_sec(address,data,(nb_byte<4096)?nb_byte:4096,temp);
    nb_byte-=4096;
    data+=4096;
    address+=4096;
  }"""
    if a not in t:
        sys.exit(f"FAIL {q}: flash_uc is not the shape this patch expects")
    t = t.replace(a, """__attribute__ ((section(".ramfunc"))) int32_t flash_uc(uint8_t *data, int32_t nb_byte, uint8_t *temp)
{
  int32_t address=0;
  while(nb_byte>0)
  {
    if(write_uc_flash_sec(address,data,(nb_byte<4096)?nb_byte:4096,temp))
      return address ? address : -1;  /* the address that failed; 0 means 0 */
    nb_byte-=4096;
    data+=4096;
    address+=4096;
  }""", 1)

    # Only trip the watchdog once every sector has been verified. The caller
    # cannot be reached otherwise: the reset happens inside the while(1).
    a = """  put_hvalue(WDTCNT,0x5A5A);  //release write protection
  put_hvalue(WDTCNT,0xFF00);  //rewrite counter register to overflow => overflow in 256us
  while(1);

}"""
    if a not in t:
        sys.exit(f"FAIL {q}: the deliberate watchdog reset is not where it was")
    t = t.replace(a, """  put_hvalue(WDTCNT,0x5A5A);  //release write protection
  put_hvalue(WDTCNT,0xFF00);  //rewrite counter register to overflow => overflow in 256us
  while(1);

  return 0;  /* unreachable: the line above is the reset */
}""", 1)
    write_flat(q, t, nl)
    print(f"[ok]   {q}: the flash writer verifies its erase and every word")

    q = root / "inc/utils/mem.h"
    t, nl = read_flat(q)
    a = '__attribute__ ((section(".ramfunc"))) void flash_uc('
    if a not in t:
        sys.exit(f"FAIL {q}: no flash_uc declaration to retype")
    t = t.replace(a, '__attribute__ ((section(".ramfunc"))) int32_t flash_uc(', 1)
    write_flat(q, t, nl)
    print(f"[ok]   {q}: flash_uc returns a status")

    q = root / "inc/vm/vlog.h"
    t, nl = read_flat(q)
    a = "void sysFlash(uint8_t *firmware,int32_t len);"
    if a not in t:
        sys.exit(f"FAIL {q}: no sysFlash declaration to retype")
    write_flat(q, t.replace(a, "int32_t sysFlash(uint8_t *firmware,int32_t len);", 1), nl)
    print(f"[ok]   {q}: sysFlash returns a status")

    q = root / "src/vm/vlog.c"
    t, nl = read_flat(q)
    a = """void sysFlash(uint8_t* firmware,int32_t len)
{
  __disable_interrupt();
  flash_uc((uint8_t*)firmware,len,buffer_temp);
}"""
    if a not in t:
        sys.exit(f"FAIL {q}: sysFlash is not the shape this patch expects")
    write_flat(q, t.replace(a, """/* The last flash failure, kept where a debugger can read it.
 *
 * The bytecode is the only thing that sees sysFlash's return value, and on
 * 2026-11-07 the OTA path dropped it on the floor: an over-the-air flash
 * wrote nothing, lit the belly green, and the firmware's own diagnosis died
 * with the stack frame. Rebuilding the runtime bytecode needs the Violet
 * sources and the patched Metal compiler, which are not always at hand --
 * JTAG always is. Two words of .bss, and `nm` tells you where they are.
 *
 * nab_flash_error: 0 = nothing has failed since boot, otherwise the address
 * flash_uc stopped at (-1 for offset 0, which is a legitimate address).
 * nab_flash_calls: how many times sysFlash has been entered, so a zero error
 * can be told from a flash that was never attempted. */
int32_t nab_flash_error;
int32_t nab_flash_calls;

int32_t sysFlash(uint8_t* firmware,int32_t len)
{
  /* On success this does not return: flash_uc ends by tripping the watchdog
   * on purpose. It returns only when nothing was written, and then the
   * interrupts have to come back or the rabbit is worse off than before the
   * flash it refused. */
  int32_t bad;
  nab_flash_calls++;
  __disable_interrupt();
  bad=flash_uc((uint8_t*)firmware,len,buffer_temp);
  __enable_interrupt();
  nab_flash_error=bad;
  return bad;
}""", 1), nl)
    print(f"[ok]   {q}: sysFlash re-enables interrupts and reports the failure")

    q = root / "src/vm/vinterp.c"
    t, nl = read_flat(q)
    a = """            DBG_VM("************FLASH AND REBOOT NOW");
            sysFlash(VSTARTBIN(firmware),VSIZEBIN(firmware));"""
    if a not in t:
        sys.exit(f"FAIL {q}: OPflashFirmware is not the shape this patch expects")
    write_flat(q, t.replace(a, """            DBG_VM("************FLASH AND REBOOT NOW");
            /* Returns only on failure -- a successful flash resets the chip
             * from inside sysFlash. The opcode used to leave the firmware on
             * the stack either way, which is how a flash that wrote nothing
             * looked like one that worked.
             *
             * On failure it now answers the *address* flash_uc stopped at
             * (-1 for offset 0, since 0 is a legitimate address and nil is
             * reserved), not a bare nil. An OTA that writes nothing is
             * otherwise indistinguishable from one that writes half the
             * image, and on 2026-11-07 that distinction was the whole
             * question: the write failed, the firmware knew, and nobody
             * could ask it what it knew. `if x!=nil` still reads as before
             * for every caller that only wants a yes or no. */
            {
              int32_t bad=sysFlash(VSTARTBIN(firmware),VSIZEBIN(firmware));
              if (bad!=0)
                VSTACKSET(0,INTTOVAL(bad));
            }""", 1), nl)
    print(f"[ok]   {q}: flashFirmware answers nil when nothing was written")

    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t, nl = read_flat(q)
    a = """		Secholn "######### sig OK, flashing";
		setleds 0xffffff;
		flashFirmware firm 0x13fb6754 0x0407FE58;
		nil
	)"""
    if a not in t:
        sys.exit(f"FAIL {q}: httpflash is not the shape this patch expects "
                 f"— run the bootloader step first")
    write_flat(q, t.replace(a, """		Secholn "######### sig OK, flashing";
		setleds 0xffffff;
		if (flashFirmware firm 0x13fb6754 0x0407FE58)==nil then
		(
			// A successful flash never gets here: the chip resets itself
			// from inside the opcode. Getting here means the erase or a
			// word did not take and the old firmware is still in place.
			Secholn "######### flash FAILED, firmware unchanged";
			setleds 0xff0000;
			page_error
		)
		else nil
	)""", 1), nl)
    print(f"[ok]   {q}: a flash that wrote nothing serves the error page")


def patch_flash_precheck(root: Path) -> None:
    """Refuse, before anything is erased, an image that cannot be a firmware.

    docs/OTA-ANTIBRICK.md, section 3. The signature says who built the image
    and that it arrived whole; it does not say the file is a Nab.bin. Nothing
    between verifySig and the first erase looks at the image at all, and the
    writer takes any length: 126,977 bytes and it erases the configuration
    sector, 200,000 and it issues erase commands past the end of the flash.
    The first erase is where a refusal stops being free, so everything that
    can be decided from the bytes is decided here:

    - the length: at least four sectors, at most the 31 the linker script
      gives the image (0x1F000, sector 31 is the configuration);
    - the reset vector: `ldr pc,[pc,#n]`, which is what every image this
      toolchain has produced starts with, loading an ARM address that lies
      inside the image itself.

    A refusal answers 0xE1 to the bytecode (the OTA path reports it as
    /vl/otafail?e=000000E1) and leaves the flash untouched.

    Deliberately NOT here: any comparison with the version in place. A
    tester has to be able to flash an older signed image back; the six
    images in releases/ start the same way and pass (two of them uploaded
    through scenarios/vm/config-upload.txt, the oldest and the newest).

    The bootloader's upload page is fixed in the same step, because its
    failure branch could not be reached: it showed the error page when
    flashFirmware answered nil, and the opcode never answers nil -- on a
    refusal the image is still on the stack, on a failure the address is.
    A successful flash does not return at all, so whatever comes back is a
    failure, and the page now says so.
    """
    q = root / "src/vm/vinterp.c"
    t = q.read_text()
    if "nab_fw_plausible" in t:
        print(f"[skip] {q}: flashFirmware already looks at the image first")
    else:
        a = "  return d == 0;\n}\n"
        if t.count(a) != 1:
            sys.exit(f"FAIL {q}: no nab_fw_verified to put the precheck after "
                     "(run the vinterp step first)")
        t = t.replace(a, a + (
            "/* flash_precheck: 1 iff `img` can be this board's firmware. Runs before\n"
            "   the first erase, which is the last moment a refusal costs nothing.\n"
            "   No version check: an older signed image must stay flashable. */\n"
            "static int nab_fw_plausible(const unsigned char *img, int len)\n"
            "{\n"
            "  uint32_t v, t;\n"
            "  if (len < 0x4000 || len > 0x1F000) return 0;\n"
            "  v = img[0] | (img[1] << 8) | (img[2] << 16) | ((uint32_t)img[3] << 24);\n"
            "  if ((v & 0xFFFFF000) != 0xE59FF000) return 0;  /* ldr pc,[pc,#n] */\n"
            "  v = 8 + (v & 0xFFF);\n"
            "  if (v > 0x100) return 0;\n"
            "  t = img[v] | (img[v+1] << 8) | (img[v+2] << 16) | ((uint32_t)img[v+3] << 24);\n"
            "  return t >= 0x08000020 && t < 0x08000000 + (uint32_t)len && !(t & 3);\n"
            "}\n"), 1)
        a = ("              &&nab_fw_verified((const unsigned char*)VSTARTBIN(firmware),VSIZEBIN(firmware)))\n")
        if t.count(a) != 1:
            sys.exit(f"FAIL {q}: OPflashFirmware's guard is not the shape this patch expects")
        t = t.replace(a, (
            "              &&nab_fw_verified((const unsigned char*)VSTARTBIN(firmware),VSIZEBIN(firmware))\n"
            "              &&(nab_fw_plausible((const unsigned char*)VSTARTBIN(firmware),VSIZEBIN(firmware))\n"
            "                 ||((VSTACKSET(0,INTTOVAL(0xE1))),0)))\n"), 1)
        q.write_text(t)
        print(f"[ok]   {q}: flashFirmware checks length and reset vector before the first erase")

    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t, nl = read_flat(q)
    if "######### flash REFUSED" in t:
        print(f"[skip] {q}: httpflash already treats any return as a failure")
        return
    a = """		if (flashFirmware firm 0x13fb6754 0x0407FE58)==nil then
		(
			// A successful flash never gets here: the chip resets itself
			// from inside the opcode. Getting here means the erase or a
			// word did not take and the old firmware is still in place.
			Secholn "######### flash FAILED, firmware unchanged";
			setleds 0xff0000;
			page_error
		)
		else nil
	)"""
    if a not in t:
        # An image without flash_verify has no failure branch to repair, and
        # the emulator's tree has no bootloader step at all.
        print(f"[skip] {q}: no flash_verify failure branch in httpflash to repair")
        return
    write_flat(q, t.replace(a, """		flashFirmware firm 0x13fb6754 0x0407FE58;
		// A successful flash never gets here: the chip resets itself from
		// inside the opcode. Whatever it answered, getting here means the
		// image was refused before the first erase.
		Secholn "######### flash REFUSED, firmware unchanged";
		setleds 0xff0000;
		page_error
	)""", 1), nl)
    print(f"[ok]   {q}: httpflash shows the error page whenever flashFirmware returns")


def patch_flash_no_return(root: Path) -> None:
    """Once the first sector is erased, finish the flash or die trying.

    docs/OTA-ANTIBRICK.md, section 3. flash_verify made the writer notice a
    sector that did not erase or a word that did not take -- and then return
    the address to its caller. The caller is sysFlash, in the flash, and by
    then the flash is sectors 0..k-1 of the new image followed by the rest of
    the old one: the return lands in code that is no longer there, the image
    that would have fixed it is in a heap nobody will read again, and the
    next power-up finds half a firmware. A write error that was survivable
    is turned into a brick by reporting it.

    The image is in RAM, the writer is in RAM, and the power is on. So:

    - a sector that fails is erased and written again, four times, with the
      watchdog fed before each attempt;
    - if a sector still will not take, the rest are written anyway, and the
      whole image is gone over again, three passes -- a pass costs nothing
      for the sectors that took, because:
    - a sector that already holds the bytes it is asked to hold is skipped,
      before any erase. Writing the image that is already there erases
      nothing at all, and a pass after a failure only touches what failed;
    - flash_uc does not return. When it has done what it can it resets, with
      the address of the last sector that refused in nab_flash_error, which
      crash_report puts in the next boot URL if there is a next boot.

    The watchdog: write_uc_flash_sec "stops" it with set_bit(TBGCON,0x80),
    but TBGCON takes a write only after 0x5A has been written to it (see
    wdt_start and reset_uc, which both do), so that halt is probably ignored
    and the 2.09 s window probably keeps running across all thirty-odd
    sectors with interrupts off. Nobody has measured which. Feeding it
    between sectors is right under both readings: it is the same two writes
    to WDTCON the interpreter makes before every opcode, done at the point
    where the sector writer has put TBGCON back as it found it.

    What this does not change is a power cut: the image in RAM goes with the
    power. emulator/flashsim measures both, before and after.
    """
    q = root / "src/utils/mem.c"
    t, nl = read_flat(q)
    if "flash_no_return" in t:
        print(f"[skip] {q}: flash_uc already finishes what it starts")
        return
    a = """  int32_t address=0;
  while(nb_byte>0)
  {
    if(write_uc_flash_sec(address,data,(nb_byte<4096)?nb_byte:4096,temp))
      return address ? address : -1;  /* the address that failed; 0 means 0 */
    nb_byte-=4096;
    data+=4096;
    address+=4096;
  }
"""
    if a not in t:
        sys.exit(f"FAIL {q}: flash_uc is not the shape flash_verify leaves "
                 "(run flash_verify first)")
    t = t.replace(a, """  /* flash_no_return: see apply-mods.py. Nothing below returns to the
   * caller, because after the first erase the caller is not there. */
  int32_t address, left, bad=0;
  uint32_t n, i;
  uint8_t *src, pass, tries;
  for(pass=0; pass<3; pass++)
  {
    bad=0;
    for(address=0, src=data, left=nb_byte; left>0;
        address+=4096, src+=4096, left-=4096)
    {
      n=(left<4096)?(uint32_t)left:4096;
      /* A sector that already holds these bytes is left alone. */
      for(i=0; i<n && ((uint8_t*)address)[i]==src[i]; i++);
      if(i==n) continue;
      for(tries=0; tries<4; tries++)
      {
        /* TBGCON as the sector writer leaves it on success, then the
         * interpreter's own CLR_WDT. */
        clr_bit(TBGCON,0x80);
        put_value(WDTCON,0xC3);
        put_value(WDTCON,0x3C);
        if(!write_uc_flash_sec(address,src,n,temp)) break;
      }
      if(tries==4) bad=address ? address : -1;
    }
    if(!bad) break;
  }
  { extern int32_t nab_flash_error; nab_flash_error=bad; }
""", 1)
    write_flat(q, t, nl)
    print(f"[ok]   {q}: flash_uc retries, skips what is already written, and never returns")


def patch_rx_recovery(root: Path) -> None:
    """Stop a burst of receive errors from killing the radio until the mains.

    `usb_robustness` bounded the retries after a bulk-in error, which was the
    right half of the fix. The other half it left in place: once the budget
    is spent it still does `rt2501_connected = 0`, and that is the same
    one-way door its own comment describes -- only the USB connect hook sets
    the flag back, and a stick soldered to the board never disconnects. So
    the patch did not close the door, it moved it ten errors further back.

    Measured on Nabazprout over JTAG, 2026-11-07, after a clean power cycle:

        rt2501_rx_errors = 11      (the limit is 10)
        rt2501_connected = 0
        ieee80211_state  = IDLE,  scan_cache_len = 0,  rssi_average = 0
        rt2501_channel   = 14

    rt2501_state() answers RT2501_S_BROKEN for ever from there. The
    bootloader's `initW` only acts on RT2501_S_IDLE, so it never scans again,
    `wifiscans` stays nil, `wifiAuth` returns 0 at its second line, and the
    belly holds the orange that `setleds 0xff8000` left on it. That is the
    "unplug it and plug it back in" bug, and it is the only way out.

    Eleven errors across a fourteen-channel sweep is about one per channel,
    which fits a sweep reprogramming the RF while transfers are in flight --
    a budget of ten is then smaller than one full scan, so the radio dies
    during its first long sweep and survives whenever the network turns up on
    an early channel. That is inference, not measurement: the counter proves
    the door, not what knocked on it. Both halves are widened anyway, since
    both are wrong in the same direction.

    So: a bulk-in error never clears rt2501_connected. The receive path is
    parked instead, and rt2501_timer() rebuilds it -- at most one attempt per
    tick, which bounds the work whatever the radio is doing, and refills the
    budget with it so a burst cannot accumulate into a death sentence.
    """
    f = root / "src/usb/rt2501usb.c"
    t, nl = read_flat(f)

    if "rt2501_rx_down" in t:
        print(f"[skip] {f}: receive path already recoverable")
        return

    a = ("/* Consecutive receive errors, reset by any good frame. Only the USB connect\n"
         "   hook sets rt2501_connected back to 1, so giving up has to be a decision\n"
         "   rather than a reflex. */\n"
         "static uint8_t rt2501_rx_errors;\n"
         "#define RT2501_RX_ERROR_LIMIT 10")
    if a not in t:
        sys.exit(f"FAIL {f}: the rx error counter is not where usb_robustness "
                 f"left it -- run that step first")
    t = t.replace(a, (
        "/* Consecutive receive errors, reset by any good frame. */\n"
        "static uint8_t rt2501_rx_errors;\n"
        "/* Set when the budget above is spent. rt2501_timer() rebuilds the\n"
        "   receive path and clears this; nothing else does. */\n"
        "static uint8_t rt2501_rx_down;\n"
        "/* One full channel sweep costs about one error per channel, so this has\n"
        "   to be comfortably more than fourteen or the first long scan spends it. */\n"
        "#define RT2501_RX_ERROR_LIMIT 64"), 1)

    stale = "       until the next power cycle. Re-arm a bounded number of times first;\n" \
            "       if they all fail, give up exactly as before. */"
    if stale not in t:
        sys.exit(f"FAIL {f}: usb_robustness's comment about giving up is not where "
                 f"it was, and leaving a stale one would be worse than not patching")
    t = t.replace(stale, (
        "       until the next power cycle. Re-arm a bounded number of times first;\n"
        "       if they all fail, park the receive path -- see below. Nothing here\n"
        "       gives up for good any more. */"), 1)

    b = ('    DBG_WIFI("too many RX errors, radio down"EOL);\n'
         "    rt2501_connected = 0;\n"
         "    return;")
    if b not in t:
        sys.exit(f"FAIL {f}: the give-up branch is not where usb_robustness left it")
    t = t.replace(b, (
        "    /* Not rt2501_connected = 0: that is the one-way door, and a bulk-in\n"
        "       error is not evidence the dongle left. Park the receive path and\n"
        "       let the timer rebuild it. */\n"
        '    DBG_WIFI("too many RX errors, parking the receive path"EOL);\n'
        "    rt2501_rx_down = 1;\n"
        "    return;"), 1)

    c = ("void rt2501_timer(void)\n"
         "{\n"
         "  if(!rt2501_connected) return;\n"
         "  if(rt2501_rx_starved) rt2501_submit_rx();")
    if c not in t:
        sys.exit(f"FAIL {f}: rt2501_timer is not where usb_robustness left it")
    t = t.replace(c, (
        "void rt2501_timer(void)\n"
        "{\n"
        "  if(!rt2501_connected) return;\n"
        "  if(rt2501_rx_starved) rt2501_submit_rx();\n"
        "  if(rt2501_rx_down)\n"
        "  {\n"
        "    /* One attempt per tick: bounded whatever the radio is doing, and the\n"
        "       budget refills with it so a burst during a sweep cannot add up to a\n"
        "       permanent death. A radio that really is gone simply arrives here\n"
        "       again next tick. */\n"
        "    rt2501_rx_down = 0;\n"
        "    rt2501_rx_errors = 0;\n"
        "    rt2501_frame_position = 0;\n"
        "    rt2501_submit_rx();\n"
        "  }"), 1)

    write_flat(f, t, nl)
    print(f"[ok]   {f}: a receive-error burst no longer costs the radio until the mains")


def patch_persist_counters(root: Path) -> None:
    """Put the six diagnostic counters where a reset cannot erase them.

    On 2026-11-08 an OTA downloaded, rebooted, and wrote nothing. Every
    counter that would have said how far it got reads 0 afterwards -- not
    because nothing happened, but because they live in .bss and the startup
    zero-loop clears 0x100003c0..0x1000129c on the way back up. The reboot
    destroys the evidence of what caused it.

    Reporting it over HTTP instead does not work: an httprequest issued from
    inside an HTTP_NORMAL callback, while that callback still holds the
    215 KB body, wedges the rabbit -- measured the same morning, and it
    stopped answering XMPP altogether.

    `.extbss` is NOLOAD and nothing clears it, so these survive a soft reset
    and stay readable over JTAG afterwards. They are NOT initialised, so the
    first values after a power cycle are whatever was in external RAM: read
    them once as a baseline, then again after the attempt, and compare. The
    delta is the measurement.
    """
    attr = ' __attribute__((section(".extbss")))'
    sites = (
        ("src/vm/vinterp.c", ("nab_verify_calls", "nab_verify_ok",
                              "nab_verify_nomem", "nab_verify_badargs")),
        ("src/vm/vlog.c",    ("nab_flash_error", "nab_flash_calls")),
    )
    for rel, names in sites:
        q = root / rel
        t, nl = read_flat(q)
        for n in names:
            decl = f"int32_t {n};"
            if f"int32_t {n}{attr};" in t:
                print(f"[skip] persist_counters: {rel}:{n} already in .extbss")
                continue
            if decl not in t:
                sys.exit(f"FAIL {q}: no `{decl}` to move to .extbss "
                         "(run this step after the one that declares it)")
            t = t.replace(decl, f"int32_t {n}{attr};", 1)
            print(f"[ok]   {rel}: {n} -> .extbss (survives a soft reset)")
        write_flat(q, t, nl)

    # The magic word, and the one place that clears the set.
    #
    # Surviving a reset is only half of it: NOLOAD means nothing initialises
    # these, so after a power cycle they hold whatever external RAM came up
    # with -- and "0x42015c70" is indistinguishable from "never written".
    # Measured 2026-11-08: the first read after a power cycle returned six
    # plausible-looking words, none of them a count.
    #
    # So main() clears the set exactly once per power-up, keyed on a magic
    # word that lives in the same section. A soft reset finds the magic
    # intact and leaves the counters alone, which is the whole point; a
    # power cycle almost certainly does not, and starts them at zero. The
    # residual risk is that external RAM comes up holding 0x4E414231 at that
    # one address, in which case a boot's counters read as the previous
    # boot's -- one chance in 2^32, against a diagnostic, and worth it to
    # stop reading noise as evidence.
    q = root / "src/vm/vlog.c"
    t, nl = read_flat(q)
    magic_decl = f"int32_t nab_diag_magic{attr};"
    if magic_decl not in t:
        anchor = f"int32_t nab_flash_error{attr};"
        if anchor not in t:
            sys.exit(f"FAIL {q}: no .extbss nab_flash_error to declare the "
                     "magic word beside")
        t = t.replace(anchor, magic_decl + "\n" + anchor, 1)
        write_flat(q, t, nl)
        print(f"[ok]   src/vm/vlog.c: nab_diag_magic -> .extbss")

    q = root / "src/main.c"
    t, nl = read_flat(q)
    if "nab_diag_magic" in t:
        print(f"[skip] persist_counters: src/main.c already clears the set")
        return
    anchor = '  DBG_MAIN("vmemInit"EOL);'
    if anchor not in t:
        sys.exit(f"FAIL {q}: no vmemInit call to clear the counters before")
    init = (
        "  /* Clear the diagnostic counters once per power-up. They live in\n"
        "   * .extbss so a soft reset -- the one flashFirmware ends with --\n"
        "   * keeps them, and this runs after the hardware is up, so external\n"
        "   * RAM is addressable. See patch_persist_counters. */\n"
        "  {\n"
        "    extern int32_t nab_diag_magic, nab_verify_calls, nab_verify_ok,\n"
        "                   nab_verify_nomem, nab_verify_badargs,\n"
        "                   nab_flash_calls, nab_flash_error;\n"
        "    if(nab_diag_magic != 0x4E414231)\n"
        "    {\n"
        "      nab_diag_magic = 0x4E414231;\n"
        "      nab_verify_calls = nab_verify_ok = 0;\n"
        "      nab_verify_nomem = nab_verify_badargs = 0;\n"
        "      nab_flash_calls = nab_flash_error = 0;\n"
        "    }\n"
        "  }\n\n"
    )
    t = t.replace(anchor, init + anchor, 1)
    write_flat(q, t, nl)
    print(f"[ok]   src/main.c: the counters start at zero once per power-up")


def patch_reset_reason(root: Path) -> None:
    """Record WHO reset the chip, where a reset cannot erase it.

    On 2026-11-08 an OTA got as far as a belly light past parsefirm_fw and
    then the chip reset -- no stage reported a refusal, nothing was written,
    and four explanations (truncated body, recursion depth, VM memory, the
    watchdog) had each been measured away. What was left was a reset with
    no author.

    Every software reset on this part goes through reset_uc(); the callers
    are the VM's out-of-memory path (vmem.c), the bytecode's own `reboot`
    opcode (vinterp.c) and the flash writer's end (mem.c). Each now leaves a
    reason word in .extbss first, next to the diagnostic counters, so one
    JTAG read after the attempt names the author:

        0  no software reset since power-up: the hardware watchdog, or none
        1  VM out of memory ("?OM Error" path, vmem.c)
        2  the bytecode asked for it (OPreboot with the two keys)
        3  flash_uc finished writing and reset on purpose
        4  sysReboot() from a caller not listed above

    nab_reset_count counts reset_uc() entries. Both are cleared with the
    counters once per power-up, by the magic-word block in main().
    """
    attr = ' __attribute__((section(".extbss")))'
    q = root / "src/vm/vlog.c"
    t, nl = read_flat(q)
    anchor = f"int32_t nab_diag_magic{attr};"
    if anchor not in t:
        sys.exit(f"FAIL {q}: no nab_diag_magic in .extbss -- run persist_counters first")
    if "nab_reset_reason" not in t:
        t = t.replace(anchor,
                      f"int32_t nab_reset_reason{attr};\n"
                      f"int32_t nab_reset_count{attr};\n" + anchor, 1)
        a = "void sysReboot()\n{\n  reset_uc();\n}"
        if a not in t:
            sys.exit(f"FAIL {q}: sysReboot is not the shape this patch expects")
        # The .extbss declarations sit further down the file, beside sysFlash;
        # sysReboot comes first, so it declares what it touches.
        t = t.replace(a, "void sysReboot()\n{\n"
                         "  extern int32_t nab_reset_reason;\n"
                         "  if(!nab_reset_reason) nab_reset_reason=4;\n"
                         "  reset_uc();\n}", 1)
        write_flat(q, t, nl)
        print(f"[ok]   src/vm/vlog.c: nab_reset_reason/count -> .extbss, sysReboot tags 4")
    else:
        print(f"[skip] reset_reason: vlog.c already patched")

    q = root / "src/main.c"
    t, nl = read_flat(q)
    if "nab_reset_count++" not in t:
        a = "      nab_diag_magic = 0x4E414231;\n"
        if a not in t:
            sys.exit(f"FAIL {q}: no magic-word block -- run persist_counters first")
        t = t.replace("    extern int32_t nab_diag_magic, nab_verify_calls, nab_verify_ok,\n",
                      "    extern int32_t nab_reset_reason, nab_reset_count;\n"
                      "    extern int32_t nab_diag_magic, nab_verify_calls, nab_verify_ok,\n", 1)
        t = t.replace(a, a + "      nab_reset_reason = nab_reset_count = 0;\n", 1)
        a2 = "void reset_uc(void)\n{\n"
        if a2 not in t:
            sys.exit(f"FAIL {q}: no reset_uc to count")
        t = t.replace(a2, a2 + "  { extern int32_t nab_reset_count; nab_reset_count++; }\n", 1)
        write_flat(q, t, nl)
        print(f"[ok]   src/main.c: reason/count cleared at power-up, reset_uc counts")
    else:
        print(f"[skip] reset_reason: main.c already patched")

    for rel, old, new in (
        ("src/vm/vmem.c",
         "      _vmem_broken=1;\n      sysReboot();",
         "      _vmem_broken=1;\n      { extern int32_t nab_reset_reason; nab_reset_reason=1; }\n      sysReboot();"),
        ("src/vm/vinterp.c",
         '            DBG_VM("************REBOOT NOW");\n            sysReboot();',
         '            DBG_VM("************REBOOT NOW");\n            { extern int32_t nab_reset_reason; nab_reset_reason=2; }\n            sysReboot();'),
        ("src/utils/mem.c",
         "  put_hvalue(WDTCNT,0x5A5A);  //release write protection\n  put_hvalue(WDTCNT,0xFF00);  //rewrite counter register to overflow => overflow in 256us\n  while(1);",
         "  { extern int32_t nab_reset_reason; nab_reset_reason=3; }\n  put_hvalue(WDTCNT,0x5A5A);  //release write protection\n  put_hvalue(WDTCNT,0xFF00);  //rewrite counter register to overflow => overflow in 256us\n  while(1);"),
    ):
        q = root / rel
        t, nl = read_flat(q)
        if "nab_reset_reason" in t:
            print(f"[skip] reset_reason: {rel} already tagged"); continue
        if t.count(old) != 1:
            sys.exit(f"FAIL {q}: expected exactly one reset site, found {t.count(old)}")
        write_flat(q, t.replace(old, new, 1), nl)
        print(f"[ok]   {rel}: reset site tagged")


def patch_link_liveness(root: Path) -> None:
    """Notice a dead association, instead of trusting beacons for ever.

    Two failures measured over JTAG on 2026-11-07/08, both ending at the
    mains, both the same mistake in ieee80211.c: in RUN the only thing that
    refreshes IEEE80211_RUN_TIMEOUT is a beacon from the AP (input_mgt,
    comment "(2)"), and a beacon says the AP's radio is on, not that it
    still knows us.

      - A UniFi SSID reprovisioned WPA3->WPA2: the controller showed the
        rabbit offline, the rabbit showed W5 (RUN, SAE) for ten minutes and
        more, nothing it sent reached the LAN. hostapd's farewell is ONE
        broadcast deauthenticate (hostapd_deauth_all_stas, addr1
        ff:ff:ff:ff:ff:ff), BIP-signed under 802.11w. The stock input path
        drops any deauthenticate whose addr1 is not the rabbit's own MAC,
        so that frame went by; the restarted AP beacons with the same
        BSSID, and the timeout is refreshed by a stranger.
      - An AP that forgets the station and says nothing (an idle timeout, a
        deauthenticate the rabbit was deaf for) looks identical from
        inside: beacons, no data, RUN. The AP answers the next data frame
        with a deauthenticate, reason 7 -- unprotected, because it has no
        keys for a station it does not know -- and under 802.11w
        ieee80211_mgmt_ok() throws exactly that frame away.

    Three changes, all in ieee80211.c:

    1. A deauthenticate or disassociate addressed to the broadcast address
       is accepted when addr2/addr3 are our AP's. Under 802.11w it still
       has to pass ieee80211_mgmt_ok(), and BIP covers group-addressed
       management frames -- that is what the IGTK is for.

    2. A liveness watchdog on UNICAST data from the AP. The driver notes
       counter_timer at every FROMDS data frame addressed to our MAC that
       the hardware decrypted (rt2501_rx_callback drops CipherErr frames
       before they get here). A live session never lets that age reach a
       minute: the bytecode writes " " to its XMPP socket every 8 s
       (XmppTcpIdleTime, main.mtl) and the server's acknowledgement comes
       back through the AP. So after IEEE80211_LINK_PROBE_MS (60 s) of
       silence the driver sends a null-data frame every 10 s -- the frame
       the stock code already sends in answer to the AP's own ping, so no
       new TX path -- and an AP that has forgotten us answers each with a
       deauthenticate addressed to us. After IEEE80211_LINK_DEAD_MS (180 s)
       with still nothing it gives the association up itself, which covers
       an AP that answers nothing at all. Both drop to IDLE, which the boot
       bytecode (initW) and the runtime ("wifi lost") already treat as the
       start of a fresh scan. Broadcasts are deliberately not counted: a
       station the AP has silently forgotten still decrypts them.

    3. Under 802.11w, a deauthenticate from our AP that FAILS the MMIE check
       is still ignored while the link has carried unicast data in the last
       60 s (scenarios/wpa3-deauth-forged.txt: a forgery costs nothing), and
       obeyed once the watchdog is already probing and this is what came
       back. The trade, stated plainly: a forger has to wait for a link
       that has been dead for a minute, and then costs the rabbit one SAE
       handshake it cannot break. The standard's answer here is an SA Query,
       which needs CCMP on a management frame; the RT2573 does not do that
       and a software CCMP is a later, larger change.

    Also here, because they are a few bytes and the hardware run needs
    them (dcc_telemetry prints them): counters of open-system
    authentication frames sent and received (with the last status), of
    beacons heard from the target BSSID while waiting in AUTH/ASSOC, of
    probes sent and associations given up, of bulk-out URBs that completed
    in error (usbhcore.c) and of OPbytecode loads (vinterp.c: 0 means the
    bootloader is still driving). The 2026-11-08 "deauthenticated, never
    re-authenticates" case (W2 for 8+ minutes on WPA2) cannot be told apart
    from a deaf radio, a wrong channel or a silent AP without them.

    Controls, in firmware-arm/emulator: scenarios/wpa2-ap-restart.txt,
    wpa2-ap-forget.txt, wpa3-ap-restart.txt, wpa3-ap-forget.txt fail
    without this step (RUN for ever, one association) and pass with it;
    wpa3-deauth-forged.txt must keep passing. Hardware risk: the null-data
    TX runs from rt2501_timer(), i.e. the main loop, after its critical
    section -- the same context and the same rt2501_send() path as the
    bytecode's own frames. Measured on the ARM7TDMI build, 2026-11-08: lean
    107 904 -> 108 272 (+368), lean-wpa3 117 692 -> 118 460 (+768),
    lean-wpa3-slim 107 228 -> 108 332 (+1 104, 18 644 spare).
    """
    f = root / "src/net/ieee80211.c"
    t, nl = read_flat(f)
    if "ieee80211_link_last_ucast" in t:
        print(f"[skip] link_liveness: {f} already patched")
        return

    def one(old: str, new: str, what: str) -> None:
        nonlocal t
        if t.count(old) != 1:
            sys.exit(f"FAIL {f}: link_liveness, {what}: anchor found {t.count(old)} times")
        t = t.replace(old, new, 1)

    # 1. state and the two helpers, after the association globals (and after
    #    the hunk of patches/wpa2-scanned-ciphers.patch, which ends three
    #    lines above this anchor and must keep applying).
    one("uint16_t ieee80211_assoc_rateset;\n",
        "uint16_t ieee80211_assoc_rateset;\n"
        "\n"
        "/* link_liveness (apply-mods.py): when the AP last sent us a unicast\n"
        "   data frame, the watchdog that reads it, and counters for\n"
        "   dcc_telemetry. Times are counter_timer milliseconds. */\n"
        "#include \"utils/delay.h\"\n"
        "uint32_t ieee80211_link_last_ucast;\n"
        "static uint32_t ieee80211_link_next_probe;\n"
        "uint8_t ieee80211_link_probes, ieee80211_link_deaths;\n"
        "uint8_t ieee80211_auth_tx, ieee80211_auth_rx, ieee80211_bss_heard;\n"
        "uint16_t ieee80211_auth_status;\n"
        "uint8_t nab_tx_errors, nab_bc_loads;\n"
        "#define IEEE80211_LINK_PROBE_MS       60000\n"
        "#define IEEE80211_LINK_PROBE_EVERY_MS 10000\n"
        "#define IEEE80211_LINK_DEAD_MS       180000\n"
        "\n"
        "/* Called from ieee80211_timer() in RUN, inside its critical section.\n"
        "   Returns 1 when a null-data probe is due; the caller sends it after\n"
        "   enable_ohci_irq(), since rt2501_send() has a critical section of\n"
        "   its own. */\n"
        "static int32_t ieee80211_link_watch(void)\n"
        "{\n"
        "\tuint32_t idle = counter_timer - ieee80211_link_last_ucast;\n"
        "\n"
        "\tif(idle < IEEE80211_LINK_PROBE_MS) return 0;\n"
        "\tif(idle >= IEEE80211_LINK_DEAD_MS) {\n"
        "\t\tDBG_WIFI(\"link: no unicast from the AP for 180 s, association given up\"EOL);\n"
        "\t\tieee80211_link_deaths++;\n"
        "\t\tieee80211_state = IEEE80211_S_IDLE;\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\tif((int32_t)(counter_timer - ieee80211_link_next_probe) < 0) return 0;\n"
        "\tieee80211_link_next_probe = counter_timer + IEEE80211_LINK_PROBE_EVERY_MS;\n"
        "\tieee80211_link_probes++;\n"
        "\treturn 1;\n"
        "}\n"
        "\n"
        "/* A deauthenticate/disassociate from our AP that 802.11w refused. A\n"
        "   forgery while the link carries traffic; the truth once the watchdog\n"
        "   is already probing (see the docstring in apply-mods.py). */\n"
        "static void ieee80211_link_suspect(const struct ieee80211_frame *fr)\n"
        "{\n"
        "\tif(ieee80211_mode != IEEE80211_M_MANAGED || ieee80211_state != IEEE80211_S_RUN) return;\n"
        "\tif(memcmp(fr->i_addr1, rt2501_mac, IEEE80211_ADDR_LEN) != 0) return;\n"
        "\tif(memcmp(fr->i_addr2, ieee80211_assoc_mac, IEEE80211_ADDR_LEN) != 0) return;\n"
        "\tif(memcmp(fr->i_addr3, ieee80211_assoc_bssid, IEEE80211_ADDR_LEN) != 0) return;\n"
        "\tif(counter_timer - ieee80211_link_last_ucast < IEEE80211_LINK_PROBE_MS) return;\n"
        "\tDBG_WIFI(\"link: an unprotected deauth after a minute without the AP -- believed\"EOL);\n"
        "\tieee80211_link_deaths++;\n"
        "\tieee80211_state = IEEE80211_S_IDLE;\n"
        "}\n",
        "the globals")

    # 2. the broadcast farewell is ours too
    one("\t\t\t\tif(ieee80211_state != IEEE80211_S_IDLE) {\n"
        "\t\t\t\t\tif(memcmp(fr->i_addr1, rt2501_mac, IEEE80211_ADDR_LEN) != 0) break;\n",
        "\t\t\t\tif(ieee80211_state != IEEE80211_S_IDLE) {\n"
        "\t\t\t\t\t/* link_liveness: hostapd's farewell on a restart is one\n"
        "\t\t\t\t\t   BROADCAST deauthenticate (hostapd_deauth_all_stas). */\n"
        "\t\t\t\t\tif(memcmp(fr->i_addr1, rt2501_mac, IEEE80211_ADDR_LEN) != 0\n"
        "\t\t\t\t\t   && memcmp(fr->i_addr1, ieee80211_broadcast_address, IEEE80211_ADDR_LEN) != 0) break;\n",
        "the deauth addr1 check")

    # 3. unicast data from the AP is what proves the association
    one("\t\t/* Source address (i_addr3) can be anything */\n"
        "\t\tsource_mac = fr->header.i_addr3;\n"
        "\t\tieee80211_new_rssi_sample(rssi);\n",
        "\t\t/* Source address (i_addr3) can be anything */\n"
        "\t\tsource_mac = fr->header.i_addr3;\n"
        "\t\tieee80211_new_rssi_sample(rssi);\n"
        "\t\t/* link_liveness: unicast only -- a station the AP has forgotten\n"
        "\t\t   still decrypts broadcasts under the old group key. */\n"
        "\t\tif(memcmp(fr->header.i_addr1, rt2501_mac, IEEE80211_ADDR_LEN) == 0)\n"
        "\t\t\tieee80211_link_last_ucast = counter_timer;\n",
        "the data-frame BSSID check")

    # 4. a fresh association starts the clock
    one("\t\t\t\tieee80211_rssi_sample_index = 0;\n"
        "\t\t\t\tieee80211_rssi_average = -100;\n",
        "\t\t\t\tieee80211_rssi_sample_index = 0;\n"
        "\t\t\t\tieee80211_rssi_average = -100;\n"
        "\t\t\t\tieee80211_link_last_ucast = counter_timer;  /* link_liveness */\n"
        "\t\t\t\tieee80211_link_next_probe = counter_timer;\n",
        "the association-success block")

    # 5. the timer runs the watchdog; the probe goes out after the critical section
    # Two anchors rather than the whole block: wpa3_sae rewrites the
    # timeout branch (ieee80211_sae_forget on an AUTH timeout).
    one("void ieee80211_timer(void)\n"
        "{\n"
        "\tdisable_ohci_irq();\n",
        "void ieee80211_timer(void)\n"
        "{\n"
        "\tint32_t probe = 0;  /* link_liveness */\n"
        "\n"
        "\tdisable_ohci_irq();\n",
        "the head of ieee80211_timer")
    one("\t} else {\n"
        "\t\t/* Master mode */\n"
        "\t\tuint32_t i;\n",
        "\t\tif(ieee80211_state == IEEE80211_S_RUN) probe = ieee80211_link_watch();  /* link_liveness */\n"
        "\t} else {\n"
        "\t\t/* Master mode */\n"
        "\t\tuint32_t i;\n",
        "the managed branch of ieee80211_timer")
    one("\tenable_ohci_irq();\n"
        "}\n"
        "\n"
        "int32_t rt2501_rssi_average(void)\n",
        "\tenable_ohci_irq();\n"
        "\t/* link_liveness: a null-data frame to the AP. One that still knows us\n"
        "\t   acknowledges it and nothing more; one that has forgotten us answers\n"
        "\t   with a deauthenticate, reason 7, addressed to us. */\n"
        "\tif(probe) rt2501_send(NULL, 0, ieee80211_assoc_mac, 1, 0);\n"
        "}\n"
        "\n"
        "int32_t rt2501_rssi_average(void)\n",
        "the tail of ieee80211_timer")

    # 6. telemetry: beacons from the target while waiting for it
    one("\t\t\tif(ieee80211_mode == IEEE80211_M_MANAGED) {\n"
        "\t\t\t\tif(ieee80211_state == IEEE80211_S_SCAN) {\n",
        "\t\t\tif(ieee80211_mode == IEEE80211_M_MANAGED) {\n"
        "\t\t\t\t/* link_liveness telemetry: the AP we are waiting on is audible */\n"
        "\t\t\t\tif((ieee80211_state == IEEE80211_S_AUTH || ieee80211_state == IEEE80211_S_ASSOC)\n"
        "\t\t\t\t   && memcmp(fr->i_addr3, ieee80211_assoc_bssid, IEEE80211_ADDR_LEN) == 0)\n"
        "\t\t\t\t\tieee80211_bss_heard++;\n"
        "\t\t\t\tif(ieee80211_state == IEEE80211_S_SCAN) {\n",
        "the beacon handler")

    # 7. telemetry: authentication frames addressed to us, whatever the state
    one("\t\tcase IEEE80211_FC0_SUBTYPE_AUTH:\n"
        "\t\t\tif(length < (sizeof(struct ieee80211_frame)+6)) return;\n",
        "\t\tcase IEEE80211_FC0_SUBTYPE_AUTH:\n"
        "\t\t\tif(length < (sizeof(struct ieee80211_frame)+6)) return;\n"
        "\t\t\tif(memcmp(fr->i_addr1, rt2501_mac, IEEE80211_ADDR_LEN) == 0) {\n"
        "\t\t\t\tieee80211_auth_rx++;  /* link_liveness telemetry */\n"
        "\t\t\t\tieee80211_auth_status = (uint16_t)(frame_current[4] | (frame_current[5] << 8));\n"
        "\t\t\t}\n",
        "the auth handler")

    # 8. telemetry: open-system authentication frames that left
    one("\t/* Frame sent, update the state machine */\n"
        "\tieee80211_state = IEEE80211_S_AUTH;\n",
        "\t/* Frame sent, update the state machine */\n"
        "\tieee80211_auth_tx++;  /* link_liveness telemetry */\n"
        "\tieee80211_state = IEEE80211_S_AUTH;\n",
        "the auth sender")

    # 9. under 802.11w, read a refused deauthenticate in context (wpa3_sae)
    pmf = "\t\t\tif(!ieee80211_mgmt_ok((const uint8_t *)fr, length)) break;\n"
    if pmf in t:
        one(pmf,
            "\t\t\tif(!ieee80211_mgmt_ok((const uint8_t *)fr, length)) {\n"
            "\t\t\t\tieee80211_link_suspect(fr);  /* link_liveness */\n"
            "\t\t\t\tbreak;\n"
            "\t\t\t}\n",
            "the 802.11w gate")
        pmf_note = ", and the 802.11w gate reads a refused deauth in context"
    else:
        pmf_note = ""
    write_flat(f, t, nl)

    # TX errors: bulk-out URBs that completed in error. usbhcore.c is not
    # compiled into the emulator (its stub is), so this is hardware only.
    q = root / "src/usb/usbhcore.c"
    u, unl = read_flat(q)
    a = "static void usbh_free_urb_callback(PURB urb)\n{\n  hcd_free(urb->buffer);\n"
    if "nab_tx_errors" not in u:
        if u.count(a) != 1:
            sys.exit(f"FAIL {q}: link_liveness, usbh_free_urb_callback is not the shape expected")
        u = u.replace(a, "static void usbh_free_urb_callback(PURB urb)\n{\n"
                         "  /* link_liveness telemetry: a frame the stick refused to take */\n"
                         "  if(urb->status == URB_XFERERR) { extern uint8_t nab_tx_errors; nab_tx_errors++; }\n"
                         "  hcd_free(urb->buffer);\n", 1)
        write_flat(q, u, unl)

    # Bytecode loads: 0 = the bootloader is driving, 1 = the runtime is.
    q = root / "src/vm/vinterp.c"
    v, vnl = read_flat(q)
    a = "      case OPbytecode:\n        DBG_VM(EOL\"#################### OPbytecode\"EOL);\n"
    if "nab_bc_loads" not in v:
        if v.count(a) != 1:
            sys.exit(f"FAIL {q}: link_liveness, OPbytecode is not the shape expected")
        v = v.replace(a, a + "        { extern uint8_t nab_bc_loads; nab_bc_loads++; }  /* link_liveness telemetry */\n", 1)
        write_flat(q, v, vnl)

    print(f"[ok]   link_liveness: broadcast deauth obeyed, unicast watchdog with "
          f"null-data probes{pmf_note}; auth/tx/bytecode counters for telemetry")


def patch_dcc_console(root: Path) -> None:
    """Mirror the serial console onto the JTAG debug channel.

    NOT IN ANY MODE. Measured 2026-11-08: with this step in, two images
    (builds 1208 and 1147) booted, scanned, and heard zero access points --
    orange for ever, ieee80211_state IDLE, scan_cache_len 0 -- while the
    same image without it (1243) associated in seconds. The radio and USB
    code is byte-identical between them; the only difference on the running
    path is this mirror in putch_uart. Mechanism not yet understood: keep
    the step for the investigation, do not ship it.

    The firmware has written a 115200-baud console since 2006 -- every
    Secholn in the bytecode ends in putch_uart() -- but the UART is only
    pads on the PCB, so nobody has ever read it. The ARM7TDMI's Debug
    Communications Channel is two coprocessor-14 registers the core can
    write while running and OpenOCD reads while polling, over the five JTAG
    wires that are already connected. `target_request debugmsgs enable` on
    the host, no halt, no session lost.

    Two things make this safe on a rabbit nobody is listening to:
    - every write is bounded (DCC_SPIN status reads, a few ms at 32 MHz),
      and a timeout mutes the channel for the next 256 characters, so an
      unattached debugger costs one short stall per 256 bytes of console;
    - characters go out one per word (TARGET_REQ_DEBUGCHAR), so nothing
      depends on a multi-word frame surviving a slow bit-banged poll.

    mcr/mrc p14 are ARM-mode only; the two functions that touch them carry
    target("arm") and are reached through interworking from Thumb code.
    The emulator is untouched: it links its own stubs/uart.c.
    """
    hdr = root / "inc/utils/dcc.h"
    src = root / "src/utils/dcc.c"
    if src.exists():
        print(f"[skip] dcc_console: {src} already present")
    else:
        hdr.write_text(
            "#ifndef _DCC_H_\n#define _DCC_H_\n#include <stdint.h>\n"
            "/* Best-effort character out on the EmbeddedICE debug comms channel.\n"
            " * Never blocks for more than a few milliseconds; drops when no\n"
            " * debugger is draining the channel. See src/utils/dcc.c. */\n"
            "void dcc_putc(uint8_t c);\n#endif\n")
        src.write_text(
            '#include "utils/dcc.h"\n\n'
            "/* OpenOCD's target_request protocol (contrib/libdcc): one word,\n"
            " * request type in the low byte, the character in bits 16..23. */\n"
            "#define TARGET_REQ_DEBUGCHAR 0x02\n"
            "#define DCC_SPIN  20000   /* status reads before giving up: ~ms */\n"
            "#define DCC_MUTE  256     /* characters dropped after a timeout */\n\n"
            "static uint32_t dcc_muted;\n\n"
            "__attribute__((target(\"arm\"), noinline))\n"
            "static int dcc_try_write(uint32_t w)\n{\n"
            "  uint32_t st, n;\n"
            "  for(n=0;n<DCC_SPIN;n++)\n  {\n"
            "    asm volatile(\"mrc p14, 0, %0, c0, c0\" : \"=r\"(st));\n"
            "    if(!(st & 2))\n    {\n"
            "      asm volatile(\"mcr p14, 0, %0, c1, c0\" : : \"r\"(w));\n"
            "      return 1;\n    }\n  }\n  return 0;\n}\n\n"
            "void dcc_putc(uint8_t c)\n{\n"
            "  if(dcc_muted) { dcc_muted--; return; }\n"
            "  if(!dcc_try_write(TARGET_REQ_DEBUGCHAR | ((uint32_t)c << 16)))\n"
            "    dcc_muted = DCC_MUTE;\n}\n")
        print(f"[ok]   src/utils/dcc.c + inc/utils/dcc.h written")

    q = root / "src/hal/uart.c"
    t, nl = read_flat(q)
    if "dcc_putc" in t:
        print(f"[skip] dcc_console: uart.c already mirrors"); return
    a = ("  /* write characters to UART Transmitter Holding register */\n"
         "  put_value(UARTTHR0,c);\n}")
    if t.count(a) != 1:
        sys.exit(f"FAIL {q}: putch_uart is not the shape this patch expects")
    t = t.replace(a, a[:-1] + "  dcc_putc(c);   /* and the JTAG debug channel, see utils/dcc.c */\n}", 1)
    if '#include "utils/dcc.h"' not in t:
        t = '#include "utils/dcc.h"\n' + t
    write_flat(q, t, nl)
    print(f"[ok]   src/hal/uart.c: putch_uart mirrors every byte onto the DCC")


def patch_dcc_telemetry(root: Path) -> None:
    """A status line on the JTAG debug channel, never waiting for anyone.

    NOT IN ANY SHIPPED MODE; `lean-wpa3-tel` is lean-wpa3 plus this.

    Reading the rabbit's state over JTAG meant halting it, and on 2026-11-08
    a radio went deaf after a series of short halts -- which left a test
    unreadable: the bug under test, or the probe? The DCC console
    (patch_dcc_console) read without halting but killed Wi-Fi association;
    the suspect is its wait: up to DCC_SPIN status reads per character, for
    every byte of the console, whenever the host had not drained the last
    word yet. This does the opposite on every count:

    - one character per rt2501_timer() tick, and only if the channel is
      free: one status read, no loop. Busy, the same character is offered
      again next tick; nothing is lost, nothing waits;
    - a short line, `W5 A2 S0 C8 E2 X0 K1 D0 P0 T258 R0/0 a1 r1/0 h0 q0 L0 U3
      Y0 B1 F0/1c`, snapshotted at its first character: ieee80211_state,
      authmode, SAE state, SAE cache entries, eapol_state, USB receive
      errors, rt2501_connected, management frames 802.11w refused,
      deauth/disassoc seen with the Protected bit, ieee80211_timeout, reset
      reason/count; then link_liveness's counters: open-auth frames sent,
      auth frames received to us / the last status word, beacons from the
      target BSSID heard while in AUTH/ASSOC, null-data probes sent,
      associations given up, seconds since the last unicast data frame
      from the AP, bulk-out URB errors, bytecode loads (0 = the bootloader
      is still driving), and F the ASIC's own count of frames never
      acknowledged / frames acknowledged (STA_CSR4/5). Added 2026-11-08 for
      the deauthenticated-never-re-authenticates case: `a` rising with `r`
      at 0 and `h` rising says the AP hears us and does not answer; `h` at
      0 says we are deaf or on the wrong channel; `Y` rising says the
      frames never left the stick; `F` failed rising with each `a` says
      they left and nobody acknowledged them (wrong channel or rate, or an
      AP that ignores this MAC); about 85 characters, so one line every
      85 ticks;
    - from the top of rt2501_timer(), before its `if(!rt2501_connected)
      return`, so a radio that has given up still reports that it has.

    OpenOCD's `target_request debugmsgs enable` (jtag/listen.sh) prints it.
    mcr/mrc p14 are ARM-mode only, hence target("arm"). rt2501usb.c is not
    compiled into the emulator, so it is untouched.
    """
    src = root / "src/utils/dcctel.c"
    if src.exists():
        print(f"[skip] dcc_telemetry: {src} already present")
        return
    ie = root / "src/net/ieee80211.c"
    t = read_src(ie)
    if "ieee80211_sae_count" not in t:
        sys.exit(f"FAIL {ie}: dcc_telemetry needs wpa3_sae first")
    if "ieee80211_link_last_ucast" not in t:
        sys.exit(f"FAIL {ie}: dcc_telemetry needs link_liveness first (its counters)")
    nl = "\r\n" if "\r\n" in t else "\n"
    # Appended at the end, as patch_wpa3_timing does: wpa2-scanned-ciphers.patch
    # anchors near the top and must keep applying.
    t = t + (nl + "/* For dcc_telemetry: SAE state low nibble, cache entries high. */" + nl
             + "uint8_t ieee80211_sae_diag(void)" + nl + "{" + nl
             + "  return (uint8_t)((ieee80211_sae.state & 0x0F)"
             + " | (ieee80211_sae_count << 4));" + nl + "}" + nl)
    write_src(ie, t)
    src.write_text(
        "#include <stdint.h>\n\n"
        "/* See patch_dcc_telemetry in apply-mods.py. */\n"
        "extern uint32_t ieee80211_state, ieee80211_timeout;\n"
        "extern uint8_t ieee80211_authmode;\n"
        "extern uint8_t eapol_state;\n"
        "extern uint8_t ieee80211_pmf_dropped, ieee80211_pmf_protbit;\n"
        "extern int32_t nab_reset_reason, nab_reset_count;\n"
        "extern volatile uint32_t counter_timer;\n"
        "extern uint32_t ieee80211_link_last_ucast;\n"
        "extern uint8_t ieee80211_link_probes, ieee80211_link_deaths;\n"
        "extern uint8_t ieee80211_auth_tx, ieee80211_auth_rx, ieee80211_bss_heard;\n"
        "extern uint16_t ieee80211_auth_status;\n"
        "extern uint8_t nab_tx_errors, nab_bc_loads;\n"
        "extern uint16_t nab_tx_acked, nab_tx_failed;\n"
        "void rt2501_tx_stats_poll(void);\n"
        "uint8_t ieee80211_sae_diag(void);\n\n"
        "#define TARGET_REQ_DEBUGCHAR 0x02\n\n"
        "static char dcctel_line[96];\n"
        "static uint8_t dcctel_pos, dcctel_len;\n\n"
        "__attribute__((target(\"arm\"), noinline))\n"
        "static int dcctel_try(uint32_t w)\n{\n"
        "  uint32_t st;\n"
        "  asm volatile(\"mrc p14, 0, %0, c0, c0\" : \"=r\"(st));\n"
        "  if(st & 2) return 0;   /* the host has not read the last word */\n"
        "  asm volatile(\"mcr p14, 0, %0, c1, c0\" : : \"r\"(w));\n"
        "  return 1;\n}\n\n"
        "static char *dcctel_hex(char *p, uint32_t v)\n{\n"
        "  char tmp[8]; int n = 0;\n"
        "  do { tmp[n++] = \"0123456789abcdef\"[v & 15]; v >>= 4; } while(v && n < 8);\n"
        "  while(n) *p++ = tmp[--n];\n"
        "  return p;\n}\n\n"
        "static void dcctel_snapshot(uint8_t rx_errors, uint8_t connected)\n{\n"
        "  char *p = dcctel_line;\n"
        "  uint8_t sae = ieee80211_sae_diag();\n"
        "  if(connected) rt2501_tx_stats_poll();\n"
        "  *p++ = 'W'; p = dcctel_hex(p, ieee80211_state);\n"
        "  *p++ = ' '; *p++ = 'A'; p = dcctel_hex(p, ieee80211_authmode);\n"
        "  *p++ = ' '; *p++ = 'S'; p = dcctel_hex(p, sae & 15);\n"
        "  *p++ = ' '; *p++ = 'C'; p = dcctel_hex(p, sae >> 4);\n"
        "  *p++ = ' '; *p++ = 'E'; p = dcctel_hex(p, eapol_state);\n"
        "  *p++ = ' '; *p++ = 'X'; p = dcctel_hex(p, rx_errors);\n"
        "  *p++ = ' '; *p++ = 'K'; p = dcctel_hex(p, connected);\n"
        "  *p++ = ' '; *p++ = 'D'; p = dcctel_hex(p, ieee80211_pmf_dropped);\n"
        "  *p++ = ' '; *p++ = 'P'; p = dcctel_hex(p, ieee80211_pmf_protbit);\n"
        "  *p++ = ' '; *p++ = 'T'; p = dcctel_hex(p, ieee80211_timeout);\n"
        "  *p++ = ' '; *p++ = 'R'; p = dcctel_hex(p, (uint32_t)nab_reset_reason);\n"
        "  *p++ = '/'; p = dcctel_hex(p, (uint32_t)nab_reset_count);\n"
        # link_liveness: a open-auth frames sent, r auth frames received to
        # us / last status, h beacons from the target while in AUTH/ASSOC,
        # q null-data probes, L associations given up, U seconds since the
        # last unicast data frame from the AP, Y bulk-out URB errors,
        # B bytecode loads (0: the bootloader is driving).
        "  *p++ = ' '; *p++ = 'a'; p = dcctel_hex(p, ieee80211_auth_tx);\n"
        "  *p++ = ' '; *p++ = 'r'; p = dcctel_hex(p, ieee80211_auth_rx);\n"
        "  *p++ = '/'; p = dcctel_hex(p, ieee80211_auth_status);\n"
        "  *p++ = ' '; *p++ = 'h'; p = dcctel_hex(p, ieee80211_bss_heard);\n"
        "  *p++ = ' '; *p++ = 'q'; p = dcctel_hex(p, ieee80211_link_probes);\n"
        "  *p++ = ' '; *p++ = 'L'; p = dcctel_hex(p, ieee80211_link_deaths);\n"
        "  *p++ = ' '; *p++ = 'U'; p = dcctel_hex(p, (counter_timer - ieee80211_link_last_ucast) / 1000);\n"
        "  *p++ = ' '; *p++ = 'Y'; p = dcctel_hex(p, nab_tx_errors);\n"
        "  *p++ = ' '; *p++ = 'B'; p = dcctel_hex(p, nab_bc_loads);\n"
        # F: frames the ASIC gave up on after every retry (no ACK from the
        # AP) / frames it got an ACK for. The one counter that tells "the AP
        # ignores our auth" from "our auth never reaches the air".
        "  *p++ = ' '; *p++ = 'F'; p = dcctel_hex(p, nab_tx_failed);\n"
        "  *p++ = '/'; p = dcctel_hex(p, nab_tx_acked);\n"
        "  *p++ = '\\n';\n"
        "  dcctel_len = (uint8_t)(p - dcctel_line);\n"
        "  dcctel_pos = 0;\n}\n\n"
        "void dcctel_tick(uint8_t rx_errors, uint8_t connected)\n{\n"
        "  if(dcctel_pos >= dcctel_len) dcctel_snapshot(rx_errors, connected);\n"
        "  if(dcctel_try(TARGET_REQ_DEBUGCHAR\n"
        "                | ((uint32_t)(uint8_t)dcctel_line[dcctel_pos] << 16)))\n"
        "    dcctel_pos++;\n}\n")
    q = root / "src/usb/rt2501usb.c"
    t, nl2 = read_flat(q)
    a = "void rt2501_timer(void)\n{\n  if(!rt2501_connected) return;\n"
    if t.count(a) != 1:
        sys.exit(f"FAIL {q}: rt2501_timer is not the shape dcc_telemetry expects")
    t = t.replace(a, "void dcctel_tick(uint8_t rx_errors, uint8_t connected);\n\n"
                  "void rt2501_timer(void)\n{\n"
                  "  dcctel_tick(rt2501_rx_errors, (uint8_t)rt2501_connected);  /* dcc_telemetry */\n"
                  "  if(!rt2501_connected) return;\n", 1)
    # The ASIC's own TX statistics. STA_CSR4 holds the no-retry and one-retry
    # counts, STA_CSR5 the multi-retry and retry-fail counts (16 bits each,
    # cleared by the read -- Ralink RT2573 register map, the same registers
    # rt2x00's rt73usb_link_stats reads). Two control transfers per snapshot,
    # i.e. every ~80 ticks: lighter than rt2501_calibrate, which reads BBP
    # registers every RT2501_CAL_PERIOD (40) ticks through the same path.
    # Hardware risk: a control read on a stick that has stopped answering
    # blocks like any other; dcctel_snapshot only calls this while
    # rt2501_connected says the stick is there.
    t = t + ("\n/* dcc_telemetry: the ASIC's TX statistics, accumulated (the registers\n"
             "   clear on read). failed = frames given up on after every retry, i.e.\n"
             "   never acknowledged by the AP; acked = everything else. */\n"
             "uint16_t nab_tx_acked, nab_tx_failed;\n"
             "void rt2501_tx_stats_poll(void)\n{\n"
             "  uint32_t c4 = rt2501_read(rt2501_dev, STA_CSR4);\n"
             "  uint32_t c5 = rt2501_read(rt2501_dev, STA_CSR5);\n"
             "  nab_tx_acked += (uint16_t)((c4 & 0xffff) + (c4 >> 16) + (c5 & 0xffff));\n"
             "  nab_tx_failed += (uint16_t)(c5 >> 16);\n}\n")
    write_flat(q, t, nl2)
    print(f"[ok]   dcc_telemetry: a status line on the debug channel, one char per tick")


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

    Measured with mtl_comp on the lean-wpa3 bootloader, 2026-11-08: 517 B
    alone, 551 B with inline_dump_calls (the June estimate said ~300).
    Caller sites stay 1:1 valid.
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

    # 1. Delete the identity definitions `fun dump s=s;;` and
    #    `fun dumpscan l0=l0;;` that patch_strip_dump wrote -- BEFORE the
    #    keyword pass below, which would otherwise turn `fun dump s=s;;`
    #    into `fun s=s;;` and leave two junk functions named `s` and `l0`
    #    behind (it did, until the compiled function count was looked at
    #    on 2026-11-08: 220 before and after, where 218 was expected).
    for head, body in (("fun dump s=", "s"), ("fun dumpscan l0=", "l0")):
        target = f"{head}{body};;{eol}"
        if s.count(target) != 1:
            sys.exit(f"FAIL inline_dump_calls: identity form {target!r} not found "
                     "exactly once -- did strip_dump run?")
        s = s.replace(target, "", 1)

    # 2. Delete `dump ` and `dumpscan ` keywords from code. These are
    #    1-arg functions reduced to identity by patch_strip_dump, so
    #    deleting the keyword is a no-op semantically.
    pat = re.compile(r"\b(?:dumpscan|dump)\b[ \t]+")
    n_call = 0
    def _sub(_m):
        nonlocal n_call
        n_call += 1
        return ""
    s = pat.sub(_sub, s)
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

    Measured with mtl_comp on the lean-wpa3 bootloader, 2026-11-08: 146 B
    for the 63 sites left after strip_secho (about 2 B each -- the opcode
    byte and a shorter OPdrop sequence; the argument expression stays).
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

    Measured with mtl_comp on the lean-wpa3 bootloader, 2026-11-08: 214 B
    across the 3 fun bodies (the June estimate said ~120).
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

    Measured with mtl_comp on the lean-wpa3 bootloader, 2026-11-08: 2 036 B
    for 106 sites (the June estimate said ~1 KB). The biggest single
    bytecode saving; the strings were the weight, not the opcodes.
    Lost observable: the rabbit's boot log on the UART pad, which a
    production rabbit does not have wired -- and the emulator's rabbit-vm
    transcript loses the same lines (its assertions are on the 802.11
    state, not on the console).
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
    change, just no longer paying for a value we already know at build
    time. Measured with mtl_comp on the lean-wpa3 bootloader, 2026-11-08:
    105 B, not the ~520 the June estimate said -- the fun is small, and the
    46-byte literal it becomes costs most of what the call did.
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


_UART_FMT_C = r'''
/* Decimal and hex without newlib's sprintf. sprintf was the only thing
   left pulling _svfprintf_r, _printf_i and nano's malloc/free/realloc into
   the image once the DEBUG_* paths were off: 4.3 KB of lean-wpa3, measured
   2026-11-08 (apply-mods.py uart_no_sprintf). The two helpers write the
   same bytes as the formats they replace -- "%ld", "0x%lx", "%04lx",
   "%02x" -- which tests/test_uart_fmt.c checks against sprintf itself. */
static void put_hex_uart(uint32_t v, uint32_t min_digits)
{
  uint8_t d[8];
  uint32_t n=0;
  do {
    uint32_t x=v&15;
    d[n++]=(uint8_t)(x<10 ? '0'+x : 'a'+x-10);
    v>>=4;
  } while(v);
  while(n<min_digits) d[n++]='0';
  while(n) putch_uart(d[--n]);
}

static void put_dec_uart(int32_t v)
{
  uint8_t d[10];
  uint32_t n=0;
  uint32_t u=(uint32_t)v;
  if(v<0) { putch_uart('-'); u=0u-u; }
  do { d[n++]=(uint8_t)('0'+u%10); u/=10; } while(u);
  while(n) putch_uart(d[--n]);
}

'''


def patch_small_strings(root: Path) -> None:
    """strcmp, strcpy and strlen as plain loops, not newlib's.

    newlib's ARM string functions are written for speed: word-at-a-time,
    unrolled, alignment cases -- strcmp alone is 548 bytes, strcpy 240,
    strlen 96, measured in naboot-1.0.0.3's Nab.elf. Nothing on a hot path
    calls them here: they compare configuration keys, copy SSIDs and measure
    URLs, a few dozen bytes at a time. A byte loop does the same in about
    twenty bytes each. A definition in an object file wins over the one in
    libc.a, so no call site changes.

    memcpy, memset and memmove keep newlib's: they move USB buffers, TLS
    records and the bytecode, where speed is the point.

    The loops carry no-tree-loop-distribute-patterns: GCC otherwise
    recognises "walk to the NUL" as strlen and emits a call to strlen --
    from inside strlen."""
    src = root / "src/utils/smallstr.c"
    if src.exists():
        print(f"[skip] small_strings: {src} already present")
        return
    src.write_text(
        "/* See patch_small_strings in apply-mods.py. */\n"
        "#include <stddef.h>\n\n"
        "#define PLAIN __attribute__((optimize(\"no-tree-loop-distribute-patterns\")))\n\n"
        "PLAIN int strcmp(const char *a, const char *b)\n{\n"
        "  const unsigned char *p = (const unsigned char *)a;\n"
        "  const unsigned char *q = (const unsigned char *)b;\n"
        "  while(*p && *p == *q) { p++; q++; }\n"
        "  return (int)*p - (int)*q;\n}\n\n"
        "PLAIN char *strcpy(char *d, const char *s)\n{\n"
        "  char *r = d;\n"
        "  while((*d++ = *s++) != 0)\n    ;\n"
        "  return r;\n}\n\n"
        "PLAIN size_t strlen(const char *s)\n{\n"
        "  const char *p = s;\n"
        "  while(*p)\n    p++;\n"
        "  return (size_t)(p - s);\n}\n")
    print(f"[ok]   small_strings: {src}")


def patch_uart_no_sprintf(root: Path) -> None:
    """Take sprintf out of the serial console.

    patch_makefile's docstring says switching the DEBUG_* defines off "drops
    the printf family". Measured on lean-wpa3 (2026-11-08), it does not:
    src/hal/uart.c itself calls sprintf from putint_uart, puthx_uart, dump
    and dumpbin, and the first three are live -- putint/puthx through the
    VM's Iecho opcodes (vlog.c consoleint/consolehx), dump from eapol.c on
    every EAPOL message 1 and MIC mismatch. So the linker keeps
    lib_a-sprintf, nano-svfprintf, nano-vfprintf_i, and the nano malloc,
    free, realloc, sbrk, msize and mlock they drag in: 4 316 bytes of flash
    in the .map, plus the reent block in .data.

    Two small formatters replace the four calls and write the same bytes
    (tests/test_uart_fmt.c compares them with sprintf). The UART is still
    written one busy-waited byte at a time, so the only timing change is
    the formatting itself, microseconds against a 115 200-baud link.

    Console path: hardware-risk by this project's rule, even though the
    output is identical. The emulator does not compile this file (its
    stubs/uart.c prints to stdout), which is why the test exists.
    """
    p = root / "src/hal/uart.c"
    s, nl = read_flat(p)
    if "put_hex_uart" in s:
        print(f"[skip] {p}: sprintf already gone")
        return
    edits = [
        ('#include <stdio.h>  // sprintf\n', ''),
        ('/**\n * @brief Write an Integer to the serial port, in decimal form\n',
         _UART_FMT_C.lstrip("\n") +
         '/**\n * @brief Write an Integer to the serial port, in decimal form\n'),
        ('  uint8_t buf[32];\n  sprintf((char*)buf,"%ld",v);\n  putst_uart(buf);\n',
         '  put_dec_uart(v);\n'),
        ('  uint8_t buf[12];\n  sprintf((char*)buf,"0x%lx",v);\n  putst_uart(buf);\n',
         '  putst_uart((uint8_t*)"0x");\n  put_hex_uart(v,1);\n'),
        ('  uint8_t buffer[64];\n  putst_uart((uint8_t*)EOL);\n',
         '  putst_uart((uint8_t*)EOL);\n'),
        ('    sprintf((char*)buffer,"%04lx ",i);\n    putst_uart(buffer);\n',
         '    put_hex_uart((uint32_t)i,4);\n    putch_uart(\' \');\n'),
        ('      sprintf((char*)buffer,"%02x ",src[i+j]); putst_uart(buffer);\n',
         '      put_hex_uart(src[i+j],2); putch_uart(\' \');\n'),
        ('  uint8_t buffer[6];\n  for(i=0;i<n;i++)\n',
         '  for(i=0;i<n;i++)\n'),
        ('    sprintf((char*)buffer,"%02x.",(p[i])&255);\n    putst_uart(buffer);\n',
         '    put_hex_uart((uint32_t)((p[i])&255),2);\n    putch_uart(\'.\');\n'),
    ]
    for old, new in edits:
        if s.count(old) != 1:
            sys.exit(f"FAIL {p}: anchor not found exactly once: {old[:60]!r}")
        s = s.replace(old, new, 1)
    if re.search(r"\bsprintf\s*\(", s):
        sys.exit(f"FAIL {p}: a sprintf call survived the rewrite")
    write_flat(p, s, nl)
    print(f"[ok]   {p}: putint/puthx/dump/dumpbin format without sprintf")


_DBG_FLAG = {"DBG": "DEBUG", "DBG_AUD": "DEBUG_AUDIO"}


def _debug_depth_at(lines: list[str]) -> list[bool]:
    """For each line, whether it sits inside a #if/#ifdef on a DEBUG_* flag
    (the production branch of its #else counts as outside). _NAB_SIM blocks
    count as well: that is the emulator's host-only tracing (its
    opnetauth-trace.patch), never compiled into a rabbit."""
    out = []
    stack: list[bool] = []
    for l in lines:
        t = l.strip()
        if t.startswith("#if"):
            stack.append("DEBUG" in t or "_NAB_SIM" in t or t.startswith("#if 0"))
        elif t.startswith("#else") and stack:
            stack[-1] = False
        elif t.startswith("#endif") and stack:
            stack.pop()
        out.append(any(stack))
    return out


def _production_sprintf_sites(text: str) -> list[int]:
    """Line numbers (1-based) of sprintf(...) calls compiled into a build
    with every DEBUG_* flag off: outside such blocks, not in a comment."""
    lines = text.split("\n")
    in_dbg = _debug_depth_at(lines)
    # block comments, so a sprintf inside /* ... */ is not counted
    commented = [False] * len(lines)
    for m in re.finditer(r"/\*.*?\*/", text, re.S):
        a = text.count("\n", 0, m.start())
        b = text.count("\n", 0, m.end())
        for k in range(a, b + 1):
            commented[k] = True
    sites = []
    for i, l in enumerate(lines):
        if re.search(r"\bsprintf\s*\(", l) and not l.strip().startswith("//") \
                and not in_dbg[i] and not commented[i]:
            sites.append(i + 1)
    return sites


def patch_strip_dbg_sprintf(root: Path) -> None:
    """Stop formatting debug text that nothing prints.

    Thirteen `sprintf(dbg_buffer, ...)` calls in src/net and src/usb sit
    outside any #ifdef DEBUG_* block (one of them per transmitted frame, in
    rt2501_make_tx_descriptor). Each is followed by `DBG_WIFI(dbg_buffer)`
    or `DBG_USB(dbg_buffer)`, and with the DEBUG_* flags off
    (patch_makefile) those macros expand to nothing: the text is formatted
    into dbg_buffer and never read -- rfid.c's snprintf is the only other
    writer and it is already under DEBUG_RFID. Together with uart.c (see
    patch_uart_no_sprintf) these were what kept sprintf, newlib's printf
    core and nano malloc in the image: 4 316 bytes of lean-wpa3 (.map,
    2026-11-08). The uart step alone measured +88 bytes, because eapol.o
    still pulled sprintf in; the two steps are one saving.

    Each sprintf/DBG pair is wrapped in `#ifdef DEBUG_<flag>` ... `#endif`,
    so a debug build keeps its messages and a production build compiles
    nothing. The pairing is checked site by site and the step fails on a
    sprintf(dbg_buffer, ...) it cannot pair, so a site added later is noticed
    rather than left behind. Behaviour: none, the buffer had no reader. Timing:
    rt2501_rx_callback (USB bulk RX error), rt2501_connect, usbh_connect and
    the SAE handlers no longer spend a sprintf -- faster, by microseconds.
    USB and radio files are edited, so: hardware-risk by this project's rule.
    """
    start_re = re.compile(r"^[ \t]*sprintf\(\s*(?:\(char\s*\*\)\s*)?dbg_buffer\s*,", re.M)
    dbg_re = re.compile(r"\s*(DBG(?:_[A-Z]+)?)\(\s*(?:\(char\s*\*\)\s*)?dbg_buffer\s*\);[ \t]*")
    total = 0
    for p in sorted(root.glob("src/**/*.c")):
        if not p.is_file() or p.name == "uart.c":
            continue
        s, nl = read_flat(p)
        if "sprintf" not in s or "/* dbg-sprintf-guarded */" in s:
            continue
        sites = _production_sprintf_sites(s)
        if not sites:
            continue
        lines = s.split("\n")
        # work from the bottom so earlier offsets stay valid
        for ln in reversed(sites):
            line_start = sum(len(x) + 1 for x in lines[:ln - 1])
            m = start_re.match(s, line_start)
            if not m:
                sys.exit(f"FAIL strip_dbg_sprintf: {p}:{ln}: a sprintf that does not "
                         "write dbg_buffer -- not touching it, look at it")
            # the end of the sprintf statement: ';' at depth 0, outside strings
            i = m.end()
            depth = 1
            in_str = False
            while i < len(s):
                c = s[i]
                if in_str:
                    if c == "\\":
                        i += 1
                    elif c == '"':
                        in_str = False
                elif c == '"':
                    in_str = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                elif c == ";" and depth == 0:
                    break
                i += 1
            if i >= len(s):
                sys.exit(f"FAIL strip_dbg_sprintf: {p}:{ln}: unterminated sprintf")
            stmt_end = i + 1
            d = dbg_re.match(s, stmt_end)
            if not d:
                sys.exit(f"FAIL strip_dbg_sprintf: {p}:{ln}: sprintf(dbg_buffer) not "
                         "followed by DBG_x(dbg_buffer); -- not touching it")
            macro = d.group(1)
            flag = _DBG_FLAG.get(macro, macro.replace("DBG_", "DEBUG_"))
            block_end = d.end()
            # consume the rest of the DBG line
            eol = s.find("\n", block_end)
            eol = len(s) if eol < 0 else eol
            block = s[line_start:eol]
            s = s[:line_start] + f"#ifdef {flag}\n{block}\n#endif" + s[eol:]
            total += 1
            lines = s.split("\n")
        left = _production_sprintf_sites(s)
        if left:
            sys.exit(f"FAIL strip_dbg_sprintf: {p}: sprintf still compiled at lines {left}")
        s = s.replace("#include \"utils/debug.h\"", "#include \"utils/debug.h\"  /* dbg-sprintf-guarded */", 1) \
            if '#include "utils/debug.h"' in s else "/* dbg-sprintf-guarded */\n" + s
        write_flat(p, s, nl)
        print(f"[ok]   {p}: {len(sites)} sprintf(dbg_buffer) call(s) now under their DEBUG_* flag")
    if total == 0:
        print("[skip] strip_dbg_sprintf: nothing left to guard")
    else:
        print(f"[ok]   strip_dbg_sprintf: {total} debug sprintf call(s) compiled out of production")


def patch_md5_compact(root: Path) -> None:
    """Fold the 64 unrolled MD5 steps into a loop over two tables.

    hash.c carries Colin Plumb's MD5 with every step written out, and at -O1
    on the ARM7TDMI that is 2 176 bytes of code for MD5Transform alone -- the
    third-largest function in the image after interpGo and ieee80211_input
    (lean-wpa3 .map, 2026-11-08). It is live: hmac_md5 is the MIC of WPA
    (TKIP) EAPOL frames, key descriptor version 1.

    The replacement is the same algorithm as a 64-pass loop: Rivest's T
    table (256 B) and the 16 shift amounts (16 B). The T constants are not
    retyped -- they are lifted from the 64 MD5STEP lines being replaced, and
    the input-word and shift schedule those lines use is checked against the
    closed forms the loop relies on, so a tree whose unrolled code differs
    fails this step instead of getting a different hash.

    tests/test_md5.c runs RFC 1321's seven digests and RFC 2202's HMAC-MD5
    vectors through the patched file, with the pristine file as the control.
    Timing: a few hundred more cycles per 64-byte block on the EAPOL path,
    which is handshake-time, not frame-time.
    """
    p = root / "src/net/hash.c"
    s, nl = read_flat(p)
    if "md5_t[64]" in s:
        print(f"[skip] {p}: MD5Transform already compact")
        return
    head = "static void MD5Transform(uint32_t buf[4], uint32_t in[16])\n{\n"
    start = s.find(head)
    if start < 0:
        sys.exit(f"FAIL {p}: MD5Transform definition not found")
    end = s.find("\n}\n", start)
    if end < 0:
        sys.exit(f"FAIL {p}: MD5Transform body end not found")
    body = s[start:end + 3]
    steps = re.findall(
        r"MD5STEP\((F[1-4]), (\w), (\w), (\w), (\w), in\[(\d+)\] \+ (0x[0-9a-fA-F]+), (\d+)\);",
        body)
    if len(steps) != 64:
        sys.exit(f"FAIL {p}: expected 64 MD5STEP lines, found {len(steps)}")
    shifts = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21]
    regs = "abcd"
    consts = []
    for i, (f, w, x, y, z, g, k, sh) in enumerate(steps):
        rnd = i % 4  # step i uses (a,b,c,d) rotated right i times: abcd, dabc, cdab, bcda
        want_regs = regs[-rnd:] + regs[:-rnd] if rnd else regs
        if f != f"F{i // 16 + 1}":
            sys.exit(f"FAIL {p}: step {i} uses {f}")
        if w + x + y + z != want_regs:
            sys.exit(f"FAIL {p}: step {i} register order {w}{x}{y}{z}")
        g_want = (i, (5 * i + 1) & 15, (3 * i + 5) & 15, (7 * i) & 15)[i // 16]
        if int(g) != g_want:
            sys.exit(f"FAIL {p}: step {i} reads in[{g}], loop form wants in[{g_want}]")
        if int(sh) != shifts[(i // 16) * 4 + (i & 3)]:
            sys.exit(f"FAIL {p}: step {i} shifts by {sh}")
        consts.append(int(k, 16))
    t_rows = []
    for r in range(0, 64, 4):
        t_rows.append("\t" + ", ".join(f"0x{v:08x}" for v in consts[r:r + 4]) + ",")
    compact = (
        "/* Rivest's T[i] = floor(2^32 * |sin(i+1)|), in the order the 64 unrolled\n"
        "   MD5STEP lines used them. Lifted from those lines by apply-mods.py\n"
        "   md5_compact, which also checked their input-word and shift schedule\n"
        "   against the closed forms below. The unrolled transform was 2 176 bytes\n"
        "   of ARM code; this is a 64-pass loop over 272 bytes of tables\n"
        "   (lean-wpa3, 2026-11-08). */\n"
        "static const uint32_t md5_t[64] = {\n" + "\n".join(t_rows) + "\n};\n"
        "static const uint8_t md5_s[16] = {\n"
        "\t7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21\n};\n\n"
        + head +
        "\tuint32_t a = buf[0], b = buf[1], c = buf[2], d = buf[3];\n"
        "\tuint32_t i;\n\n"
        "\tfor (i = 0; i < 64; i++) {\n"
        "\t\tuint32_t f, g, s, t;\n"
        "\t\tif (i < 16)      { f = d ^ (b & (c ^ d)); g = i; }\n"
        "\t\telse if (i < 32) { f = c ^ (d & (b ^ c)); g = (5 * i + 1) & 15; }\n"
        "\t\telse if (i < 48) { f = b ^ c ^ d;         g = (3 * i + 5) & 15; }\n"
        "\t\telse             { f = c ^ (b | ~d);      g = (7 * i) & 15; }\n"
        "\t\ts = md5_s[((i >> 2) & 12) | (i & 3)];\n"
        "\t\tt = a + f + md5_t[i] + in[g];\n"
        "\t\ta = d; d = c; c = b;\n"
        "\t\tb += (t << s) | (t >> (32 - s));\n"
        "\t}\n\n"
        "\tbuf[0] += a;\n\tbuf[1] += b;\n\tbuf[2] += c;\n\tbuf[3] += d;\n}\n"
    )
    s = s[:start] + compact + s[end + 3:]
    write_flat(p, s, nl)
    print(f"[ok]   {p}: MD5Transform is a 64-pass loop (T lifted from the unrolled steps)")


def _parse_byte_table(src: str, decl: str, path: Path) -> tuple[int, int, list[int]]:
    """(start, end, values) of `decl ... = { ... };` in src."""
    start = src.find(decl)
    if start < 0:
        sys.exit(f"FAIL {path}: {decl!r} not found")
    end = src.find("};", start)
    if end < 0:
        sys.exit(f"FAIL {path}: end of {decl!r} not found")
    end += 2
    body = src[src.index("{", start) + 1:end - 2]
    vals = [int(x, 0) for x in re.findall(r"0x[0-9a-fA-F]+|\b\d+\b", body)]
    return start, end, vals


def patch_share_sbox(root: Path) -> None:
    """One AES S-box instead of two.

    crypto/aes128.c (the decrypt side eapol.c unwraps the GTK with) and
    crypto/cmac.c (the forward AES that BIP and SAE's CMAC need) each carry
    their own 256-byte forward S-box: .rodata.aes_sbox and .rodata.sbox,
    byte-identical, 512 bytes of flash in the lean-wpa3 .map (2026-11-08).
    This exports aes128.c's and points cmac.c at it. The step compares the
    two tables before it deletes one; the code around them is untouched.

    Done here rather than in crypto/ so that `lean` and `lean-wpa3`, which
    do not list this step, rebuild exactly as before.
    """
    aes = root / "src/net/aes128.c"
    cmac = root / "src/net/cmac.c"
    a, anl = read_flat(aes)
    c, cnl = read_flat(cmac)
    if "#define sbox aes_sbox" in c:
        print(f"[skip] {cmac}: already shares aes128.c's S-box")
        return
    _, _, aes_vals = _parse_byte_table(a, "static const uint8_t aes_sbox[256] = {", aes)
    cs, ce, cmac_vals = _parse_byte_table(c, "static const uint8_t sbox[256] = {", cmac)
    if len(aes_vals) != 256 or aes_vals != cmac_vals:
        sys.exit(f"FAIL share_sbox: the two S-boxes differ ({len(aes_vals)} vs "
                 f"{len(cmac_vals)} values) -- refusing to merge them")
    c = c[:cs] + (
        "/* The forward S-box is aes128.c's (apply-mods.py share_sbox): the\n"
        "   two tables were byte-identical and the second one cost 256 bytes\n"
        "   of flash (lean-wpa3, 2026-11-08). */\n"
        "extern const uint8_t aes_sbox[256];\n"
        "#define sbox aes_sbox"
    ) + c[ce:]
    a = a.replace(
        "static const uint8_t aes_sbox[256] = {",
        "/* Shared with cmac.c -- see apply-mods.py share_sbox. */\n"
        "const uint8_t aes_sbox[256] = {", 1)
    write_flat(cmac, c, cnl)
    write_flat(aes, a, anl)
    print(f"[ok]   {cmac}: uses aes128.c's S-box (tables compared equal, 256 B dropped)")


def patch_prune_dead_funs(root: Path) -> None:
    """Delete the bootloader functions nothing references.

    The Metal compiler emits every `fun` whether or not anything calls it.
    A reachability walk over the compiled lean-wpa3 bootloader (every
    OPint/OPintb immediate that equals a function index, from `main`) found
    three unreachable functions, and a reference count over the source adds
    two whose only other mention is their own recursion:

        listnth        def + 1 self-call          42 B
        itoanil        def only                   24 B
        unregudp       def only                   17 B
        wifiConnected  def only                   25 B
        httpgetheader  the SECOND definition (line ~1773): the one call,
                       in findsize, comes before it and binds to the
                       first, so the redefinition is never used  49 B

    The counts are re-checked here before anything is deleted: a tree where
    a caller has appeared fails the step -- and so does mtl_comp, which is
    how the first draft of this list (it named the first httpgetheader) was
    caught: "unknown label 'httpgetheader'". ~160 bytes, measured with
    mtl_comp (2026-11-08). The `prune_orphans` step of phase8-rom covered a
    different list and needs rom_pages first.
    """
    boot = root / "mtl/boot/boot.0.0.0.13.mtl"
    s = boot.read_bytes().decode("latin-1")
    if "/* dead-funs-pruned */" in s:
        print(f"[skip] {boot}: dead functions already pruned")
        return
    eol = "\r\n" if "\r\n" in s else "\n"

    def block(src: str, head: str, nth: int = 1) -> tuple[int, int]:
        start = -1
        for _ in range(nth):
            start = src.find(head, start + 1)
            if start < 0:
                return -1, -1
        i = start + len(head)
        n = len(src)
        while i < n - 1:
            if src[i:i + 2] == ";;":
                j = i + 2
                while j < n and src[j] in " \t":
                    j += 1
                if j < n and src[j] in "\r\n":
                    j += 2 if src[j:j + 2] == "\r\n" else 1
                    return start, j
            i += 1
        return -1, -1

    targets = [
        ("listnth", 2, "fun listnth l i=", 1),
        ("itoanil", 1, "fun itoanil l=", 1),
        ("unregudp", 1, "fun unregudp port=", 1),
        ("wifiConnected", 1, "fun wifiConnected=", 1),
        ("httpgetheader", 3, "fun httpgetheader res =", 2),
    ]
    for name, expected, _, _ in targets:
        n_refs = len(re.findall(rf"\b{name}\b", s))
        if n_refs != expected:
            sys.exit(f"FAIL prune_dead_funs: `{name}` has {n_refs} refs, expected "
                     f"{expected} -- a caller appeared, not deleting it")
    if s.count("fun httpgetheader res =") != 2:
        sys.exit("FAIL prune_dead_funs: expected two httpgetheader definitions")
    for name, _, head, nth in targets:
        start, end = block(s, head, nth)
        if start < 0:
            sys.exit(f"FAIL prune_dead_funs: could not delimit `{head}`")
        s = s[:start] + s[end:]
    s = f"/* dead-funs-pruned */{eol}" + s
    boot.write_bytes(s.encode("latin-1"))
    print(f"[ok]   {boot}: pruned {len(targets)} unreferenced functions")


def patch_opt_size(root: Path) -> None:
    """Compile at -Os instead of -O1. A MEASUREMENT step, in no shipped mode.

    Every hardware-validated image was built at -O1; a flag change rewrites
    every function, and a layout shift alone has been enough to wake an old
    bug on this project. The step exists so the saving can be measured and
    written down, not so it can be flashed.
    """
    p = root / "Makefile"
    s = p.read_text()
    if "CFLAGS += -Os\n" in s:
        print(f"[skip] {p}: already -Os")
        return
    if "CFLAGS += -O1\n" not in s:
        sys.exit(f"FAIL {p}: 'CFLAGS += -O1' anchor missing")
    p.write_text(s.replace("CFLAGS += -O1\n", "CFLAGS += -Os\n", 1))
    print(f"[ok]   {p}: -O1 -> -Os (measurement build only)")


def patch_violet_hardening(root: Path) -> None:
    """Small robustness fixes in Violet-original C, from the audit.

    - ieee80211.c: the RSN case of the beacon scan advanced frame_current by
      the element length and the loop advanced it again, so every element
      after an RSN one was misparsed. The extra advance is dropped.
    - vlog.c sysCmp: when both ends run short it compared the LONGER of the two
      remainders, reading past the shorter. It compares the shorter now.
    - vaudio.c mydecodeinit: the ADPCM step index came from the data unclamped,
      so a crafted resource over-read the 89-entry step table. Clamped to 0..88.
    - vinterp.c OPstrsub/OPstrcpy/OPload: `start+len>size` overflows for large
      signed values and lets a huge length through; rephrased as
      `len>size-start`, which cannot overflow.

    All Violet-original. Idempotent.
    """
    # 1. The beacon RSN double-advance.
    p = root / "src/net/ieee80211.c"
    s = read_src(p)
    if "/* violet-hardening */" not in s:
        nl = "\r\n" if "\r\n" in s else "\n"
        old = ("                DBG_WIFI(\"WPA2 supported\"EOL);" + nl +
               "                frame_current += frame_current[1];" + nl)
        if s.count(old) != 1:
            sys.exit(f"FAIL {p}: the RSN double-advance is not where this patch expects it")
        s = s.replace(old, "                DBG_WIFI(\"WPA2 supported\"EOL);  /* violet-hardening: the loop advances, do not advance twice */" + nl, 1)
        write_src(p, s)
        print(f"[ok]   {p}: the beacon RSN case no longer double-advances")

    # 2. sysCmp compares the shorter remainder.
    p = root / "src/vm/vlog.c"
    s = read_src(p)
    if "/* violet-hardening */" not in s:
        nl = "\r\n" if "\r\n" in s else "\n"
        old = "    if (ldst-i>lsrc-j)" + nl + "      len=ldst-i;"
        if s.count(old) != 1:
            sys.exit(f"FAIL {p}: sysCmp is not the shape this patch expects")
        s = s.replace(old, "    if (ldst-i<lsrc-j)  /* violet-hardening: the shorter, not the longer */" + nl + "      len=ldst-i;", 1)
        write_src(p, s)
        print(f"[ok]   {p}: sysCmp no longer reads past the shorter string")

    # 3. ADPCM step index clamped.
    p = root / "src/vm/vaudio.c"
    s = read_src(p)
    if "/* violet-hardening */" not in s:
        nl = "\r\n" if "\r\n" in s else "\n"
        old = "  PredictedValue=s0;" + nl + "  StepIndex=st;"
        if s.count(old) != 1:
            sys.exit(f"FAIL {p}: mydecodeinit is not the shape this patch expects")
        s = s.replace(old, "  PredictedValue=s0;" + nl + "  if(st<0)st=0;else if(st>88)st=88;  /* violet-hardening */" + nl + "  StepIndex=st;", 1)
        write_src(p, s)
        print(f"[ok]   {p}: the ADPCM step index is clamped to the table")

    # 4. The string-op length overflow (OPstrsub/OPstrcpy/OPload).
    p = root / "src/vm/vinterp.c"
    s = read_src(p)
    if "/* violet-hardening */" not in s:
        old = "if (start+len>VSIZEBIN(q)) len=VSIZEBIN(q)-start;"
        n = s.count(old)
        if n < 1:
            sys.exit(f"FAIL {p}: the string-length clamp is not where this patch expects it")
        s = s.replace(old, "if (len>VSIZEBIN(q)-start) len=VSIZEBIN(q)-start;  /* violet-hardening: no start+len overflow */")
        write_src(p, s)
        print(f"[ok]   {p}: {n} string-length clamps cannot overflow")


def patch_strip_factory_test(root: Path) -> None:
    """Remove Violet's factory test mode from the bootloader (2.8 KB of flash).

    Holding the head button for more than seven seconds at power-on in config
    mode turned the rabbit white and mute, then each click ran a production
    test (LEDs, ears, RFID, a tone, the microphone). Nobody uses it and it is
    a trap for whoever holds the button too long. With it go the RECLIB block
    (record to a RIFF, only the microphone test called it), the tone
    generator and the `midi_start` blob whose one use is commented out.

    What config mode did besides is kept exactly: `loop` still waits for the
    button to be released, turns the LEDs blue and only then runs the Wi-Fi.

    Patches boot.0.0.0.13.mtl. Idempotent.
    """
    p = root / "mtl/boot/boot.0.0.0.13.mtl"
    s, nl = read_flat(p)
    if "const TESTDELAY=7000;;" not in s:
        print(f"[skip] {p}: the factory test mode is already gone")
        return

    def cut(text: str, start: str, end: str, what: str, keep_start: bool = False) -> str:
        if text.count(start) != 1:
            sys.exit(f"FAIL {p}: expected one {what}, found {text.count(start)}")
        i = text.index(start)
        j = text.index(end, i) + len(end)
        return text[:i + (len(start) if keep_start else 0)] + text[j:]

    # The record library: its last function is recriff, closed by the ifdef's brace.
    i = s.index("ifdef RECLIB {") if s.count("ifdef RECLIB {") == 1 else sys.exit(
        f"FAIL {p}: the RECLIB block is not where this patch expects it")
    j = s.index("}\n", s.index("fun recriff", i)) + 2
    s = s[:i] + s[j:]
    # The MIDI blob.
    s = cut(s, "var midi_start=", ";;\n", "midi_start")
    # The test variables, the tone generator and its callback; `start` stays.
    s = cut(s, "var start;;\n", "const TESTDELAY=7000;;\n", "test variable block", keep_start=True)
    # The test branches of loop.
    head = "\telse if start!=nil then\n"
    if s.count(head) != 1 or "fun main=" not in s:
        sys.exit(f"FAIL {p}: loop is not the shape this patch expects")
    i = s.index(head)
    j = s.index("fun main=", i)
    s = s[:i] + (
        "\telse if start!=nil then\n"
        "\t(\n"
        "\t\tif !button2 then\n"
        "\t\t(\n"
        "\t\t\tsetleds 0xff;\n"
        "\t\t\tset start=nil\n"
        "\t\t)\n"
        "\t)\n"
        "\telse wifiRun;\n"
        "\t0;;\n\n\n") + s[j:]
    write_flat(p, s, nl)
    print(f"[ok]   {p}: factory test mode, record library and MIDI blob removed")


def patch_strip_eapol_dump(root: Path) -> None:
    """Drop the hex dumps eapol.c sends to the serial port during the handshake.

    Every message 1/4 was dumped whole to a UART no production rabbit has a pad
    for, and so were both MICs on a mismatch, the group key and the key data
    (key material on a serial line, were one wired). The calls go; `dump` and
    its hex printer then fall to --gc-sections, and the handshake no longer
    waits on the UART.

    Patches eapol.c. Idempotent.
    """
    p = root / "src/net/eapol.c"
    s, nl = read_flat(p)
    lines = s.split("\n")
    kept = [ln for ln in lines if not re.fullmatch(r"\s*dump\([^;]*\);\s*", ln)]
    n = len(lines) - len(kept)
    if n == 0:
        print(f"[skip] {p}: no dump() call left")
        return
    write_flat(p, "\n".join(kept), nl)
    print(f"[ok]   {p}: {n} dump() calls to the UART removed")


def patch_uart_silent(root: Path) -> None:
    """Build the VM with HIDE_VM_OUTPUT: nothing is written to the serial port.

    vlog.c already has the switch -- consolestr/consoleint/... become no-ops --
    upstream just leaves it commented out in the Makefile. With it, logGC (three
    lines of text at EVERY garbage collection), Secho and Iecho stop formatting
    for a port nobody reads on a production rabbit, and the UART print helpers
    fall to --gc-sections. The DCC telemetry is a different channel and stays.
    Only the ARM Makefile changes; the emulator has its own and keeps its console.

    Idempotent.
    """
    p = root / "Makefile"
    s = p.read_text()
    live = "OPTIONS += -DHIDE_VM_OUTPUT\n"
    if "#" + live not in s:
        if live in s:
            print(f"[skip] {p}: HIDE_VM_OUTPUT already defined")
            return
        sys.exit(f"FAIL {p}: the HIDE_VM_OUTPUT line is not where this patch expects it")
    p.write_text(s.replace("#" + live, live, 1))
    print(f"[ok]   {p}: HIDE_VM_OUTPUT defined, the VM writes nothing to the UART")


def patch_no_tkip_code(root: Path) -> None:
    """Remove the pairwise-TKIP code no_tkip_pairwise made unreachable (~1.6 KB).

    Since no_tkip_pairwise the rabbit refuses a network whose only pairwise
    cipher is TKIP, and message 1/4 is refused before any computation. What
    served that cipher stayed linked: the HMAC-MD5 MIC (five `else hmac_md5`
    branches, the only callers of MD5 in C), the RC4 unwrap of the group key
    in the group handshake, the pairwise TKIP key install, and the TKIP IV on
    transmit. They go, and MD5 and rc4.o fall to --gc-sections.

    The five MIC branches become `return`, not nothing: on the receive side an
    empty branch would leave the zeroed MIC field to be compared with the
    frame's own, and a frame carrying a zero MIC would pass. Unreachable, but
    it fails closed this way.

    Kept: the TKIP *group* key of a mixed WPA/WPA2 AP, which arrives AES-wrapped
    in a KDE and is installed by eapol_install_msg3_gtk.

    Requires no_tkip_pairwise. Patches eapol.c and ieee80211.c. Idempotent.
    """
    p = root / "src/net/eapol.c"
    s, nl = read_flat(p)
    if "/* no_tkip_code */" in s:
        print(f"[skip] {p}: the pairwise-TKIP code is already gone")
    else:
        s, n = re.subn(r"\n  else\n    hmac_md5\([^;]*\);\n",
                       "\n  else\n    return;  /* no_tkip_code */\n", s)
        if n != 5:
            sys.exit(f"FAIL {p}: expected 5 HMAC-MD5 MIC branches, found {n}")
        old = ("    else\n"
               "      rt2501_set_key(0, ptk.s.tk, ptk.s.mick.tx, ptk.s.mick.rx, RT2501_CIPHER_TKIP);\n")
        if s.count(old) != 1:
            sys.exit(f"FAIL {p}: the pairwise TKIP key install is not where this patch expects it")
        s = s.replace(old, "", 1)
        a = "    case IEEE80211_CIPHER_TKIP: // Decrypt RC4\n"
        b = "    case IEEE80211_CIPHER_CCMP:\n"
        if s.count(a) != 1 or b not in s[s.index(a):]:
            sys.exit(f"FAIL {p}: the RC4 group-key case is not where this patch expects it")
        i = s.index(a)
        s = s[:i] + s[s.index(b, i):]
        for decl in ("  uint8_t key[32];\n  struct rc4_context rc4;\n", '#include "net/rc4.h"\n'):
            if s.count(decl) != 1:
                sys.exit(f"FAIL {p}: expected one {decl.strip()!r}")
            s = s.replace(decl, "", 1)
        write_flat(p, s, nl)
        print(f"[ok]   {p}: HMAC-MD5 MIC, RC4 unwrap and pairwise TKIP install removed")

    p = root / "src/net/ieee80211.c"
    s, nl = read_flat(p)
    a = ("          case IEEE80211_CIPHER_TKIP:\n"
         "            {\n"
         "              struct ieee80211_tkip_iv iv;\n")
    b = ("          case IEEE80211_CIPHER_CCMP:\n"
         "            {\n"
         "              struct ieee80211_ccmp_iv iv;\n")
    if a not in s:
        print(f"[skip] {p}: no TKIP IV on transmit")
        return
    if s.count(a) != 1 or s.count(b) != 1 or s.index(b) < s.index(a):
        sys.exit(f"FAIL {p}: the transmit IV switch is not the shape this patch expects")
    s = s[:s.index(a)] + s[s.index(b):]
    write_flat(p, s, nl)
    print(f"[ok]   {p}: TKIP IV on transmit removed")


def patch_stub_soft_codecs(root: Path) -> None:
    """Stub the four software audio codec opcodes nothing calls (~3 KB).

    adp2wav, wav2adp, alaw2wav and wav2alaw convert between PCM, IMA ADPCM and
    A-law/mu-law on the ARM7. The microphone is encoded by the VS1003 in
    hardware, and neither the bootloader nor the runtime bytecode names them
    (reclib.mtl's only calls sit in a block comment). The opcodes stay -- a
    bytecode that used one must not run into a missing case -- but each now
    drops its arguments and answers nil; the codecs and their three tables
    (alaw, mulaw, exposant) fall to --gc-sections. The ADPCM decoder recVol
    uses is not touched.

    Patches vinterp.c. Idempotent.
    """
    p = root / "src/vm/vinterp.c"
    s, nl = read_flat(p)
    if "/* stub_soft_codecs */" in s:
        print(f"[skip] {p}: the software codec opcodes are already stubs")
        return
    for op, nargs in (("OPadp2wav", 5), ("OPwav2adp", 5), ("OPalaw2wav", 6), ("OPwav2alaw", 6)):
        head = f"      case {op}:\n"
        if s.count(head) != 1:
            sys.exit(f"FAIL {p}: expected one case {op}, found {s.count(head)}")
        i = s.index(head)
        end = "        break;\n"
        j = s.index(end, i) + len(end)
        if f"Audio{op[2].upper()}{op[3:]}(" not in s[i:j]:
            sys.exit(f"FAIL {p}: case {op} is not the shape this patch expects")
        s = (s[:i] + head +
             f"        VDROPN({nargs - 1}); VSTACKSET(0,NIL);  /* stub_soft_codecs */\n" +
             end + s[j:])
    write_flat(p, s, nl)
    print(f"[ok]   {p}: adp2wav/wav2adp/alaw2wav/wav2alaw answer nil; the codecs are unlinked")


def patch_no_wep(root: Path) -> None:
    """Remove WEP: the rabbit no longer joins a WEP network (~750 B).

    WEP has been broken since 2001 and a key falls in minutes. A network
    configured as WEP is now refused where a TKIP-only one already is (IDLE,
    the bootloader retries; never a fall-back to an open association), and
    what served it goes: shared-key authentication (the challenge reply), the
    WEP key install, the WEP IV on transmit, the cipher mapping, and in the
    bootloader the hex key parser, the WEP radio button and the "WEP auth"
    select of the config page. A scan still labels such a network "WEP".

    The bootloader half is no_wep_boot. Requires no_tkip_pairwise (it extends
    that refusal). Patches ieee80211.c. Idempotent.
    """
    p = root / "src/net/ieee80211.c"
    s, nl = read_flat(p)
    if "/* no_wep */" in s:
        print(f"[skip] {p}: WEP is already gone")
    else:
        def sub1(pattern: str, repl: str, text: str, what: str) -> str:
            out, n = re.subn(pattern, lambda m: repl, text, flags=re.S)
            if n != 1:
                sys.exit(f"FAIL {p}: expected one {what}, found {n}")
            return out

        # 1. Refuse the network, beside the TKIP refusal.
        s = sub1(r"\tif\(\(ieee80211_encryption&0xF0\) == IEEE80211_CRYPT_WPA\n",
                 "\tif((ieee80211_encryption&0xF0) == IEEE80211_CRYPT_WEP  /* no_wep */\n"
                 "\t   || (ieee80211_encryption&0xF0) == IEEE80211_CRYPT_WPA\n",
                 s, "TKIP refusal (no_tkip_pairwise)")
        # 2. The key install in the association switch.
        s = sub1(r"[ \t]*case IEEE80211_CRYPT_WEP:\n[ \t]*ieee80211_authmode = authmode;\n.*?"
                 r"RT2501_CIPHER_WEP64\);\n[ \t]*\}\n[ \t]*break;\n",
                 "", s, "WEP key install")
        # 3. Shared-key authentication: the two functions, their call, the request.
        a = "static void ieee80211_send_challenge_reply("
        b = "static void ieee80211_send_probe_response("
        if s.count(a) != 1 or s.count(b) != 1 or s.index(b) < s.index(a):
            sys.exit(f"FAIL {p}: the shared-key functions are not where this patch expects them")
        s = s[:s.index(a)] + s[s.index(b):]
        s = sub1(r"[ \t]*/\* Shared key authentication \*/\n.*?ieee80211_input_shared_auth\(.*?\);\n",
                 "\t\t\t\t\t\tieee80211_state = IEEE80211_S_IDLE;  /* no shared-key auth */\n",
                 s, "shared-key auth call")
        s = sub1(r"\t\} else \{\n[ \t]*/\* Shared key authentication: algo\[2\], seq\[2\], status\[2\] \*/\n"
                 r"(?:[ \t]*auth->auth\[\d\] = [^\n]*SHARED[^\n]*\n){4}\t\}\n",
                 "\t}\n", s, "shared-key auth request")
        # 4. Transmit: the IV and the overhead.
        s = sub1(r"[ \t]*case IEEE80211_CRYPT_WEP:\n[ \t]*fr->txd\.Iv = nab_rand\(\) & 0x00ffffff;\n"
                 r"[ \t]*fr->txd\.IvOffset = sizeof\(struct ieee80211_frame\);\n[ \t]*break;\n",
                 "", s, "WEP IV on transmit")
        s = sub1(r"[ \t]*case IEEE80211_CRYPT_WEP:\n[ \t]*encryption_overhead = 8;[^\n]*\n[ \t]*break;\n",
                 "", s, "WEP overhead")
        # 5. The cipher mapping (its default fell through into WPA).
        s = sub1(r"[ \t]*case IEEE80211_CRYPT_WEP:\n[ \t]*switch\(crypt\)\n.*?Unknown WEP Crypt\"\);\n"
                 r"[ \t]*break;\n[ \t]*\};\n",
                 "", s, "WEP cipher mapping")
        if "IEEE80211_CRYPT_WEP64" in s or "CIPHER_WEP" in s:
            sys.exit(f"FAIL {p}: a WEP use is left behind")
        write_flat(p, s, nl)
        print(f"[ok]   {p}: WEP refused; shared-key auth, key install and IV removed")


def patch_no_wep_boot(root: Path) -> None:
    """The bootloader half of no_wep: no WEP key parser, no WEP in the config page.

    Separate because the emulator builds the C with a bootloader lifted from a
    firmware image, and its own tree keeps the stock pages. Requires
    modernize_pages (it edits that page). Patches boot.0.0.0.13.mtl. Idempotent.
    """
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t, nl = read_flat(q)
    if "fun wifiwepkey val=" not in t:
        print(f"[skip] {q}: no WEP left in the bootloader")
        return
    i = t.index("fun _wifiwepkey val i len=")
    j = t.index("fun wifiAuth=", i)
    t = t[:i] + t[j:]
    for old, new, what in (
        ("\t\tlet if crypt==IEEE80211_CRYPT_WEP then confGetWifiauth else 0-> auth in\n"
         "\t\tlet if crypt==IEEE80211_CRYPT_WEP then wifiwepkey confGetWifikey0\n"
         "\t\t\telse if crypt==IEEE80211_CRYPT_WPA || crypt==IEEE80211_CRYPT_WPA2 then confGetWifipmk -> key in\n",
         "\t\tlet 0-> auth in\n"
         "\t\tlet if crypt==IEEE80211_CRYPT_WPA || crypt==IEEE80211_CRYPT_WPA2 then confGetWifipmk -> key in\n",
         "wifiAuth key choice"),
        ('<label><input type=radio name=m "::"<ENC-WEP>"::" value=\\""::"<ENC-VWEP>"::"\\">WEP</label>\n', "",
         "WEP radio button"),
        ('<label><small>WEP auth</small><select name=l>"::"<AUTH>"::"</select></label>\n', "",
         "WEP auth select"),
        ("static IP, WEP auth</summary>", "static IP</summary>", "Advanced summary"),
        ('\t\t\t["<ENC-WEP>" if crypt==IEEE80211_CRYPT_WEP then "checked"]::\n', "", "ENC-WEP marker"),
        ('\t\t\t["<ENC-VWEP>" itoa IEEE80211_CRYPT_WEP]::\n', "", "ENC-VWEP marker"),
        ('\t\t\t["<AUTH>" webSelectList auth [IEEE80211_AUTH_OPEN "OpenSystem"]::[IEEE80211_AUTH_SHARED "SharedKey"]::nil]::\n',
         "", "AUTH marker"),
    ):
        if t.count(old) != 1:
            sys.exit(f"FAIL {q}: expected one {what}, found {t.count(old)}")
        t = t.replace(old, new, 1)
    write_flat(q, t, nl)
    print(f"[ok]   {q}: WEP key parser, radio button and auth select removed")


def patch_share_page_css(root: Path) -> None:
    """One stylesheet for the four config pages instead of four (~1.9 KB).

    modernize_pages gives each page its own <style>, 4.5 KB in all, most of it
    the same charter written four times. pages/shared.css is their union
    (2.9 KB); it is stored once as `page_css` and spliced into each page when
    it is served. page_a is already a list of fragments filled by pagefill, so
    the stylesheet is one more marker; the three plain pages become functions that strcatlist their two
    halves around it, built per request like page_a and collected after.

    What differed between the pages is carried by three hooks: `class=c` on
    the body of the two centred pages (done, error), `class=e` on the error
    title, and a padding on the upgrade page's fieldset.

    Requires modernize_pages; run it after every step that edits the pages.
    Patches boot.0.0.0.13.mtl. Idempotent.
    """
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t, nl = read_flat(q)
    if "var page_css=" in t:
        print(f"[skip] {q}: the pages already share one stylesheet")
        return
    css = (Path(__file__).resolve().parent / "pages" / "shared.css").read_text().strip()
    if '"' in css or "\\" in css:
        sys.exit("FAIL pages/shared.css: a quote or a backslash would need escaping in the Metal literal")
    block = re.compile(r"<style>\n.*?\n</style>", re.S)
    before = len(t)

    # page_a: a list; the stylesheet becomes one of its fragments.
    m = re.search(r'^var page_a="(.*?)::nil;;[ \t]*\n', t, re.M | re.S)
    if m is None or len(block.findall(m.group(0))) != 1:
        sys.exit(f"FAIL {q}: page_a is not the modernised page this patch expects")
    # (A global's initialiser cannot name another global, so the fragment is a
    # <STYLE> marker and pagefill, which already walks page_a, fills it in.)
    t = t[:m.start()] + block.sub(lambda _: '"::"<STYLE>"::"', m.group(0), count=1) + t[m.end():]
    fill = '["<FIRMWARE>" webip FIRMWARE]::\n'
    if t.count(fill) != 1:
        sys.exit(f"FAIL {q}: the pagefill marker list is not where this patch expects it")
    t = t.replace(fill, fill + '\t\t\t["<STYLE>" page_css]::\n', 1)

    # The three plain pages: string -> function around the stylesheet.
    hooks = {
        "page_done": (("<body>", "<body class=c>"),),
        "page_u": (("<fieldset>", "<fieldset style=padding:16px>"),),
        "page_error": (("<body>", "<body class=c>"), ("<h1>", "<h1 class=e>")),
    }
    for name, subs in hooks.items():
        m = re.search(rf'^var {name}="(.*?)";;[ \t]*\n', t, re.M | re.S)
        if m is None or len(block.findall(m.group(1))) != 1:
            sys.exit(f"FAIL {q}: {name} is not the modernised page this patch expects")
        head, tail = block.split(m.group(1), maxsplit=1)
        for old, new in subs:
            if tail.count(old) != 1:
                sys.exit(f"FAIL {q}: expected one {old} in {name}, found {tail.count(old)}")
            tail = tail.replace(old, new, 1)
        t = (t[:m.start()] + f'fun {name}=strcatlist "{head}"::page_css::"{tail}"::nil;;\n'
             + t[m.end():])

    i = t.index("var page_a=")
    t = t[:i] + f'var page_css="<style>\n{css}\n</style>";;\n\n' + t[i:]
    write_flat(q, t, nl)
    print(f"[ok]   {q}: four stylesheets -> one ({before} -> {len(t)} source bytes)")


def patch_http_cap(root: Path) -> None:
    """The bootloader drops an HTTP_NORMAL answer past HTTP_MAX_NORMAL bytes.

    The bootloader's HTTP client (boot.0.0.0.13.mtl, `tcpread`) kept every
    segment of an HTTP_NORMAL answer in `httpreq.inputH` until the
    connection closed, with no limit; bc.jsp and kx.jsp are fetched that
    way, in plain HTTP unless the owner confirmed a key. A server, or any
    host on the path, that answers with more than the VM's 204,800-word
    heap holds runs it out of memory: a reset, and a Nabaztag comes back
    from a soft reset with its radio asleep. Measured in the emulator
    (scenarios/vm/http-big-boot.txt, the gateway's `http-big`), on the
    lean-reclaim bootloader of 2026-11-11: a megabyte resets the rabbit at
    768 KB received; so does a complete answer of 220,000 bytes (the
    segments, then two copies of the body, with the previous attempt's
    still live); 180,000 bytes do not.

    Now `Httpreq` carries `sizeH`, the bytes received so far. Past
    HTTP_MAX_NORMAL the segments are let go, the connection is closed and
    the callback gets nil -- what it gets when the name does not resolve or
    the write fails, and `getbytecode nil` is nil -- so boot_loop asks again
    after BOOT_HTTPTIMEOUT as it does for any answer that is not a
    bytecode. Segments still in flight on the closed socket and its close
    event are ignored, so the callback runs once.

    HTTP_MAX_NORMAL is 163840 (160 KB). The largest legitimate answer is
    bc.jsp: 112,349 bytes on 2026-11-11 for a 112,232-byte runtime
    (tools/plain_warren.py; an HMAC trailer adds 68), against 119 for
    kx.jsp. The ceiling leaves the bytecode 46% to grow and stays under
    the 180,000 bytes this heap was measured to hold -- six answers of
    163,059 bytes in two minutes, no reset. A bytecode past that could not
    be fetched by this bootloader with or without a ceiling.

    Not touched: bc.jsp over TLS (boot_tls's boottls_take keeps its own
    list -- the peer there holds the adoption key and serves the bytecode
    anyway), the HTTP_STREAM header block, and the config-mode server's
    upload buffer, which has to take a 250 KB .sim.

    The runtime bytecode has its own copy of this client and its own fix:
    bytecode/patch_http_cap.py. Idempotent.
    """
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t, nl = read_flat(q)
    if "HTTP_MAX_NORMAL" in t:
        print(f"[skip] {q}: HTTP_NORMAL answers are already capped")
        return
    for old, new, what in (
        ("type Httpreq=[cnxH inputH outputH indexH cbH typeH stateH startH];;\n",
         "// http_cap: an HTTP_NORMAL answer is kept whole until the connection\n"
         "// closes; past this many bytes the request is dropped (apply-mods.py).\n"
         "const HTTP_MAX_NORMAL=163840;;\n"
         "type Httpreq=[cnxH inputH outputH indexH cbH typeH stateH startH sizeH];;\n"
         "fun httpover httpreq= httpreq.sizeH!=nil && httpreq.sizeH>HTTP_MAX_NORMAL;;\n",
         "Httpreq type"),
        ("\t\tif httpreq.typeH==HTTP_NORMAL then call httpreq.cbH [httpreq strcatlist rev httpreq.inputH nil]"
         "\t// on retourne ce qui a \u00e9t\u00e9 re\u00e7u\n",
         "\t\tif httpreq.typeH==HTTP_NORMAL then\n"
         "\t\t(\n"
         "\t\t\tif httpover httpreq then nil\t// http_cap: already answered with nil\n"
         "\t\t\telse call httpreq.cbH [httpreq strcatlist rev httpreq.inputH nil]\n"
         "\t\t)\n",
         "HTTP_NORMAL end of answer"),
        ("\t\tif httpreq.typeH==HTTP_NORMAL then set httpreq.inputH=input::httpreq.inputH"
         "\t// on bufferise ce qui a \u00e9t\u00e9 re\u00e7u\n",
         "\t\tif httpreq.typeH==HTTP_NORMAL then\n"
         "\t\t(\n"
         "\t\t\t// http_cap: count before keeping; past the ceiling the request\n"
         "\t\t\t// is closed and answered once, late segments are dropped.\n"
         "\t\t\tif httpover httpreq then nil\n"
         "\t\t\telse\n"
         "\t\t\t(\n"
         "\t\t\t\tset httpreq.sizeH=(if httpreq.sizeH==nil then 0 else httpreq.sizeH)+strlen input;\n"
         "\t\t\t\tif httpover httpreq then\n"
         "\t\t\t\t(\n"
         "\t\t\t\t\tset httpreq.inputH=nil;\n"
         "\t\t\t\t\tclosetcp cnx;\n"
         "\t\t\t\t\tcall httpreq.cbH [httpreq nil];\n"
         "\t\t\t\t\tnil\n"
         "\t\t\t\t)\n"
         "\t\t\t\telse (set httpreq.inputH=input::httpreq.inputH; nil)\n"
         "\t\t\t)\n"
         "\t\t)\n",
         "HTTP_NORMAL buffering"),
    ):
        if t.count(old) != 1:
            sys.exit(f"FAIL {q}: expected one {what}, found {t.count(old)}")
        t = t.replace(old, new, 1)
    write_flat(q, t, nl)
    print(f"[ok]   {q}: HTTP_NORMAL answers counted, dropped past 160 KB")


def _der(b: bytes, i: int) -> tuple[int, int, int]:
    """Tag, start of the contents, end of the contents of the TLV at i."""
    tag, n = b[i], b[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(b[i:i + k], "big")
        i += k
    return tag, i, i + n


def boot_anchor(der: bytes) -> dict[str, bytes]:
    """The spans nab_tls matches an anchor on, as nab_x509_parse cuts them:
    the subject Name's contents, the SPKI algorithm OID, its curve OID (or
    nothing) and the BIT STRING's contents past the unused-bits byte."""
    _, a, _ = _der(der, 0)                       # Certificate
    _, p, e = _der(der, a)                       # tbsCertificate
    if der[p] == 0xA0:                           # version
        p = _der(der, p)[2]
    for _ in range(4):                           # serial, signature, issuer, validity
        p = _der(der, p)[2]
    t, sp, se = _der(der, p)                     # subject
    assert t == 0x30
    out = {"subject": der[sp:se]}
    t, kp, _ = _der(der, se)                     # SubjectPublicKeyInfo
    t, ap, ae = _der(der, kp)                    # AlgorithmIdentifier
    t, op, oe = _der(der, ap)
    out["alg"] = der[op:oe]
    out["param"] = b""
    if oe < ae and der[oe] == 0x06:
        t, qp, qe = _der(der, oe)
        out["param"] = der[qp:qe]
    t, bp, be = _der(der, ae)
    assert t == 0x03 and der[bp] == 0
    out["key"] = der[bp + 1:be]
    return out


def patch_boot_https(root: Path) -> None:
    """kx.jsp and bc.jsp over HTTPS for the pinned warren: boot-mods/boot_https.mtl.

    The last thing a rabbit sent in the clear was its first boot: kx.jsp and,
    until the owner confirms the colours, bc.jsp (2026-11-13). For one host,
    NAB_BOOT_HTTPS_HOST (terrier.cyberloutre.fr), httprequest goes to port
    443 through nab_tls's session 1, anchored on the root built in from
    NAB_BOOT_ANCHOR (keys/boot_anchor.der, ISRG Root X2: name and key, about
    180 bytes). No fallback for that host; every other host as before.

    The certificates must not have expired before the build date
    (NAB_BOOT_FLOOR, "YYYYMMDDHHMM", the build's UTC minute by default): the
    bootloader has no clock. The emulator proof builds with its own test
    root and host, since nobody but Let's Encrypt can sign under X2.

    Requires tls_certs (the https* opcodes, nab_https_vm.c) and http_cap
    (runs after it: the Httpreq type it rewrites).
    """
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t, nl = read_flat(q)
    if "bhttps_event" in t:
        print(f"[skip] {q}: the pinned warren is already fetched over HTTPS")
        return
    if not (root / "src/net/nab_https_vm.c").exists():
        sys.exit(f"FAIL {q}: tls_certs must run first")
    host = os.environ.get("NAB_BOOT_HTTPS_HOST") or "terrier.cyberloutre.fr"
    floor = os.environ.get("NAB_BOOT_FLOOR") or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M")
    if not re.fullmatch(r"[a-z0-9.-]{1,63}", host) or not re.fullmatch(r"20\d{10}", floor):
        sys.exit(f"FAIL {q}: NAB_BOOT_HTTPS_HOST {host!r} or NAB_BOOT_FLOOR {floor!r} malformed")
    here = Path(__file__).resolve().parent
    der_path = Path(os.environ.get("NAB_BOOT_ANCHOR") or "")
    for cand in ((der_path,) if der_path.name else ()) + (root.parent / "keys/boot_anchor.der", here / "keys/boot_anchor.der"):
        if cand.is_file():
            der_path = cand
            break
    else:
        sys.exit(f"FAIL {q}: no boot anchor (NAB_BOOT_ANCHOR, keys/boot_anchor.der)")
    a = boot_anchor(der_path.read_bytes())
    lines = ["/* boot_https (apply-mods.py): from " + der_path.name + ". Generated. */"]
    for k in ("subject", "alg", "param", "key"):
        v = a[k] or b"\0"   # an empty curve OID: one byte nobody reads (param_len 0 below)
        lines.append(f"static const uint8_t nab_boot_anchor_{k}[{len(v)}] = {{" + ",".join(f"0x{x:02x}" for x in v) + "};")
    lines.append(f"#define NAB_BOOT_ANCHOR_PARAM_LEN {len(a['param'])}")
    h = "\n".join(lines) + "\n"
    (root / "inc/net/nab_boot_anchor.h").write_text(h)
    vm = root / "src/net/nab_https_vm.c"
    v = vm.read_text()
    if "#define NAB_BOOT_ANCHOR 1" not in v:
        vm.write_text("#define NAB_BOOT_ANCHOR 1\n" + v)

    if "HTTP_MAX_NORMAL" not in t:
        if os.environ.get("NAB_SIM"):
            # The emulator's VM: only the C side (the anchor) is built here;
            # its bootloader bytecode comes from the image under test.
            print(f"[ok]   boot_https: anchor {der_path.name} built in (VM, no bootloader)")
            return
        sys.exit(f"FAIL {q}: http_cap must run first")
    body = (here / "boot-mods" / "boot_https.mtl").read_text()
    body = (f'const BOOT_HTTPS_HOST="{host}";;\n'
            f'const BOOT_HTTPS_FLOOR=">{floor}.";;\n' + body)
    for old, new, what in (
        ("type Httpreq=[cnxH inputH outputH indexH cbH typeH stateH startH sizeH];;\n",
         "type Httpreq=[cnxH inputH outputH indexH cbH typeH stateH startH sizeH tlsH];;\n",
         "Httpreq type"),
        ("fun httpsendreq ip x=\n", body + "\nfun httpsendreq ip x=\n", "httpsendreq"),
        ("\t\tset httpreq.cnxH=opentcp netip nil useparamip ip port fixarg4 #tcpevent httpreq;\n",
         "\t\tset httpreq.cnxH=if port==BOOT_HTTPS_PORT then opentcp netip nil useparamip ip port fixarg4 #bhttps_event httpreq\t// boot_https\n"
         "\t\t\telse opentcp netip nil useparamip ip port fixarg4 #tcpevent httpreq;\n",
         "the plain open"),
        ("\tlet cuturl url ->[host port path] in",
         "\tlet cuturl url ->[host port path] in\n"
         "\tlet if port==80 && !strcmp host BOOT_HTTPS_HOST then BOOT_HTTPS_PORT else port -> port in\t// boot_https",
         "httprequest's cuturl"),
    ):
        if t.count(old) != 1:
            sys.exit(f"FAIL {q}: expected one {what}, found {t.count(old)}")
        t = t.replace(old, new, 1)
    write_flat(q, t, nl)
    print(f"[ok]   {q}: {host} over HTTPS, anchor {der_path.name} ({sum(map(len, a.values()))} B), floor {floor}")


def patch_kx_report(root: Path) -> None:
    """The key exchange gets its own time, a second try, and says how it went.

    2026-11-13, Bunny then Nabazprout: on the real ARM7 a key exchange over
    HTTPS does not finish within BOOT_HTTPTIMEOUT (10 s), the one try it had
    per boot; the rabbit then booted without a key, in silence, and stayed
    unadopted. The emulator saw nothing: its clock does not charge the crypto.

    So kx_step waits KX_HTTPTIMEOUT (NAB_KX_HTTPTIMEOUT, seconds) and tries
    KX_TRIES times, and the boot URL carries the outcome, so the warren -- and
    whoever reads its logs -- can tell a rabbit's bug from an owner's slip:
      &k=  0 no exchange (the rabbit has its key), 1 the entropy pool never
           came (KX_WAIT), 2 no answer in time, 3 the request failed
           (connection or TLS), 4 an answer that is not a key, 5 a key
      &kt= the seconds the last attempt took
    """
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t, nl = read_flat(q)
    if "kxwhy" in t:
        print(f"[skip] {q}: the key exchange already reports")
        return
    secs = os.environ.get("NAB_KX_HTTPTIMEOUT") or "60"
    if not secs.isdigit() or not 10 <= int(secs) <= 300:
        sys.exit(f"FAIL {q}: NAB_KX_HTTPTIMEOUT must be 10-300 seconds, got {secs!r}")
    def rep(old: str, new: str, what: str) -> None:
        nonlocal t
        if t.count(old) != 1:
            sys.exit(f"FAIL {q}: expected one {what}, found {t.count(old)} (adoption_boot and boot_marker first)")
        t = t.replace(old, new, 1)
    rep("var kxdone;;\n",
        "var kxdone;;\nvar kxwhy=0;;\nvar kxt=0;;\nvar kxt0=0;;\nvar kxtries=0;;\n"
        f"const KX_HTTPTIMEOUT={secs};;\nconst KX_TRIES=2;;\n", "kxdone")
    rep("fun _kxcbhttp httpreq res=\n",
        "// kx_report: the end of the exchange, and another try after a failure.\n"
        "fun kx_done= set kxdone=1; 0;;\n"
        "fun kx_again=\n"
        "\tset kxtries=kxtries+1;\n"
        "\tset kxhttp=nil;\n"
        "\tif kxtries>=KX_TRIES then kx_done;\n"
        "\t0;;\n"
        "fun _kxcbhttp httpreq res=\n"
        "\tif httpreq!=kxhttp then 0 else\t// an attempt already given up\n", "_kxcbhttp")
    rep("\tlet kx_unhex httpgetcontent res -> w in\n"
        "\tlet if w!=nil then kxFinish w -> r in\n"
        "\tif r!=nil then\n",
        "\tlet kx_unhex httpgetcontent res -> w in\n"
        "\tlet if w!=nil then kxFinish w -> r in\n"
        "\t(\n"
        "\tset kxt=time-kxt0;\n"
        "\tset kxwhy=if res==nil then 3 else if w==nil then 4 else 5;\n"
        "\tif r!=nil then\n", "kx answer")
    rep("\tset kxdone=1;\n\t0;;\n\nfun kx_url",
        "\tif res==nil then kx_again else kx_done;\n\t0\n\t);;\n\nfun kx_url", "kx answer's end")
    rep("\t\t\tif time-kxwait>KX_WAIT then set kxdone=1;\n",
        "\t\t\tif time-kxwait>KX_WAIT then (set kxwhy=1; kx_done);\n", "pool wait")
    rep("\tif time-t0>BOOT_HTTPTIMEOUT then\n"
        "\t(\n"
        "\t\thttpabort kxhttp;\n"
        "\t\tset kxdone=1;\n"
        "\t\t0\n"
        "\t);;\n",
        "\tif time-t0>KX_HTTPTIMEOUT then\n"
        "\t(\n"
        "\t\tlet kxhttp -> h in\n"
        "\t\t(\n"
        "\t\t\tset kxt=time-kxt0;\n"
        "\t\t\tset kxwhy=2;\n"
        "\t\t\tkx_again;\n"
        "\t\t\thttpabort h\n"
        "\t\t);\n"
        "\t\t0\n"
        "\t);;\n", "kx timeout")
    # The start of each attempt, noted here: httpstart of a request already
    # closed read 0 on the rabbit (&kt=0, 2026-11-13).
    rep('\t\telse (set kxhttp=httprequest "GET" (kx_url url pub) nil #_kxcbhttp HTTP_NORMAL; 0);\n',
        '\t\telse (set kxt0=time; set kxhttp=httprequest "GET" (kx_url url pub) nil #_kxcbhttp HTTP_NORMAL; 0);\n',
        "kx request")
    rep('::"&s=1"', '::"&s=1"::"&k="::(itoa kxwhy)::"&kt="::(itoa kxt)', "boot_url's &s=1")
    write_flat(q, t, nl)
    print(f"[ok]   {q}: the key exchange waits {secs} s, tries twice, and reports in &k= and &kt=")


def patch_kx_probe(root: Path) -> None:
    """A test image only: run the key exchange at every boot, key or not.
    Never in a release; needs kx_report.

    The only rabbits on hand have a confirmed key, so they skip the exchange
    that fails on a new one (2026-11-13, Bunny). With this, one of them runs
    it anyway -- the pool, the X25519, the heap, the TLS of the boot -- and
    keeps its own key: the answer is never stored (the warren refuses a second
    key for a confirmed rabbit, 409, and logs kx_contested), and kx_report's
    &k= and &kt= say how it went.
    """
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t, nl = read_flat(q)
    if "fun kx_keep=" in t:
        print(f"[skip] {q}: the key exchange is already probed")
        return
    def rep(old: str, new: str, what: str) -> None:
        nonlocal t
        if t.count(old) != 1:
            sys.exit(f"FAIL {q}: expected one {what}, found {t.count(old)} (kx_report first)")
        t = t.replace(old, new, 1)
    rep("fun kx_done= set kxdone=1; 0;;\n",
        "// kx_probe: the key this rabbit already has, used as if the exchange had\n"
        "// been skipped.\n"
        "fun kx_keep=\n"
        "\tlet strget conf CONF_KXFLAG -> f in\n"
        "\tif f==75 || f==67 then\n"
        "\t(\n"
        "\t\tkxUse strsub conf CONF_KXKEY 32;\n"
        "\t\tif f==75 then set kxshow=time+KX_SHOW;\n"
        "\t\t0\n"
        "\t);\n"
        "\t0;;\n"
        "fun kx_done= kx_keep; set kxdone=1; 0;;\n", "kx_done")
    rep("\tlet if w!=nil then kxFinish w -> r in\n",
        "\tlet strget conf CONF_KXFLAG -> f in\n"
        "\tlet if w!=nil && f!=75 && f!=67 then kxFinish w -> r in\n", "kxFinish")
    rep("\tif f==75 || f==67 then\n"
        "\t(\n"
        "\t\tkxUse strsub conf CONF_KXKEY 32;\n"
        "\t\tif f==75 then set kxshow=time+KX_SHOW;\n"
        "\t\tset kxdone=1;\n"
        "\t\t0\n"
        "\t)\n"
        "\telse if kxhttp==nil then\n",
        "\tif f==0 && 0 then 0\t// kx_probe: no skipping, even with a key\n"
        "\telse if kxhttp==nil then\n", "skip on a stored key")
    write_flat(q, t, nl)
    print(f"[ok]   {q}: the key exchange runs at every boot (TEST IMAGE)")


def patch_owner_unlock(root: Path) -> None:
    """The owner can always leave: an unsigned image, by physical presence.

    Since flash_verify, OPflashFirmware writes only an image signed with our
    key, and the config page refuses anything else: a rabbit on Naboot could
    not go back to Violet's firmware, nor to anyone else's, nor survive the
    day we stop signing -- short of JTAG (Clara, 2026-11-13). Locking an
    owner in is not what the signature is for: it is there against a remote
    takeover, through the network or a warren.

    So the gate also opens, in C, for a rabbit that was started with its head
    button held (config mode: only the signed bootloader runs, no runtime
    bytecode) AND whose button is held again at the moment of the write. Both
    are read on the pin (push_button_value); no bytecode and no server can
    press it. The second press is the owner's "yes": a neighbour on the open
    config network cannot slip an image in on their own. The image must
    still look like a firmware (nab_fw_plausible). The config page hands any
    image that fails the signature to the flash anyway, and shows the error
    page when the gate says no, as before.
    """
    m = root / "src/main.c"
    t, nl = read_flat(m)
    if "nab_boot_button" not in t:
        a = "  init_io();\n"
        if t.count(a) != 1:
            sys.exit(f"FAIL {m}: init_io() not found once")
        t = t.replace(a, a + "  /* owner_unlock: the head button at power-on, read once (apply-mods.py) */\n"
                             "  { extern uint8_t push_button_value(void); nab_boot_button = push_button_value(); }\n", 1)
        a = "int main(void)\n{\n"
        if t.count(a) != 1:
            sys.exit(f"FAIL {m}: main() not found once")
        t = t.replace(a, "uint8_t nab_boot_button;   /* owner_unlock */\n" + a, 1)
        write_flat(m, t, nl)
    q = root / "src/vm/vinterp.c"
    t, nl = read_flat(q)
    if "nab_boot_button" in t:
        print(f"[skip] {q}: the owner can already flash an unsigned image")
        return
    a = "              &&nab_fw_verified((const unsigned char*)VSTARTBIN(firmware),VSIZEBIN(firmware))\n"
    if t.count(a) != 1:
        sys.exit(f"FAIL {q}: the signed-image guard is not there once")
    t = t.replace(a, "              &&(nab_fw_verified((const unsigned char*)VSTARTBIN(firmware),VSIZEBIN(firmware))\n"
                     "                 ||(nab_boot_button&&push_button_value()))   /* owner_unlock */\n", 1)
    t = t.replace("#include <string.h>\n", "#include <string.h>\nextern uint8_t nab_boot_button;\n"
                  "extern uint8_t push_button_value(void);\n", 1)
    write_flat(q, t, nl)

    b = root / "mtl/boot/boot.0.0.0.13.mtl"
    t, nl = read_flat(b)
    old = ("\tif firm!=nil then\n"
           "\tif sig!=nil then\n"
           "\tif (verifySig firm sig)!=0 then\n"
           "\t(\n"
           "\t\tnil;\n"
           "\t\tsetleds 0xffffff;\n")
    new = ("\tif firm==nil then (setleds 0xff; page_error)\n"
           "\telse\n"
           "\t(\n"
           "\t\t// owner_unlock: an image without a valid signature goes to the\n"
           "\t\t// flash too; the C gate takes it only from the owner's hand.\n"
           "\t\tif (if sig==nil then 0 else verifySig firm sig)!=0 then setleds 0xffffff else setleds 0xff8000;\n")
    tail_old = ("\t\tpage_error\n"
                "\t)\n"
                "\telse\n"
                "\t(\n"
                "\t\tnil;\n"
                "\t\tsetleds 0xff;\n"
                "\t\tpage_error\n"
                "\t);;\n")
    if "verifySig firm sig" not in t:
        # The emulator's VM is built without the bootloader step: its
        # bootloader bytecode comes from the image under test (bootbc.py).
        print(f"[ok]   {q}: the gate opens for the owner; {b.name} has no signed httpflash here")
        return
    if t.count(old) != 1 or t.count(tail_old) != 1:
        sys.exit(f"FAIL {b}: httpflash is not the shape owner_unlock expects")
    t = t.replace(old, new, 1).replace(tail_old, "\t\tpage_error\n\t);;\n", 1)
    write_flat(b, t, nl)
    print(f"[ok]   {q}: an unsigned image flashes with the head button held at power-on and now")


def patch_ccmp_replay_count(root: Path) -> None:
    """Count the CCMP frames a replay check would refuse. Refuses nothing.

    Nothing looked at the packet number of a received CCMP frame: the RXD's
    Iv and Eiv words, "for replay attack checking" in rt2501usb_hw.h, were
    never read, so a captured frame sent again decrypts again and is handed
    up again (the 2026-11 review's deferred finding; scenarios/
    ccmp-replay.txt shows it: one frame, delivered twice).

    The check is 802.11's: per key, the last PN accepted; a frame is fresh
    when its PN is strictly greater. What is not known is what the RT2571W
    puts in those two words on this board. Linux's rt73usb reads the same
    chip as: the CCMP header, stripped from the frame, word 2 = its bytes
    0-3 (PN0 PN1 rsvd KeyID), word 3 = bytes 4-7 (PN2..PN5), so
    PN = Eiv<<16 | (Iv & 0xffff) and the key id is Iv>>30. Nobody has read
    them on a rabbit, the emulator fills them as that driver says, and a
    wrong guess in the receive path is a deaf rabbit. So this step counts
    and lets every frame through:

      ieee80211_ccmp_seen        protected CCMP data frames examined
      ieee80211_ccmp_replay_uc   unicast frames whose PN did not increase
      ieee80211_ccmp_replay_gr   the same for group-addressed frames
      ieee80211_ccmp_retry       frames whose PN did not increase and that
                                 carry the Retry bit: the AP's own
                                 retransmission, which legitimately repeats
                                 a PN (nothing here removes duplicates)
      ieee80211_ccmp_last_iv/eiv the two words of the last frame examined

    One "last PN" for the pairwise key and one per group key id (the id is
    in the frame, not the RXD's KeyIndex, whose numbering is one more
    unknown). eapol.c's key installs go through eapol_set_key(), which
    zeroes the slot of the key being installed -- PTK, GTK, rekeys; a
    retransmitted message 3 installs nothing (eapol_ptk_install,
    gtk_no_reinstall) and so resets nothing, which is KRACK's point.
    Only frames the radio says it decrypted with AES are examined: a TKIP
    group key (mixed mode) has another layout and is left alone.

    Cost per received frame: three stores in rt2501_rx_callback; per
    protected data frame from our AP, two compares and two stores more.

    With dcc_telemetry before it in the step list, the status line grows
    by ` N<seen> p<unicast>/<group>/<retry> V<Eiv>/<Iv>` (hex), after F,
    so a reader of the fields before it sees no change. On a rabbit, N
    rising with p0/0/0 and V rising from line to line says the words are a
    PN and enforcing is safe; p rising with N says they are not what
    rt73usb says, and V shows what they are.

    ccmp_replay_enforce turns the count into a refusal. In no default mode.
    """
    h = root / "inc/net/ieee80211.h"
    t, nl = read_flat(h)
    if "ieee80211_rxd_iv" in t:
        print(f"[skip] ccmp_replay_count: already applied")
        return
    a = "void ieee80211_input(uint8_t *frame, uint32_t length, int16_t rssi);\n"
    if t.count(a) != 1:
        sys.exit(f"FAIL {h}: ieee80211_input's prototype not found")
    t = t.replace(a, a + (
        "/* ccmp_replay_count: the receive descriptor of the frame being handed\n"
        "   to ieee80211_input() -- CipherAlg, and words 2 and 3 (Iv, Eiv). */\n"
        "extern uint32_t ieee80211_rxd_iv, ieee80211_rxd_eiv;\n"
        "extern uint8_t ieee80211_rxd_alg;\n"
        "/* A key went into the radio at this index: its packet numbers start over. */\n"
        "void ieee80211_ccmp_key_installed(uint8_t index);\n"), 1)
    write_flat(h, t, nl)

    q = root / "src/net/ieee80211.c"
    t, nl = read_flat(q)
    decl_at = "static void ieee80211_input_data(uint8_t *frame, uint32_t length, int16_t rssi)\n"
    call_at = ("\t\t/* BSSID */\n"
               "\t\tif(memcmp(fr->header.i_addr2, ieee80211_assoc_bssid, IEEE80211_ADDR_LEN) != 0) return;\n")
    for a, what in ((decl_at, "ieee80211_input_data"), (call_at, "the managed-mode BSSID test")):
        if t.count(a) != 1:
            sys.exit(f"FAIL {q}: expected one {what}, found {t.count(a)}")
    t = t.replace(decl_at, (
        "/* ccmp_replay_count (apply-mods.py). The RXD of the frame in hand, as\n"
        "   rt2501_rx_callback() left it; what a replay check would have refused;\n"
        "   and the last packet number taken per key: [0] pairwise, [1+id] group. */\n"
        "uint32_t ieee80211_rxd_iv, ieee80211_rxd_eiv;\n"
        "uint8_t ieee80211_rxd_alg;\n"
        "uint16_t ieee80211_ccmp_seen;\n"
        "uint16_t ieee80211_ccmp_replay_uc, ieee80211_ccmp_replay_gr, ieee80211_ccmp_retry;\n"
        "uint32_t ieee80211_ccmp_last_iv, ieee80211_ccmp_last_eiv;\n"
        "static struct { uint32_t hi; uint16_t lo; } ieee80211_ccmp_pn[5];\n"
        "\n"
        "void ieee80211_ccmp_key_installed(uint8_t index)\n"
        "{\n"
        "\tif(index > 3) return;\n"
        "\tif(index == 0) {\n"
        "\t\tieee80211_ccmp_pn[0].hi = 0;\n"
        "\t\tieee80211_ccmp_pn[0].lo = 0;\n"
        "\t}\n"
        "\tieee80211_ccmp_pn[1 + index].hi = 0;\n"
        "\tieee80211_ccmp_pn[1 + index].lo = 0;\n"
        "}\n"
        "\n"
        "/* 1 when the frame's packet number is not above the last one taken\n"
        "   under its key. PN = Eiv<<16 | Iv&0xffff, key id = Iv>>30: the CCMP\n"
        "   header as rt73usb says the chip hands it over. */\n"
        "static int32_t ieee80211_ccmp_stale(const struct ieee80211_frame *h)\n"
        "{\n"
        "\tuint32_t iv = ieee80211_rxd_iv, hi = ieee80211_rxd_eiv;\n"
        "\tuint16_t lo = (uint16_t)iv;\n"
        "\tuint8_t group = h->i_addr1[0] & 1;\n"
        "\tuint8_t slot = group ? (uint8_t)(1 + (iv >> 30)) : 0;\n"
        "\n"
        "\tieee80211_ccmp_seen++;\n"
        "\tieee80211_ccmp_last_iv = iv;\n"
        "\tieee80211_ccmp_last_eiv = hi;\n"
        "\tif(hi > ieee80211_ccmp_pn[slot].hi\n"
        "\t   || (hi == ieee80211_ccmp_pn[slot].hi && lo > ieee80211_ccmp_pn[slot].lo)) {\n"
        "\t\tieee80211_ccmp_pn[slot].hi = hi;\n"
        "\t\tieee80211_ccmp_pn[slot].lo = lo;\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\tif(h->i_fc[1] & IEEE80211_FC1_RETRY) ieee80211_ccmp_retry++;\n"
        "\telse if(group) ieee80211_ccmp_replay_gr++;\n"
        "\telse ieee80211_ccmp_replay_uc++;\n"
        "\treturn 1;\n"
        "}\n"
        "\n") + decl_at, 1)
    t = t.replace(call_at, call_at + (
        "\t\t/* ccmp_replay_count: counted, and let through unless\n"
        "\t\t   ccmp_replay_enforce defined IEEE80211_CCMP_REPLAY_ENFORCE. */\n"
        "\t\tif((fr->header.i_fc[1] & IEEE80211_FC1_PROTECTED)\n"
        "\t\t   && ieee80211_rxd_alg == RT2501_CIPHER_AES\n"
        "\t\t   && ieee80211_ccmp_stale(&fr->header)) {\n"
        "#ifdef IEEE80211_CCMP_REPLAY_ENFORCE\n"
        "\t\t\treturn;\n"
        "#endif\n"
        "\t\t}\n"), 1)
    write_flat(q, t, nl)

    q = root / "src/usb/rt2501usb.c"
    t, nl = read_flat(q)
    a = "      ieee80211_input(rt2501_frame+sizeof(RXD_STRUC),\n"
    if t.count(a) != 1:
        sys.exit(f"FAIL {q}: the call to ieee80211_input is not where ccmp_replay_count expects it")
    t = t.replace(a, (
        "      /* ccmp_replay_count: the cipher the radio decrypted with and the\n"
        "         two words it says are the frame's IV and extended IV. */\n"
        "      ieee80211_rxd_alg = rxd->CipherAlg;\n"
        "      ieee80211_rxd_iv = rxd->Iv;\n"
        "      ieee80211_rxd_eiv = rxd->Eiv;\n") + a, 1)
    write_flat(q, t, nl)

    q = root / "src/net/eapol.c"
    t, nl = read_flat(q)
    a = "static uint8_t replay_counter[EAPOL_RPC_LENGTH];"
    if t.count(a) != 1:
        sys.exit(f"FAIL {q}: replay_counter declaration not found")
    t, n = re.subn(r"(?<![A-Za-z0-9_])rt2501_set_key\((?!\))", "eapol_set_key(", t)
    if n < 2:
        sys.exit(f"FAIL {q}: expected the pairwise and group key installs, found {n}")
    t = t.replace(a, (
        "/* ccmp_replay_count: every key this file hands the radio starts its\n"
        "   packet numbers over. */\n"
        "void ieee80211_ccmp_key_installed(uint8_t index);\n"
        "static int32_t eapol_set_key(uint8_t index, uint8_t *key, uint8_t *txmic,\n"
        "                             uint8_t *rxmic, uint8_t cipher)\n"
        "{\n"
        "  ieee80211_ccmp_key_installed(index);\n"
        "  return rt2501_set_key(index, key, txmic, rxmic, cipher);\n"
        "}\n\n") + a, 1)
    write_flat(q, t, nl)

    d = root / "src/utils/dcctel.c"
    if d.exists():
        t = d.read_text()
        edits = (
            ("static char dcctel_line[96];\n", "static char dcctel_line[168];\n", "line buffer"),
            ("#define TARGET_REQ_DEBUGCHAR 0x02\n",
             "extern uint16_t ieee80211_ccmp_seen, ieee80211_ccmp_replay_uc;\n"
             "extern uint16_t ieee80211_ccmp_replay_gr, ieee80211_ccmp_retry;\n"
             "extern uint32_t ieee80211_ccmp_last_iv, ieee80211_ccmp_last_eiv;\n"
             "#define TARGET_REQ_DEBUGCHAR 0x02\n", "DEBUGCHAR define"),
            ("  *p++ = '\\n';\n",
             "  /* ccmp_replay_count: N frames examined, p would-be-refused\n"
             "     unicast/group/retransmitted, V the last frame's Eiv/Iv. */\n"
             "  *p++ = ' '; *p++ = 'N'; p = dcctel_hex(p, ieee80211_ccmp_seen);\n"
             "  *p++ = ' '; *p++ = 'p'; p = dcctel_hex(p, ieee80211_ccmp_replay_uc);\n"
             "  *p++ = '/'; p = dcctel_hex(p, ieee80211_ccmp_replay_gr);\n"
             "  *p++ = '/'; p = dcctel_hex(p, ieee80211_ccmp_retry);\n"
             "  *p++ = ' '; *p++ = 'V'; p = dcctel_hex(p, ieee80211_ccmp_last_eiv);\n"
             "  *p++ = '/'; p = dcctel_hex(p, ieee80211_ccmp_last_iv);\n"
             "  *p++ = '\\n';\n", "end of line"),
        )
        for old, new, what in edits:
            if t.count(old) != 1:
                sys.exit(f"FAIL {d}: expected one {what}, found {t.count(old)}")
            t = t.replace(old, new, 1)
        d.write_text(t)
        print(f"[ok]   {d}: N, p and V on the telemetry line")
    else:
        print(f"[note] ccmp_replay_count: no dcc_telemetry before it, the counters are JTAG-read only")
    print(f"[ok]   ccmp_replay_count: CCMP packet numbers watched, nothing refused")


def patch_ccmp_replay_enforce(root: Path) -> None:
    """Refuse the frames ccmp_replay_count counts. IN NO DEFAULT MODE.

    Defines IEEE80211_CCMP_REPLAY_ENFORCE: a protected CCMP data frame whose
    packet number is not above the last one taken under its key is dropped
    in ieee80211_input_data, the AP's own retransmissions included (they
    are duplicates). The counters keep counting, and now count refusals.

    Shown on Nabazprout on 2026-11-12 with ccmp_replay_count alone: N rose
    to 37,573, p0/0/41, V rising. Should a rabbit's words not be the packet
    number after all, the probation below keeps it from going deaf: more
    than half of its first 256 protected frames flagged and that boot only
    counts. Proven in the emulator (scenarios/ccmp-replay-enforce.txt).
    """
    q = root / "src/net/ieee80211.c"
    t, nl = read_flat(q)
    if "#define IEEE80211_CCMP_REPLAY_ENFORCE" in t:
        print(f"[skip] {q}: CCMP replays already refused")
        return
    a = "/* ccmp_replay_count (apply-mods.py). The RXD of the frame in hand, as\n"
    if t.count(a) != 1:
        sys.exit(f"FAIL {q}: ccmp_replay_enforce needs ccmp_replay_count first")
    t = t.replace(a, "/* ccmp_replay_enforce: refuse what ccmp_replay_count counts. */\n"
                     "#define IEEE80211_CCMP_REPLAY_ENFORCE 1\n\n" + a, 1)
    # The guard (2026-11-12). Nabazprout's day of telemetry (37,573 frames,
    # p0/0/41 on a UniFi AP) says the RXD words are the packet number; one
    # rabbit and one AP is not every rabbit. So the verdict is the rabbit's
    # own: over the first 256 protected frames after boot, a misread would
    # flag nearly every frame, a real link nearly none. More than half
    # flagged and this boot only counts, as 1.1.0.0 did, instead of going
    # deaf; otherwise it refuses, and the verdict holds until the next boot
    # -- a replay flood later cannot talk it back into counting.
    s_at = "static int32_t ieee80211_ccmp_stale(const struct ieee80211_frame *h)\n"
    if t.count(s_at) != 1:
        sys.exit(f"FAIL {q}: ccmp_replay_enforce needs ieee80211_ccmp_stale")
    t = t.replace(s_at, (
        "/* ccmp_replay_enforce: 0 on probation (refusing), 1 refusing,\n"
        "   -1 counting only for this boot (apply-mods.py). */\n"
        "static int8_t ieee80211_ccmp_verdict;\n"
        "static int32_t ieee80211_ccmp_enforcing(void)\n"
        "{\n"
        "\tif(!ieee80211_ccmp_verdict && ieee80211_ccmp_seen >= 256)\n"
        "\t\tieee80211_ccmp_verdict = 2 * ((uint32_t)ieee80211_ccmp_replay_uc\n"
        "\t\t\t+ ieee80211_ccmp_replay_gr) > ieee80211_ccmp_seen ? -1 : 1;\n"
        "\treturn ieee80211_ccmp_verdict >= 0;\n"
        "}\n\n") + s_at, 1)
    r_at = "#ifdef IEEE80211_CCMP_REPLAY_ENFORCE\n\t\t\treturn;\n#endif\n"
    if t.count(r_at) != 1:
        sys.exit(f"FAIL {q}: the refusal is not where ccmp_replay_enforce expects it")
    t = t.replace(r_at, "#ifdef IEEE80211_CCMP_REPLAY_ENFORCE\n"
                        "\t\t\tif(ieee80211_ccmp_enforcing()) return;\n#endif\n", 1)
    write_flat(q, t, nl)
    print(f"[ok]   {q}: CCMP frames with a stale packet number are refused "
          f"(after a 256-frame probation that can fall back to counting)")


def patch_mixed_gtk(root: Path) -> None:
    """Accept a 32-byte group key (mixed-mode WPA2: CCMP pairwise, TKIP group).

    eapol_install_msg3_gtk() unwrapped the GTK into `gtk.s.ek` with a maximum
    of `sizeof(gtk.s.ek)` (16), but a TKIP group key is 32 bytes (key + tx/rx
    MIC). On a mixed-mode AP -- CCMP for unicast, TKIP for broadcast --
    rsn_find_gtk then refused it, message 3 went on without a group key, and
    the rabbit was deaf to broadcast and multicast. The destination is the
    32-byte `gtk.b` union; the maximum is raised to match. CCMP (a 16-byte
    key) writes the same 16 bytes as before, so a CCMP-only network is byte
    for byte unchanged.

    Patches eapol.c. Idempotent.
    """
    p = root / "src/net/eapol.c"
    s = read_src(p)
    old = "rsn_find_gtk(plain, wlen-8, gtk.s.ek, sizeof(gtk.s.ek),"
    new = "rsn_find_gtk(plain, wlen-8, gtk.b, sizeof(gtk.b),  /* mixed-gtk: a TKIP group key is 32 */"
    if new.split("/*")[0].strip() in s:
        print(f"[skip] {p}: the GTK buffer already fits a 32-byte key")
        return
    if s.count(old) != 1:
        sys.exit(f"FAIL {p}: the rsn_find_gtk call is not the shape this patch expects ({s.count(old)})")
    s = s.replace(old, new, 1)
    write_src(p, s)
    print(f"[ok]   {p}: a 32-byte TKIP group key is accepted (mixed-mode broadcast)")


def patch_usb_descriptor_bounds(root: Path) -> None:
    """Clamp USB descriptor copies to the destination structure.

    usbh_get_descriptor_all() copies each descriptor the device returns into a
    struct allocated at the struct's fixed size, using the descriptor's own
    bLength -- a byte the device controls, up to 255. A device that reports a
    bLength larger than the struct overruns the heap on write. It needs
    physical access to the USB port (a tampered Wi-Fi dongle), but it is the
    one write corruption the audit found; confirmed under ASan
    (firmware-arm/docs/VIOLET-FUZZING.txt). Each fixed-size copy is clamped to
    its destination now; the `expansion` case already allocates bLength and is
    left alone.

    Patches usbhcore.c. Idempotent.
    """
    p = root / "src/usb/usbhcore.c"
    s = read_src(p)
    if "/* usb-descriptor-bounds */" in s:
        print(f"[skip] {p}: the descriptor copies are already clamped")
        return
    subs = [
        ("memcpy((uint32_t *)*pconfiguration, (uint8_t *)ptr, bLength);",
         "memcpy((uint32_t *)*pconfiguration, (uint8_t *)ptr, bLength > sizeof(struct usb_configuration_descriptor) ? sizeof(struct usb_configuration_descriptor) : bLength); /* usb-descriptor-bounds */"),
        ("memcpy((uint32_t *)*pinterface, (uint8_t *)ptr, *ptr);",
         "memcpy((uint32_t *)*pinterface, (uint8_t *)ptr, *ptr > sizeof(struct usb_interface_descriptor) ? sizeof(struct usb_interface_descriptor) : *ptr); /* usb-descriptor-bounds */"),
        ("memcpy((uint32_t *)*pendpoint, (uint8_t *)ptr, *ptr);",
         "memcpy((uint32_t *)*pendpoint, (uint8_t *)ptr, *ptr > sizeof(struct usb_endpoint_descriptor) ? sizeof(struct usb_endpoint_descriptor) : *ptr); /* usb-descriptor-bounds */"),
        ("memcpy((void *)otg, (uint8_t *)ptr, bLength);",
         "memcpy((void *)otg, (uint8_t *)ptr, bLength > sizeof(struct usb_otg_descriptor) ? sizeof(struct usb_otg_descriptor) : bLength); /* usb-descriptor-bounds */"),
    ]
    for old, new in subs:
        if s.count(old) != 1:
            sys.exit(f"FAIL {p}: a descriptor memcpy is not where this patch expects it: {old}")
        s = s.replace(old, new, 1)
    write_src(p, s)
    print(f"[ok]   {p}: USB descriptor copies clamped to their destination size")


def patch_beacon_ie_bounds(root: Path) -> None:
    """Bound the RSN/WPA suite-count loops in the beacon parser.

    ieee80211_input_mgt(), parsing a beacon or probe response while scanning
    -- before any association -- reads a 16-bit suite count out of the RSN and
    vendor-WPA elements and walks that many 4-byte suites, with no bound
    against the end of the frame. A forged beacon with a count of 0x7fff makes
    it read up to ~256 KB past the frame: a pre-auth out-of-bounds read any
    station in radio range can trigger. Confirmed under ASan
    (firmware-arm/docs/VIOLET-FUZZING.txt). Each of the five loops is bounded
    against frame_end now, and a count read off the end of the frame yields
    zero so the loop does not run.

    Violet-original code (identical in the uplg and RedoXyde ports); the
    double-advance a line below is a separate, lower item. Patches
    ieee80211.c. Idempotent.
    """
    p = root / "src/net/ieee80211.c"
    s = read_src(p)
    if "current + IEEE80211_OUI_LEN <= frame_end" in s:
        print(f"[skip] {p}: the beacon suite-count loops are already bounded")
        return
    loop_old = "for(i=0;i<count;i++) {"
    loop_new = "for(i=0;i<count && current + IEEE80211_OUI_LEN <= frame_end;i++) {"
    if s.count(loop_old) != 5:
        sys.exit(f"FAIL {p}: expected 5 suite-count loops, found {s.count(loop_old)}")
    s = s.replace(loop_old, loop_new)
    count_old = "count = (current[0] << 0)|(current[1] << 8);"
    count_new = "count = (current + 2 <= frame_end) ? ((current[0] << 0)|(current[1] << 8)) : 0;"
    if s.count(count_old) != 5:
        sys.exit(f"FAIL {p}: expected 5 suite-count reads, found {s.count(count_old)}")
    s = s.replace(count_old, count_new)
    write_src(p, s)
    print(f"[ok]   {p}: the beacon RSN/WPA suite-count loops are bounded against frame_end")


def patch_config_no_password(root: Path) -> None:
    """The configuration page no longer hands back the Wi-Fi password.

    In config mode the rabbit is its own open access point and serves the
    settings form, which pre-filled the password field with the stored key
    (`value="<KEY>"`, "<KEY>" -> filterweb key). Anyone who joined that open
    AP and fetched the page read the Wi-Fi password in clear. The field is
    now rendered empty, and the handler keeps the stored password when the
    field comes back empty -- the same shape the SSID field already uses, so
    reconfiguring without retyping the password still works; only a non-empty
    field changes it.

    Patches boot.0.0.0.13.mtl. Idempotent.
    """
    q = root / "mtl/boot/boot.0.0.0.13.mtl"
    t = read_src(q)
    if "// config-no-password" in t:
        print(f"[skip] {q}: the config page already hides the password")
        return
    render_old = "\t\t\t[\"<KEY>\" filterweb key]::"
    render_new = "\t\t\t[\"<KEY>\" \"\"]::\t// config-no-password: never echo the stored key"
    if t.count(render_old) != 1:
        sys.exit(f"FAIL {q}: the <KEY> substitution is not the shape this patch expects ({t.count(render_old)})")
    t = t.replace(render_old, render_new, 1)
    handler_old = "\telse if v=='n' then confSetstr CONF_WIFIKEY0 val 64"
    handler_new = "\telse if v=='n' then (if val!=nil && strlen val then confSetstr CONF_WIFIKEY0 val 64)\t// config-no-password: keep the old key when left blank"
    if t.count(handler_old) != 1:
        sys.exit(f"FAIL {q}: the password handler is not the shape this patch expects ({t.count(handler_old)})")
    t = t.replace(handler_old, handler_new, 1)
    write_src(q, t)
    print(f"[ok]   {q}: the config page renders the password field empty and keeps the key when blank")


def patch_probe_ssid_bound(root: Path) -> None:
    """Bound the SSID copy in a probe request (config/master mode).

    ieee80211_input_mgt(), for a PROBE_REQ while the rabbit is its own AP,
    copies the SSID element into a 33-byte stack buffer using the element's
    own length byte -- up to 255, from any station in radio range:

        char ssid[IEEE80211_SSID_MAXLEN+1];
        ...
        for(i=0;i<frame_current[1];i++) ssid[i] = frame_current[i+2];

    A length past 32 overruns the stack. Proven in the emulator
    (scenarios/vm/probe-ssid-overflow.txt, directive inject-probe-ssid 255):
    the firmware copied and read back 255 bytes from the 33-byte buffer. The
    copy is bounded here, and ssid[0] is initialised so the `if(ssid[0]==0)`
    just below never reads an uninitialised byte when there is no SSID
    element at all.

    Violet-original code, no step of ours is near it. Idempotent.
    """
    p = root / "src/net/ieee80211.c"
    s = read_src(p)
    if "probe_ssid_bound" in s:
        print(f"[skip] {p}: the probe-request SSID copy is already bounded")
        return
    nl = "\r\n" if "\r\n" in s else "\n"

    copy_old = ("\t\t\t\t\t\tfor(i=0;i<frame_current[1];i++)" + nl +
                "\t\t\t\t\t\t\tssid[i] = frame_current[i+2];")
    copy_new = ("\t\t\t\t\t\tfor(i=0;i<frame_current[1] && i<IEEE80211_SSID_MAXLEN;i++)\t/* probe_ssid_bound */" + nl +
                "\t\t\t\t\t\t\tssid[i] = frame_current[i+2];")
    if s.count(copy_old) != 1:
        sys.exit(f"FAIL {p}: the probe-request SSID copy is not the shape this patch expects ({s.count(copy_old)})")
    s = s.replace(copy_old, copy_new, 1)

    decl_old = ("\t\t\t\tchar ssid[IEEE80211_SSID_MAXLEN+1];" + nl + nl +
                "\t\t\t\tssid_present = 0;")
    decl_new = ("\t\t\t\tchar ssid[IEEE80211_SSID_MAXLEN+1];" + nl + nl +
                "\t\t\t\tssid[0] = 0;\t/* probe_ssid_bound: no SSID element -> ssid[0] is read below */" + nl +
                "\t\t\t\tssid_present = 0;")
    if s.count(decl_old) != 1:
        sys.exit(f"FAIL {p}: the probe-request ssid declaration is not where this patch expects it ({s.count(decl_old)})")
    s = s.replace(decl_old, decl_new, 1)

    write_src(p, s)
    print(f"[ok]   {p}: probe-request SSID copy bounded to IEEE80211_SSID_MAXLEN")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="Path to the nabgcc checkout root")
    ap.add_argument(
        "--mode",
        choices=("full", "minimal", "signed-stock", "pages-only", "max", "mdns-only", "lean", "lean-wpa3", "lean-wpa3-tel", "lean-wpa3-timed", "lean-marker", "lean-nomdns", "phase8-rom", "lean-wpa3-slim", "lean-wpa3-slim-tel", "lean-wpa3-kx", "lean-wpa3-tls", "naboot-1.0.0.0", "naboot-1.0.0.1", "naboot-1.0.0.2", "naboot-1.0.0.3", "naboot-1.1.0.0", "naboot-1.2.0.0", "naboot-1.2.0.0-tel", "naboot-1.2.0.0-kxprobe", "naboot-1.2.0.1", "naboot-1.2.0.2", "naboot-1.2.0.1-kxprobe", "lean-small", "lean-reclaim", "lean-wpa3-os"),
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
            "lean-wpa3    = lean plus the WPA3-SAE client, hash-to-element "
            "when the AP offers it and hunting and pecking otherwise. A "
            "separate mode on purpose: `lean` is what every rabbit on the "
            "warren runs, and an experimental key exchange does not belong "
            "in it.\n"
            "lean-wpa3-slim = lean-wpa3 minus what nothing reads: the debug "
            "sprintf chain (newlib printf + nano malloc), the bootloader's "
            "console literals and hex dumpers, 64 unrolled MD5 steps, a second "
            "AES S-box. 107 228 B against lean-wpa3's 117 692 (2026-11-08), "
            "same behaviour; the room for firmware 1.0.0.0's TLS client.\n"
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
        "full": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,boot_marker,linker_keep",
        # Minimal omits modernize_pages + bootloader — boot.mtl untouched, so
        # the rabbit's flash + config-mode behavior is byte-equivalent to the
        # vanilla wpa2 branch HEAD. Only the C side gains the verifySig opcode
        # (dormant until called).
        "minimal": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,makefile,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,linker_keep,gc_sections",
        # Bisection helpers — same C-side as minimal, only ONE boot.mtl mod
        # added. Use them when full has bricked the rabbit to pinpoint which
        # of the two boot.mtl patches is the culprit.
        "signed-stock": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,makefile,bootloader,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,linker_keep",
        "pages-only":   "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,makefile,modernize_pages,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,linker_keep",
        # max = full + mdns. The everything-bag for a fresh flash.
        # NOTE: mdns is NEW and untested on hardware as of 2026-05-31
        # — flash mdns-only first as a less-invasive smoke test.
        "max":       "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,linker_keep",
        # mdns-only: vanilla boot.mtl + just the mDNS announcer. Use to
        # smoke-test the mdns add without entangling with bootloader/pages.
        "mdns-only": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,makefile,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,linker_keep",
        # lean: max + gc-sections. Drops the dead newlib printf glue
        # (~6 KB) that DEBUG_*-stripping made unreachable but the
        # linker still kept (no DCE). Safe because linker_keep protects
        # .intvec / .startup.* / .ramfunc / .bytecode.*.
        "lean": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,link_liveness,boot_marker,flash_verify,persist_counters,reset_reason,linker_keep,gc_sections",
        "lean-marker": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,link_liveness,boot_marker,flash_verify,persist_counters,reset_reason,linker_keep,gc_sections",
        "lean-wpa3": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,boot_marker,flash_verify,persist_counters,reset_reason,linker_keep,gc_sections",
        "lean-wpa3-tel": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,boot_marker,flash_verify,persist_counters,reset_reason,dcc_telemetry,linker_keep,gc_sections",
        # lean-wpa3 plus one timed SAE commit per boot, reported as &t= in the
        # bc.jsp query. A diagnostic build: it delays every boot by exactly
        # the thing it is measuring. Flash it, read the number off the
        # warren's HTTP log, flash lean-wpa3 back.
        "lean-wpa3-timed": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,wpa3_timing,link_liveness,boot_marker,flash_verify,persist_counters,reset_reason,linker_keep,gc_sections",
        # lean-nomdns: `lean` with the mDNS announcer/resolver removed and
        # NOTHING else changed. Diagnostic build for the connection drops
        # reported on `lean`: mdns is the only new *network* feature in that
        # mode, so flashing this isolates it from the rest of the stack.
        # A rabbit on this build needs working DNS to find its server.
        "lean-nomdns": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,linker_keep,gc_sections",
        # phase8-rom: instead of modernize_pages (which stores pages as
        # Metal globals), use rom_pages — pages live in C-side const ROM
        # and are rendered via the new OPpageRender opcode. Measures the
        # flash budget trade-off. Mutually exclusive with modernize_pages.
        "phase8-rom": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,makefile,bootloader,rom_pages,prune_orphans,inline_mkwav,strip_dump,inline_dump_calls,strip_echo_funs,strip_secho,strip_echo_opcodes,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,linker_keep,gc_sections",
        # lean-wpa3-os: lean-wpa3 compiled at -Os. A MEASUREMENT mode: it
        # says what the flag would buy and is not to be flashed (every
        # hardware-validated image is -O1; see patch_opt_size).
        "lean-wpa3-os": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,boot_marker,flash_verify,persist_counters,reset_reason,linker_keep,gc_sections,opt_size",
        # lean-wpa3-slim: lean-wpa3 with the dead weight measured out of it,
        # and nothing the rabbit does changed. C side: the serial console
        # formats without sprintf (and so without newlib's printf and
        # malloc), MD5 is a loop instead of 64 unrolled steps, cmac.c uses
        # aes128.c's S-box. Bootloader bytecode, after every step that edits
        # it: the console literals, echo opcodes and hex dumpers go (the
        # UART pad is unwired on a production rabbit), mkwav is precomputed,
        # and five unreferenced functions are dropped. Each saving is in the
        # step's docstring with the measurement that justified it.
        "lean-wpa3-slim": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,boot_marker,flash_verify,persist_counters,reset_reason,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,linker_keep,gc_sections",
        "lean-wpa3-slim-tel": "vbc,vinterp,stdlib,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,boot_marker,flash_verify,persist_counters,reset_reason,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,dcc_telemetry,linker_keep,gc_sections",
        "lean-wpa3-kx": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,boot_marker,flash_verify,persist_counters,reset_reason,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,dcc_telemetry,linker_keep,gc_sections",
        "lean-wpa3-tls": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_marker,flash_verify,persist_counters,reset_reason,crash_report,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,dcc_telemetry,linker_keep,gc_sections",
        # The published 1.0.0.0, frozen: firmware-arm/releases/ holds its signed
        # image, and this list must keep rebuilding those bytes.
        "naboot-1.0.0.0": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_marker,flash_verify,persist_counters,reset_reason,crash_report,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,dcc_telemetry,linker_keep,gc_sections",
        # 1.0.0.1: 1.0.0.0 with entropy_pool and rx_protected, and the EAPOL/SAE
        # hardening of the 2026-11-08 audit: eapol_ptk_install (F2), eapol_mic_len
        # (F3), gtk_no_reinstall (F4), no_tkip_pairwise (F8), sae_required (S2).
        "naboot-1.0.0.1": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_marker,flash_verify,persist_counters,reset_reason,crash_report,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,dcc_telemetry,linker_keep,gc_sections",
        # 1.0.0.2: 1.0.0.1 with bc.jsp over TLS for a confirmed key (boot_tls).
        "naboot-1.0.0.2": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_tls,boot_marker,flash_verify,persist_counters,reset_reason,crash_report,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,dcc_telemetry,linker_keep,gc_sections",
        # 1.0.0.3: 1.0.0.2 with SAE hunting-and-pecking in constant time
        # (crypto/p256.c, crypto/sae.c, #113); the steps are the same.
        "naboot-1.0.0.3": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_tls,boot_marker,flash_verify,persist_counters,reset_reason,crash_report,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,dcc_telemetry,linker_keep,gc_sections",
        # 1.0.0.3 with small_strings, to measure it before a release takes it.
        "lean-small": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_tls,boot_marker,flash_verify,persist_counters,reset_reason,crash_report,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,small_strings,dcc_telemetry,linker_keep,gc_sections",
        # lean-small plus the dead-code removals of docs/VIOLET-CODEMORT.txt,
        # to measure each before a release takes them.
        "lean-reclaim": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_tls,boot_marker,flash_verify,flash_precheck,flash_no_return,persist_counters,reset_reason,crash_report,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,small_strings,strip_factory_test,strip_eapol_dump,uart_silent,no_tkip_code,stub_soft_codecs,no_wep,no_wep_boot,share_page_css,http_cap,dcc_telemetry,ccmp_replay_count,linker_keep,gc_sections",
        # 1.1.0.0: 1.0.0.3 with the 2026-11 security review fixed, the dead
        # code of docs/VIOLET-CODEMORT.txt removed (WEP, the factory test, the
        # software codecs, pairwise TKIP), the flash writer that no longer
        # returns into half-written code, the HTTP ceiling, and CCMP replays
        # counted (not yet refused). The same steps as lean-reclaim, under
        # the number the boot URL carries.
        "naboot-1.1.0.0": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_tls,boot_marker,flash_verify,flash_precheck,flash_no_return,persist_counters,reset_reason,crash_report,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,small_strings,strip_factory_test,strip_eapol_dump,uart_silent,no_tkip_code,stub_soft_codecs,no_wep,no_wep_boot,share_page_css,http_cap,dcc_telemetry,ccmp_replay_count,linker_keep,gc_sections",
        # 1.1.0.0 without the DCC telemetry (Nabazprout's anti-replay day is
        # read: 37,573 frames, p0/0/41), refusing CCMP replays behind the
        # probation guard, and saying its RF chip (rfic_report).
        "naboot-1.2.0.0": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_tls,boot_marker,flash_verify,flash_precheck,flash_no_return,persist_counters,reset_reason,crash_report,rfic_report,tls_certs,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,small_strings,strip_factory_test,strip_eapol_dump,uart_silent,no_tkip_code,stub_soft_codecs,no_wep,no_wep_boot,share_page_css,http_cap,boot_https,owner_unlock,ccmp_replay_count,ccmp_replay_enforce,linker_keep,gc_sections",
        # 1.2.0.0 with the key exchange that fits a real ARM7, and says how it went.
        "naboot-1.2.0.1": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_tls,boot_marker,flash_verify,flash_precheck,flash_no_return,persist_counters,reset_reason,crash_report,rfic_report,tls_certs,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,small_strings,strip_factory_test,strip_eapol_dump,uart_silent,no_tkip_code,stub_soft_codecs,no_wep,no_wep_boot,share_page_css,http_cap,boot_https,owner_unlock,kx_report,ccmp_replay_count,ccmp_replay_enforce,linker_keep,gc_sections",
        # 1.2.0.1, and /reset leaves the Terrier as its server, not Violet's
        # r.nabaztag.com, a stranger's server today (2026-11-14, …5503).
        "naboot-1.2.0.2": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,factory_server,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_tls,boot_marker,flash_verify,flash_precheck,flash_no_return,persist_counters,reset_reason,crash_report,rfic_report,tls_certs,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,small_strings,strip_factory_test,strip_eapol_dump,uart_silent,no_tkip_code,stub_soft_codecs,no_wep,no_wep_boot,share_page_css,http_cap,boot_https,owner_unlock,kx_report,ccmp_replay_count,ccmp_replay_enforce,linker_keep,gc_sections",
        # A test image (kx_probe), never a release: the key exchange at every boot.
        "naboot-1.2.0.0-kxprobe": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_tls,boot_marker,flash_verify,flash_precheck,flash_no_return,persist_counters,reset_reason,crash_report,rfic_report,tls_certs,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,small_strings,strip_factory_test,strip_eapol_dump,uart_silent,no_tkip_code,stub_soft_codecs,no_wep,no_wep_boot,share_page_css,http_cap,boot_https,owner_unlock,kx_report,kx_probe,ccmp_replay_count,ccmp_replay_enforce,linker_keep,gc_sections",
        # The test image leaves owner_unlock out to fit (32 bytes over, 2026-11-13).
        "naboot-1.2.0.1-kxprobe": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_tls,boot_marker,flash_verify,flash_precheck,flash_no_return,persist_counters,reset_reason,crash_report,rfic_report,tls_certs,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,small_strings,strip_factory_test,strip_eapol_dump,uart_silent,no_tkip_code,stub_soft_codecs,no_wep,no_wep_boot,share_page_css,http_cap,boot_https,kx_report,kx_probe,ccmp_replay_count,ccmp_replay_enforce,linker_keep,gc_sections",
                # 1.2.0.0 with the live JTAG telemetry back (DCC, jtag/listen.sh): the
                # diagnostic build to flash on a rabbit that misbehaves (Clara, 2026-11-13).
                # Without boot_https: with it the image is 732 bytes over the flash; a
                # diagnostic build boots in the clear, as every build before 1.2.0.0 did.
                "naboot-1.2.0.0-tel": "vbc,vinterp,stdlib,x25519_wdt,strip_tweetnacl,sign_open_alias,verify_wdt,audio_fifo_data,audio_halfduplex,bounded_waits,modernize_pages,makefile,bootloader,firmware_version,mdns,wpa2_gtk,eapol_group_ack,group_cipher,eapol_msg2_ie,eapol_ptk_rekey,vm_bounds,eapol_replay,cold_state,usb_robustness,usb_descriptor_bounds,violet_hardening,rx_recovery,extbss,no_libc_malloc,wpa3_sae,wpa3_boot_pw,wpa3_eapol_akm,entropy_pool,rx_protected,eapol_ptk_install,eapol_mic_len,gtk_no_reinstall,mixed_gtk,no_tkip_pairwise,probe_ssid_bound,config_no_password,beacon_ie_bounds,sae_required,wpa3_h2e,link_liveness,adoption_kx,adoption_boot,tls_psk,boot_tls,boot_marker,flash_verify,flash_precheck,flash_no_return,persist_counters,reset_reason,crash_report,rfic_report,tls_certs,uart_no_sprintf,strip_dbg_sprintf,md5_compact,share_sbox,strip_secho,strip_echo_opcodes,strip_echo_funs,strip_dump,inline_dump_calls,inline_mkwav,prune_dead_funs,small_strings,strip_factory_test,strip_eapol_dump,uart_silent,no_tkip_code,stub_soft_codecs,no_wep,no_wep_boot,share_page_css,http_cap,owner_unlock,dcc_telemetry,ccmp_replay_count,ccmp_replay_enforce,linker_keep,gc_sections",
    }
    steps_str = args.steps if args.steps else default_steps[args.mode]
    steps = steps_str.split(",")
    # A release mode says its own version: naboot-1.0.0.1 is 1.0.0.1 in the
    # boot URL, whatever NAB_FIRMWARE_VERSION says.
    # A variant (naboot-1.2.0.0-tel) says the release it is a variant of: the
    # Terrier reads the version as four numbers, and anything else would be
    # served the recovery runtime of a firmware before 1.0.0.0.
    if args.mode.startswith("naboot-") and not args.steps:
        os.environ["NAB_FIRMWARE_VERSION"] = args.mode[len("naboot-"):].split("-")[0]
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
        missing = [d for d in ("no_libc_malloc", "extbss", "wpa2_gtk")
                   if d not in steps]
        if missing:
            sys.exit(
                "FAIL step list: wpa3_sae needs " + ", ".join(missing) +
                ". nab_rand() comes from no_libc_malloc and is what SAE's "
                "secrecy rests on; extbss is what zeroes the SAE context at "
                "boot. Without either, this builds a WPA3 client that is "
                "worse than no WPA3 client."
            )

    # wpa3_boot_pw is the other half of wpa3_sae: the driver can do SAE, and
    # this is what gives it a password to do it with. Shipping one without the
    # other is a WPA3 client that silently associates over WPA2 -- which is
    # what 52283f99 did, and the reason this check is here rather than in a
    # comment.
    if "wpa3_timing" in steps and "wpa3_boot_pw" not in steps:
        sys.exit("FAIL step list: wpa3_timing needs wpa3_boot_pw — it times a "
                 "commit built from the config sector's passphrase.")
    if "wpa3_boot_pw" in steps and "wpa3_sae" not in steps:
        sys.exit("FAIL step list: wpa3_boot_pw is useless without wpa3_sae.")
    if "wpa3_eapol_akm" in steps and "wpa3_sae" not in steps:
        sys.exit("FAIL step list: wpa3_eapol_akm is useless without wpa3_sae.")
    # A SAE exchange that succeeds and is then followed by WPA2-PSK's
    # handshake is refused by every real access point at message 2
    # (patch_wpa3_eapol_akm). The emulator's AP did that for ten days.
    if "wpa3_sae" in steps and "wpa3_eapol_akm" not in steps:
        sys.exit("FAIL step list: wpa3_sae without wpa3_eapol_akm authenticates "
                 "with SAE and then runs the four-way handshake on WPA2's key "
                 "schedule, which no real AP accepts. Add wpa3_eapol_akm.")
    # link_liveness hooks the 802.11w gate that wpa3_sae inserts into the
    # deauthenticate handler, so when both are present it must come after.
    if ("link_liveness" in steps and "wpa3_sae" in steps
            and steps.index("link_liveness") < steps.index("wpa3_sae")):
        sys.exit("FAIL step list: link_liveness must come after wpa3_sae, whose "
                 "802.11w gate it reads a refused deauthenticate through.")
    # wpa3_h2e edits text wpa3_sae and wpa3_eapol_akm put there, and its
    # message 2 relies on the AKM-defined schedule being in place.
    if "wpa3_h2e" in steps and ("wpa3_sae" not in steps or "wpa3_eapol_akm" not in steps):
        sys.exit("FAIL step list: wpa3_h2e needs wpa3_sae and wpa3_eapol_akm.")
    # ...and it anchors on the text entropy_pool (the credit gate before the
    # commit) and sae_required (the cache's forget) leave in ieee80211_sae_begin
    # and the SAE cache, so when those are present it must come after them.
    # H2E draws its rand and mask through the same callback: from the pool.
    for before in ("entropy_pool", "sae_required"):
        if ("wpa3_h2e" in steps and before in steps
                and steps.index("wpa3_h2e") < steps.index(before)):
            sys.exit(f"FAIL step list: wpa3_h2e must come after {before}, whose "
                     "text in ieee80211.c it edits.")
    if "eapol_msg2_ie" in steps and "group_cipher" not in steps:
        sys.exit("FAIL step list: eapol_msg2_ie reads ieee80211_group_cipher, "
                 "which group_cipher provides.")
    # ...but only for a real image. An emulator build has no Metal VM and no
    # bootloader, so it omits vbc/vinterp/stdlib and models wifiAuth in C
    # instead; requiring the opcode there would just stop it compiling.
    # The emulator's rabbit-vm now carries vbc/vinterp too, so its OTA path
    # has a signature gate; it still reads the passphrase out of the config
    # page itself and ships no boot bytecode for wpa3_boot_pw to patch. Its
    # Containerfile says so with NAB_SIM=1.
    if ("wpa3_sae" in steps and "vbc" in steps
            and "wpa3_boot_pw" not in steps
            and not os.environ.get("NAB_SIM")):
        sys.exit(
            "FAIL step list: wpa3_sae without wpa3_boot_pw builds a WPA3 "
            "client whose password is never captured, so SAE never starts "
            "and the association falls back to PSK without saying so -- "
            "silently, because that fallback is there for transition-mode "
            "APs. That was 52283f99, and it took a WPA3-only UniFi to find "
            "it. Add wpa3_boot_pw."
        )

    # entropy_pool rewrites the SAE stream wpa3_sae installs, shares the
    # .extbss scratch buffer extbss declares, and replaces the randbuffer
    # no_libc_malloc left; adoption_kx's nab_kx_vm.c draws its key through
    # the gate entropy_pool provides. Order, like the others.
    if "entropy_pool" in steps:
        for dep in ("wpa3_sae", "extbss", "no_libc_malloc"):
            if dep not in steps or steps.index(dep) > steps.index("entropy_pool"):
                sys.exit(f"FAIL step list: entropy_pool must come after {dep}, "
                         "which it edits or relies on.")
    if ("adoption_kx" in steps and "entropy_pool" not in steps
            and args.mode != "naboot-1.0.0.0"):  # released before the pool
        sys.exit("FAIL step list: adoption_kx draws the adoption key through "
                 "ieee80211_rng_key_bytes, which entropy_pool provides. A key "
                 "from the timer is the 2026-11-08 audit's S1; add entropy_pool.")
    if "ccmp_replay_enforce" in steps and (
            "ccmp_replay_count" not in steps
            or steps.index("ccmp_replay_count") > steps.index("ccmp_replay_enforce")):
        sys.exit("FAIL step list: ccmp_replay_enforce refuses what "
                 "ccmp_replay_count counts; it must come after it.")
    # The N/p/V fields are appended to the line dcc_telemetry writes.
    if ("ccmp_replay_count" in steps and "dcc_telemetry" in steps
            and steps.index("dcc_telemetry") > steps.index("ccmp_replay_count")):
        sys.exit("FAIL step list: ccmp_replay_count must come after "
                 "dcc_telemetry, whose status line it extends.")
    if "no_wep" in steps and "no_tkip_pairwise" not in steps:
        sys.exit("FAIL step list: no_wep refuses a WEP network inside the refusal "
                 "no_tkip_pairwise installs; add no_tkip_pairwise.")
    if ("no_tkip_code" in steps and "ccmp_replay_count" in steps
            and steps.index("ccmp_replay_count") < steps.index("no_tkip_code")):
        sys.exit("FAIL step list: no_tkip_code must come before ccmp_replay_count, "
                 "which rewrites the key installs it removes one of.")
    if "no_tkip_code" in steps and "no_tkip_pairwise" not in steps:
        sys.exit("FAIL step list: no_tkip_code removes the code that serves a "
                 "TKIP pairwise cipher; without no_tkip_pairwise that cipher "
                 "is still negotiated. Add no_tkip_pairwise.")
    if "rx_protected" in steps and "wpa2_gtk" not in steps:
        sys.exit("FAIL step list: rx_protected sets eapol_secure where message 3 "
                 "ends, text wpa2_gtk owns; add wpa2_gtk.")
    if "no_libc_malloc" in steps and "extbss" not in steps:
        sys.exit(
            "FAIL step list: no_libc_malloc changes the linked object set, so "
            "the 85 orphaned .bss.* sections move inside .bss and land on the "
            "stacks that init.s reserves -- the 2026-06-20 brick. Add extbss, "
            "which places .bss.* explicitly and moves 8 KB of scratch to "
            "external RAM. See patch_no_libc_malloc's docstring."
        )
    if "persist_counters" in steps and "extbss" not in steps:
        sys.exit(
            "FAIL step list: persist_counters puts the diagnostic counters in "
            ".extbss, and extbss is the step that declares that section. "
            "Without it they land in .bss and the startup zero-loop erases "
            "them -- which is the whole thing this step exists to stop."
        )
    if "factory_server" in steps and "bootloader" not in steps:
        sys.exit("FAIL step list: factory_server edits the bootloader, which "
                 "minimal must keep byte-identical to vanilla wpa2.")
    if "firmware_version" in steps and "bootloader" not in steps:
        sys.exit(
            "FAIL step list: firmware_version edits the bootloader, and a step "
            "list without `bootloader` is minimal's -- whose bootloader must "
            "stay byte-identical to vanilla wpa2."
        )
    if "reset_reason" in steps and (
            "persist_counters" not in steps
            or steps.index("persist_counters") > steps.index("reset_reason")):
        sys.exit(
            "FAIL step list: reset_reason writes next to the .extbss counters "
            "and into the power-up block that persist_counters creates -- it "
            "must come after persist_counters."
        )
    # flash_no_return rewrites the loop flash_verify leaves in flash_uc and
    # stores into nab_flash_error, which flash_verify declares; and a writer
    # that never comes back needs every refusal made before it is called.
    if "flash_no_return" in steps:
        for dep in ("flash_verify", "flash_precheck"):
            if dep not in steps or steps.index(dep) > steps.index("flash_no_return"):
                sys.exit(f"FAIL step list: flash_no_return must come after {dep}.")
    if "flash_precheck" in steps and "vinterp" not in steps:
        sys.exit("FAIL step list: flash_precheck extends the guard vinterp puts "
                 "on flashFirmware.")
    if "persist_counters" in steps:
        order = steps.index("persist_counters")
        for dep in ("stdlib", "flash_verify"):
            if dep in steps and steps.index(dep) > order:
                sys.exit(
                    f"FAIL step list: persist_counters must run after {dep}, "
                    "which is what declares the counters it moves."
                )
    # The 2026-11-08 audit's EAPOL steps edit text the earlier steps leave:
    # eapol_ptk_install moves the replay commit eapol_replay introduced and
    # the message-1 path eapol_ptk_rekey opened; eapol_mic_len and
    # gtk_no_reinstall anchor on wpa2_gtk's and group_cipher's handlers;
    # sae_required shares buffer_temp and SHA-256 with entropy_pool and
    # wpa3_sae. Order, like the others.
    for step_name, deps in (
            ("eapol_ptk_install", ("eapol_replay", "eapol_ptk_rekey", "wpa2_gtk")),
            ("eapol_mic_len", ("wpa2_gtk",)),
            ("gtk_no_reinstall", ("wpa2_gtk", "group_cipher", "wpa3_sae")),
            ("no_tkip_pairwise", ("wpa3_sae",)),
            ("sae_required", ("wpa3_sae", "entropy_pool"))):
        if step_name in steps:
            for dep in deps:
                if dep not in steps or steps.index(dep) > steps.index(step_name):
                    sys.exit(f"FAIL step list: {step_name} must come after {dep}, "
                             "whose text it edits or relies on.")
    fns = {
        "eapol_replay": patch_eapol_replay,
        "cold_state": patch_cold_state,
        "vbc": patch_vbc_h,
        "vinterp": patch_vinterp_c,
        "stdlib": patch_stdlib_core,
        "strip_tweetnacl": strip_tweetnacl,
        "x25519_wdt": patch_x25519_wdt,
        "adoption_kx": patch_adoption_kx,
        "crash_report": patch_crash_report,
        "tls_psk": patch_tls_psk,
        "adoption_boot": patch_adoption_boot,
        "sign_open_alias": patch_sign_open_aliasing,
        "verify_wdt": patch_verify_wdt,
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
        "wpa3_boot_pw": patch_wpa3_boot_password,
        "wpa3_timing": patch_wpa3_timing,
        "wpa3_eapol_akm": patch_wpa3_eapol_akm,
        "entropy_pool": patch_entropy_pool,
        "rx_protected": patch_rx_protected,
        "eapol_ptk_install": patch_eapol_ptk_install,
        "eapol_mic_len": patch_eapol_mic_len,
        "gtk_no_reinstall": patch_gtk_no_reinstall,
        "no_tkip_pairwise": patch_no_tkip_pairwise,
        "probe_ssid_bound": patch_probe_ssid_bound,
        "config_no_password": patch_config_no_password,
        "beacon_ie_bounds": patch_beacon_ie_bounds,
        "usb_descriptor_bounds": patch_usb_descriptor_bounds,
        "mixed_gtk": patch_mixed_gtk,
        "violet_hardening": patch_violet_hardening,
        "strip_factory_test": patch_strip_factory_test,
        "strip_eapol_dump": patch_strip_eapol_dump,
        "uart_silent": patch_uart_silent,
        "no_tkip_code": patch_no_tkip_code,
        "stub_soft_codecs": patch_stub_soft_codecs,
        "no_wep": patch_no_wep,
        "no_wep_boot": patch_no_wep_boot,
        "share_page_css": patch_share_page_css,
        "sae_required": patch_sae_required,
        "wpa3_h2e": patch_wpa3_h2e,
        "eapol_msg2_ie": patch_eapol_msg2_ie,
        "boot_marker": patch_boot_marker,
        "flash_verify": patch_flash_verify,
        "flash_precheck": patch_flash_precheck,
        "flash_no_return": patch_flash_no_return,
        "usb_robustness": patch_usb_robustness,
        "rx_recovery": patch_rx_recovery,
        "extbss": patch_extbss,
        "persist_counters": patch_persist_counters,
        "firmware_version": patch_firmware_version,
        "factory_server": patch_factory_server,
        "boot_tls": patch_boot_tls,
        "reset_reason": patch_reset_reason,
        "dcc_console": patch_dcc_console,
        "dcc_telemetry": patch_dcc_telemetry,
        "link_liveness": patch_link_liveness,
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
        "uart_no_sprintf": patch_uart_no_sprintf,
        "strip_dbg_sprintf": patch_strip_dbg_sprintf,
        "md5_compact": patch_md5_compact,
        "share_sbox": patch_share_sbox,
        "prune_dead_funs": patch_prune_dead_funs,
        "small_strings": patch_small_strings,
        "http_cap": patch_http_cap,
        "boot_https": patch_boot_https,
        "owner_unlock": patch_owner_unlock,
        "kx_report": patch_kx_report,
        "kx_probe": patch_kx_probe,
        "ccmp_replay_count": patch_ccmp_replay_count,
        "ccmp_replay_enforce": patch_ccmp_replay_enforce,
        "rfic_report": patch_rfic_report,
        "tls_certs": patch_tls_certs,
        "opt_size": patch_opt_size,
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
