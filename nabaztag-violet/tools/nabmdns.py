#!/usr/bin/env python3
"""nabmdns — a one-name mDNS responder for the rabbit's `.local` server name.

Pairs with the firmware mDNS resolver (firmware/mdnsresolve.mtl): the rabbit
resolves its XMPP server by sending an A query for `<name>.local` to the
multicast group 224.0.0.251:5353 instead of unicast DNS to the gateway. This
daemon joins that group and answers the one name we own (`nabaztag.local`) with
the server's IP. Unlike the unicast `nabdns.py` helper, this is a *standard*
mDNS responder — no gateway spoofing, no raw sockets, not tied to a client IP.

The rabbit queries from source port 1597 (its DNS client port, != 5353), so per
RFC 6762 §6.7 this is a "legacy unicast query": we echo the query id and answer
*unicast* back to the source. Multicast-sourced (port 5353) queries get a
multicast answer.

Config via env vars:
    NABMDNS_NAME     the name to answer            (default: nabaztag.local)
    NABMDNS_ANSWER   the IP to return for it       (required)
    NABMDNS_GROUP    multicast group              (default: 224.0.0.251)
    NABMDNS_PORT     multicast port               (default: 5353)

Needs host networking (to be on the rabbit's L2 segment). No special caps.
"""
import os
import socket
import struct
import sys

NAME = os.environ.get("NABMDNS_NAME", "nabaztag.local").rstrip(".").lower().encode()
ANSWER = os.environ["NABMDNS_ANSWER"]
GROUP = os.environ.get("NABMDNS_GROUP", "224.0.0.251")
PORT = int(os.environ.get("NABMDNS_PORT", "5353"))
TTL = 120


def parse_qname(data, off):
    labels = []
    while True:
        if off >= len(data):
            return None, off
        l = data[off]
        if l == 0:
            off += 1
            break
        if l & 0xc0:  # compression pointer in a question — ignore
            return None, off + 2
        labels.append(data[off + 1:off + 1 + l])
        off += 1 + l
    return b".".join(labels), off


s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
except (AttributeError, OSError):
    pass
s.bind(("", PORT))
mreq = socket.inet_aton(GROUP) + socket.inet_aton("0.0.0.0")
s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

print("nabmdns up: answering %s A -> %s on %s:%d" % (NAME.decode(), ANSWER, GROUP, PORT), flush=True)
answered = 0
while True:
    try:
        data, addr = s.recvfrom(2048)
    except OSError:
        continue
    if len(data) < 12:
        continue
    flags = struct.unpack("!H", data[2:4])[0]
    if flags & 0x8000:  # a response, not a query
        continue
    qd = struct.unpack("!H", data[4:6])[0]
    if qd < 1:
        continue
    qname, off = parse_qname(data, 12)
    if qname is None or off + 4 > len(data):
        continue
    qtype = struct.unpack("!H", data[off:off + 2])[0]
    if qname.lower() != NAME or qtype not in (1, 255):  # A or ANY
        continue

    txid = data[0:2]
    question = data[12:off + 4]
    answer = (b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, TTL, 4)
              + socket.inet_aton(ANSWER))
    resp = txid + struct.pack("!HHHHH", 0x8400, 1, 1, 0, 0) + question + answer
    dest = addr if addr[1] != PORT else (GROUP, PORT)
    s.sendto(resp, dest)
    answered += 1
    if answered <= 5 or answered % 10 == 0:
        print("answered #%d from %s:%d" % (answered, addr[0], addr[1]), flush=True)
