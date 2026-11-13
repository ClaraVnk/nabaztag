# OpenJabNab's Violet bootcode sources, vendored

The Metal sources of the runtime Violet's servers sent every Nabaztag, as
OpenJabNab published them. Every runtime this project serves is built from
here by `bytecode/build-bytecode.sh`, so the project no longer depends on the
upstream repository staying online.

| | |
|---|---|
| upstream | https://github.com/OpenJabNab/OpenJabNab |
| commit | `640257f3cef63fb428d80b8c171f3b15d17ab0ed` (2016-04-30) |
| path | bootcode/ in that repository |
| licence | GPL-2.0 (`COPYING`, the upstream file) |

Left out: upstream's bootcode/compiler directory, OpenJabNab's copies of the Metal compiler
(`mtl_linux`, `parser`). The runtime is compiled by
`firmware-arm/tools/mtl_comp.py`, byte-identical to the C++ compiler.

Nothing here is edited: the patches in `bytecode/` are applied to a copy at
build time.

    bytecode/build-bytecode.sh <copy of this bootcode/> firmware-arm/tools/mtl_comp.py out.hybrid

To refresh (only if upstream ever moves, which it has not since 2016):
git archive <commit> COPYING bootcode, from a clone, drop the compiler directory,
update the commit above.
