# Violet's and OpenJabNab's reference bytecodes, vendored

`bootcode.violet` (Violet's original runtime) and `bootcode.ojn` (OpenJabNab's),
kept by the add-on for protocol work and comparison. The add-on serves its own
hybrid build, not these.

| | |
|---|---|
| upstream | https://github.com/OpenJabNab/OpenJabNab |
| commit | `640257f3cef63fb428d80b8c171f3b15d17ab0ed` (2016-04-30) |
| path | http-wrapper/ojn_local/bootcode/ in that repository |
| licence | as distributed by OpenJabNab (`COPYING`, its GPL-2.0 file); `bootcode.violet` is Violet's own runtime |
| sha256 | `bootcode.violet` 59e91c6a432056ee94b7c8fe8a1eb7da23a9f4539b9c60e031290322bce4de53 |
| | `bootcode.ojn` 4b051f42a647e55deacea95d30bdf14c31283cfc2048e3bbe78991e4df7e6724 |

They were fetched from GitHub at every image build until 2026-11-12; they are
here so the add-on builds, and the history of the hardware stays readable,
even if that repository disappears.
