# Update trust and signing keys (P5, DECISIONS #55, #61)

Status: the trust configuration is implemented in the Buildroot skeleton (`os/br2-external`). It is verified with the **real RAUC 1.13
host tool** for signatures and keyrings (`tests/os/test_update_trust.py`), and the update management on top of it (docs/OTA.md) with the
**real RAUC 1.13 D-Bus service** in a container (`scripts/run_ota.sh`). **Installing an update on a device has not been tested** (no
image built, nothing booted). The official release CA **does not exist yet**.

## 1. Goals
* Official images accept only official release bundles.
* A self-built image can be configured to accept **official** bundles, its **own**, or **both**. The choice is explicit and visible.
* The release signing key is never present on a build machine, in CI or in a pull-request job.
* Development and release keys are separate and cannot be confused.

## 2. Key hierarchy
| Key | Where it lives | Signs | Notes |
|---|---|---|---|
| **Release CA** (root of official trust) | offline (hardware token or air-gapped machine) | the release signing certificate only | its **public certificate** is `os/br2-external/board/hifi-bridge/keys/release-ca.pem` (to be created) |
| **Release signing key** | offline, same place | official update bundles (by re-signing) and flash manifests | certificate with `extendedKeyUsage=codeSigning`; short validity; rotated through the CA |
| **Own/development CA** | the builder's machine (`.keys/`, git-ignored) | own signing certificates | each self-builder makes their own; never shared |
| **CI/development signing key** | the build machine or CI secret store | bundles built by CI (development-signed) | accepted only by images whose trust includes that CA |

Only **public certificates** are ever built into an image. `post-build.sh` refuses any input file that contains a private key, more than one
certificate, or a certificate that is not a CA.

## 3. The explicit choice (Buildroot `menuconfig` → hifi-bridge → "Accepted update signatures")
| Choice | Keyring in the image | Accepts | Typical use |
|---|---|---|---|
| *not configured* | – | – | **the build fails** with a pointer to this document |
| `official releases only` | release CA | official bundles | official images; a self-builder who only wants official updates |
| `own update CA only` | own CA | own bundles | development, forks |
| `official releases and own update CA` | release CA + own CA | both | a self-builder who wants official updates **and** own test builds |

The image records the choice in `/etc/hifi-bridge/update-trust.json`: the mode, subject and SHA-256 fingerprint of each certificate, and
the **official update source** (`official_source`, section 4a; `null` in own-only images).
The WebUI update page (`/settings/update`, docs/OTA.md) shows the mode, every certificate's subject and SHA-256 fingerprint and the
official source before any installation.

**Compatible string:** RAUC installs a bundle only if its `compatible` equals the device's. Official bundles use `hifi-bridge-rpi3`,
`hifi-bridge-rpi4` and `hifi-bridge-rpizero2w`. A self-built image that wants official updates must keep the same string (the default).

**Consequence to state in the UI:** installing an **official** bundle on a self-built image replaces the whole root file system, including the
keyring. From then on the device runs the official image with official-only trust. A device that should keep its own trust must stay on
own bundles. This is the documented, intended behaviour.

## 4. Release flow (no release key in CI)
1. CI (or a developer) builds the image and the RAUC bundle, **signed with a development/CI key**. Pull-request jobs only ever see
   development keys generated for the test run (`tests/os` creates throwaway CAs).
2. A person transfers the bundle to the offline signing machine and checks its hash against the CI log.
3. There: `rauc resign --cert=release-signing.pem --key=<token URI or file> --signing-keyring=release-ca.pem in.raucb out.raucb`.
   This replaces the signature; the content stays identical. Verified in `tests/os`: after re-signing, the bundle is accepted by the
   release keyring and refused by the development keyring.
