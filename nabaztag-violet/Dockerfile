ARG BUILD_FROM=python:3.12-alpine
FROM ${BUILD_FROM}

WORKDIR /app

# Nabaztag bytecodes are Violet's proprietary firmware, not committed — fetched
# at build. Three variants (selectable via the `bootcode` option): violet/ojn
# from the OpenJabNab distribution, pub from the live community server's build.
# python3 installed explicitly so the image works regardless of the Alpine base.
RUN apk add --no-cache python3 curl ca-certificates && \
    curl -fsSL -o /app/bootcode.violet \
      https://raw.githubusercontent.com/OpenJabNab/OpenJabNab/master/http-wrapper/ojn_local/bootcode/bootcode.violet && \
    curl -fsSL -o /app/bootcode.ojn \
      https://raw.githubusercontent.com/OpenJabNab/OpenJabNab/master/http-wrapper/ojn_local/bootcode/bootcode.ojn && \
    curl -fsSL -A "MTL" -o /app/bootcode.pub \
      "http://r.nabaztag.com/vl/bc.jsp?v=0.0.0.13&m=00:1d:92:16:4f:1c&l=00:00:00:00:00:00&p=00:00:00:00:00:00&h=4" && \
    [ -s /app/bootcode.violet ] && [ -s /app/bootcode.ojn ] && [ -s /app/bootcode.pub ]

# Dependency-free server (stdlib only); reads /data/options.json at runtime.
COPY server.py /app/

CMD [ "python3", "/app/server.py" ]
