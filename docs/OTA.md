# Software updates, rollback and backup (phase 2b step 6, DECISIONS #10, #53, #55, #61, #70)

Status (2026-11-19): implemented and tested **without hardware**. The bridge's side runs against the **real RAUC 1.13 D-Bus service**
in a container (U-Boot backend on a file-backed environment, file-backed slots). **No bundle has been installed into a slot, nothing has
been booted, and the boot-count rollback has not run on a board.** Those are hardware-gate items (section 10).

## 1. Building blocks
| Piece | Where | Privilege |
|---|---|---|
| RAUC D-Bus client (jeepney) | `bridge/btbridge/ota/rauc.py` | bridge user `hifi-bridge` |
| Update state machine, snapshot, boot reconciliation | `bridge/btbridge/ota/manager.py` | bridge user |
| Update check (GitHub Releases), download, multipart upload | `ota/source.py`, `ota/transfer.py` | bridge user |
| Post-boot health check | `ota/health.py`, unit `hifi-bridge-health.service` | bridge user |
| Configuration backup/restore | `ota/backup.py` | bridge user |
| WebUI pages `/settings/update`, `/settings/backup` | `webui/views_update.py`, `static/update.{js,css}`, `i18n/update.*.json` | bridge user |
| Installing, marking slots, writing the U-Boot environment | RAUC (`rauc.service`) | root |
| Restart after the owner's confirmation | `hifi-bridge-reboot.path` + `.service` | root, takes no input |
| Bundle build (`make ota`) | `scripts/os_bundle.sh` | build host, in Docker |

## 2. Privilege boundary
* The bridge never runs anything as root. It talks to RAUC over the system D-Bus (`de.pengutronix.rauc.Installer` on `/`).
* **D-Bus policy** `overlay/etc/dbus-1/system.d/de.pengutronix.rauc-hifi-bridge.conf`: upstream RAUC's policy lets *any* local user call
  RAUC. Ours (read after it) denies RAUC to everybody by default and allows root, and the `hifi-bridge` user for the Installer interface
  plus reading (not setting) properties. Verified with a real `dbus-daemon`: uid 990 can call `GetAll`/`Mark`, uid 1234 is refused
  (`AccessDenied`), `Properties.Set` is refused for uid 990; a control run without our file shows uid 1234 getting in.
