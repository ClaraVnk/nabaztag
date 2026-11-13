#!/usr/bin/env python3
"""A firmware update fetched by the rabbit itself, over HTTPS (Naboot 1.2.0.0).

2026-11-13. An update came from the warren: as a URL on the warren
(fwota.mtl), then inside the session (patch_ota_in_session.py). Either way,
updating every rabbit is the warren's bandwidth, an image of ~126 KB per
rabbit. The images are published on GitHub; a rabbit that can speak HTTPS
(tls_certs) can fetch its own, as it plays an https:// stream
(patch_https_play.py). Nothing is lost: the image is still checked against
our Ed25519 key by the one gate there is, fwota.mtl's ota_flash.

The warren pushes the root(s) of the download host's chain as a session
resource, as for a stream, then sends

    FW https://host/path#r<N>@<YYYYMMDDHHMMSS> <128 hex digits of the signature>

The URL is the final one: GitHub's release download redirects to another
host with another chain, so the warren follows the redirect itself and hands
over where it leads (valid a few minutes, used at once). The signature comes
with the order, since GitHub cannot send fwota's X-Nab-Sig header; the
download takes fwota's binary path (HTTP_STREAM into one buffer of
Content-Length bytes), the one that holds a 126 KB image.

Gated by patch_https_play.py's "xmpp-https" literal (the firmware has the
https opcodes) and by its own, "xmpp-otahttps": the warren knows from it
that this runtime reads the order. Any other FW is what it was.

Applied to main.mtl and fwota.mtl in a sources directory, after
patch_ota_in_session.py and patch_https_play.py. Idempotent.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/* ota-https */"

# fwota.mtl: the signature may come with the order, and the binary path can
# be started with it, whatever the URL ends with.
SIG_OLD = '\tlet ota_hdr_sig hdr -> sig in\n'
SIG_NEW = ('\t// ota-https: a signature that came with the order wins over a header.\n'
           '\tlet if g_ota_bsig!=nil then g_ota_bsig else ota_hdr_sig hdr -> sig in\n')
ENTRY_ANCHOR = 'fun flashFromUrl url=\n'
ENTRY = '''/* ota-https */
// The binary path, with the signature the warren sent with the order
// (bytecode/patch_ota_https.py). No failure report: the host is not ours.
fun flashFromUrlSig url sig=
	set g_ota_url="";
	set g_ota_bstate=OTA_BIN_HEAD;
	set g_ota_bbuf=nil;
	set g_ota_bsig=sig;
	set g_ota_boff=0;
	httprequest "GET" url nil #ota_bin_cb HTTP_STREAM;;

'''

# main.mtl: after HttpsPlay, whose roots and clock it sets the same way.
PLAY_END = ('\t\t\tset wav_live=atoi strsub val i+2 j-i-2;\n'
            '\t\t\t0\n'
            '\t\t);\n'
            '\t\t1\n'
            '\t);;\n')
FW_FUN = '''
/* ota-https */
// Its own literal, flipped to 1 under the HMAC by a warren that sends these
// orders: an older runtime with https streams but not this would take the
// order for a URL.
var XmppOtaHttpsPolicy="xmpp-otahttps:0";;
fun XmppOtaHttpsOn= (strget XmppOtaHttpsPolicy 14)==49;;
// `FW https://host/path#r<N>@<YYYYMMDDHHMMSS> <128 hex>`: the image fetched
// by the rabbit itself, the roots in resource N, the signature in the order.
// 1 when the value was such an order, started or not; 0 otherwise.
fun XmppFwHttps val=
	if val==nil || (strlen val)<9 || strcmp (strsub val 0 8) "https://" then 0
	else let strstr val " " 0 -> sp in
	let strstr val "#r" 0 -> i in
	let if i==nil then nil else strstr val "@" i -> j in
	let if j==nil then nil else XmppResFind XmppResStore (atoi strsub val i+2 j-i-2) -> roots in
	if !HttpsOn || !XmppOtaHttpsOn || sp==nil || roots==nil || sp!=j+15 || (strlen val)-sp-1!=128 then
		(Secholn "ota: FW https without roots, signature or policy"; led 0 OTA_STEP_BAD; 1)
	else let strnew 64 -> sig in
	(
		getbinary_fw sig val 0 (sp+1) 64;
		set HttpsRoots=strcatlist roots;
		set HttpsNow=strsub val j+1 14;
		Secho "ota: FW over https at "; Iecholn time_ms;
		led 0 OTA_STEP_RUN;
		flashFromUrlSig (strsub val 0 i) sig;
		1
	);;
'''
FW_OLD = '\t\telse if !strcmp key "FW" then (if !(XmppFwTake val) then flashFromUrl val;0)\n'
FW_NEW = ('\t\telse if !strcmp key "FW" then (if !(XmppFwTake val) then '
          'if !(XmppFwHttps val) then flashFromUrl val;0)\n')


def _read(p: Path) -> tuple[str, str]:
    raw = p.read_bytes()
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"   # main.mtl carries 8-bit comments


def patch(src: Path) -> bool:
    main, fw = src / "main.mtl", src / "fwota.mtl"
    m, menc = _read(main)
    f, fenc = _read(fw)
    if MARKER in m:
        print(f"[skip] {main}: updates over https already")
        return False
    for what, text, needle in (("HttpsPlay's end", m, PLAY_END), ("the FW command", m, FW_OLD),
                               ("ota_bin_head's signature", f, SIG_OLD),
                               ("flashFromUrl", f, ENTRY_ANCHOR)):
        if text.count(needle) != 1:
            sys.exit(f"FAIL {src}: expected one {what}, found {text.count(needle)} "
                     "(patch_ota_in_session.py and patch_https_play.py first)")
    f = f.replace(SIG_OLD, SIG_NEW, 1).replace(ENTRY_ANCHOR, ENTRY + ENTRY_ANCHOR, 1)
    m = m.replace(PLAY_END, PLAY_END + FW_FUN, 1).replace(FW_OLD, FW_NEW, 1)
    for s in (SIG_NEW, ENTRY, FW_FUN, FW_NEW):
        assert s.isascii(), "what we insert must be ASCII: main.mtl is latin-1"
    fw.write_bytes(f.encode(fenc))
    main.write_bytes(m.encode(menc))
    print(f"[ok]   {src}: `FW https://...#rN@time <sig>` fetches the image over HTTPS")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path/to/sources>")
    patch(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
