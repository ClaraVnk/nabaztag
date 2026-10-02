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
# Nabaztag bytecodes are Violet's proprietary firmware, not committed — fetched
# at build from the OpenJabNab distribution (selectable via the `bootcode` option:
# violet/ojn).
RUN apk add --no-cache python3 curl ca-certificates espeak-ng libstdc++ ffmpeg && \
    curl -fsSL -o /app/bootcode.violet \
      https://raw.githubusercontent.com/OpenJabNab/OpenJabNab/master/http-wrapper/ojn_local/bootcode/bootcode.violet && \
    curl -fsSL -o /app/bootcode.ojn \
      https://raw.githubusercontent.com/OpenJabNab/OpenJabNab/master/http-wrapper/ojn_local/bootcode/bootcode.ojn && \
    [ -s /app/bootcode.violet ] && [ -s /app/bootcode.ojn ]

# Bundled local STT (whisper.cpp + small model) for Phase-2 voice input.
COPY --from=whisper /out/whisper-cli /usr/local/bin/whisper-cli
COPY --from=whisper /out/models /app/models

# Dependency-free server (Python stdlib only); reads /data/options.json at runtime.
COPY server.py /app/
# Original Nabaztag jingles (Standard MIDI, extracted from the firmware) — the
# server renders them to audio on the fly via a tiny built-in synth.
COPY sounds /app/sounds

CMD [ "python3", "/app/server.py" ]
