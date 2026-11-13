#!/bin/bash
# Build the full hybrid runtime bytecode from the stock Violet bootcode:
#   - micstream  : server-triggered mic UDP stream (RS/RT commands)
#   - SV         : volume command
#   - fwota      : Ed25519-gated OTA (FW command) — needs the verifySig opcode,
#                  so it MUST be compiled with the patched compiler (see below)
#                  and only *runs* on Naboot firmware.
#   - mdns       : the mDNS announcer (publishes naboot.local)
#   - mdnsresolve: the mDNS resolver — resolves a .local server name over
#                  multicast so a rabbit whose gateway drops its unicast DNS can
#                  still find its server. The one piece that must never be lost.
#
# A rebuild can never silently drop any of these: this script applies them all,
# in order, with the right compiler, from a stock Violet bootcode tree.
#
# Usage: build-bytecode.sh <bootcode-dir> [compiler] [out.bin]
#   <bootcode-dir>  the stock Violet bootcode tree (has sources/, preproc.pl)
#                   e.g. /root/violet-bootcode in the nabi-build container.
#   [compiler]      Metal compiler. Default: this repo's
#                   firmware-arm/tools/mtl_comp.py -- a Python compiler
#                   byte-identical to RedoXyde's C++ mtl_compiler (gated by
#                   tools/run-tests.sh) that already knows the verifySig opcode
#                   (152) fwota needs, so the bytecode builds from the repo
#                   alone, no external binary. Pass a path ending in .py to use
#                   another Python compiler, or any other path for a native
#                   mtl_compiler binary (invoked `CC -s IN OUT`).
#   [out.bin]       output (default: <bootcode-dir>/bootcode_hybrid.bin)
#
# All patch scripts are idempotent; re-running on an already-patched tree is safe.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BC="${1:?give the Violet bootcode dir (the one with sources/ and preproc.pl)}"
CC="${2:-$HERE/../firmware-arm/tools/mtl_comp.py}"
OUT="${3:-$BC/bootcode_hybrid.bin}"

[ -d "$BC/sources" ]         || { echo "no $BC/sources — is this a bootcode tree?"; exit 1; }
[ -f "$BC/sources/main.mtl" ] || { echo "no sources/main.mtl"; exit 1; }
case "$CC" in
  *.py) [ -f "$CC" ] || { echo "compiler not found: $CC"; exit 1; } ;;
  *)    [ -x "$CC" ] || { echo "compiler not executable: $CC"; exit 1; } ;;
esac
grep -q "fun dnsreq" "$BC/sources/dns.mtl" || { echo "sources/dns.mtl: no dnsreq — not stock Violet?"; exit 1; }

echo "==> copying our modules into sources/"
cp "$HERE/micstream.mtl" "$HERE/fwota.mtl" "$HERE/mdns.mtl" \
   "$HERE/mdnsresolve.mtl" "$BC/sources/"

echo "==> patch_main.py  (micstream/fwota/mdns includes + RS/RT/SV/FW + announcer tick)"
python3 "$HERE/patch_main.py" "$BC/sources/main.mtl"
echo "==> patch_dns.py   (mDNS resolver for .local)"
python3 "$HERE/patch_dns.py" "$BC/sources/dns.mtl"
echo "==> patch_reconnect.py  (reconnect instead of rebooting on a closed socket)"
python3 "$HERE/patch_reconnect.py" "$BC/sources/main.mtl"

