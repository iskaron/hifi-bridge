# OS tooling for phase-2b preparation (ONE image for all of it):
#  * Buildroot host prerequisites (configuration checks, show-info licence reports; later also the real image build),
#  * genimage/mtools/dosfstools/e2fsprogs/squashfs-tools for the disk-layout test with dummy content,
#  * RAUC (host tool) + openssl for bundle-signature and keyring tests,
#  * python3-cryptography for the flash tool's core logic tests,
#  * the SOURCE of libubootenv at the version Buildroot pins (sha256 = Buildroot's package/libubootenv/libubootenv.hash; checked against the
#    pinned tree by scripts/os_defconfigs.sh) + zlib/libyaml headers: os/tools/build_libubootenv.sh compiles the image's fw_printenv/fw_setenv
#    (with our patches) natively for the environment corruption simulations (tests/os, image check). Never installed system-wide.
# No board image is built with it in this package. Lab/CI only.
FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential make gcc g++ patch perl python3 python3-pytest python3-cryptography python3-coverage file wget ca-certificates cpio unzip rsync bc \
      git xz-utils bzip2 libncurses-dev \
      genimage mtools dosfstools e2fsprogs squashfs-tools fdisk rauc openssl u-boot-tools zlib1g-dev libyaml-dev \
 && rm -rf /var/lib/apt/lists/*
ARG LIBUBOOTENV_VERSION=0.3.7
ARG LIBUBOOTENV_SHA256=92021fc5a0a965030588d0c867a771d28b28c0d17c9fd022f0dafe9f01de714e
RUN mkdir -p /opt/src && wget -q -O /opt/src/libubootenv-${LIBUBOOTENV_VERSION}.tar.gz \
      https://github.com/sbabic/libubootenv/archive/v${LIBUBOOTENV_VERSION}/libubootenv-${LIBUBOOTENV_VERSION}.tar.gz \
 && echo "${LIBUBOOTENV_SHA256}  /opt/src/libubootenv-${LIBUBOOTENV_VERSION}.tar.gz" | sha256sum -c -
ENV PYTHONPATH=/work/bridge:/work/flashtool PYTHONDONTWRITEBYTECODE=1
WORKDIR /work
