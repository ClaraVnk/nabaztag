ARG BUILD_FROM=python:3.12-alpine
FROM ${BUILD_FROM}

WORKDIR /app

# The original Nabaztag bytecode is Violet's proprietary firmware, so it is not
# committed here — fetch it at build time from the OpenJabNab distribution.
# Install python3 explicitly so the image works regardless of the (Alpine) base.
RUN apk add --no-cache python3 curl ca-certificates && \
    curl -fsSL -o /app/bootcode.violet \
      https://raw.githubusercontent.com/OpenJabNab/OpenJabNab/master/http-wrapper/ojn_local/bootcode/bootcode.violet && \
    [ -s /app/bootcode.violet ]

# Dependency-free server (stdlib only); reads /data/options.json at runtime.
COPY server.py /app/

CMD [ "python3", "/app/server.py" ]
