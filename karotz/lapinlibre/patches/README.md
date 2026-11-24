# Terrier OS's patches to LapinLibre

Applied by `Containerfile` on the fork's commit (`LL_COMMIT`) before the
build, in name order, as one commit "Terrier OS patches" (a fixed date, so
the commit and `git describe` are the same from one build to the next).
`test-patches.sh` checks that they still apply to `LL_COMMIT` and that the
Python they touch compiles. Nothing here is sent upstream by the build:
Clara decides what is reported to LapinLibre.

| patch | what, and why |
|---|---|
| 0001 | inetd takes 600 web connections a minute, not 60: past the count BusyBox inetd stops httpd for ten minutes, and the Terrier client on the rabbit opens one connection per LED or ear order (`test-inetd-rate.sh`, 2026-11-24). |
| 0003 | the install removes `/usr/etc/inetd.conf` as it does `inittab`, so the new one (0001) is copied at the next boot; `linuxrc` copies these files only when missing. |
| 0002 | `LL_BUILD_VERSION` names the release (Terrier OS 1.0.0, `../VERSION`); the LapinLibre commit stays in `/karotz/etc/lapinlibre-base`. |
