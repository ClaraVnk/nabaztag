# LapinLibre as Terrier OS ships it: review of 2026-11-24

Read, on the dev VM's built root (`~/lapinlibre-work/ll/work/sysroot-core`,
LapinLibre `v0.2.0-58-g19ea8d7`, read only) and in the fork's sources at
that commit: the web API (usr/share/lapinlibre/web.sh, `web-hw.sh`,
`web-media.sh`, `web-extra.sh`, `web-tags.sh`, every `usr/www/cgi-bin/*`),
`karotz/scripts/{startup,network,hotspot,inetd,sshd,usbkey}.sh`,
etc/inetd.conf, karotz/etc/httpd.conf, etc/dbus-1/system.d/karotz.conf,
`etc/passwd`, etc/busybox.conf, `linuxrc`; `userland/src/usr/{webcam-daemon,
multimedia-daemon,karotz-player,libkarotz-common}`. Security first, then
correctness, then what the Terrier client relies on. LapinLibre is
upstream: nothing here is reported there by me; the fixes that belong to
our build are in `patches/` (applied by `Containerfile`), the rest are
findings for Clara.

## 1. Security

### 1.1 Who runs what

The API is well partitioned, and better than OpenKarotz's. httpd runs as
the user `web` (uid 80, `etc/inetd.conf:3`, `etc/passwd`), so every CGI
runs as `web`: it can write only `/var/run/lapinlibre-web`,
`/usr/etc/conf/web` (the tag store, 0700) and the lock; it reaches the
daemons over D-Bus only (`karotz.conf:12-14`: the group web may call
`com.mindscape.karotz.*`, owns nothing). `reboot` is the one suid
(`busybox.conf: reboot = sx- root.web`). No input is ever evaluated:
values go through `case` patterns and are passed as single arguments
(`web.sh:22-24`), the D-Bus arguments are typed (`dbus_call`, `web.sh:745`),
the logs never carry a value (`web_log`, `web.sh:137`). I found **no
command, argument or path injection** in any endpoint: every file name
goes through `valid_id` (`[A-Za-z0-9_-]{1,64}`), `valid_snapshot_file`
(a fixed shape) or `valid_tag`; every URL through `valid_url` (http(s),
no quote, no `::`); numbers through `param_int`.

### 1.2 What a LAN client can do without a password (finding, Clara's call)

With no `WEB_PASSWORD` in lapinlibre.txt there is no login
(`inetd.sh:97-98`, `web.sh:317-320`), and anything on the LAN (the
private ranges, `lan_address`) may: reboot the rabbit, take pictures with
its camera (`snapshot`, `snapshot_view`) and fetch them, make it speak and
play any http(s) URL, read the system log (`tools_log`, `lapinlibre/logs`:
client addresses, the Wi-Fi name `hotspot.sh:96`), the process list, the
Wi-Fi MAC, the SSH keys' fingerprints, and **send HTTP requests from the
rabbit to any http(s) URL and read the first 1024 bytes of the answer**
(`rfid_test_url`, `web-tags.sh url_request`): a classic SSRF onto the
owner's own LAN (a router's admin page, a printer), with Basic credentials
of the client's choice. Everything is logged with the client's address.

The Terrier client talks to the API from `127.0.0.1` without credentials
(terrier/karotz/src/ok.c, `call()`): **a default password would cut the
rabbit off from the Terrier** unless the client learns it. So the finding
is: either the client reads the hash's companion (a token file in
`/usr/etc/conf/web`, root-owned, readable by the client's user) or the API
exempts `127.0.0.1` from the password (`web.sh:318`: one `case`). The
second is simpler and keeps the LAN rule for everyone else. Not patched
here: it changes what every owner sees; Clara decides.

### 1.3 Cross-site requests

`same_origin` (`web.sh:286-304`) refuses a state change when
`Sec-Fetch-Site` is not same-origin/none, or when `Origin`/`Referer` name
another host, and lets through a request with none of the three (scripts,
home-automation). Every current browser sends `Sec-Fetch-Site`; older
Safari sends `Referer` on cross-site GET. Read-only endpoints
(`web_begin read`) skip the rule, which is right. Sound.

### 1.4 The setup network

`hotspot.sh` opens an **open** access point `Karotz-XXXX` on three head
presses (`:7-13`, by design: whoever presses has the rabbit in hand), for
600 s, and `wifi_setup` takes the SSID and password over clear HTTP
(`:18-21`, the quoting rule for wpa_supplicant checked twice,
`hotspot.sh:91-94`). The settings file is written by `web`, read and
re-checked by root. Acceptable for a setup window; the risk is a
neighbour within 600 s. A finding, not a bug.

### 1.5 Secrets on disk

