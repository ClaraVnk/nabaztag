ARG BUILD_FROM=python:3.12-alpine

# --- Stage 1: build whisper.cpp (bundled, local STT) + fetch a model -------- #
# Built on the SAME base as the runtime image so the musl ABI matches. OpenMP is
# off and libstdc++/libgcc are linked statically, so the resulting binary needs
# only musl libc at runtime. The model download is a separate layer so swapping
# the model doesn't force a whisper recompile.
FROM ${BUILD_FROM} AS whisper
RUN apk add --no-cache build-base cmake git curl
RUN git clone --depth 1 https://github.com/ggerganov/whisper.cpp /src && \
    cmake -S /src -B /b -DCMAKE_BUILD_TYPE=Release -DGGML_OPENMP=OFF \
      -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON -DBUILD_SHARED_LIBS=OFF \
      -DCMAKE_EXE_LINKER_FLAGS="-static-libgcc -static-libstdc++" && \
    cmake --build /b -j"$(nproc)" --target whisper-cli && \
    install -D "$(find /b -name whisper-cli -type f | head -1)" /out/whisper-cli
ARG WHISPER_MODEL=ggml-small.bin
RUN mkdir -p /out/models && \
    curl -fsSL -o "/out/models/${WHISPER_MODEL}" \
      "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${WHISPER_MODEL}" && \
    [ -s "/out/models/${WHISPER_MODEL}" ]

# --- Stage 2: the add-on --------------------------------------------------- #
FROM ${BUILD_FROM}
WORKDIR /app

# python3 = the server; espeak-ng = local TTS; libstdc++ = whisper.cpp runtime.
# Nabaztag bytecodes are Violet's proprietary firmware, not committed — fetched
# at build from the OpenJabNab distribution (selectable via the `bootcode` option:
# violet/ojn).
RUN apk add --no-cache python3 curl ca-certificates espeak-ng libstdc++ && \
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
