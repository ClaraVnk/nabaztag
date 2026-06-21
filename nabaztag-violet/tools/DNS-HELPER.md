# Resolving the rabbit's server when the gateway drops its DNS

## Symptom

The rabbit reboots, fetches its bytecode and `locate.jsp` fine, then loops
forever on `A? <xmpp-domain>` to the gateway **with no answer**, and stays
"all orange" (no server). Meanwhile *other* hosts on the same network resolve
the same name just fine.

## Root cause

The gateway's DNS selectively refuses queries coming **from the rabbit's IP**.
Proven with a control test: the *same* query sent with the HA host's source IP
is answered; sent with the rabbit's source IP it gets nothing. The only variable
is the source IP → a per-client DNS ACL / DNS-flood guard on the gateway (the
rabbit hammers ~1 query/s, which can trip flood protection). It is **not** the
firmware, the source port, or L2 isolation.

## Deployed solution: mDNS (no gateway DNS, no spoofing)

The rabbit asks for its server over **multicast mDNS** instead of unicast DNS to
the gateway, and HA answers with a standard mDNS responder. Three pieces:

1. **Firmware** (`firmware/mdnsresolve.mtl` + `patch_dns.py`): the bootcode's
   `dnsreq` routes `.local` names to `224.0.0.251:5353` instead of `netdns:53`.
   Built into the served bytecode — no flashing. See `firmware/README.md`.
2. **locate** returns a `.local` xmpp domain: set the add-on option
   `server_address = nabaztag.local`.
3. **`nabmdns.py`** answers `nabaztag.local` on the multicast group. Because the
   rabbit queries from port 1597 (!= 5353) it's an RFC 6762 §6.7 "legacy unicast
   query", so the reply is unicast straight back to the rabbit. This is a
   *standard* responder — no gateway spoofing, not tied to a client IP. (Avahi
   on the host could do the same job if it can publish the name.)

Deploy the responder as a persistent, auto-restarting container (host
networking, no special caps). Replace the placeholders:

```sh
B64=$(base64 nabmdns.py | tr -d '\n')
docker rm -f nabaztag-mdns 2>/dev/null
docker run -d --name nabaztag-mdns \
  --network host --restart unless-stopped \
  -e NABMDNS_NAME=nabaztag.local \
  -e NABMDNS_ANSWER=<HA_IP> \
  --entrypoint /bin/sh <IMAGE> \
  -c "echo $B64 | base64 -d > /m.py && exec python3 /m.py"
```

`docker logs nabaztag-mdns` → `nabmdns up: ...` then, once the rabbit boots,
`answered #N from <RABBIT_IP>:1597`.

VLAN caveat: mDNS doesn't cross VLANs. If HA and the rabbit are on different
VLANs, enable an mDNS reflector for the group on the switch/router (UniFi:
per-network "Multicast DNS").

> Mic streaming caveat: the add-on's `_server_ip()` does `gethostbyname(
> server_address)` for the optional `RS` mic command. With a `.local` name it
> can't resolve that itself (bridge network, no mDNS), so if you enable
> `auto_listen`/`personality`, give the add-on container a hosts entry
> (`nabaztag.local <HA_IP>`) or set the server IP another way. The default
> (mic off) is unaffected.

## The clean fix (network side)

If you can change the gateway: whitelist the rabbit's IP in the DNS-flood /
threat protection, or add an explicit Allow rule `RABBIT_IP -> GATEWAY_IP
udp/tcp 53`, or hand the rabbit a working resolver via DHCP. Then you don't need
any of the above.

## Legacy fallback: unicast spoofer (`nabdns.py`)

Before the mDNS resolver existed, `nabdns.py` answered the rabbit's *unicast*
`A? <name>` on the gateway's behalf (it sniffs the query and injects a spoofed
reply, needs `CAP_NET_RAW`). Superseded by the mDNS path above; kept for a stock
(un-patched) bytecode that still does unicast DNS. Deploy/remove docs are in the
file's header; container name was `nabaztag-dns`.