- `web_password.hash`: SHA-512 crypt, 0600 root (`inetd.sh:52`); the
  plain `web_password` is removed once hashed. Good.
- The tag store (`/usr/etc/conf/web/tags`, 0600 `web`) holds URL
  actions' Basic passwords in clear (`web.sh:834-838`); `tag_action` never
  returns them (`lapinlibre/tag_action:16-19`). Fine for what it is.
- `wpa.conf` 0600 root (`hotspot.sh:93`). `random-seed` 0600 root
  (`startup.sh:100-102`). Dropbear host keys root's; public parts copied
  for the API (`sshd.sh:9-11`). Good.
- `network.sh:45` **sources** `/usr/etc/conf/network.conf` as shell.
  Root writes it (hotspot.sh, applysettings.sh from the key), so no
  escalation; but a key's `lapinlibre.txt` with `IP="$(...)"` runs code at
  boot. The key is the installer already, so this is a robustness note:
  read the four values with `read`/`case` instead of `.`.

### 1.6 SSH and telnet

Dropbear `-s -R` (no passwords, public keys only), waits with no
authorized key (`sshd.sh:36-38,53`). Telnet (`inetd.sh:102-104`) is a
**root shell with no password on port 23**, only when the install set
`TELNET=yes`, and the status page says so (`telnet_on`). Fine as a
testing switch; the install's default is what matters (not read here).

## 2. Correctness and robustness

### 2.1 inetd stops the web API for ten minutes after 60 connections in one (fixed, patch 0001)

`etc/inetd.conf:3` (made by `image/tools/image.py:213`): `nowait.60`.
BusyBox inetd counts a service's connections and, past the count within a
minute, **refuses the next and pauses the service for ten minutes**
(inetd.c `RETRYTIME`). Measured in a container (`test-inetd-rate.sh`,
2026-11-24): with `.60`, 59 of 61 connections in a row answered; with
`.600`, 61 of 61. The Terrier client on the rabbit itself opens one HTTP
connection per LED or ear order (`ok.c call()`): a choreography is a move
every 1.5 s (`rabbit.c:46`), each with an ear and a LED call, beside the
status polls and the owner's browser refreshing the page. A scanner on the
LAN reaches 60 in a second and the rabbit stops obeying the Terrier for
ten minutes, silently. Patch 0001 makes it 600: still a cap (each CGI is a
shell on an ARM920T), ten times what the rabbit's own traffic needs.

### 2.2 The shutter sound: the chain is whole, the silence is not explained

