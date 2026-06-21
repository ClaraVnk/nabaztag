#!/usr/bin/env python3
"""nabdns — last-resort DNS answerer for a rabbit whose gateway drops its queries.

Some routers (seen on a UniFi gateway) silently refuse to answer the unicast DNS
queries coming from one specific client IP — a per-client ACL / DNS-flood guard.
The Nabaztag then loops forever on `A? <xmpp-domain>` and never reaches XMPP, so
it stays "all orange" (no server).

Rather than touch the gateway firewall, this tiny daemon runs on a host that sits
on the same L2 segment (host networking) and *answers the rabbit on the gateway's
behalf*: it sniffs the rabbit's `A? <NAME>` query to the gateway and injects a
spoofed UDP DNS reply (source = gateway IP:53) pointing <NAME> at the server.
It only ever answers ONE name for ONE client — nothing else on the LAN is touched.

This is a workaround. The clean fix is on the network side (allow the rabbit's
DNS, or hand it a working resolver via DHCP). Remove this once that's done.

Config via env vars (all required except IFACE / NABDNS_NAME / duration):

    NABDNS_IFACE     L2 interface to sniff/inject on        (default: eth0)
    NABDNS_RABBIT    the rabbit's IP (only answer this src)
    NABDNS_GATEWAY   the DNS the rabbit queries (spoof src)
    NABDNS_ANSWER    the IP <NAME> should resolve to (the server)
    NABDNS_NAME      the hostname to answer                 (default: nabaztag.lan)

    argv[1]          run duration in seconds (default: run effectively forever)

Needs CAP_NET_RAW and host networking (AF_PACKET sniff + IPPROTO_RAW inject).
"""
import os
import socket
import struct
import sys
import time

IFACE = os.environ.get("NABDNS_IFACE", "eth0")
RABBIT = os.environ["NABDNS_RABBIT"]
GW = os.environ["NABDNS_GATEWAY"]
ANSWER = os.environ["NABDNS_ANSWER"]
QNAME = os.environ.get("NABDNS_NAME", "nabaztag.lan").encode()


def cks(d):
    if len(d) % 2:
        d += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(d) // 2), d))
    s = (s >> 16) + (s & 0xffff)
    s += s >> 16
    return (~s) & 0xffff


def parse_qname(data, off):
    labels = []
    while True:
        l = data[off]
        if l == 0:
            off += 1
            break
        labels.append(data[off + 1:off + 1 + l])
        off += 1 + l
    return b".".join(labels), off


tx = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
tx.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
rx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
rx.bind((IFACE, 0))
rx.settimeout(1.0)

duration = float(sys.argv[1]) if len(sys.argv) > 1 else 10 ** 9
deadline = time.time() + duration
sent = 0
print("nabdns up: answering %s  A? %s -> %s" % (RABBIT, QNAME.decode(), ANSWER), flush=True)

while time.time() < deadline:
    try:
        frame = rx.recv(2048)
    except socket.timeout:
        continue
    if len(frame) < 42 or frame[12:14] != b"\x08\x00":  # not IPv4
        continue
    ip = frame[14:]
    ihl = (ip[0] & 0x0f) * 4
    if ip[9] != 17:  # not UDP
        continue
    src = "%d.%d.%d.%d" % tuple(ip[12:16])
    dst = "%d.%d.%d.%d" % tuple(ip[16:20])
    if src != RABBIT or dst != GW:
        continue
    udp = ip[ihl:]
    sport = struct.unpack("!H", udp[0:2])[0]
    if struct.unpack("!H", udp[2:4])[0] != 53:  # not to :53
        continue
    dns = udp[8:]
    if len(dns) < 12 or struct.unpack("!H", dns[4:6])[0] < 1:
        continue
    txid = dns[0:2]
    qname, off = parse_qname(dns, 12)
    qtype = struct.unpack("!H", dns[off:off + 2])[0]
    if qname.lower() != QNAME.lower() or qtype != 1:  # only NAME / A
        continue

    question = dns[12:off + 4]
    ans = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton(ANSWER)
    resp = txid + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0) + question + ans
    udplen = 8 + len(resp)
    ph = socket.inet_aton(GW) + socket.inet_aton(RABBIT) + struct.pack("!BBH", 0, 17, udplen)
    uhdr = struct.pack("!HHHH", 53, sport, udplen, 0)
    uck = cks(ph + uhdr + resp)
    uhdr = struct.pack("!HHHH", 53, sport, udplen, uck)
    tot = 20 + udplen

    def iph(c):
        return struct.pack("!BBHHHBBH4s4s", 0x45, 0, tot, 0, 0, 64, 17, c,
                           socket.inet_aton(GW), socket.inet_aton(RABBIT))

    pkt = iph(cks(iph(0))) + uhdr + resp
    tx.sendto(pkt, (RABBIT, 0))
    sent += 1
    if sent <= 5 or sent % 10 == 0:
        print("answered #%d sport=%d txid=%s" % (sent, sport, txid.hex()), flush=True)

print("done, sent %d answers" % sent, flush=True)
