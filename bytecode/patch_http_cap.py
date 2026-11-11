#!/usr/bin/env python3
"""An HTTP_NORMAL answer is counted as it arrives and dropped past a ceiling.

http.mtl's httptcpread kept every segment of an HTTP_NORMAL answer in
httpreq.inputH until the connection closed, with no limit, then built the
whole body twice (once to read the status code, once for the callback). A
server -- or any host on the path, these requests are plain HTTP -- that
answers with more than the heap holds runs the VM out of memory, which is a
reset, and a Nabaztag comes back from a soft reset with its radio asleep.
Measured in the emulator (scenarios/http/big-runtime.txt, the gateway's
`http-big`): a 200,000-byte answer to locate.jsp resets the rabbit, a
megabyte does at 666 KB received; 100,000 bytes do not.

Now:
  - Httpreq carries sizeH, the bytes received so far, and maxH, this
    request's own ceiling (nil: HTTP_MAX_NORMAL);
  - httptcpread adds each segment's length before it keeps the segment.
    Past the ceiling the segments are let go, the connection is closed and
    the callback gets nil -- what it already gets for a 404, a failed
    connect or a name that does not resolve, so every caller has a branch
    for it. Segments still in flight for the closed socket, and its close
    event, are ignored (sizeH stays past the ceiling) so the callback runs
    once;
  - HTTP_MAX_NORMAL is 16384. What the runtime asks for with HTTP_NORMAL
    are locate.jsp (three lines), rfid.jsp and record.jsp (empty, or "ok"),
    the otafail report and the registration mails (no callback), the
    interactive-mode requests (a message packet) and a warren's error
    page: under 1 KB each on the add-on and on the Terrier. 16 KB is sixteen
    times that and holds 48 KB live at worst (the segments and two copies);
  - the one large HTTP_NORMAL download is the hex .sim of fwota.mtl's
    legacy path (a URL not ending in .bin). flashFromUrl raises that
    request's own ceiling to FW_SIM_MAX_BYTES, and a 302 hands the ceiling
    on to the request that follows it. See FW_SIM_MAX_BYTES below for the
    figure. scenarios/ota/self-start.txt with naboot-stable.signed.sim:
    216,175 bytes down, the signature gate reached at 70% of the heap,
    before the patch and after.

Not touched: HTTP_STREAM (sounds, the .bin OTA) hands each segment to its
callback and keeps nothing after the header block; the header block itself
(everything up to the first blank line) is still unbounded, and so is
locate.jsp over TLS (LocateTlsTake), whose peer holds the adoption key.

Applied to http.mtl and fwota.mtl in a sources directory, after
build-bytecode.sh has copied fwota.mtl there. Idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* http-cap */"

# --- http.mtl ---------------------------------------------------------

TYPE_OLD = "type Httpreq=[cnxH inputH outputH indexH cbH typeH stateH aliveH sonH redirLevelH];;\n"
TYPE_NEW = (
    "/* http-cap */\n"
    "// An HTTP_NORMAL answer is kept whole until the connection closes; past\n"
    "// this many bytes the request is dropped instead (the callback gets nil).\n"
    "// See bytecode/patch_http_cap.py for how the figure was chosen.\n"
    "const HTTP_MAX_NORMAL=16384;;\n"
    "//  - sizeH : bytes received so far ; maxH : this request's own ceiling (nil = HTTP_MAX_NORMAL)\n"
    "type Httpreq=[cnxH inputH outputH indexH cbH typeH stateH aliveH sonH redirLevelH sizeH maxH];;\n"
    "\n"
    "// Has this request received more than it may keep?\n"
    "fun httpover httpreq=\n"
    "\thttpreq.sizeH!=nil && httpreq.sizeH>(if httpreq.maxH==nil then HTTP_MAX_NORMAL else httpreq.maxH);;\n"
)

# A redirect keeps the ceiling of the request it replaces.
REDIR_OLD = "\t\t\t\t\t\t\tset newReq.redirLevelH = httpreq.redirLevelH + 1;\n"
REDIR_NEW = REDIR_OLD + "\t\t\t\t\t\t\tset newReq.maxH = httpreq.maxH;\t// http-cap\n"

# The close of a request that was already refused is not a second answer.
END_OLD = ("\t\tif httpreq.typeH==HTTP_NORMAL then\n"
           "\t\t(\n"
           "\t\t\tlet rev httpreq.inputH nil -> l in\n")
END_NEW = ("\t\tif httpreq.typeH==HTTP_NORMAL then\n"
           "\t\t(\n"
           "\t\t\tif httpover httpreq then nil else\t// http-cap: already answered with nil\n"
           "\t\t\tlet rev httpreq.inputH nil -> l in\n")

KEEP_OLD = ("\t\tif httpreq.typeH==HTTP_NORMAL then\n"
            "\t\t\tset httpreq.inputH=input::httpreq.inputH\t// on bufferise ce qui a \xe9t\xe9 re\xe7u\n")
KEEP_NEW = ("\t\tif httpreq.typeH==HTTP_NORMAL then\n"
            "\t\t(\n"
            "\t\t\t// http-cap: count before keeping. A request already past its\n"
            "\t\t\t// ceiling was closed and answered; what still arrives is dropped.\n"
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
            "\t\t)\n")

# --- fwota.mtl --------------------------------------------------------

FW_CONST_OLD = "const FW_MAX_BYTES=126976;;\n"
FW_CONST_NEW = (
    FW_CONST_OLD +
    "\n"
    "/* http-cap */\n"
    "// The hex form is fetched whole with HTTP_NORMAL, whose answers stop at\n"
    "// HTTP_MAX_NORMAL unless the request says otherwise. This is what it\n"
    "// says: the largest .sim this path has installed (naboot-stable, a\n"
    "// 107,904-byte image, 215,970 bytes of .sim) and 2 KB of response\n"
    "// headers. A larger image runs the VM out of memory as hex whatever\n"
    "// the ceiling (six times the image live at once, see below) and comes\n"
    "// as .bin.\n"
    "const FW_SIM_MAX_BYTES=218018;;\n"
)

FW_REQ_OLD = "\telse httprequest \"GET\" url nil #ota_cb HTTP_NORMAL;;\n"
FW_REQ_NEW = ("\telse let httprequest \"GET\" url nil #ota_cb HTTP_NORMAL -> r in\n"
              "\t(\n"
              "\t\tset r.maxH=FW_SIM_MAX_BYTES;\t// http-cap\n"
              "\t\tr\n"
              "\t);;\n")


def _load(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def _apply(path: Path, edits: list[tuple[str, str, str]]) -> bool:
    text, encoding = _load(path)
    if MARKER in text:
        print(f"[skip] {path}: HTTP_NORMAL answers are already capped")
        return False
    for what, old, _ in edits:
        if text.count(old) != 1:
            sys.exit(f"FAIL {path}: expected one {what}, found {text.count(old)}")
    for _, old, new in edits:
        text = text.replace(old, new, 1)
    path.write_bytes(text.encode(encoding))
    return True


def patch(sources: Path) -> None:
    http, fwota = sources / "http.mtl", sources / "fwota.mtl"
    for f in (http, fwota):
        if not f.is_file():
            sys.exit(f"FAIL {f}: not found (run after build-bytecode.sh copied fwota.mtl)")
    if _apply(http, [("Httpreq type", TYPE_OLD, TYPE_NEW),
                     ("redirect's redirLevelH", REDIR_OLD, REDIR_NEW),
                     ("HTTP_NORMAL end of answer", END_OLD, END_NEW),
                     ("HTTP_NORMAL buffering", KEEP_OLD, KEEP_NEW)]):
        print(f"[ok]   {http}: HTTP_NORMAL answers counted, dropped past their ceiling")
    if _apply(fwota, [("FW_MAX_BYTES", FW_CONST_OLD, FW_CONST_NEW),
                      ("the hex .sim request", FW_REQ_OLD, FW_REQ_NEW)]):
        print(f"[ok]   {fwota}: the hex .sim request carries its own ceiling")


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