# pacifox's follow-up (issue #23), the one fix of the five that is measured.
# With the server's HOST absent -- nothing answering ARP, the HA VM off -- the
# stock image soft-reboots every ten seconds for ever, 356 times in an hour of
# the emulator, and each soft reboot leaves the radio asleep. The other four
# fixes in that script are not wrong, they are not yet reached; its docstring
# says which and why.
echo "==> patch_reconnect_followup.py softreboot  (no reboot left on the locate.jsp path)"
python3 "$HERE/patch_reconnect_followup.py" "$BC/sources" softreboot
echo "==> patch_wifi_reconnect.py  (rejoin WPA/WPA2 after a link loss)"
python3 "$HERE/patch_wifi_reconnect.py" "$BC/sources"
# TLS 1.3 on the XMPP socket when the rabbit has an adoption key and the
# warren's locate.jsp names a TLS port; plain otherwise. The opcodes it
# calls (tlsStart..tlsClose) exist from firmware mode lean-wpa3-tls on;
# on an older firmware the bytecode never reaches them, because locate
# only offers TLS to rabbits whose owner confirmed a key, and a key
# needs that firmware's bootloader. The output carries the literal
# "xmpp-tls:0" exactly once: the warren flips it to "xmpp-tls:1" under the
# HMAC for a confirmed rabbit, and that rabbit then opens the TLS port even
# when locate.jsp (plain HTTP) was stripped of its xmpp_tls line.
echo "==> patch_tls.py  (TLS 1.3 PSK on the XMPP connection)"
python3 "$HERE/patch_tls.py" "$BC/sources/main.mtl"
# The RFID report rides inside that TLS session instead of plain rfid.jsp
# (a <message> with violet:nabaztag:rfid, the button's shape) when the warren
# flipped "xmpp-rfid:0" to 1 under the HMAC; rfid.jsp as before otherwise.
echo "==> patch_rfid_xmpp.py  (RFID report over the XMPP session when it runs TLS)"
python3 "$HERE/patch_rfid_xmpp.py" "$BC/sources/main.mtl"
echo "==> patch_liveness.py  (no BOSH; a write is not a read; 150 s read timeout)"
python3 "$HERE/patch_liveness.py" "$BC/sources/main.mtl"
# What the warren has the rabbit say rides inside that TLS session too (an
# announce record, then the raw bytes, then `ST res:N`) when the warren
# flipped "xmpp-res:0" to 1 under the HMAC; ST http://.../res/ as before
# otherwise.
echo "==> patch_res_in_session.py  (sounds inside the XMPP session when it runs TLS)"
python3 "$HERE/patch_res_in_session.py" "$BC/sources/main.mtl"
# locate.jsp on the TLS port, as the bootloader asks for bc.jsp, when that
# warren requires TLS; in the clear after an attempt that got no answer.
echo "==> patch_locate_tls.py  (locate.jsp over TLS when the warren requires it)"
python3 "$HERE/patch_locate_tls.py" "$BC/sources/main.mtl"
# A program's jingles and choreographies (MU, MC, CH) come down the session
# as res:N too, when the warren flipped "xmpp-rsc:0" to 1 under the HMAC.
echo "==> patch_rsc_in_session.py  (jingles and choreographies inside the session)"
python3 "$HERE/patch_rsc_in_session.py" "$BC/sources/main.mtl"
# "Talk to it": `RS session` streams the microphone inside the TLS session
# (announce + raw ADPCM) instead of UDP, when the warren flipped "xmpp-mic:0".
echo "==> patch_mic_in_session.py  (the microphone inside the session)"
python3 "$HERE/patch_mic_in_session.py" "$BC/sources/main.mtl"
# Over-the-air updates come down the session too: an announce with fw='1',
# the image, then `FW res:N <sig>`, when the warren flipped "xmpp-ota:0".
echo "==> patch_ota_in_session.py  (firmware updates inside the session)"
python3 "$HERE/patch_ota_in_session.py" "$BC/sources/main.mtl"
# A radio or a podcast comes down the session as well, pulled by the rabbit
# and played as it arrives: `ST live:N`, when the warren flipped "xmpp-live:0".
echo "==> patch_live_in_session.py  (radio and podcasts inside the session)"
python3 "$HERE/patch_live_in_session.py" "$BC/sources/main.mtl"
# Blanks ahead of a stanza (a whitespace keepalive) no longer swallow it
# and every stanza after it.
echo "==> patch_xml_leading_blank.py  (blanks ahead of a stanza)"
python3 "$HERE/patch_xml_leading_blank.py" "$BC/sources/xmlparser.mtl"

