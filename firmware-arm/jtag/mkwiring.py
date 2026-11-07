#!/usr/bin/env python3
"""Draw the five jumpers, so nobody has to rebuild this from a forum post.

    ./mkwiring.py -o wiring.svg

The rabbit's header has no silkscreen and the Pi's has forty pins, which is
exactly the combination that produces TDI and TDO swapped and an afternoon
of "all zeroes". So the picture is generated, lives next to the OpenOCD
config, and is regenerated from this one table if anything ever changes.

Colours are explicit and the background is opaque: a transparent SVG with
dark strokes disappears in a dark-themed README.
"""

from __future__ import annotations

import argparse
from pathlib import Path

# The rabbit's 8-pin header, top to bottom, board seen from the front.
RABBIT = [
    (1, "3V3", "Vref — leave unconnected", None),
    (2, "GND", "", 20),
    (3, "nTRST", "leave unconnected", None),
    (4, "TDI", "", 19),
    (5, "TMS", "", 24),
    (6, "TCK", "", 23),
    (7, "TDO", "", 21),
    (8, "RESETN", "leave unconnected — see README", None),
]

# Pi 40-pin header: what each used pin is, for the label beside it.
PI_NAMES = {19: "GPIO10", 20: "GND", 21: "GPIO9", 23: "GPIO11", 24: "GPIO8"}

WIRE_COLOUR = {
    20: "#444444",   # ground, drawn dark like the wire it should be
    19: "#c2410c",
    24: "#047857",
    23: "#1d4ed8",
    21: "#a21caf",
}