web-hw.sh:65-66 and `snapshot:4-5` say webcam-daemon plays the shutter
itself. The code does ask for it: `webcam-daemon.c:302-303` pulses the LED
and calls `multimediaPlay(self->multimedia, NULL,
"file:///usr/karotz/sounds/shutter.wav", 1)` (the proxy is lazy,
`dbus_load_proxy`, never NULL; `RESULT_CALL` returns FALSE on a NULL and
on a D-Bus error, and the caller ignores it). multimedia-daemon's `play`
(`multimedia-daemon.c api_play`, ~line 1105) sees no `::` (not an API
command), no .asx/.m3u/.pls (`is_playlist`, :794-803), and hands the
string to karotz-player (`player_control_play`, :926-955: `loadfile
'file:///...' 0` to a running player, else a new one with the path).
karotz-player opens it with `avformat_open_input` (`karotz-player.c:517`),
and libavformat's `file:` protocol takes file:///usr/... as a path. The
file ships (usr/karotz/sounds/shutter.wav, 5,336 bytes, PCM 16-bit mono
22,050 Hz, the same format as `boot-ready.wav`, which plays at every boot
through the same player). So **nothing in the sources refuses the call**.
What the code cannot show, and Gengar can, in this order: (1) `logread |
grep -i multimedia` after a snapshot: "player_control_play path:
file:///..." present or not (if absent, the D-Bus call never arrived:
`dbus-monitor --system` beside it); (2) whether a *running* player was
there (`loadfile` to a FIFO nobody reads: `send_player_cmd` logs "no
player reads"); (3) `karotz-player file:///usr/karotz/sounds/shutter.wav`
by hand on the rabbit (ffmpeg's `file` protocol may be compiled out of
LapinLibre's build: `system/external/package/mpd-karotz` builds its own
ffmpeg 6.1, whose protocol list I did not read). Until then the two
comments are a claim, not a measure; I left them, since the first
measurement is cheap and decides the fix (a plain path in webcam-daemon,
or a file:// strip in multimedia-daemon).

### 2.3 Locks and timeouts

- The one hardware lock (`web_lock`, `web.sh:765-776`) is `flock` on fd 9
  with a 5 s wait, released by `web_unlock` or at exit; `timeout`'s own
  watcher is kept from inheriting it (`9>&-` everywhere, `:745,:16`).
  Right, and the reason `CONFIG_FILE_LOCKING` mattered under 6.1.
- Every D-Bus call has a 5 s reply timeout inside a 7 s `timeout`
  (`:745`); the camera waits 10 s (`web-hw.sh:91`); `setclock` holds a
  CGI for up to 28 s (`setclock:10-14`) and `ears_reset_wait` 10 s. With
  60 connections a minute these waits were part of the starvation (§2.1).
- `read_body` (`web.sh:362-382`) reads at most `CONTENT_LENGTH` bytes
  with `read -n`; a client that sends fewer bytes than it announced
  holds the CGI until httpd's own timeout. No lock is held yet. Fine.
- `take_picture` reads `/tmp/picture.jpg` only after the daemon's answer
  or its event with this request's id (`web-hw.sh:66-69,83-88`): no stale
  picture. Good.

### 2.4 Start-up

- `startup.sh:101`: the seed and `/proc/interrupts` are mixed into
  `/dev/urandom` and a new seed written; right for a kernel with no
  hardware entropy (the 6.1 config has no `CONFIG_RANDOM_TRUST_*` either).
- `startup.sh:115-134`: without a network the retry loop runs for ever
  in the background and three head presses open the setup network. Good.
  With a network, `services` waits up to 30 s for led-daemon, then up to
  30 s for the clock: a rabbit without NTP reach is ready after 60 s. Fine.
- `network.sh:50`: `wpa_supplicant -Dnl80211,wext`: on 6.1 nl80211 is
  there (`CONFIG_CFG80211`), and `associated()` reads `iwconfig`
  (`:33`), which needed `CONFIG_CFG80211_WEXT` (found 2026-11-24). A
  `wpa_cli status` would not need wext; the image has no wpa_cli (`:30`).
- `linuxrc:67-69`: `passwd`, `inittab`, `inetd.conf` are copied to
  `/usr/etc` once, then only when missing; the install removes `inittab`
  so the new one is taken (`image/usbkey/autorun:159`) but **not
  `inetd.conf`**: a rabbit installed before patch 0001 would keep
  `nowait.60` for good. Patch 0003 removes it too at the end of the
  install. `passwd` stays (it holds no password; nothing changes it).

## 3. What the Terrier client relies on

terrier/karotz/src/ok.c: `leds`, `ears` (`noreset`), `ears_reset`,
`sleep`, `wakeup?silent=1`, `sound?url=`, `sound_control?cmd=quit|volume+N+1`,
`status`, all from `127.0.0.1`, no credentials, one connection each.

- `sound_control?cmd=volume+50+1` (`ok_volume`, `ok.c:118`) is OpenKarotz's
  mplayer slave syntax; LapinLibre's `sound_control` takes `pause resume
  stop quit next previous` only (`sound_control:8`) and would refuse it.
  Nothing in the client calls `ok_volume` today (defined, unused), so
  nothing breaks; if the Terrier's volume ever reaches the Karotz, it
  needs a LapinLibre `volume` command (its player has `softvol`).
- `ears` without `noreset` recalibrates first (10 s wait): the client
  passes `noreset` for its moves and `ears_reset` apart. Right.
- `status` gives `version` as `BUILD_ID (version)`; with patch 0002 the
  version becomes `1.0.0` and the LapinLibre commit moves to
  `/karotz/etc/lapinlibre-base`: the client's update check, if it parses
  `version`, must expect the new shape (the Terrier's `needs_update`
  logic: to check on the Terrier side before 1.0.0 ships).
- The password question (§1.2) is the one that can cut the client off.

## 4. Implemented in this branch

| change | proof |
|---|---|
| `patches/0001`: inetd `nowait.600` | `test-inetd-rate.sh`: 59/61 with 60, 61/61 with 600 (dev VM, 2026-11-24) |
| `patches/0002`: `LL_BUILD_VERSION`, `/karotz/etc/lapinlibre-base` | `test-patches.sh`: applies to `19ea8d7`, `image.py` compiles, override taken |
| `patches/0003`: the install removes `/usr/etc/inetd.conf` so 0001 reaches installed rabbits | `test-patches.sh`; the copy itself is `linuxrc:67-69` (needs Gengar for the whole chain) |
| `Containerfile`: applies `patches/` as one dated commit; `build.sh`: `LL_BUILD_VERSION` from `VERSION` (1.0.0) | `test-patches.sh`; the build itself is the next Terrier OS build (hours) |

Needs Gengar: §2.2 (the shutter's first measurement), the install with
0003 end to end (`/usr/etc/inetd.conf` recopied), §3 (the version string
the Terrier reads).
