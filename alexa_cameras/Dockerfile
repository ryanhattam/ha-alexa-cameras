# The Home Assistant builder always passes --build-arg BUILD_FROM from build.yaml, so
# the default below is inert for add-on builds. It exists so `docker build` works on its
# own for a standalone container (see docker-compose.yml and docs/DOCKER.md) — x86_64,
# which is what Unraid runs. Deliberately the same base image the add-on uses, so the
# runtime (s6-overlay as PID 1, ffmpeg, python3) is identical in both modes.
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.19
FROM ${BUILD_FROM}

# ffmpeg: transcode/mux to MPEG-TS HLS. bash: run script (mapfile). python3 + py3-yaml:
# config parsing + HTTP/UI server. procps: pkill, used to reload camera workers.
RUN apk add --no-cache bash ffmpeg python3 py3-yaml procps

COPY run.sh /run.sh
COPY haapi.py /haapi.py
COPY hlsd.py /hlsd.py
COPY ui.py /ui.py
COPY injector.py /injector.py
COPY config.yaml /manifest.yaml
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
