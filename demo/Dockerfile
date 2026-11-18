# Hi-Fi Bridge interactive demo: ONE image for every demo service (bridge node, virtual phones console, Squeezelite players, gateway).
# Debian trixie: PipeWire 1.4 (closest to Buildroot's 1.2.8; capture via pw-cat --raw), Python 3.13.
# Build context = repository root, filtered by /.dockerignore (allowlist: no secrets/, no .lab/, no .git/).
# Nothing here is a product image: the product code is copied unchanged; everything simulated lives in testkit/ and demo/.
FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-numpy python3-jeepney python3-cryptography python3-pytest python3-coverage \
      dbus-daemon pipewire pipewire-bin pipewire-pulse wireplumber pulseaudio-utils \
      squeezelite flac mpg123 vorbis-tools openssl curl procps tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# Unprivileged runtime user; the named volumes are created from these (owned) directories.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin hifi \
 && mkdir -p /data /boot-demo /owner /run/hifi /var/cache/hifi-demo \
 && chown hifi:hifi /data /boot-demo /owner /run/hifi /var/cache/hifi-demo
COPY bridge/btbridge /opt/hifi-bridge/bridge/btbridge
COPY testkit/btkit /opt/hifi-bridge/testkit/btkit
COPY testkit/pw/start_pipewire.sh /opt/hifi-bridge/testkit/pw/start_pipewire.sh
COPY flashtool/hififlash /opt/hifi-bridge/flashtool/hififlash
COPY demo /opt/hifi-bridge/demo
ENV PYTHONPATH=/opt/hifi-bridge/bridge:/opt/hifi-bridge/testkit:/opt/hifi-bridge/flashtool:/opt/hifi-bridge/demo \
    XDG_RUNTIME_DIR=/run/hifi/pw
WORKDIR /opt/hifi-bridge
USER hifi
ENTRYPOINT ["/usr/bin/tini", "--"]
LABEL org.opencontainers.image.title="hifi-demo" org.opencontainers.image.source="https://github.com/iskaron/hifi-bridge" net.hifi-bridge.component="demo"
