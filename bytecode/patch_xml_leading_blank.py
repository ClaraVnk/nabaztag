#!/usr/bin/env python3
"""Blanks ahead of a stanza no longer swallow it.

Violet's ParseXMLPacket handed back, for text in front of an element, the
text as the rest of the buffer: " <message>...</message>" left " " in
xSession.sGate.pBuffer and the message was gone. The next read appended to
that blank and lost its stanza the same way, and so on until the rabbit was
unplugged. A whitespace keepalive from the server (the warren sent one every
45 s until terrier #140) was enough to deafen a rabbit for good, on
2026-11-11, on two rabbits on 1.0.0.3.

Now ParseXMLPacket skips spaces, tabs and line ends before parsing, and a
buffer of nothing but blanks is consumed whole. Any other text in front of
an element is treated as before.

Patches sources/xmlparser.mtl; independent of every other patch. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* xml-leading-blank */"

OLD = ("fun ParseXMLPacket XML =\n"
       "    let ParseXML XML -> xml in\n")
NEW = ("/* xml-leading-blank */\n"
       "// Index of the first character that is not a blank.\n"
       "// See bytecode/patch_xml_leading_blank.py.\n"
       "fun XmlBlankEnd s i n =\n"
       "\tif i >= n then i\n"
       "\telse let strget s i -> c in\n"
       "\tif c==32 || c==10 || c==13 || c==9 then XmlBlankEnd s (i+1) n else i;;\n\n"
       "fun ParseXMLPacket XML =\n"
       "    let if XML == nil then 0 else strlen XML -> n in\n"
       "    let XmlBlankEnd XML 0 n -> i in\n"
       "    if n != 0 && i >= n then [inXml:nil EndStrS:nil]\t// blanks only: consumed\n"
       "    else\n"
       "    let ParseXML (if i == 0 then XML else strsub XML i nil) -> xml in\n")


def patch(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("latin-1"), "latin-1"   # Violet's sources carry 8-bit comments
    if MARKER in text:
        print(f"[skip] {path}: blanks ahead of a stanza already skipped")
        return False
    if text.count(OLD) != 1:
        sys.exit(f"FAIL {path}: expected one ParseXMLPacket, found {text.count(OLD)}")
    text = text.replace(OLD, NEW, 1)
    assert NEW.isascii(), "what we insert must be ASCII: the sources are latin-1"
    path.write_bytes(text.encode(encoding))
    print(f"[ok]   {path}: blanks ahead of a stanza no longer swallow it")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources/xmlparser.mtl>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
