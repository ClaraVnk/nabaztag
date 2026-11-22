# Reading a rabbit's Diagnostic line

What a rabbit that never reaches its server can still say, and how to read it.

## Nabaztag:tag (Naboot 1.2.1.0 and later)

The setup page (head held while plugging it in, then `http://192.168.0.1`)
shows a **Diagnostic** line, kept in flash by the boot (`boot_diag` in
`firmware-arm/apply-mods.py`); the next boot request the Terrier receives
carries it as `&dg=`, and the rabbit's report reads it out in words
(`diag.explain_dg`). An example:

    2 t119 W0 A2 E0 a0 r2/0 h86 q0 L0 U77 B0 g0 e4a c1 s0 k0 m0
    n0 b0 u0 D0/0/0 I0.0.0.0 G192.0.2.1 N0.0.0.0

**The letters after `t` up to `u` are hexadecimal** (`wifi_diag`): `h86` is
134, not 86. The first number, `t`, `D`, `I`, `G` and `N` are decimal.

| Field | Means |
|---|---|
| first number | why it stopped: `1` the server did not answer, `2` the Wi-Fi was given up |
| `t` | seconds since power-up when it stopped |
| `W` | Wi-Fi state: `0` idle (not associated), `1` scanning, `2` authenticating, `3` associating, `4` WPA key exchange, `5` connected |
| `A` | authentication: `0` open, `1` shared (WEP), `2` SAE, i.e. **WPA3** (it tries SAE only when the access point offers it) |
| `E` | WPA key exchange's step; `0`: never started |
| `a` | authentication frames sent |
| `r` | authentication replies received `/` the last reply's status (`0` success) |
| `h` | beacons and probe answers heard |
| `q` | link probes sent while connected |
| `L` | links declared dead |
| `U` | seconds since the last frame addressed to it |
| `B` | bytecode loads |
| `g` | group cipher: `0` none yet, `2` TKIP, `4` CCMP |
| `e` | encryption: high digit the family (`0` none, `1` WEP, `2` WPA, `4` WPA2), low digit the ciphers (before associating: `8` group CCMP, `4` group TKIP, `2` pairwise CCMP, `1` pairwise TKIP; once associated: `4` CCMP, `2` TKIP). Not WPA3: that is `A2` |
| `c` | channel |
| `s` | received signal average (raw), `0` when not associated |
| `k` | group keys received in the key exchange |
| `m` | messages 1 of the WPA four-way handshake received (`eapol_diag`, 1.2.1.1 and later) |
| `n` | messages 3 received: `E1` with `n0` is an access point that never sent one (it refused the rabbit's message 2) |
| `b` | broadcast and multicast frames received; `0` with `u` rising: the group key is not read, every DHCP offer is lost |
| `u` | frames addressed to it received |
| `D` | DHCP: requests sent `/` offers `/` acknowledgements for this rabbit |
| `I`, `G`, `N` | its address, gateway and DNS server, `0.0.0.0` when it got none |

The example reads: the Wi-Fi given up after 119 s, idle, WPA2 with CCMP,
**WPA3 (SAE) tried**, two authentication replies with status 0, no key
exchange ever started, 134 beacons heard, channel 1, no DHCP request. The
access point is in WPA2/WPA3 mixed mode: set it to WPA2 only, then save the
rabbit's Wi-Fi again on its setup page (a rabbit that once joined a network
over WPA3 refuses WPA2 on it until then).

Two more marks travel in the boot URL, not on the page: `&k=` the key
exchange (`0` none: it has its key, `1` the entropy pool never came, `2` no
answer in time, `3` the request failed, `4` an answer that is not a key,
`5` a key) and `&kt=` the seconds it took. `k=0` while the Terrier holds no
key for the rabbit: it kept an old one; wipe it (`http://192.168.0.1/reset`)
and set it up again.

## Karotz (Terrier OS)

Three quick presses on its head open the setup network; its page has a
**Diagnostic** block (`setup_diag`): the Terrier OS and client versions, its
MAC address, the Wi-Fi's name (never its password), the client's last
failing step and how often, and the setup network's last log lines.

| Step | Means |
|---|---|
| `dns` | the server's name was not found |
| `tcp` | the server could not be reached |
| `auth` | the server refused its key |
| `os-layout` | a Terrier OS update refused: the client lives where an install empties |
| `os-usbkey` | a Terrier OS update refused: a USB key is plugged in; take it out |
| `os-install` | the update's installer stopped |
