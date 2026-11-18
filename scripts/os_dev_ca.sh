#!/usr/bin/env bash
# Temporary DEVELOPMENT update CA for internal builds (docs/UPDATE_TRUST.md, "own" trust). Never a release key.
#   scripts/os_dev_ca.sh            create it once (reused afterwards, so rebuilds get the same keyring and stay comparable)
#   scripts/os_dev_ca.sh --rotate   throw it away and create a new one
# Location: $HIFI_LAB/dev-update-ca (default .lab/, git-ignored), directory 0700, keys 0600:
#   ca.pem (public, built into the image keyring)   ca.key (private)
#   signing.pem (codeSigning, for development bundles)   signing.key (private)
# The private keys are created inside a throwaway container and never mounted into a build container (scripts/os_build.sh mounts only
# ca.pem). Prints nothing secret.
set -euo pipefail
cd "$(dirname "$0")/.."
LAB="${HIFI_LAB:-$PWD/.lab}"
D="$LAB/dev-update-ca"
if [ "${1:-}" = "--rotate" ]; then rm -rf "$D"; fi
if [ -s "$D/ca.pem" ] && [ -s "$D/ca.key" ] && [ -s "$D/signing.pem" ] && [ -s "$D/signing.key" ]; then
	echo "dev CA: reusing $D/ca.pem"
	exit 0
fi
mkdir -p "$D"
chmod 0700 "$D"
docker image inspect btbridge-os:dev >/dev/null 2>&1 || docker build -q -f docker/Dockerfile.os -t btbridge-os:dev docker >/dev/null
docker run --rm --name "hifi-os-devca-$$" --network none -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$D":/ca btbridge-os:dev bash -c '
set -euo pipefail
umask 077
cd /ca
id=$(openssl rand -hex 4)
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout ca.key -out ca.pem -days 365 \
  -subj "/O=Hi-Fi Bridge development/OU=temporary, not for release/CN=Hi-Fi Bridge DEV update CA $id" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
printf "basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=codeSigning\n" > ext
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout signing.key -out signing.csr \
  -subj "/O=Hi-Fi Bridge development/OU=temporary, not for release/CN=Hi-Fi Bridge DEV bundle signing $id" 2>/dev/null
openssl x509 -req -in signing.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out signing.pem -days 365 -extfile ext 2>/dev/null
rm -f signing.csr ext ca.srl
chmod 0644 ca.pem signing.pem
chmod 0600 ca.key signing.key
openssl x509 -in ca.pem -noout -subject -fingerprint -sha256'
cat > "$D/README.txt" <<'EOF'
TEMPORARY DEVELOPMENT update CA created by scripts/os_dev_ca.sh. Not a release key. Never commit, copy or publish the *.key files.
Images built with it (scripts/os_build.sh) accept only bundles signed by signing.pem. Rotate with: scripts/os_dev_ca.sh --rotate
EOF
echo "dev CA: created $D/ca.pem"
