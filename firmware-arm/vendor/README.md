# Vendored upstream firmware source

The Nabaztag:tag v2 firmware is built on RedoXyde's `nabgcc` fork and its
Metal compiler `mtl_linux`, both on GitHub. The emulator and the flashed
firmware clone them at a pinned commit. GitHub repositories disappear, and if
these did we would lose the only source the rabbit runs — so a copy lives here,
and the build reads it instead of the network. The image now builds offline and
survives upstream vanishing.

## What is here

- `nabgcc/` — the firmware fork, at commit
  `2c05b53fe4b17506cfff6570b9f1624ff7c2cdc3`
  (`https://github.com/RedoXyde/nabgcc`).
- `nabgcc/mtl/mtl_linux/` — the Metal compiler, at commit
  `7e557a15373523f819e49d57e7a9c5419327b651`
  (`https://github.com/RedoXyde/mtl_linux`; a submodule of nabgcc, filled in
  here with its real content). We build the bytecode with our own
  `../tools/mtl_comp.py`, byte-identical to this C++ compiler; it is kept for
  cross-checking and because `apply-mods.py` reads
  `mtl/mtl_linux/src/vcomp/stdlib_core.cpp`.

Both `LICENSE` files are kept beside the code they cover; nothing here is ours.

## What was removed

- `nabgcc/openocd/openocd` — a 9.6 MB prebuilt OpenOCD binary, not source and
  not used by the build (the firmware is compiled with `arm-none-eabi-gcc`).
  The flashing configs under `nabgcc/openocd/` (`nabaztagv2.cfg`, `interface/`,
  `target/`) are kept. Our own patched OpenOCD for writing the flash lives with
  the flash-station tooling, not here.
- the two `.git` directories (this is a snapshot, not a working clone).

## Refreshing from upstream

Only to move to a newer upstream commit — the point of vendoring is that you do
not have to:

```sh
git clone https://github.com/RedoXyde/nabgcc.git /tmp/nabgcc
git -C /tmp/nabgcc checkout <new-commit>
git clone https://github.com/RedoXyde/mtl_linux.git /tmp/mtl_linux
rm -rf nabgcc && git -C /tmp/nabgcc archive HEAD | (mkdir nabgcc && tar -x -C nabgcc)
git -C /tmp/mtl_linux archive HEAD | (mkdir nabgcc/mtl/mtl_linux && tar -x -C nabgcc/mtl/mtl_linux)
rm -f nabgcc/openocd/openocd
```

Then bump `NABGCC_COMMIT` / `MTL_LINUX_COMMIT` in `../Dockerfile` and
`../emulator/Containerfile` to match, and run `../emulator/run.sh` to confirm
it still builds.
