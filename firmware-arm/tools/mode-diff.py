#!/usr/bin/env python3
"""How apply-mods.py's modes differ: each one against a base, as steps.

    firmware-arm/tools/mode-diff.py                       # every naboot-1.2.* mode against naboot-1.2.0.1
    firmware-arm/tools/mode-diff.py naboot-1.2.0.3        # every mode against that one
    firmware-arm/tools/mode-diff.py naboot-1.2.0.3 naboot-1.2.0.3-tel

Every naboot-* mode is a full copy of an 80-odd step list, so a step left
out of one variant is invisible in a diff of the file (review C-2,
2026-11-18: naboot-1.2.0.1-kxprobe has no owner_unlock -- on purpose, the
comment above it says, to fit the flash). This prints, per mode, the steps
it adds (+), the steps it lacks (-), and whether the steps it shares keep
the base's order. tests/apply_mods/test_review_fixes.py pins the variants
that must stay a base plus known extras.
"""

import re
import sys
from pathlib import Path

APPLY = Path(__file__).resolve().parents[1] / "apply-mods.py"


def modes() -> dict[str, list[str]]:
    src = APPLY.read_text()
    m = re.search(r"default_steps = \{(.*?)\n    \}\n", src, re.S)
    out = {}
    for name, steps in re.findall(r'"([^"]+)":\s*"([^"]*)"', m.group(1)):
        out[name] = steps.split(",")
    return out


def diff(base: list[str], other: list[str]) -> tuple[list[str], list[str], bool]:
    added = [s for s in other if s not in base]
    missing = [s for s in base if s not in other]
    shared = [s for s in other if s in base]
    ordered = shared == [s for s in base if s in other]
    return added, missing, ordered


def main() -> int:
    all_modes = modes()
    args = sys.argv[1:]
    base = args[0] if args else "naboot-1.2.0.1"
    if base not in all_modes:
        sys.exit(f"unknown mode {base}; modes: {', '.join(all_modes)}")
    todo = args[1:] or [m for m in all_modes if m.startswith("naboot-1.2.") and m != base]
    print(f"base {base}: {len(all_modes[base])} steps")
    for name in todo:
        if name not in all_modes:
            sys.exit(f"unknown mode {name}")
        added, missing, ordered = diff(all_modes[base], all_modes[name])
        bits = [f"+{s}" for s in added] + [f"-{s}" for s in missing]
        print(f"{name:32s} {' '.join(bits) or '(same steps)'}"
              f"{'' if ordered else '  ORDER DIFFERS'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
