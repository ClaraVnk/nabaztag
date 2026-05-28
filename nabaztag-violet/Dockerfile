ARG BUILD_FROM=python:3.12-alpine
FROM ${BUILD_FROM}

WORKDIR /app

# Nabaztag bytecodes are Violet's proprietary firmware, not committed — fetched
# at build from the OpenJabNab distribution (selectable via the `bootcode` option:
# violet/ojn). python3 installed explicitly so the image works on any Alpine base.
RUN apk add --no-cache python3 curl ca-certificates && \
    curl -fsSL -o /app/bootcode.violet \
      https://raw.githubusercontent.com/OpenJabNab/OpenJabNab/master/http-wrapper/ojn_local/bootcode/bootcode.violet && \
    curl -fsSL -o /app/bootcode.ojn \
      https://raw.githubusercontent.com/OpenJabNab/OpenJabNab/master/http-wrapper/ojn_local/bootcode/bootcode.ojn && \
    [ -s /app/bootcode.violet ] && [ -s /app/bootcode.ojn ]

# Dependency-free server (stdlib only); reads /data/options.json at runtime.
COPY server.py /app/

CMD [ "python3", "/app/server.py" ]