BG, INK, MUTED = "#fdfdfb", "#161616", "#6b6b6b"
PAD, PADY = 26, 24          # rabbit pin pitch, Pi pin pitch
RX, RY = 150, 150           # rabbit header origin
PX, PY = 620, 118           # Pi header origin (left column)
W, H = 1040, 760


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", type=Path, default=Path("wiring.svg"))
    args = ap.parse_args()

    s: list[str] = []
    add = s.append
    add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" font-family="ui-monospace,SFMono-Regular,'
        f'Menlo,Consolas,monospace" font-size="13">')
    add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
    add(f'<text x="{W//2}" y="34" text-anchor="middle" fill="{INK}" '
        f'font-size="17" font-weight="600">Nabaztag/tag v2 JTAG → Raspberry Pi'
        f'</text>')
    add(f'<text x="{W//2}" y="54" text-anchor="middle" fill="{MUTED}">'
        f'five jumpers, no probe — the rabbit stays on its own mains adapter'
        f'</text>')

    # ---- the rabbit header
    add(f'<text x="{RX - 110}" y="{RY - 28}" fill="{INK}" font-weight="600">'
        f'rabbit, 8-pin header</text>')
    add(f'<text x="{RX - 110}" y="{RY - 12}" fill="{MUTED}" font-size="11">'
        f'top-left of the board, front view, under the base</text>')
    rabbit_xy = {}
    for i, (pin, name, note, pi) in enumerate(RABBIT):
        y = RY + i * PAD
        used = pi is not None
        col = WIRE_COLOUR.get(pi, "#d4d4d4") if used else "#d4d4d4"
        add(f'<rect x="{RX}" y="{y}" width="16" height="16" rx="3" '
            f'fill="{col if used else BG}" stroke="{col if used else "#bbbbbb"}"/>')
        # Numbers and names to the LEFT of the pins. To the right they sat
        # exactly on the wires leaving those same pins, and every signal
        # name came out struck through.
        add(f'<text x="{RX - 76}" y="{y + 12}" text-anchor="end" '
            f'fill="{MUTED}">{pin}</text>')
        add(f'<text x="{RX - 10}" y="{y + 12}" text-anchor="end" '
            f'fill="{INK if used else MUTED}" '
            f'{"font-weight=\"600\"" if used else ""}>{name}</text>')
        # The per-pin notes used to be printed here, and the wires ran
        # straight through them. One line under the block says the same
        # thing and nothing crosses it.
        rabbit_xy[pin] = (RX + 16, y + 8)


    # ---- the Pi header, odd pins left, even pins right, pin 1 top-left
    add(f'<text x="{PX}" y="{PY - 30}" fill="{INK}" font-weight="600">'
        f'Raspberry Pi, 40-pin header</text>')
    add(f'<text x="{PX}" y="{PY - 14}" fill="{MUTED}" font-size="11">'
        f'physical pins — same on a 2, 3, 4 or Zero 2 W</text>')
    add(f'<text x="{PX - 4}" y="{PY - 2}" fill="{MUTED}" font-size="10">'
        f'pin 1</text>')
    pi_xy = {}
    label_x = PX + 66
    for row in range(20):
        y = PY + row * PADY
        for col, pin in ((0, row * 2 + 1), (1, row * 2 + 2)):
            x = PX + col * 28
            used = pin in PI_NAMES
            c = WIRE_COLOUR.get(pin, "#d4d4d4")
            add(f'<rect x="{x}" y="{y}" width="15" height="15" rx="3" '
                f'fill="{c if used else BG}" '
                f'stroke="{c if used else "#cfcfcf"}"/>')
            if used:
                # odd pin above the row line, even pin below it: pins 19/20
                # and 23/24 share a row and would otherwise print on top of
                # each other.
                dy = 1 if col == 0 else 15
                add(f'<text x="{label_x}" y="{y + dy}" fill="{c}" '
                    f'font-size="11.5" font-weight="600">pin {pin}'
                    f'<tspan fill="{MUTED}" font-weight="400">\u00a0·\u00a0'
                    f'{PI_NAMES[pin]}</tspan></text>')
            pi_xy[pin] = (x, y + 7)

    # ---- the wires, drawn behind nothing and labelled on the curve
    for pin, name, note, pi in RABBIT:
        if pi is None:
            continue
        x1, y1 = rabbit_xy[pin]
        x2, y2 = pi_xy[pi]
        mid = (x1 + x2) / 2
        add(f'<path d="M {x1 + 2} {y1} C {mid} {y1}, {mid} {y2}, {x2 - 2} {y2}" '
            f'fill="none" stroke="{WIRE_COLOUR[pi]}" stroke-width="2.2" '
            f'stroke-linecap="round"/>')

    # ---- the legend, which is the thing people actually copy
    ly = RY + 9 * PAD + 30
    add(f'<text x="{RX - 110}" y="{ly - 20}" fill="{MUTED}" font-size="11">'
        f'pins 1, 3 and 8 stay unconnected — 3V3 is only a Vref, '
        f'and RESETN is a trap</text>')
    add(f'<text x="{RX - 110}" y="{ly}" fill="{INK}" font-weight="600">'
        f'the five jumpers</text>')
    for i, (pin, name, note, pi) in enumerate(w for w in RABBIT if w[3]):
        y = ly + 22 + i * 19
        add(f'<rect x="{RX - 110}" y="{y - 9}" width="11" height="11" rx="2" '
            f'fill="{WIRE_COLOUR[pi]}"/>')
        add(f'<text x="{RX - 92}" y="{y}" fill="{INK}" font-size="12">'
            f'rabbit {pin} {name}'
            f'<tspan fill="{MUTED}">\u00a0\u00a0→\u00a0\u00a0</tspan>'
            f'Pi {pi} <tspan fill="{MUTED}">{PI_NAMES[pi]}</tspan></text>')

    # ---- the three things that bite, on the picture itself
    y = H - 96
    add(f'<text x="40" y="{y}" fill="{INK}" font-weight="600">'
        f'and three things that bite</text>')
    for i, line in enumerate((
        "RESETN stays unconnected. Wired, this chip answers DBGACK timeout,",
        "and reset halt does not work on it however you configure it.",
        "The Pi does not power the rabbit: an unpowered board never answers.",
        "The first halt on a running rabbit often times out — run it again.",
    )):
        add(f'<text x="40" y="{y + 20 + i * 17}" fill="{MUTED}" '
            f'font-size="11">• {line}</text>')

    add("</svg>")
    args.output.write_text("\n".join(s) + "\n")
    print(f"{args.output}: {len(s)} elements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
