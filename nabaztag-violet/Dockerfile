ARG BUILD_FROM=python:3.15-alpine

# --- Stage 1: build whisper.cpp (bundled, local STT) + fetch a model -------- #
# Built on the SAME base as the runtime image so the musl ABI matches. OpenMP is
# off and libstdc++/libgcc are linked statically, so the resulting binary needs
# only musl libc at runtime. The model download is a separate layer so swapping
# the model doesn't force a whisper recompile.
FROM ${BUILD_FROM} AS whisper
RUN apk add --no-cache build-base cmake curl
# Pinned: a release tag, as a source archive from this project's own mirror
# release (mirror-2026-11, a `git archive` of the tag's commit), checked
# against its SHA-256. It was cloned from github.com/ggml-org/whisper.cpp
# until 2026-11-12; the mirror keeps the add-on building if that ever goes.
# Renovate cannot track this; the weekly security workflow reports when a
# newer release exists.
ARG MIRROR=https://github.com/ClaraVnk/nabaztag/releases/download/mirror-2026-11
ARG WHISPER_VERSION=v1.9.4
ARG WHISPER_COMMIT=927cfce34f31707e17f2bff35c349632fb9e2c3a
ARG WHISPER_SRC_SHA256=365bf8e2aa11b79b0df74fcd2dc3a368ed284eee1e7276f98f3ceaa33a861d89
RUN curl -fsSL -o /whisper.tar.gz "${MIRROR}/whisper.cpp-${WHISPER_VERSION}.tar.gz" && \
    echo "${WHISPER_SRC_SHA256}  /whisper.tar.gz" | sha256sum -c - && \
    mkdir /src && tar -xzf /whisper.tar.gz -C /src --strip-components=1 && \
    cmake -S /src -B /b -DCMAKE_BUILD_TYPE=Release -DGGML_OPENMP=OFF \
      -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON -DBUILD_SHARED_LIBS=OFF \
      -DCMAKE_EXE_LINKER_FLAGS="-static-libgcc -static-libstdc++" && \
    cmake --build /b -j"$(nproc)" --target whisper-cli && \
    install -D "$(find /b -name whisper-cli -type f | head -1)" /out/whisper-cli
# The model from a fixed revision (huggingface.co/ggerganov/whisper.cpp at
# WHISPER_MODEL_REVISION), copied to the mirror release and checked against its
# published SHA-256. Changing the model means changing all three together and
# uploading the new file to the mirror.
ARG WHISPER_MODEL=ggml-small.bin
ARG WHISPER_MODEL_REVISION=5359861c739e955e79d9a303bcbc70fb988958b1
ARG WHISPER_MODEL_SHA256=1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b
RUN mkdir -p /out/models && \
    curl -fsSL -o "/out/models/${WHISPER_MODEL}" \
      "${MIRROR}/${WHISPER_MODEL}" && \
    echo "${WHISPER_MODEL_SHA256}  /out/models/${WHISPER_MODEL}" | sha256sum -c -

# --- Stage 2: the add-on --------------------------------------------------- #
FROM ${BUILD_FROM}
WORKDIR /app

# python3 = the server; espeak-ng = local TTS; libstdc++ = whisper.cpp runtime.
# The stock Violet and OpenJabNab bytecodes, kept for protocol work and
# comparison; what the server actually serves is the hybrid build copied below.
# They are in vendor/openjabnab/ (fetched from GitHub at build time until
# 2026-11-12, so a vanished upstream would have broken the build), still
# checked against their SHA-256.
ARG OJN_COMMIT=640257f3cef63fb428d80b8c171f3b15d17ab0ed
ARG BOOTCODE_VIOLET_SHA256=59e91c6a432056ee94b7c8fe8a1eb7da23a9f4539b9c60e031290322bce4de53
ARG BOOTCODE_OJN_SHA256=4b051f42a647e55deacea95d30bdf14c31283cfc2048e3bbe78991e4df7e6724
COPY vendor/openjabnab/bootcode.violet vendor/openjabnab/bootcode.ojn /app/
RUN apk add --no-cache python3 curl ca-certificates espeak-ng libstdc++ ffmpeg && \
    echo "${BOOTCODE_VIOLET_SHA256}  /app/bootcode.violet" | sha256sum -c - && \
    echo "${BOOTCODE_OJN_SHA256}  /app/bootcode.ojn" | sha256sum -c -

# Bundled local STT (whisper.cpp + small model) for Phase-2 voice input.
COPY --from=whisper /out/whisper-cli /usr/local/bin/whisper-cli
COPY --from=whisper /out/models /app/models

# The runtime bytecode the rabbit downloads at /vl/bc.jsp — the hybrid build,
# versioned HERE (bytecode/build-bytecode.sh writes a new one locally; copy it
# to bootcode.hybrid and update the checksum below in the same commit — the one
# on the bootcode line, not the Whisper model's a few lines up). The
# checksum is verified at build so the file can never drift unnoticed. Without
# it a fresh install has no bytecode at all and every boot 500s (issue #2).
COPY bootcode.hybrid /app/bootcode.hybrid
RUN echo "c0ac2ecfb9942fcb4d451f2019bb162dcde76870ad7eb64bf9e8a3ba9c501f80  /app/bootcode.hybrid" \
    | sha256sum -c -

# The runtime for rabbits that ran the adoption exchange (Naboot 1.0.0.0 and
# later): the hybrid plus the TLS client and the fixes since, built from
# OpenJabNab 640257f by bytecode/build-bytecode.sh (see bytecode/README.md).
# Never served to a stock rabbit, which lacks the opcodes it calls.
COPY bootcode.tls /app/bootcode.tls
RUN echo "2c561ba955746c001eb5a778d5dbe9635d19be52a57c533ed53fde6b78be4cee  /app/bootcode.tls" \
    | sha256sum -c -

# Dependency-free server (Python stdlib only); reads /data/options.json at runtime.
# adoption.py: the per-rabbit key and TLS 1.3 PSK for Naboot rabbits.
COPY server.py adoption.py /app/
# Original Nabaztag jingles (Standard MIDI, extracted from the firmware) — the
# server renders them to audio on the fly via a tiny built-in synth.
COPY sounds /app/sounds

CMD [ "python3", "/app/server.py" ]
