# Naboot full — config-mode AP regression — forensics & recovery plan

Loutre flashed `naboot-full.signed.sim` on Nabi. The rabbit no longer brings
up its config-mode AP (the `Nabaztag-XXXX` SSID never appears). Recovery
needs JTAG (DollaTek J-Link V8 ordered, ETA ~3-5 jours), then we can
A/B-test which patch broke things.

This document captures the night's static analysis so we can hit the ground
running the moment the probe arrives. **None of this is hardware-verified
yet** — it's a hypothesis ranking, not a fix.

---

## What we know

* `boot.0.0.0.13.mtl` is the **Metal bootloader bytecode** embedded into the
  native firmware. The C startup calls `main` (Metal), which:
  1. Reads the head button → `master`
  2. Loads config from flash (`confInit`)
  3. **Calls `wifiInit 1`** — this is what gates the AP
  4. Registers the `loop` callback (`loopcb #loop`)
  5. Starts the network stack (`netstart`)
  6. Sets LEDs (blue if `master`, magenta otherwise)
* If main() crashes BEFORE `wifiInit`, no AP. If it crashes AFTER but
  before `wifiRun` ever transitions to `gomasterW`, no AP either.
* `full` vs `minimal` differs ONLY in `boot.mtl` content. The C side
  (`vinterp.c` OPverifySig case, `nab_sig.c`, `tweetnacl.c`-stripped,
  pubkey header) is identical. So if `minimal` works and `full` doesn't,
  the regression is in the boot.mtl changes.
* The build log confirms boot.mtl compiles cleanly — the compiler prints
  `Compiler : done !` and produces 82-byte `main`, 96-byte `getsigreq`,
  107-byte `httpflash`. So the issue (if any) is **runtime semantic**,
  not parse-time.
* `--gc-sections` is NOT in the linker line (confirmed by reading the
  `bin/.build.log`). The upstream `Makefile` ships a commented
  `#~ LDFLAGS += -Wl,--gc-sections`, and our old idempotency check
  `if "gc-sections" not in s` matched the comment → silently no-opped the
  patch. So that linker flag never landed. **Lucky** — `ml67q4051.ld`
  has zero `KEEP()` directives, so `--gc-sections` would have pruned
  `.intvec` (only referenced by the ARM7 hardware, not by C code) →
  vector-table garbage → IRQ-time crash → exactly the failure mode we
  saw. That apply-mods bug is now fixed (and gc-sections is no longer
  enabled by default — see `apply-mods.py:patch_makefile`).

## Suspect ranking (boot.mtl-side, since C-side is identical)

| # | Suspect | Likelihood | Why |
|---|---------|------------|-----|
| 1 | `modernize_pages` — replaced `page_a/done/u/error` with new HTML | **High** | Touches the most code. Static analysis found two real differences vs vanilla: (a) the new `page_a.html` is **missing** `<LOGIN>` and `<PWD>` markers that `httpindex` still tries to substitute (functional regression but not crash), (b) it produces a many-element cons list `"frag"::"<MARKER>"::"frag"::nil` whereas the original mixes `::nil` and `::"frag"` in the middle of the list (intentionally weird upstream syntax) — structurally similar but not byte-identical. `httpindex`/`cbhttp` are not called from `main()`, so this would only crash AFTER the AP comes up — *unless* the bytecode top-level `var page_a=…` initialization runs at load-time and fails then. |
| 2 | `patch_bootloader` — rewrote `httpflash` + added `const sigmarker` and `fun getsigreq` | Medium | `httpflash` and `getsigreq` are not called from `main()` — only when a `.sim` is uploaded. The new `const sigmarker="-sig-";;` is evaluated at load-time. If it conflicted with something, would manifest immediately. Reviewed: the new `httpflash` has nested `if A then if B then if C then X else Y` — Metal's dangling-`else` binds `else` to the innermost `if`, which is what we want. |
| 3 | `strip_tweetnacl` (C side) | Low | Only removes unused TweetNaCl entries. The regex is anchored on `^(int|void)\s+<name>\s*\(`, so it can't over-match. |
| 4 | `patch_vinterp_c` (new OPverifySig case) | Low | The C `case OPverifySig:` is unreachable from `main()` (Metal `verifySig` is only called by the new `httpflash`). Adding a switch case doesn't corrupt the other cases. |
| 5 | `strip_tweetnacl` over-aggressive regex | Very low | The unused-list is sorted longest-first specifically to avoid prefix collisions. Manually verified the regex anchors. |