4. Likewise the flash manifest is signed with `openssl dgst -sha256 -sign` (ECDSA P-256); the flash tool verifies it (docs/FLASH_TOOL.md).
5. The **owner** publishes the re-signed bundle, the image, the manifest and its signature as a **GitHub Release** of `iskaron/hifi-bridge`
   (manually; no workflow creates releases, and no release key is ever stored as a GitHub secret).

## 4a. Official update source (DECISIONS #70)
Official bundles are distributed through **GitHub Releases of `iskaron/hifi-bridge`**. Images that accept official bundles carry this
source fixed in `update-trust.json`:
```json
"official_source": {"type": "github-releases", "repository": "iskaron/hifi-bridge",
  "releases": "https://github.com/iskaron/hifi-bridge/releases", "api": "https://api.github.com/repos/iskaron/hifi-bridge/releases"}
```
* It is **transport only.** A downloaded bundle is installed only if RAUC verifies its signature against the keyring; a compromised
  GitHub account alone cannot install anything.
* It is **not configurable** in the image configuration, so an official image cannot be pointed at another source. Own-only images record
  `null`; self-builders distribute their own bundles by upload.
* The update check (`ota/source.py`) only **reports** a newer release; download and installation each need an owner action
  (PRODUCT_SPEC). It sends no credentials, ignores drafts and pre-releases, looks for the asset `hifi-bridge-<compatible>-<version>.raucb`,
  refuses non-HTTPS URLs and redirects, and links release notes only inside the official releases URL.
* While the repository is private, devices cannot reach its releases without credentials, and devices never get a token
  (OPEN_QUESTIONS #45).

## 5. Rotation and revocation
* **Signing certificate:** short-lived; a new one is issued by the release CA. There is nothing to change on devices.
* **Release CA:** add the new CA certificate to `release-ca.pem` (both CAs). Ship a release signed with the **old** chain that carries the
  new keyring. After that, release with the new chain. Remove the old CA in a later release.
* **Compromise:** the keyring travels with the root file system, so an update signed with a still-trusted key can replace it. CRL checking is
  off (`check-crl=false`); revocation means shipping a new keyring. This limitation is accepted for v1 and needs an owner decision
  if stronger revocation is wanted.

## 6. Tested vs. not tested
| Statement | Evidence |
|---|---|
| The keyring holds exactly the chosen CAs; no private key; the choice is recorded | UNIT (`post-build.sh` with a fake target tree) |
| The official source is recorded for official/both and `null` for own | UNIT (same test) |
| The update check against GitHub Releases | UNIT against a local server imitating the API (incl. 404 = private, rate limit, offline); the real GitHub only after OPEN_QUESTIONS #45 (G4 U-2) |
| A bundle is staged only if RAUC's `InspectBundle` verifies it AND its compatible matches (RAUC does not compare it there) | **real RAUC 1.13** D-Bus service (`tests/ota`) |
| A signer without codeSigning, an unknown CA, a truncated or random file are refused by `InspectBundle` | **real RAUC 1.13** D-Bus service |
| A payload byte changed after signing passes `InspectBundle` (only manifest + verity root are signed); dm-verity must reject it at install | **real RAUC 1.13** (finding); the rejection needs a device |
| Only root and `hifi-bridge` may call RAUC (image D-Bus policy) | real dbus-daemon + real RAUC service, with a control run without the policy |
| official ⇒ accepts release, refuses own; own ⇒ the reverse; both ⇒ both | **real RAUC 1.13** `rauc info` with the generated keyrings |
| Re-signing gives a release bundle without the release key ever being in the build | real RAUC `resign` |
| A certificate without codeSigning purpose is refused | real RAUC with `check-purpose=codesign` |
| `mark-good`/`mark-active`/`mark-bad` write the U-Boot variables `boot.cmd` uses | **real RAUC 1.13** + `fw_setenv` on a file-backed environment |
| Install into a slot, slot switch, boot-count rollback, power loss during install | **NOT TESTED** – hardware gate (docs/OTA.md section 10); in Docker RAUC stops at the bundle mount (no loop device) |
