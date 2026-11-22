#!/usr/bin/env python3
"""Draw the five jumpers from a Nabaztag v1's programming header J2 to a
PICkit, to READ its firmware (never to write it).

    ./mkwiring.py -o wiring.svg

Made from ../v1-serial/mkwiring.py: same picture, other table. The v1's
PIC18LF6627 (Clara's v1, 2026-11-22: "PIC18LF6…", 0443; Newbaztag read a
PIC18F6627 the same way) is programmed over ICSP on J2. Violet's schematic
(the FCC filing, ../vendor/newbaztag/doc/schematics/schematics-630159.pdf)
gives J2 as MCLR, 3.3v, GND, RB7 (PGD), RB6 (PGC): the same order as a
PICkit's own pins 1 to 5, so the wires go straight across.
"""

from __future__ import annotations

import argparse
from pathlib import Path

# J2, as Violet's schematic numbers it, and the PICkit pin each goes to.
RABBIT = [
    (1, "MCLR", "MCLR/VPP", 1),
    (2, "3V3", "VDD (sense only)", 2),
    (3, "GND", "VSS", 3),
    (4, "PGD", "ICSPDAT/PGD", 4),
    (5, "PGC", "ICSPCLK/PGC", 5),
]
PICKIT = {1: "MCLR/VPP", 2: "VDD", 3: "VSS (GND)", 4: "ICSPDAT", 5: "ICSPCLK", 6: "LVP/AUX"}
WIRE_COLOUR = {1: "#7c3aed", 2: "#c2410c", 3: "#444444", 4: "#0369a1", 5: "#15803d"}

BG, INK, MUTED = "#fdfdfb", "#161616", "#6b6b6b"
PAD = 30
RX, RY = 190, 160          # rabbit header origin
KX, KY = 700, 160          # PICkit header origin
W, H = 1040, 720


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", type=Path, default=Path("wiring.svg"))
    args = ap.parse_args()
    s: list[str] = []
    add = s.append
    add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" font-size="13">')
    add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
    add(f'<text x="{W//2}" y="34" text-anchor="middle" fill="{INK}" font-size="17" font-weight="600">'
        f'Nabaztag v1 firmware read → PICkit (ICSP)</text>')
    add(f'<text x="{W//2}" y="54" text-anchor="middle" fill="{MUTED}">five jumpers, read only — '
        f'the rabbit stays on its own 8 V adapter, the PICkit does not power it</text>')

    add(f'<text x="{RX - 150}" y="{RY - 30}" fill="{INK}" font-weight="600">rabbit, J2 (5-pin header)</text>')
    add(f'<text x="{RX - 150}" y="{RY - 13}" fill="{MUTED}" font-size="11">'
        f'next to the Atmel AT45DB081B; GND is the middle pin</text>')
    rxy = {}
    for i, (pin, name, _, k) in enumerate(RABBIT):
        y = RY + i * PAD
        c = WIRE_COLOUR[k]
        add(f'<rect x="{RX}" y="{y}" width="16" height="16" rx="3" fill="{c}" stroke="{c}"/>')
        add(f'<text x="{RX - 70}" y="{y + 12}" text-anchor="end" fill="{MUTED}">{pin}</text>')
        add(f'<text x="{RX - 10}" y="{y + 12}" text-anchor="end" fill="{INK}" font-weight="600">{name}</text>')
        rxy[pin] = (RX + 16, y + 8)

    add(f'<text x="{KX}" y="{KY - 30}" fill="{INK}" font-weight="600">PICkit 3 / 4 (6-pin header)</text>')
    add(f'<text x="{KX}" y="{KY - 13}" fill="{MUTED}" font-size="11">pin 1 is the one under the ▶ arrow</text>')
    kxy = {}
    for i in range(6):
        pin = i + 1
        y = KY + i * PAD
        used = pin in WIRE_COLOUR
        c = WIRE_COLOUR.get(pin, "#cfcfcf")
        add(f'<rect x="{KX}" y="{y}" width="16" height="16" rx="3" fill="{c if used else BG}" stroke="{c}"/>')
        if pin == 1:
            add(f'<text x="{KX - 16}" y="{y + 13}" fill="{INK}" font-size="13">▶</text>')
        add(f'<text x="{KX + 28}" y="{y + 12}" fill="{c if used else MUTED}" '
            f'{"font-weight=\"600\"" if used else ""}>pin {pin}'
            f'<tspan fill="{MUTED}" font-weight="400"> · {PICKIT[pin]}'
            f'{"" if used else "  (leave free)"}</tspan></text>')
        kxy[pin] = (KX, y + 8)

    for pin, name, _, k in RABBIT:
        x1, y1 = rxy[pin]
        x2, y2 = kxy[k]
        mid = (x1 + x2) / 2
        add(f'<path d="M {x1 + 2} {y1} C {mid} {y1}, {mid} {y2}, {x2 - 2} {y2}" fill="none" '
            f'stroke="{WIRE_COLOUR[k]}" stroke-width="2.2" stroke-linecap="round"/>')

    ly = RY + 6 * PAD + 40
    add(f'<text x="{RX - 150}" y="{ly}" fill="{INK}" font-weight="600">the five jumpers, straight across</text>')
    for i, (pin, name, kname, k) in enumerate(RABBIT):
        y = ly + 22 + i * 19
        add(f'<rect x="{RX - 150}" y="{y - 9}" width="11" height="11" rx="2" fill="{WIRE_COLOUR[k]}"/>')
        add(f'<text x="{RX - 132}" y="{y}" fill="{INK}" font-size="12">rabbit {pin} {name}'
            f'<tspan fill="{MUTED}">  →  </tspan>PICkit {k} '
            f'<tspan fill="{MUTED}">{kname}</tspan></text>')

    y = H - 118
    add(f'<text x="40" y="{y}" fill="{INK}" font-weight="600">before plugging in</text>')
    for i, line in enumerate((
        "J2 has no pin 1 mark: GND (pin 3, the middle) has continuity to the 8 V adapter's black wire;",
        "  rabbit powered, pin 2 reads 3.3 V and pin 4 does not: pin 1 is the end next to pin 2.",
        "Both power supplies on one earthed strip (a USB laptop on battery is fine): no leakage shocks.",
        "MPLAB IPE: device PIC18LF6627, Power > \"Power target circuit from tool\" OFF (the rabbit has its own).",
        "Then Read, and Export > Hex. Never Program or Erase: Violet's firmware has no other copy.",
    )):
        add(f'<text x="40" y="{y + 20 + i * 17}" fill="{MUTED}" font-size="11">• {line}</text>')
    add("</svg>")
    args.output.write_text("\n".join(s) + "\n")
    print(f"{args.output}: {len(s)} elements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