## Hypothesis A — most likely

**`modernize_pages` produces a valid-but-different page_a cons list, and
some side-effect of how/when the bytecode loader allocates the constant
strings overruns memory.** The new page_a is ~5 KB smaller than the
original, but it has **more elements** in the list (one per fragment, vs
the original's ~30 elements). Each Metal list element is a cons cell
plus a string heap allocation; the bytecode loader allocates these at
boot from the VM heap (`VMALLOC*`). On a 16 KB SRAM device, even a few
hundred extra bytes of heap fragmentation could starve `wifiInit`'s
allocations.

This is the leading candidate because:
* It's the only patch that touches code reached at bytecode load time
  (top-level `var` initialization runs before `main()`).
* It changes the heap allocation pattern significantly.
* The C-side patches don't touch any allocator.

## Hypothesis B — backup

**Some marker in the new page_a is being matched by Metal's tokenizer.**
The HTML contains CSS like `var(--bg)` — `var` is a Metal keyword. If
the compiler's string lexer has a bug that lets `var` "leak" inside a
string literal, the bytecode would be corrupted. **Strongly doubt** —
this would have shown up in the compile log; we verified `Compiler :
done !` cleanly.

## Builds ready to flash (built on the VPS, 2026-05-31)

Six variants signed and waiting under `firmware-arm/bin/`:

```
firmware-arm/bin/
├── naboot-minimal.signed.sim       238 706 B   (RESCUE — vanilla boot.mtl)
├── naboot-signed-stock.signed.sim  238 098 B   (bisect: bootloader patch only)
├── naboot-pages-only.signed.sim    228 706 B   (bisect: page rewrite only)
├── naboot-mdns-only.signed.sim     238 850 B   (bisect: mdns only)
├── naboot-full.signed.sim          229 058 B   (modernized UI + sig gate)
└── naboot-max.signed.sim           230 162 B   (full + mdns; daily-driver)
```

All six pass `verify_sim.py` against `keys/signing_pubkey.h`. Build was
done with `./build.sh --remote rocky@vps-ee4c4993.vps.ovh.net:10022
--runtime podman --mode <mode>` (build.sh supports `--runtime podman`
and `host:port` remotes).

What `max` adds vs `full`: the boot-side mDNS announcer
(`boot-mods/mdns.mtl`) — sends a "naboot.local → <netip>" mDNS A
response to 224.0.0.251:5353 every 60 s. Wire-identical design to the
runtime `firmware/mdns.mtl`. Useful both in config mode
(`naboot.local → 192.168.0.1` so the setup page is reachable by name)
and in station mode (so HA / SSH can find the rabbit without IP
scanning). +1.1 KB on disk, +552 B on the ARM7's flash. UNTESTED on
hardware as of writing — flash `mdns-only` first as a smoke test if
you want to be conservative.

Sanity-check vs the previous full build: the new `full/Nab.bin` is 2.7 KB
SMALLER than the previously-bricked `bin/Nab.bin` (114,448 vs 117,204).
That's likely a minor GCC patch update between yesterday's HAOS-Docker
build and tonight's VPS build (same Debian bookworm major, possibly
a different point release of `gcc-arm-none-eabi`). If `full` works on
re-flash, the bug may have been a transient compiler quirk, not our
patches — worth retrying `full` once recovery is done.

## Recovery plan (when J-Link arrives)

The order maximizes information per flash.

### Step 1 — Dump the bricked firmware (BEFORE re-flashing)

```sh
# Wire the J-Link to the rabbit's JTAG pads (TDI/TDO/TCK/TMS/GND/3V3).
# OpenOCD interface for the DollaTek J-Link V8:
openocd \
  -f interface/jlink.cfg \
  -c "transport select jtag" \
  -c "adapter speed 1000" \
  -f target/ml67q40xx.cfg \
  -c "init; halt; dump_image bricked.bin 0x08000000 0x1F000; exit"
```

