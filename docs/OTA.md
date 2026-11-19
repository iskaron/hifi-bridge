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
* `installed` means RAUC has written the other slot and made it primary. If the owner does not restart, the next power cycle still boots
  the new slot (RAUC semantics); the page says so. "Keep the current version" marks the booted slot active again.

## 4. Health check, mark-good and rollback (U-Boot boot-count, provisional DECISIONS #53)
RAUC's U-Boot backend works with `BOOT_ORDER` and `BOOT_<slot>_LEFT`; `boot.cmd` decrements the counter on **every** boot. Verified with
real RAUC writing a file-backed environment: `mark-good` sets `BOOT_A_LEFT=3`, `mark-active other` puts B first with `BOOT_B_LEFT=3`,
`mark-bad` sets the counter to 0 and removes the slot from `BOOT_ORDER`.

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
| not enough space on the data partition (upload or download) | refused before a byte is stored, "Not enough free space" | UNIT |
| upload too large / chunked / bad Content-Length / no session / wrong CSRF / cross-origin | refused before the body is read, connection closed | UNIT (real HTTP) |
| upload interrupted / too slow (overall deadline 60 s + 64 KiB/s) | partial file deleted, "incomplete" / "took too long" | UNIT (real HTTP) |
| download interrupted, server ignores Range, wrong size, wrong digest | resumable partial kept / restart from 0 / refused | UNIT (local HTTP server) |
| redirect away from HTTPS during check or download | refused | UNIT |
| installation fails (RAUC `Completed(1)`) | bundle kept for a retry, RAUC's `LastError` shown, slot order unchanged | REAL RAUC (fails at the mount step in Docker) + UNIT |
| RAUC daemon restarts during the installation (no `Completed`) | "interrupted"; if RAUC had activated the new slot it counts as installed | UNIT (fault injection) |
| RAUC unreachable > 120 s during the installation | "interrupted" | UNIT |
| bridge restarts while RAUC still installs | monitoring resumes | UNIT |
| bridge was down when RAUC finished | reconciled from RAUC's primary slot | UNIT |
| power cut during the installation | previous slot boots (RAUC deactivates the target before writing); "interrupted", bundle kept | UNIT (reconcile) - **device: G2 P3-5** |
| power cut after the installation, before the restart | new slot boots, is confirmed by the health check | UNIT |
| new slot fails the health check / does not boot | rollback (section 4) | UNIT - **device: G2 P3-4** |
| update check: repository private (404), rate limit, offline, bad answer | "not publicly reachable" (OPEN_QUESTIONS #45), "too many requests", "not reachable", "unexpected answer" | UNIT |
| restart helper missing (development system) | "cannot restart itself here", state unchanged | UNIT, BROWSER |

## 6. Pre-update snapshot and downgrades
Before each installation the manager copies `settings.json`, `secrets.json`, `network.json` and `certs/` to
`<data>/ota/snapshots/<slot>/` with `meta.json` (`version`, `slot`, `schema_version`, `created`); one snapshot per slot, replaced
atomically. After a rollback the older version keeps using the **current** data: ConfigStore keeps unknown keys verbatim and never
lowers `schema_version` (DECISIONS #75-#77), so returning to the newer version later loses nothing. Restoring the snapshot is therefore
**only an owner action** ("Restore snapshot and restart" on the backup page): the request names the snapshot; at the next start
`prepare_data()` applies it before the configuration is loaded - only if it was taken by exactly the running version on the booted slot.
Rule for future releases: an incompatible data change must raise the settings `schema_version` and migrate.

## 7. Configuration backup
`/settings/backup` exports a JSON file (`format: hifi-bridge-backup`, `format_version: 1`, `app_version`, `settings_schema`, `settings`,
`secrets`). Imports from older format versions are migrated (registered upgrade functions); newer ones are refused ("update first").
* **Secrets are excluded by default** (`secrets: null`); the page lists what must be set again.
* **Optionally included, encrypted** with an owner passphrase (≥ 10 characters): only the owner password hash, the stream token and the
  MQTT password. Never: the device CA key (DECISIONS #57), session keys, setup codes, trust keys, an own TLS key.
* Encryption with the standard library only (DECISIONS #26): scrypt (n=2^14, r=8, p=1) → 32-byte encryption key + 32-byte MAC key;
  keystream HMAC-SHA256(key, nonce‖counter); tag HMAC-SHA256 over the canonical JSON of the whole file (encrypt-then-MAC). The tag is
  checked before anything is decrypted; a wrong passphrase and a modified file are indistinguishable on purpose. KDF parameters in the file
  are bounded (no CPU/memory bomb). **This construction needs a review** (proposed decision, section 11).
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
| `tests/ota/test_rauc_real.py` | **real RAUC 1.13 service**, real dbus-daemon with upstream + our policy, real `fw_setenv` on a file-backed U-Boot environment, bundles from `os_bundle.sh` with throw-away keys | `scripts/run_ota.sh` (image `btbridge-ota`) |
| `tests/os/test_ota_image.py` | `post-build.sh` on a fake target tree; unit/tmpfiles/policy wiring | `scripts/run_os.sh` |
| `tests/browser/test_ui_update.py` | real Chromium + axe-core against the lab appliance with the simulated RAUC (`--sim-rauc`) | `scripts/run_browser.sh` |
| units | `systemd-analyze verify` + `security` (health check 1.5 OK) | `scripts/run_static.sh` |

**Limit:** without loop devices and device-mapper (not allowed on the development host), RAUC cannot mount a bundle; the real installation
stops at the mount step, which the tests assert as a clean failure.

## 10. Still needed on hardware (add to docs/HARDWARE_GATE_CHECKLIST.md G2/G4)
* P3-3/P3-4 with **this** flow: upload in the WebUI → install → restart → health check marks good; a deliberately broken bridge in slot B
  → `mark-bad` + reboot → A; a kernel that does not boot → 3 attempts → A. Time from "Restart now" to the page being back.
* P3-5/P3-6 power cuts during install, during the first boot of B, during the health check.
* dm-verity refusing a bundle whose payload was changed after signing.
* `FailureAction=reboot`, the reboot path unit and the tmpfiles directory under real systemd; the D-Bus policy with the image's dbus 1.14.
* Space: bundle size vs. data partition (PHASE2B_PLAN section 3); a full data partition during an upload.
* U-1/U-2 (G4): the update check finds a test release on the public GitHub Releases (needs OPEN_QUESTIONS #45 resolved).
