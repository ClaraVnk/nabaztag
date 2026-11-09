ARG BUILD_FROM=python:3.12-alpine

# --- Stage 1: build whisper.cpp (bundled, local STT) + fetch a model -------- #
# Built on the SAME base as the runtime image so the musl ABI matches. OpenMP is
# off and libstdc++/libgcc are linked statically, so the resulting binary needs
# only musl libc at runtime. The model download is a separate layer so swapping
# the model doesn't force a whisper recompile.
FROM ${BUILD_FROM} AS whisper
RUN apk add --no-cache build-base cmake git curl
# Pinned: a release tag, and the commit it must resolve to, so a moved tag
# fails the build instead of silently shipping other code. Dependabot cannot
# track this; the weekly security workflow reports when a newer release exists.
ARG WHISPER_VERSION=v1.9.4
ARG WHISPER_COMMIT=927cfce34f31707e17f2bff35c349632fb9e2c3a
RUN git clone --depth 1 --branch "${WHISPER_VERSION}" https://github.com/ggml-org/whisper.cpp /src && \
    [ "$(git -C /src rev-parse HEAD)" = "${WHISPER_COMMIT}" ] && \
    cmake -S /src -B /b -DCMAKE_BUILD_TYPE=Release -DGGML_OPENMP=OFF \
      -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON -DBUILD_SHARED_LIBS=OFF \
      -DCMAKE_EXE_LINKER_FLAGS="-static-libgcc -static-libstdc++" && \
    cmake --build /b -j"$(nproc)" --target whisper-cli && \
    install -D "$(find /b -name whisper-cli -type f | head -1)" /out/whisper-cli
# The model from a fixed revision, checked against its published SHA-256.
# Changing the model means changing all three together.
ARG WHISPER_MODEL=ggml-small.bin
ARG WHISPER_MODEL_REVISION=5359861c739e955e79d9a303bcbc70fb988958b1
ARG WHISPER_MODEL_SHA256=1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b
RUN mkdir -p /out/models && \
    curl -fsSL -o "/out/models/${WHISPER_MODEL}" \
      "https://huggingface.co/ggerganov/whisper.cpp/resolve/${WHISPER_MODEL_REVISION}/${WHISPER_MODEL}" && \
    echo "${WHISPER_MODEL_SHA256}  /out/models/${WHISPER_MODEL}" | sha256sum -c -

# --- Stage 2: the add-on --------------------------------------------------- #
FROM ${BUILD_FROM}
WORKDIR /app

# python3 = the server; espeak-ng = local TTS; libstdc++ = whisper.cpp runtime.
# The STOCK Violet/OpenJabNab bytecodes are Violet's proprietary firmware, not
# committed — fetched here at build time. They are kept for protocol work and
# comparison; what the server actually serves is the hybrid build copied below.
# From a fixed OpenJabNab commit, each checked against its SHA-256, so a change
# upstream fails the build instead of shipping other bytes.
ARG OJN_COMMIT=640257f3cef63fb428d80b8c171f3b15d17ab0ed
ARG BOOTCODE_VIOLET_SHA256=59e91c6a432056ee94b7c8fe8a1eb7da23a9f4539b9c60e031290322bce4de53
ARG BOOTCODE_OJN_SHA256=4b051f42a647e55deacea95d30bdf14c31283cfc2048e3bbe78991e4df7e6724
RUN apk add --no-cache python3 curl ca-certificates espeak-ng libstdc++ ffmpeg && \
    curl -fsSL -o /app/bootcode.violet \
      "https://raw.githubusercontent.com/OpenJabNab/OpenJabNab/${OJN_COMMIT}/http-wrapper/ojn_local/bootcode/bootcode.violet" && \
    curl -fsSL -o /app/bootcode.ojn \
      "https://raw.githubusercontent.com/OpenJabNab/OpenJabNab/${OJN_COMMIT}/http-wrapper/ojn_local/bootcode/bootcode.ojn" && \
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

# Dependency-free server (Python stdlib only); reads /data/options.json at runtime.
COPY server.py /app/
# Original Nabaztag jingles (Standard MIDI, extracted from the firmware) — the
# server renders them to audio on the fly via a tiny built-in synth.
COPY sounds /app/sounds

CMD [ "python3", "/app/server.py" ]
