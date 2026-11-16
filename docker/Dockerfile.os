# OS tooling for phase-2b preparation (ONE image for all of it):
#  * Buildroot host prerequisites (configuration checks, show-info licence reports; later also the real image build),
#  * genimage/mtools/dosfstools/e2fsprogs/squashfs-tools for the disk-layout test with dummy content,
#  * RAUC (host tool) + openssl for bundle-signature and keyring tests,
#  * python3-cryptography for the flash tool's core logic tests.
# No board image is built with it in this package. Lab/CI only.
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential make gcc g++ patch perl python3 python3-pytest python3-cryptography file wget ca-certificates cpio unzip rsync bc \
      git xz-utils bzip2 libncurses-dev \
      genimage mtools dosfstools e2fsprogs squashfs-tools fdisk rauc openssl u-boot-tools \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONPATH=/work/bridge:/work/flashtool PYTHONDONTWRITEBYTECODE=1
WORKDIR /work