# A DHCP reply is trusted only in a window after we asked and with our id;
# without it an unsolicited ACK could rewrite the rabbit's IP/gateway/DNS.
echo "==> patch_dhcp_xid.py  (DHCP replies checked)"
python3 "$HERE/patch_dhcp_xid.py" "$BC/sources/dhcp.mtl"

# DNS query ids are random (harder to forge a reply, which the SRV path turns
# into the XMPP server) and reply record counts are bounded (no reboot).
echo "==> patch_dns_antispoof.py  (random DNS ids, bounded counts)"
python3 "$HERE/patch_dns_antispoof.py" "$BC/sources/dns.mtl"

# An HTTP_NORMAL answer is counted as it arrives and dropped past 16 KB (the
# hex .sim OTA request carries its own, larger ceiling): a server or a host
# on the path can no longer fill the heap, which was a reboot.
echo "==> patch_http_cap.py  (HTTP_NORMAL answers bounded)"
python3 "$HERE/patch_http_cap.py" "$BC/sources"

echo "==> patch_https_play.py  (https:// streams played by the rabbit itself, Naboot 1.2.0.0)"
python3 "$HERE/patch_https_play.py" "$BC/sources"

echo "==> patch_ota_https.py  (an update fetched by the rabbit itself over HTTPS, Naboot 1.2.0.0)"
python3 "$HERE/patch_ota_https.py" "$BC/sources"

# A long press of the head goes to the warren as a long_click when the
# warren flipped "xmpp-btn:0", instead of recording the room to record.jsp.
echo "==> patch_btn_in_session.py  (a long press goes to the warren, not to record.jsp)"
python3 "$HERE/patch_btn_in_session.py" "$BC/sources/main.mtl"
if [ "${OTA_REPORT:-}" = 1 ]; then
  echo "==> patch_ota_report.py  (TEST RUNTIME: each step of an update goes to the warren)"
  python3 "$HERE/patch_ota_report.py" "$BC/sources"
fi

# A rabbit that speaks in its sleep (a tag, a reminder during quiet hours)
# goes back to sleep instead of staying half awake and deaf to sleep commands.
echo "==> patch_sleep_after_program.py  (asleep before a program, asleep after it)"
python3 "$HERE/patch_sleep_after_program.py" "$BC/sources/main.mtl"

echo "==> preprocessing + compiling (with $CC)"
( cd "$BC/sources" && ../preproc.pl < main.mtl | ../preproc_remove_extra_protos.pl > ../bootcode.mtl )
case "$CC" in
  *.py) python3 "$CC" "$BC/bootcode.mtl" -o "$OUT" ;;
  *)    "$CC" -s "$BC/bootcode.mtl" "$OUT" ;;
esac

# The literal the warren flips for a confirmed rabbit must be there exactly
# once, or the Terrier leaves the policy alone and TLS stays optional; the
# RFID one once too, or the tag never leaves rfid.jsp; the resource one once,
# or every sound is still fetched over plain HTTP; the program-resource one
# once, or jingles and choreographies are; the microphone one once, or what
# is said to the rabbit still crosses the internet in UDP; the update one
# once, or every firmware image is still fetched over plain HTTP; the stream
# one once, or the radio and the podcasts are; the button one once, or a
# long press still records the room to record.jsp.
python3 - "$OUT" <<'PY'
import sys
b = open(sys.argv[1], "rb").read()
for lit in (b"xmpp-tls:0", b"xmpp-rfid:0", b"xmpp-res:0", b"xmpp-rsc:0", b"xmpp-mic:0", b"xmpp-ota:0",
            b"xmpp-live:0", b"xmpp-https:0", b"xmpp-otahttps:0", b"xmpp-btn:0"):
    n = b.count(lit)
    if n != 1:
        sys.exit(f"FAIL: {n} {lit.decode()!r} literals in the bytecode, expected 1")
PY

echo "==> built $OUT ($(wc -c < "$OUT") bytes)"
echo "    deploy: copy it to the add-on's /data/bootcode.hybrid"
echo "    note: the FW/OTA path only *runs* on Naboot firmware (verifySig opcode);"
echo "          on stock firmware it stays dormant unless a FW command is sent."