* **Restart:** the bridge creates an empty file `/run/hifi-bridge-ota/reboot-request` (tmpfiles: directory owned by `hifi-bridge`, 0750;
  the only path outside its state directory it may write, `ReadWritePaths=-/run/hifi-bridge-ota`). `hifi-bridge-reboot.path` starts
  `hifi-bridge-reboot.service` (root), which deletes the file and runs `systemctl --no-block reboot`. Nothing reads the file's content,
  so there is no input to validate (cf. the network agent's SO_PEERCRED requests, which do carry data).
* The health check runs as `hifi-bridge` too (no capabilities, `IPAddressAllow=localhost` only, exposure 1.5); a failing first boot is
  turned into a reboot by systemd's `FailureAction=reboot`, not by a privileged helper.

## 3. State machine
Persistent phases are stored atomically in `<data>/ota/state.json` under an advisory lock (the health check is a second process).
`downloading`, `uploading` and `verifying` exist only in memory; a partial download can be resumed after a restart.

```
idle ──download/upload──> verifying ──RAUC InspectBundle ok + compatible matches──> staged ──owner: install──> installing
installing ──Completed(0)──> installed ──owner: restart──> rebooting ──new slot boots──> confirming ──health ok──> idle "updated"
installing ──Completed(≠0) / RAUC gone──> staged + error              installed ──owner: keep current──> idle "cancelled"
rebooting/confirming ──previous slot boots (boot-count or failed health)──> idle "rolled_back"
rebooting ──no restart within 5 min (same boot)──> installed/switching + "did not restart"
idle ──owner: previous version──> switching ──restart──> confirming ──health ok──> idle "switched"
```
* Nothing is downloaded or installed without an owner action (DECISIONS #10). The check only reports.
* **Verification:** a bundle is staged only after RAUC's `InspectBundle` accepted its signature against the image keyring
  (`check-purpose=codesign`) **and** the manager compared the bundle's `compatible` with the device's. RAUC's `InspectBundle` does **not**
  compare the compatible (verified with real RAUC) - RAUC checks it again during the installation.
* **Payload integrity:** `InspectBundle` verifies the signed manifest and the verity root hash, **not** the payload (a changed payload byte
  passes inspection - verified). dm-verity rejects changed blocks while RAUC installs; that needs a device (gate item).
* Before every installation the bridge's own data is snapshotted (section 6); after a successful install the bundle file is deleted
  (before the state says "installed", so a power cut never leaves an orphaned bundle).
* **Staging cleanup (DECISIONS #121/#128):** the boot step removes every staging file no persistent state needs (an interrupted upload
  or download, an unreferenced bundle, temporary files); the bridge start removes an interrupted upload. A `staged`/`installing` bundle
  is kept for the owner's install or retry. Full table: docs/PERSISTENCE.md section 5.
* `installed` means RAUC has written the other slot and made it primary. If the owner does not restart, the next power cycle still boots
  the new slot (RAUC semantics); the page says so. "Keep the current version" marks the booted slot active again.

## 4. Health check, mark-good and rollback (U-Boot boot-count, provisional DECISIONS #53)
RAUC's U-Boot backend works with `BOOT_ORDER` and `BOOT_<slot>_LEFT`; `boot.cmd` decrements the counter on **every** boot. Verified with
real RAUC writing a file-backed environment: `mark-good` sets `BOOT_A_LEFT=3`, `mark-active other` puts B first with `BOOT_B_LEFT=3`,
`mark-bad` sets the counter to 0 and removes the slot from `BOOT_ORDER`.

**Two copies of the environment (review N-6, package 7b-2).** U-Boot rewrites the environment on every boot (counter) and RAUC on every
mark-good/activation. It now exists twice on the boot FAT (`uboot.env`, `uboot-redund.env`; `CONFIG_ENV_REDUNDANT`, `uboot.fragment`;
`/etc/fw_env.config` lists both). Every write goes to the **older** copy with the next serial byte, readers take the newest copy with a
valid CRC, so a power cut during a write leaves the previous state. Both copies ship with the card (U-Boot's default environment from
`u-boot-initial-env` + `BOOT_ORDER=A B`, `BOOT_A_LEFT=3`, `BOOT_B_LEFT=0`, `hifi_env=1`, serials 1/0): fw_setenv refuses to work while one
file is missing. Verified in Docker with the real RAUC 1.13 + libubootenv `fw_setenv` (`tests/ota`: writes alternate; tearing the newest
copy gives exactly the previous state; RAUC activates a slot with **two** writes - `BOOT_B_LEFT=3` first, then `BOOT_ORDER` - and the
state between them still boots A) and with the build's own uboot-tools `fw_printenv`/`fw_setenv` on the image's copies (image check).

`boot.cmd` fails safe when U-Boot has to use its **built-in** default environment (both copies invalid; recognised by the missing
`hifi_env`): it does not behave like a fresh card (which would never try B), but gives A and then B three attempts each, saves that, and
passes `hifi.bootenv=reset` on the kernel command line of that boot. `panic=10` makes a slot whose kernel loads but cannot run restart, so
the attempt counter can fall back. Residual risks (HARDWARE-PENDING, G2 P3-5/P3-6):
* both copies invalid at once (FAT damage, not a single torn write): the previous slot choice is lost; A is tried first, so the device may
  run the older version after an update (the update page still shows both slots and offers the switch). The marker is visible only in
  `/proc/cmdline` and the U-Boot console today (OPEN_QUESTIONS proposal: show it on the update page);
* Linux-side: if fw_setenv ever saw both copies invalid it would write uboot-tools' **generic** default environment (`bootcmd=bootp ...`
  NFS boot) - the device would no longer boot from the card. U-Boot has written a valid copy at the start of the same boot, so this needs
  independent damage of both copies while Linux runs (proposal: libubootenv with `/etc/u-boot-initial-env` as its default);
* a torn write of the FAT directory sector itself (not the file data) is not covered by the redundancy;
* the saved environment keeps board values U-Boot only sets when missing (`serial#`, `usbethaddr`/`ethaddr`, `fdtfile`) from the first Pi
  the card booted in (unchanged behaviour, noted for card moves between boards).

`hifi-bridge-health.service` (after `hifi-bridge.service`) waits up to 300 s until all checks pass: `/data` is a mount point, the
state directory is writable (write + fsync + delete), `/healthz` on 127.0.0.1:80 says `ok` or `degraded` (this also proves the WebUI
answers; `degraded` such as "Lyrion unreachable" is healthy on purpose) and RAUC answers. Then:

| Situation | Action |
|---|---|
| checks pass | `mark-good booted` (resets the counter), record this slot's version in `ota/slots.json`, finish "updated"/"switched" |
| checks fail on the **first boot of a new slot** (`confirming`) | `mark-bad booted`, record the reason, exit 1 → `FailureAction=reboot` → U-Boot boots the previous slot → "rolled_back: the check after the start failed" |
| the new slot does not reach the health check (kernel panic, hang) | it is never marked good; after 3 attempts U-Boot falls back → "rolled_back: it did not finish starting" |
| checks fail on an **established** slot | recorded in `ota/health.json`, no reboot (an external problem must not cause a reboot loop). The slot is not marked good, so after three such boots in a row the boot-count falls back to the other slot |
| the checker itself crashes | exit 0, slot not confirmed (a checker bug never reboots; the boot-count still applies) |

Upstream RAUC's `rauc-mark-good.service` would mark every boot good and defeat this; `post-build.sh` masks it.

**Manual rollback:** the update page offers "Start version X next time" when the other slot is bootable (`boot-status good`) and its
version is known (RAUC's `bundle.version`, or recorded by an earlier healthy boot in `ota/slots.json`). It runs `mark-active other`;
the owner then confirms the restart.

## 5. Failure matrix (what the device does, what the page shows)
| Fault | Result | Evidence |
|---|---|---|
| bundle not signed by the keyring / signer without codeSigning / damaged or truncated file / not a bundle | refused at verification, file deleted, "not signed by a key this device trusts, or damaged" + RAUC's message | REAL RAUC + UNIT |
| bundle for another board (`compatible`) | refused at verification by the manager | REAL RAUC + UNIT |
| the pre-update snapshot cannot be written | nothing is installed, "The current settings could not be saved first" | UNIT |
| not enough space on the data partition (upload or download) | refused before a byte is stored, "Not enough free space" | UNIT |
| upload too large / chunked / bad Content-Length / no session / wrong CSRF / cross-origin | refused before the body is read, connection closed | UNIT (real HTTP) |
| upload interrupted / too slow (overall deadline 60 s + 64 KiB/s) | partial file deleted, "incomplete" / "took too long" | UNIT (real HTTP) |
| download interrupted, server ignores Range, wrong size, wrong digest | resumable partial kept / restart from 0 / refused | UNIT (local HTTP server) |
| redirect away from HTTPS during check or download | refused | UNIT |
| installation fails (RAUC `Completed(1)`) | bundle kept for a retry, RAUC's `LastError` shown, slot order unchanged | REAL RAUC (fails at the mount step in Docker) + UNIT |
| RAUC daemon restarts during the installation (no `Completed`) | "interrupted"; if RAUC had activated the new slot it counts as installed | UNIT (fault injection) |
| RAUC unreachable > 120 s during the installation | "interrupted" | UNIT |
| RAUC answers with an error reply (not "service gone"), or something unexpected fails in the monitor, for > 120 s | "interrupted", every action possible again (review N-5; a short burst is tolerated) | UNIT |
| RAUC reports a malformed `Progress` value | shown as 0 %, the installation continues | UNIT |
| storage error or malformed RAUC answer while verifying / finishing an upload | bundle deleted, "could not be checked (storage error)" / "upload failed", back to idle (review N-10) | UNIT |
| bridge restarts while RAUC still installs | monitoring resumes | UNIT |
| bridge was down when RAUC finished | reconciled from RAUC's primary slot | UNIT |
| power cut during the installation | previous slot boots (RAUC deactivates the target before writing); "interrupted", bundle kept | UNIT (reconcile) - **device: G2 P3-5** |
| power cut after the installation, before the restart | new slot boots, is confirmed by the health check | UNIT |
| new slot fails the health check / does not boot | rollback (section 4) | UNIT - **device: G2 P3-4** |
| update check: repository private (404), rate limit, offline, bad answer | "not publicly reachable" (OPEN_QUESTIONS #45), "too many requests", "not reachable", "unexpected answer" | UNIT |
| restart helper missing (development system) | "cannot restart itself here", state unchanged | UNIT, BROWSER |
| restart confirmed, but nothing restarts (the root helper did not act) | after 5 min on the same boot (`REBOOT_WAIT_S`) back to "installed"/"switching" with "The device did not restart" and what to do; "Restart now" is offered again (review N-10). `update.js` reloads after 180 s, so the page shows it at the second reload at the latest | UNIT |

## 6. Pre-update snapshot and downgrades
Before each installation the manager copies `settings.json`, `secrets.json`, `network.json` and `certs/` to
`<data>/ota/snapshots/<slot>/` with `meta.json` (`version`, `slot`, `schema_version`, `created`); one snapshot per slot, replaced
atomically. After a rollback the older version keeps using the **current** data: ConfigStore keeps unknown keys verbatim and never
lowers `schema_version` (DECISIONS #75-#77), so returning to the newer version later loses nothing. Restoring the snapshot is therefore
**only an owner action** ("Restore snapshot and restart" on the backup page): the request names the snapshot; at the next start
`prepare_data()` applies it before the configuration is loaded - only if it was taken by exactly the running version on the booted slot.
It runs in the **boot step** (`btbridge.bootfiles`, `hifi-bridge-boot.service`), because the network agent `hifi-netd` is ordered after
that unit and reads `network.json` and the host name only at its own start (review N-4: run from the bridge, which starts after the
agent, the restored network settings were shown but not applied until the next boot). The boot unit therefore has no `ProcSubset=pid`:
it reads the booted slot (`rauc.slot=`) from `/proc/cmdline`; without a known slot the request waits (`deferred`). The bridge still calls
`prepare_data()` before loading its configuration (a no-op when the boot step did it; a fallback for setups without the boot unit, where
the agent would follow only at the next boot). The restore is all or nothing (every file is copied first, then swapped in); any error is
logged, recorded as `restore_failed` (shown on the update and backup pages, DE/EN) and the request is dropped, so a failing card can
never cause a restart loop; the device starts with its current settings and the owner may try again.
Rule for future releases: an incompatible data change must raise the settings `schema_version` and migrate.
The snapshots contain copies of `secrets.json` (owner password hash, stream token, MQTT password): they are protected like the originals
(data partition, 0700/0600) and **removed by a factory reset** (`recovery.factory_reset` → `ota.manager.forget_owner_data`), together with
a pending restore request, the update state and any bundle. If the snapshot cannot be written (e.g. full disk), nothing is installed.

## 7. Configuration backup
`/settings/backup` exports a JSON file (`format: hifi-bridge-backup`, `format_version: 2`, `app_version`, `settings_schema`, `settings`,
`secrets`). Imports from older supported format versions are migrated (registered upgrade functions); newer ones are refused ("update
first"). Version 1 (an own keystream construction) existed only on the unmerged development branch, never on a device, and is refused.
* **Secrets are excluded by default** (`secrets: null`); the page lists what must be set again.
* **Optionally included, encrypted** with an owner passphrase (≥ 10 characters): only the owner password hash, the stream token and the
  MQTT password. Never: the device CA key (DECISIONS #57), session keys, setup codes, trust keys, an own TLS key.
* Cryptography (DECISIONS #50: OpenSSL, no own cryptography):
  * `hashlib.scrypt` (n=2^14, r=8, p=1, random 16-byte salt per file) → 64 bytes: 32 bytes of encryption key material and an independent
    32-byte MAC key.
  * **AES-256-CTR via the openssl CLI** (`openssl enc -aes-256-ctr -pbkdf2 -iter 1 -md sha256 -nosalt -pass fd:N`). The key material
    reaches openssl **only through an inherited pipe** - never on the command line, in the environment or in a file (`-K`/`-iv` were
    rejected because they put the key into argv). openssl derives key + IV from it with one PBKDF2 round: the input is already 256 bits
    of scrypt output, unique per file, so neither salt nor stretching is needed there and (key, IV) never repeat.
  * Encrypt-then-MAC: tag = HMAC-SHA256(MAC key, canonical header ‖ ciphertext); the header is the whole file except ciphertext and tag
    (format version, settings, KDF parameters, cipher names). The tag is compared in constant time **before** openssl decrypts anything;
    a wrong passphrase and a modified file are indistinguishable on purpose. KDF parameters in the file are bounded (no CPU/memory bomb).
  * `openssl cms -pwri_password` was not needed: `enc` with `-pass fd:N` keeps all key material off the command line.
  * Tests against the real openssl 3.0 of the lab image: round trip, an independent decryption with the openssl command itself, wrong
    passphrase, tampering with header/salt/KDF parameters/ciphertext/tag/truncation (openssl is never called), a correctly tagged foreign
    payload, a process check (a wrapper records `/proc/self/cmdline` and `environ` of the openssl process: neither passphrase nor key
    material appears), and openssl missing/failing/hanging.
* Restore validates everything first (format, every setting) and changes nothing if anything is wrong; settings unknown to this version are
  skipped and listed; restored secrets are applied at once (new stream token, MQTT reconnect); a restored owner password ends all sessions.

## 8. Reproducible bundles (`make ota`)
`make ota ROOTFS=output/images/rootfs.ext4 COMPATIBLE=hifi-bridge-rpi3 [VERSION=x] [CERT=… KEY=…]` → `scripts/os_bundle.sh` (in the
`btbridge-os` image) → `output/ota/hifi-bridge-<compatible>-<version>.raucb` (the asset name the update check looks for).
* Without CERT/KEY it creates **development** keys once in `.keys/dev-update/` (git-ignored, 0700/0600, CN "… DEVELOPMENT …"); use
  `.keys/dev-update/ca.pem` as the own update CA of a self-built image. A certificate without codeSigning is refused; a key tracked by git
  is refused. **Release keys never enter this script**: official bundles are re-signed offline (docs/UPDATE_TRUST.md section 4).
* `SOURCE_DATE_EPOCH` (default: last commit time) pins file times: two runs give the same image checksum and a **byte-identical squashfs**
  (verified). Not reproducible by RAUC's design: the random dm-verity salt (so hash tree and manifest hash differ) and the CMS signing time.

## 9. Tests
| Suite | What is real | Command |
|---|---|---|
| `tests/unit/test_ota_*.py`, `test_webui_update.py`, `test_appliance_updates.py` | real HTTP, real ConfigStore, real files; RAUC simulated (in-process fake or `btkit.fake_rauc` on a private dbus-daemon); fault injection for every failure path | `scripts/run_unit.sh` |
| `tests/ota/test_rauc_real.py` | **real RAUC 1.13 service**, real dbus-daemon with upstream + our policy, real `fw_setenv` on the product's two-copy file-backed U-Boot environment (torn newest copy -> previous state), bundles from `os_bundle.sh` with throw-away keys | `scripts/run_ota.sh` (image `btbridge-ota`) |
| `tests/os/test_ota_image.py` | `post-build.sh` on a fake target tree; unit/tmpfiles/policy wiring | `scripts/run_os.sh` |
| `tests/browser/test_ui_update.py` | real Chromium + axe-core against the lab appliance with the simulated RAUC (`--sim-rauc`) | `scripts/run_browser.sh` |
| units | `systemd-analyze verify` + `security` (health check 1.5 OK) | `scripts/run_static.sh` |

**Limit:** without loop devices and device-mapper (not allowed on the development host), RAUC cannot mount a bundle; the real installation
stops at the mount step, which the tests assert as a clean failure.

## 10. Still needed on hardware (add to docs/HARDWARE_GATE_CHECKLIST.md G2/G4)
* P3-3/P3-4 with **this** flow: upload in the WebUI → install → restart → health check marks good; a deliberately broken bridge in slot B
  → `mark-bad` + reboot → A; a kernel that does not boot → 3 attempts → A. Time from "Restart now" to the page being back.
* P3-5/P3-6 power cuts during install, during the first boot of B, during the health check - including during U-Boot's `saveenv` and
  RAUC's two-step activation (the other environment copy must be used; console shows no "bad CRC, using default environment").
* dm-verity refusing a bundle whose payload was changed after signing.
* `FailureAction=reboot`, the reboot path unit and the tmpfiles directory under real systemd; the D-Bus policy with the image's dbus 1.14.
* Space: bundle size vs. data partition (docs/PERSISTENCE.md 2.1: 256 MiB before growth holds one bundle of a full slot); a full data
  partition during an upload.
* U-1/U-2 (G4): the update check finds a test release on the public GitHub Releases (needs OPEN_QUESTIONS #45 resolved).