The dump (`bricked.bin`, 124 KB) is the EXACT image Loutre is running.
Diff against `firmware-arm/bin/full/Nab.bin` to confirm what shipped.

### Step 2 — Flash `minimal` (known-good safety net)

```sh
openocd \
  -f interface/jlink.cfg \
  -c "transport select jtag" \
  -c "adapter speed 1000" \
  -f target/ml67q40xx.cfg \
  -c "init; halt; program firmware-arm/bin/minimal/Nab.bin verify reset exit 0x08000000"
```

`minimal` has `boot.mtl` **byte-identical** to vanilla `wpa2` HEAD —
exactly the firmware Kevin's sysadmin already flashed successfully. If
config mode comes back here, we've confirmed the regression is in
`boot.mtl` (rules out Hypothesis B etc).

### Step 3 — Bisect via OTA (no more JTAG needed)

`minimal` has the `verifySig` opcode wired in, so it can OTA-upgrade
to a signed `.sim` from any future build. From the config portal:

```sh
# Re-build the bisection variants on the HAOS host. The new modes
# isolate ONE risky patch at a time on top of minimal.
cd firmware-arm
./build.sh --mode signed-stock   # = minimal + httpflash sig gate (no page changes)
./build.sh --mode pages-only     # = minimal + modernized UI (no sig gate)
```

Then in config mode (head-button + power → join `Nabaztag-XXXX` AP →
`192.168.0.1/u.htm`):

1. Upload `bin/signed-stock/firmware0.0.0.13.signed.sim`.
   * **AP still works after reboot** → the bootloader patch is safe,
     bug is in `modernize_pages`. Go to step 3b.
   * **AP gone again** → bug is in `patch_bootloader` (or `sigmarker`
     init). Recover via JTAG → minimal.
2. (3b) Upload `bin/pages-only/firmware0.0.0.13.signed.sim`.
   * **AP still works** → both individual patches are safe but their
     interaction in `full` regressed. Investigate the size delta /
     heap pressure of having both.
   * **AP gone** → confirmed: `modernize_pages` is the bug. Investigate
     the page_a cons-list shape. The leading suspect is heap
     fragmentation from too many list elements. Fix candidate: merge
     consecutive non-marker fragments into one big string (the marker
     is the only thing that needs to be its own element).
3. (only after `full` proves clean) Upload `bin/mdns-only/...signed.sim`
   to validate the mDNS code on hardware. Once it works, upload
   `bin/max/...signed.sim` for the everything-bag daily driver.

## Fixes shipped tonight (in apply-mods.py)

1. `patch_makefile` no longer tries to enable `--gc-sections`. The
   linker script lacks `KEEP()` directives → enabling gc-sections would
   prune `.intvec` → IRQ crash → AP failure. The previous idempotency
   check `if "gc-sections" not in s` accidentally protected us from
   ever shipping it; restructured so we don't depend on that
   accident. Future work: add `KEEP(*(.intvec)) KEEP(*(.startup.*))
   KEEP(*(.ramfunc))` to `sys/ml67q4051.ld` and re-enable.
2. New build modes: `--mode signed-stock` (sig wiring + bootloader
   patch, no page changes) and `--mode pages-only` (sig wiring + page
   changes, no bootloader patch). Used for the bisection in Step 3
   above.

## Open questions

* Is the bytecode loader (in `vloader.c`) allocating the full string
  table at load time, or lazily? If lazy, hypothesis A is weaker.
* What's the exact heap pressure from the new page_a cons list vs
  vanilla? Need to count cons cells + string heap usage. The compiler
  output shows main is 82 bytes either way, but the data sections
  differ.
* Is there a UART log we can capture during boot to see how far main()
  gets? The DEBUG_VM/AUDIO/MAIN flags are now disabled in our build —
  re-enabling them temporarily for a debug build (`./build.sh --mode
  minimal` then manually un-comment in Makefile) would give us
  `MACecho` / `Secholn` output over UART.
