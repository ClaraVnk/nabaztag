# Layered on the prebuilt OpenJabNab image (see build.yaml). Supervisor passes
# BUILD_FROM at build time; the default keeps `docker build` working standalone.
ARG BUILD_FROM=antoineaumjaud/docker-openjabnab:latest@sha256:77e335bcfc336bd401b5462c11e4bf361018ff0817ba8a5a22961509cb8a19bb
FROM ${BUILD_FROM}

# OpenJabNab's HTTP listener binds 127.0.0.1 only, so Apache reverse-proxies
# :80 -> 127.0.0.1:8080. Enable the proxy modules at build time.
RUN a2enmod proxy proxy_http rewrite

# Add-on entrypoint: reads HA options, renders persistent config into /data,
# then starts OpenJabNab (and optionally the admin UI). Replaces the image's
# baked-in start.sh.
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
