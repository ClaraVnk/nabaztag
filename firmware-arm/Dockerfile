# Build environment for the Nabaztag:tag v2 ARM firmware (nabgcc fork).
# Target SoC: OKI ML67Q4051 (ARM7TDMI). Output: firmware0.0.0.13.sim ready to
# upload via the rabbit's config-mode page (no opening required).

FROM debian:bookworm

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      gcc-arm-none-eabi \
      libnewlib-arm-none-eabi \
      build-essential \
      g++-multilib \
      php-cli \
      python3 \
      git \
      ca-certificates \
      make \
 && rm -rf /var/lib/apt/lists/*

ARG NABGCC_COMMIT=2c05b53fe4b17506cfff6570b9f1624ff7c2cdc3
ARG MTL_LINUX_COMMIT=7e557a15373523f819e49d57e7a9c5419327b651

WORKDIR /build
RUN git clone https://github.com/RedoXyde/nabgcc.git \
 && cd nabgcc \
 && git checkout "$NABGCC_COMMIT" \
 && git clone https://github.com/RedoXyde/mtl_linux.git mtl/mtl_linux \
 && cd mtl/mtl_linux && git checkout "$MTL_LINUX_COMMIT"

# Drop our crypto module + vendored TweetNaCl + the embedded public key.
# .c files go under src/crypto/ (compiled in via the existing src/**/*.c
# glob). Headers go under inc/ so the existing -Iinc/ resolves them from
# any source file (vinterp.c lives in src/vm/ and needs to #include them).
COPY upstream/tweetnacl/tweetnacl.c /build/nabgcc/src/crypto/tweetnacl.c
COPY crypto/nab_sig.c               /build/nabgcc/src/crypto/nab_sig.c
COPY upstream/tweetnacl/tweetnacl.h /build/nabgcc/inc/tweetnacl.h
COPY crypto/nab_sig.h               /build/nabgcc/inc/nab_sig.h
COPY keys/signing_pubkey.h          /build/nabgcc/inc/signing_pubkey.h

# Apply our source modifications (idempotent, prints what landed).
COPY apply-mods.py /build/apply-mods.py
# Quick sanity probe before the real patch run — confirms apply-mods.py is
# the version we just shipped and the stdlib anchors are still present in
# upstream. (Helps diagnose cache hits that mask stale patch logic.)
RUN sha256sum /build/apply-mods.py /build/nabgcc/mtl/mtl_linux/src/vcomp/stdlib_core.cpp \
 && grep -c verifySig /build/nabgcc/mtl/mtl_linux/src/vcomp/stdlib_core.cpp || true
RUN python3 /build/apply-mods.py /build/nabgcc
RUN grep -c verifySig /build/nabgcc/mtl/mtl_linux/src/vcomp/stdlib_core.cpp \
 && grep -c OPverifySig /build/nabgcc/mtl/mtl_linux/inc/vm/vbc.h

# Optional patches/ dir (for ad-hoc tweaks that don't belong in apply-mods.py).
COPY patches/ /patches/
RUN cd /build/nabgcc && \
    set -e; \
    if ls /patches/*.patch >/dev/null 2>&1; then \
      for p in /patches/*.patch; do \
        echo "Applying $p"; \
        git apply --check "$p"; \
        git apply "$p"; \
      done; \
    else \
      echo "no .patch files to apply"; \
    fi

# Build: bytecode bootloader → ELF → bin → .sim
RUN cd /build/nabgcc \
 && make mtl_boot \
 && make -j"$(nproc)" \
 && php utils/mkfirmware.php bin/Nab.bin bin/firmware0.0.0.13.sim \
 && arm-none-eabi-size bin/Nab.elf \
 && ls -la bin/Nab.bin bin/firmware0.0.0.13.sim

# Outputs the artifacts to a tar on stdout when run with no command.
CMD ["sh", "-c", "tar -C /build/nabgcc/bin -cf - Nab.bin Nab.elf Nab.map firmware0.0.0.13.sim 2>/dev/null || tar -C /build/nabgcc/bin -cf - Nab.bin firmware0.0.0.13.sim"]
