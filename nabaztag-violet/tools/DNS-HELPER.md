# DNS helper — when the gateway drops the rabbit's queries

## Symptom

The rabbit reboots, fetches its bytecode and `locate.jsp` fine, then loops
forever on `A? nabaztag.lan` to the gateway **with no answer**, and stays
"all orange" (no server). Meanwhile *other* hosts on the same network resolve
`nabaztag.lan` just fine.

## Root cause

The gateway's DNS selectively refuses queries coming **from the rabbit's IP**.
Proven with a control test: the *same* `A? nabaztag.lan` query sent with the
HA host's source IP is answered; sent with the rabbit's source IP it gets
nothing. The only variable is the source IP → a per-client DNS ACL / DNS-flood
guard on the gateway (the rabbit hammers ~1 query/s, which can trip flood
protection). It is **not** the firmware, the source port, or L2 isolation.

## The clean fix (network side — preferred)

On the gateway, allow the rabbit's DNS, e.g. one of:

* whitelist the rabbit's IP in the DNS-flood / threat protection, or
* an explicit **Allow** rule `RABBIT_IP -> GATEWAY_IP udp/tcp 53` above any block, or
* hand the rabbit a working resolver via DHCP (point its DNS at the HA host,
  which already resolves `nabaztag.lan`).

If you can do this, you don't need the helper below — remove it.

## The workaround (host side — no firewall change)

`nabdns.py` runs on a host that shares the rabbit's L2 segment (host
networking) and answers the rabbit *on the gateway's behalf*: it sniffs the
rabbit's `A? <NAME>` and injects a spoofed reply (source = gateway:53) pointing
`<NAME>` at the server. It only ever answers one name for one client.

### Deploy as a persistent, auto-restarting container

Run on the HA host (needs Docker access, host networking, `CAP_NET_RAW`).
Replace the placeholders with your values:

* `<L2_IFACE>`   — interface on the rabbit's segment (e.g. `eth0`)
* `<RABBIT_IP>`  — the rabbit's fixed IP
* `<GATEWAY_IP>` — the DNS the rabbit queries (its gateway)
* `<HA_IP>`      — where `nabaztag.lan` should resolve (the add-on host)
* `<IMAGE>`      — any image with Python 3 (the nabaztag-violet add-on image works,
                   so nothing extra is pulled)

```sh
B64=$(base64 nabdns.py | tr -d '\n')
docker rm -f nabaztag-dns 2>/dev/null
docker run -d --name nabaztag-dns \
  --network host --cap-add NET_RAW --restart unless-stopped \
  -e NABDNS_IFACE=<L2_IFACE> \
  -e NABDNS_RABBIT=<RABBIT_IP> \
  -e NABDNS_GATEWAY=<GATEWAY_IP> \
  -e NABDNS_ANSWER=<HA_IP> \
  -e NABDNS_NAME=nabaztag.lan \
  --entrypoint /bin/sh <IMAGE> \
  -c "echo $B64 | base64 -d > /nabdns.py && exec python3 /nabdns.py"
```

Check it: `docker logs nabaztag-dns` → `nabdns up: answering ...` and, once the
rabbit queries, `answered #1 ...`.

### Remove it (once the network-side fix is in place)

```sh
docker rm -f nabaztag-dns
```

## Note on firmware mDNS

The boot-loader carries an mDNS **announcer** (`boot-mods/mdns.mtl`) that
publishes `naboot.local -> rabbit IP`. That helps you *reach the rabbit by
name*; it does **not** resolve the server, so it does not replace this helper.
A firmware mDNS **resolver** (rabbit resolves a `.local` server name over
multicast, bypassing the gateway DNS entirely) is the proper firmware-side
alternative — see the project notes.
