# Pinned container images

Every third-party image used by the lab, the demo, CI and the scripts is referenced as `name:tag@sha256:<digest>` - an exact version **and** the
content digest (the multi-architecture manifest list, so amd64 and arm64 hosts get the tested bits). A tag such as `stable` or `latest` can
change under us and silently change test results (OPEN_QUESTIONS #48; DECISIONS #73).

The single source of truth is [`docker/image-pins.txt`](../docker/image-pins.txt). `tests/entry/test_image_pins.py` (part of
`scripts/run_entry_tests.sh` and CI) fails when

* any compose `image:`, Dockerfile `FROM` or script reference lacks the digest, or carries a digest different from the lock file,
* a pin uses a moving tag (`latest`, `stable`, `rc`, `dev`, ...),
* a third-party image is referenced that is not in the lock file.

Images built from this repository (`btbridge-*:dev`, `hifi-demo:dev`) are not third-party and are not pinned; they are built from the pinned bases.

| Image | Tag | Digest | Used for |
|---|---|---|---|
| lmscommunity/lyrionmusicserver | 9.1.2 (was `stable`, 2026-10-24) | `sha256:a8709abd...3a1e21` | real Lyrion: lab, demo |
| eclipse-mosquitto | 2.0.20 | `sha256:21421af7...62d0eb` | MQTT broker |
| ghcr.io/home-assistant/home-assistant | 2026.11.1 | `sha256:6785d1ec...e59e82` | real Home Assistant (profile `ha`) |
| mcr.microsoft.com/playwright/python | v1.49.1-noble | `sha256:42e251ec...4f6866` | browser tests |
| debian | bookworm-slim / trixie-slim | `sha256:734a72b3...b179bc` / `sha256:a72b4433...63b6b2` | lab, Avahi, PipeWire, demo, static, syslog, OS, entry-test images |
| python | 3.11-slim / 3.14-slim | `sha256:07d48240...b535` / `sha256:9aaa4740...802f` | bandit, compile check / Python 3.14 run |
| alpine | 3.20 | `sha256:d9e853e8...b6bc` | chown helper, Docker matrix sandboxes |
| koalaman/shellcheck-alpine | v0.10.0 | `sha256:5921d946...db63` | scripts/run_static.sh |
| rhysd/actionlint | 1.7.7 | `sha256:887a259a...47e9` | scripts/run_static.sh |
| ghcr.io/gitleaks/gitleaks | v8.24.3 | `sha256:e1b35e12...07055` | scripts/run_secrets.sh |
| docker (dind) | 24.0.9 ... 29.1.5 | see the lock file | Docker-version matrix and clean-environment test |

(Full digests: the lock file.) The Debian bases are pinned too, so `apt-get install` inside the images still sees the package versions of the
day the image is built - the base only fixes the starting point; the apt state of a rebuild can differ.

## Updating a pin
1. `scripts/image_pins.sh resolve` asks the registries (needs network and `docker buildx`) which digest every tag points to today and lists
   differences. It changes nothing.
2. Edit `docker/image-pins.txt` (new tag and/or digest; take the digest of the manifest list, not of one platform).
3. `scripts/image_pins.sh apply` rewrites every reference in the compose files, Dockerfiles and scripts; `scripts/image_pins.sh check` verifies.
4. Run the tests that use the image: `scripts/run_entry_tests.sh` always; a changed Lyrion needs `scripts/run_integration.sh` and
   `scripts/test_demo.sh`, Home Assistant `scripts/run_homeassistant.sh`, Playwright `scripts/run_browser.sh`, Debian bases a full
   `scripts/build_images.sh` and `scripts/run_all.sh`. Record the change in DECISIONS/THIRD_PARTY.
5. Update the table above and `docs/THIRD_PARTY.md` if the tag changed.
