# The base is named here, not in build.yaml: since 2026 the Supervisor builds
# with BuildKit and no longer reads build.yaml (Home Assistant's developer
# blog, 2026-04-02), and a user's install was built on Home Assistant's own
# base, s6 and no Python ("python3: not found", Vantosha's owner, 2026-11-22).
# 3.13 at least: TLS for adopted rabbits needs SSLContext.set_psk_server_callback.
# A multi-platform image: amd64 and aarch64 from the same line.

# --- Stage 1: build whisper.cpp (bundled, local STT) ------------------------ #
# Built on the SAME base as the runtime image so the musl ABI matches. OpenMP is
# off and libstdc++/libgcc are linked statically, so the resulting binary needs
# only musl libc at runtime.
FROM python:3.15-alpine AS whisper
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
# No model in the image since 0.19.0: the small one was ~466 MB, two thirds of
# the image, sent to every install while voice is off by default. server.py
# downloads the chosen one into /data/models on first use, from a fixed
# revision and checked against its SHA-256 (WHISPER_MODELS there).

# --- Stage 2: the add-on --------------------------------------------------- #
FROM python:3.15-alpine
LABEL org.opencontainers.image.title="Nabaztag Terrier" \
      org.opencontainers.image.source="https://github.com/ClaraVnk/nabaztag"
WORKDIR /app

# The server runs on the base image's own Python (/usr/local/bin/python3);
# Alpine's python3 package was a second, unused interpreter (+37 MB) and curl
# was never called at runtime. espeak-ng = local TTS; libstdc++ = whisper.cpp
# runtime; ffmpeg = audio conversion; ca-certificates = the model download
# and https:// sounds.
# The stock Violet and OpenJabNab bytecodes, kept for protocol work and
# comparison; what the server actually serves is the hybrid build copied below.
# They are in vendor/openjabnab/ (fetched from GitHub at build time until
# 2026-11-12, so a vanished upstream would have broken the build), still
# checked against their SHA-256.
ARG OJN_COMMIT=640257f3cef63fb428d80b8c171f3b15d17ab0ed
ARG BOOTCODE_VIOLET_SHA256=59e91c6a432056ee94b7c8fe8a1eb7da23a9f4539b9c60e031290322bce4de53
ARG BOOTCODE_OJN_SHA256=4b051f42a647e55deacea95d30bdf14c31283cfc2048e3bbe78991e4df7e6724
COPY vendor/openjabnab/bootcode.violet vendor/openjabnab/bootcode.ojn /app/
RUN apk add --no-cache ca-certificates espeak-ng libstdc++ ffmpeg && \
    echo "${BOOTCODE_VIOLET_SHA256}  /app/bootcode.violet" | sha256sum -c - && \
    echo "${BOOTCODE_OJN_SHA256}  /app/bootcode.ojn" | sha256sum -c -

# Bundled local STT (whisper.cpp; its model is fetched on first use).
COPY --from=whisper /out/whisper-cli /usr/local/bin/whisper-cli

# The runtime bytecode the rabbit downloads at /vl/bc.jsp — the hybrid build,
# versioned HERE (bytecode/build-bytecode.sh writes a new one locally; copy it
# to bootcode.hybrid and update the checksum below in the same commit — the one
# on the bootcode line, not the whisper.cpp source's above). The
# checksum is verified at build so the file can never drift unnoticed. Without
# it a fresh install has no bytecode at all and every boot 500s (issue #2).
COPY bootcode.hybrid /app/bootcode.hybrid
RUN echo "c0ac2ecfb9942fcb4d451f2019bb162dcde76870ad7eb64bf9e8a3ba9c501f80  /app/bootcode.hybrid" \
    | sha256sum -c -

# The runtime for rabbits that ran the adoption exchange (Naboot 1.0.0.0 and
# later): the hybrid plus the TLS client and the fixes since, built from
# OpenJabNab 640257f by RUNTIME=tls bytecode/build-bytecode.sh (see bytecode/README.md).
# Never served to a stock rabbit, which lacks the opcodes it calls.
COPY bootcode.tls /app/bootcode.tls
RUN echo "16efd859b467e04c6510045b8b399af5684e55ff90310991151258c7c226333e  /app/bootcode.tls" \
    | sha256sum -c -

# For adopted rabbits on Naboot 1.2.0.0 and later: the same plus DHCP and DNS
# replies checked, HTTP answers bounded and https:// streams opened by the
# rabbit itself -- Le Terrier's baseline-https, built by
# bytecode/build-bytecode.sh with its default RUNTIME. It calls the https
# opcodes, so it goes to no older image.
COPY bootcode.https /app/bootcode.https
RUN echo "a6500263f7e0181017c4f4ded48ebe6f11a9fdded0f0dea96ef21deb3f8f7de8  /app/bootcode.https" \
    | sha256sum -c -

# Dependency-free server (Python stdlib only); reads /data/options.json at runtime.
# adoption.py: the per-rabbit key and TLS 1.3 PSK for Naboot rabbits.
COPY server.py adoption.py /app/
# Original Nabaztag jingles (Standard MIDI, extracted from the firmware) — the
# server renders them to audio on the fly via a tiny built-in synth.
COPY sounds /app/sounds

CMD [ "python3", "/app/server.py" ]
