# Independent review of the phase-2b code (packages 3-6), 2026-11-19

Scope: network (`bridge/btbridge/net/*`, `webui/views_network.py`, hifi-netd/hifi-net-* units, nftables/hostapd/dnsmasq/avahi config),
OTA (`bridge/btbridge/ota/*`, `webui/views_update.py`, RAUC/U-Boot config, hifi-bridge-health), flash tool (`flashtool/hififlash/**`),
OS (`os/br2-external/**`, `os/tools/*`, `scripts/os_*.sh`). Base: develop @ 71cd40d (working tree incl. uncommitted measurement JSONs).
Findings of reports/REVIEW_PHASE2B_EXISTING_CODE.md are not repeated.

Method: code reading against DECISIONS #73-#104 / OPEN_QUESTIONS #50-#77 / docs; small reproductions in disposable `--network none`
containers of the existing `btbridge-lab:dev` image (no compose project started, no host hardware touched); the built development rootfs
(`.lab/br-out/rpi3_64/images/rootfs.ext4`) and the U-Boot `.config` were inspected read-only (debugfs inside `btbridge-os:dev`).
Repro scripts (not versioned): `/tmp/claude-1000/-home-olm-bluetooth-audio-bridge/dc390eb2-01ac-4eef-8770-82ab98b54819/scratchpad/repro/`
(`$R` below). Run pattern:

    docker run --rm --network none -v "$PWD/bridge":/src/bridge:ro -v "$R":/repro:ro btbridge-lab:dev python3 /repro/<script>.py
    # pytest variant (N-1): -v "$PWD":/work:ro -w /work ... python3 -m pytest -q -s -p no:cacheprovider -c /work/pytest.ini --rootdir /work /repro/test_review_n_setup.py

CONFIRMED = reproduced or unambiguous from code/config; PLAUSIBLE = mechanism shown, real-world trigger/timing not provable without hardware.

## Summary

| ID | Sev | Status | Area | One line |
|---|---|---|---|---|
| N-1 | High | CONFIRMED | Network/WebUI | Network pages are unreachable until the setup wizard is finished, and the wizard needs a reachable Lyrion: hotspot-only onboarding (and fixing a mistyped provisioned Wi-Fi password) is a dead end |
| N-2 | Medium | CONFIRMED | Network (bridge) | A status answer fetched before `save()` is applied after it and deletes the new trial's pending file: the owner cannot confirm, the change rolls back after 3 min |
| N-3 | Medium | CONFIRMED (mechanism) / PLAUSIBLE (timing) | Network (agent+bridge) | Agent lock held across slow tool calls (scan 20 s, systemctl 30 s) + 15 s client timeout; on timeout the bridge deletes the pending file although the agent applied it -> agent and `network.json` diverge |
| N-4 | Medium | CONFIRMED | OTA/Network | Owner-requested snapshot restore runs after hifi-netd has loaded `network.json`/host name: the restored network config is shown but not applied until the next boot; `prepare_data` errors crash-loop the service |
| N-5 | Medium | CONFIRMED | OTA | Install monitor thread dies on a RAUC error reply (or any unexpected exception): UI stuck in "installing", every action "busy" until the bridge restarts |
| N-6 | Medium | CONFIRMED (config) / PLAUSIBLE (impact, gate P3-5) | OS/U-Boot | Non-redundant U-Boot environment in a FAT file, rewritten on every boot (`saveenv`) and by RAUC: a power cut can tear it -> default env -> wrong slot / lost boot counters |
| N-7 | Low | CONFIRMED | Network (agent) | One wpa_supplicant `CtrlClient` used concurrently by the tick thread and the status thread: swapped replies, spurious "Wi-Fi connection lost", sockets closed under the other thread |
| N-8 | Low | CONFIRMED | Firewall/OS | `udp sport 3483 udp dport 1024-65535 accept` lets any LAN host reach every high UDP port by spoofing the source port; systemd-resolved (LLMNR on, not in DECISIONS #89) is enabled in the image |
| N-9 | Low | CONFIRMED | OTA/backup | A backup whose scrypt parameters pass the bounds check but exceed `maxmem` raises ValueError -> HTTP 500 instead of "invalid file" |
| N-10 | Low | CONFIRMED (code) / PLAUSIBLE (trigger) | OTA | Other wedge states: non-RAUC exceptions in `_verify`/`finish_upload` leave `_busy` set; "rebooting" has no way out if the restart helper never acts (update.js reload loop) |
| N-11 | Low | CONFIRMED | i18n/guidance | Missing DE/EN strings for reachable error codes (raw codes shown); several errors do not tell the owner what to do |
| N-12 | Low | CONFIRMED | Privacy | Diagnostics bundle contains the agent's Wi-Fi scan list (neighbours' SSIDs, signal, channel) |
| N-13 | Low | CONFIRMED | OS | `hifi-bridge-growdata.service` guard `ConditionPathExists=!/data/.grown` is evaluated before `/data` is mounted -> never effective; growdata runs every boot |
| N-14 | Low | PLAUSIBLE | OS (defence in depth) | Boot FAT (boot.scr, config.txt, uboot.env, firmware) is owned/writable by uid 990 for every hifi-bridge process; only the main unit's sandbox keeps the network-facing process from root-equivalent boot control |

No private keys or credentials committed (tracked "PRIVATE KEY" strings are test fixtures/scanner patterns; `.keys/`, `.lab/`, `**/secrets/**`
ignored). Cryptography uses OpenSSL (`openssl enc` CLI, `hashlib.scrypt`/HMAC, `cryptography` for ECDSA) - no own primitives (DECISIONS #50).
No simulated/build-only result is labelled hardware-validated in the reviewed docs (package 6 is "BUILD-VERIFIED only"). No V1 feature creep
found beyond accepted decisions.

---

## HIGH

### N-1 Setup dead end: network settings are gated behind the wizard, the wizard needs a reachable Lyrion (CONFIRMED)
* Code: `bridge/btbridge/webui/server.py:329-331` redirects every authenticated non-`/setup`, non-`/api/` request to `/setup/bluetooth`
  while `setup_complete` is false - this includes `GET/POST /settings/network*` (`views_network.py:426-432`).
  `views.py:285-286` (`setup_lyrion_post`) refuses to continue unless the Lyrion server answers; there is no skip.
  The hotspot has no uplink by design (`netrender.dnsmasq`: no router, no DNS; DECISIONS #86).
* Scenario: card written without the home Wi-Fi password - which docs/PROVISIONING.md:61 explicitly recommends ("to be entered later in the
  WebUI over HTTPS (Settings -> Network)") - or with a mistyped one; no Ethernet. The device opens the setup hotspot; the owner joins it,
  sets the owner password with the setup code, and is then forced into the wizard. Settings -> Network redirects to the wizard; the
  Lyrion step cannot succeed because nothing behind the hotspot is reachable. The only way out is re-flashing / editing the card. This
  contradicts PRODUCT_SPEC ("On configured Wi-Fi failure ... offer recoverable setup fallback") for first setup.
* Repro: `test_review_n_setup.py` (real WebUI + real agent over a UNIX socket + SIMULATED radio, hotspot active, owner logged in):
  `GET /settings/network -> 303 /setup/bluetooth`, `POST /settings/network/wifi -> 303 /setup/bluetooth`. (The harness's fake LMS factory
  accepts any host, so the "Lyrion unreachable" refusal is shown by code: views.py:285-286.)
* Fix (smallest reversible, no product change): allow `/settings/network`, its POST actions and `/api/network` while setup is incomplete
  (add them to the wizard allow-list), and link to it from the wizard's Lyrion step ("no server found? connect the device to your network
  first"); alternatively a network step before the Lyrion step. Record the onboarding order in OPEN_QUESTIONS if the owner should choose.
* Test: UNIT - fresh device, hotspot active, owner password set, `setup_complete` false: `GET /settings/network` = 200, joining a Wi-Fi
  creates a trial, confirming promotes `network.json`; other settings pages still redirect to the wizard. BROWSER: same flow on the
  phone viewport.

## MEDIUM

### N-2 Stale agent status deletes the pending file of a trial that just started (CONFIRMED)
* Code: `net/netclient.py:146-158` - `status()` calls the agent OUTSIDE `self._lock`, then applies `_sync_pending(st)` INSIDE it;
  `save()` (`:161-180`) holds the lock while writing `network.pending.json` and asking for the trial. A status snapshot taken before
  the trial (watch thread every 5 s, `/api/network` polling by network.js, every page load) waits for the lock and then sees
  `trial.id != pending id` -> `_drop_pending()` (`:132`).
* Scenario: owner joins a Wi-Fi / changes Ethernet while connected that way (a "pending" trial); the confirm banner appears, "Keep" answers
  "There is no change waiting for confirmation", the agent rolls the change back after 3 min. Fails safe, but the owner cannot make any
  connectivity-affecting change while the race hits; N-3 widens the window to seconds.
* Repro: `stale_status.py` -> `save: pending | agent trial: pending | pending file after the stale status: False | Keep: False`.
* Fix: take the status inside `_lock` (simplest), or carry a generation counter: `save()` bumps it, `_sync_pending` ignores answers
  requested before the last bump; never delete a pending file whose id the agent has not yet seen unless the agent is newer than it.
* Test: UNIT with a fake agent whose status answer is computed before and delivered after `save()` -> pending file kept, confirm succeeds.

### N-3 Agent lock held across slow tool calls; bridge deletes a pending file the agent already applied (CONFIRMED mechanism, PLAUSIBLE timing)
* Code: `net/netagent.py:137-144` `tick()` holds `Agent.lock` while `NetworkManager.tick` drives `LinuxSystem` (`netsys.py:339-352`:
  `systemctl stop`, `iw reg set`, `iw dev scan` up to 20 s, `nft`, `networkctl reload/reconfigure`, `systemctl start`, each with a 30 s
  timeout); `status()` (`:119-121`) and every trial/confirm/revert (`:103`) need the same lock. Bridge side: `AgentClient` timeout 15 s
  (`netclient.py:36`); on `AgentUnavailable` `save()` deletes the pending file (`:171-173`) even if the agent read and applied it.
* Scenario: the hotspot opens or a probe starts (scan + unit restarts) while the owner saves -> page loads block up to 15 s and then show
  "Network service not reachable"; if the agent processes the trial after reading the file but answers late, a `confirmed` change stays
  active in the agent while `network.json` still has the old config (lost silently at the next agent start; the UI shows the old config),
  a `pending` change cannot be confirmed and rolls back.
* Repro: `agent_lock.py` (status blocked 2.8 s behind a 3 s simulated hotspot start); `save_timeout.py` (agent `confirmed`, bridge has no
  `network.json`/pending file, `config()` shows DHCP while the agent applied a static address).
* Fix: run system actions outside the state lock (decide under the lock, apply in one worker, serve `status` from a snapshot); on a
  timeout keep the pending file and let `_sync_pending` reconcile by trial id (it already promotes/deletes correctly).
* Test: UNIT - slow fake system (5 s per action): `status` answers < 0.5 s during a tick; client timeout after the agent applied a
  confirmed trial -> the next poll promotes the file.

### N-4 Snapshot restore is not applied to the network agent; `prepare_data` failures crash-loop the service (CONFIRMED)
* Code: `appliance.py:92-94` runs `ota.manager.prepare_data` inside `hifi-bridge.service`; `hifi-netd.service` is
  `Before=hifi-bridge.service` (overlay unit) and reads `network.json` and `local_hostname` only at its start (`netagent.py:81-90`) and
  on the `hostname` op. `prepare_data` (`ota/manager.py:193-232`) restores `network.json`, `settings.json`, `secrets.json`, `certs/`
  with no exception handling.
* Scenario: owner restores the pre-update snapshot (Backup page) -> reboot -> netd applies the CURRENT network config and host name, then
  the bridge replaces the files: the Network page shows the restored config, the radio/addresses/host name follow the old one until the
  next reboot; the next owner change computes its "changed sections" against the agent's state, not the displayed one. Any OSError in
  `prepare_data` (ENOSPC, EIO on the card) aborts `appliance.run` before the WebUI starts; the request file stays, every restart repeats
  it until the start limit (20/600 s) leaves the WebUI down.
* Fix: apply the restore in `hifi-bridge-boot.service` (netd is already `After=` it) or add an agent `reload` op the bridge calls after a
  restore; guard `prepare_data` (log, record `restore_failed`, drop the request, continue).
* Test: UNIT - restore request with a different `network.json`/`local_hostname` -> agent config and host name equal the restored ones
  after start; `prepare_data` with a failing copy -> appliance starts, error shown on the Backup page, request removed.

### N-5 OTA install monitor dies on a RAUC error reply (CONFIRMED)
* Code: `ota/manager.py:541-570` `_monitor` catches only `RaucUnavailable`; `RaucError` (any D-Bus error name not in
  `rauc.UNAVAILABLE_ERRORS`, e.g. `LimitsExceeded`, a RAUC error on `GetAll`) or a malformed `Progress` value ends the thread.
* Scenario: the persistent phase stays `installing`, progress freezes, `install`/`discard`/`rollback` answer "busy" until the bridge
  restarts (then `resume()` reconciles correctly). During the most sensitive operation the owner sees a hung update and is likely to cut
  power.
* Repro: `ota_monitor.py` -> `thread died: ota-install RaucError ... | phase: installing | monitor alive: False | next action: busy: installing`.
* Fix: catch `RaucError` like `RaucUnavailable` (count towards `rauc_gone_s`), wrap the loop body in a catch-all that ends in
  `_finish(None, ...)` (state `install_interrupted`, DE/EN guidance).
* Test: UNIT - fake RAUC raising RaucError / returning a 2-tuple Progress during an install -> phase leaves `installing` within
  `rauc_gone_s`, error shown, actions possible again.

### N-6 Non-redundant U-Boot environment on FAT, rewritten every boot (CONFIRMED config, PLAUSIBLE impact - gate G2 P3-5)
* Config: `.lab/br-out/rpi3_64/build/uboot-2026.01/.config`: `CONFIG_ENV_IS_IN_FAT=y`, `# CONFIG_ENV_REDUNDANT is not set`;
  `post-build.sh:89` writes one `fw_env.config` line; `boot.cmd:32` `saveenv` on EVERY boot (counter decrement) and RAUC rewrites the file
  from Linux on every mark-good (health check) and activation; `/boot` is a non-journaled vfat (`post-build.sh` fstab).
* Scenario: power cut while U-Boot or `fw_setenv` rewrites `uboot.env` -> CRC error -> U-Boot uses its built-in default environment ->
  `BOOT_ORDER="A B"`, `BOOT_B_LEFT=0`: after an update to B the device silently boots A (old version, B never tried again until RAUC
  writes the env), boot counters reset. Repeated FAT writes on every boot also raise the risk of FAT damage on the partition that holds
  the firmware. RAUC's U-Boot documentation recommends a redundant environment.
* Fix: `CONFIG_ENV_REDUNDANT=y` with `CONFIG_ENV_FAT_FILE_REDUND` and two `fw_env.config` lines (or an env in raw MMC sectors outside
  the FAT); keep the decision in DECISIONS/OPEN_QUESTIONS (provisional #53).
* Test: image check asserting both env copies and matching fw_env.config; tests/os - corrupt one env copy in a built image, `fw_printenv`
  still returns the other; device: G2 P3-5 power-cut matrix.

## LOW

### N-7 Shared wpa_supplicant control socket used from two threads (CONFIRMED)
* Code: `netagent.py:133` `status()` -> `system.wifi_details()` (SIGNAL_POLL) outside `Agent.lock`; `netagent.py:138` `tick()` ->
  `system.observe()` (ATTACH/STATUS/`events()`) also outside the lock; both use the same `CtrlClient` (`netsys.py:75-112`), whose
  `events()` toggles the socket to non-blocking and whose error path closes it.
* Effect: a STATUS reply consumed as SIGNAL_POLL (and vice versa) -> `wpa_state ""` -> `CONNECTED -> CONNECTING` + "Wi-Fi connection
  lost" log, `wifi.signal` missing, socket closed under the other thread (events such as WRONG_KEY lost). Repro `ctrl_race.py`
  (stress): 6/200 STATUS answers without `wpa_state`, 5/200 SIGNAL_POLL without RSSI, 4 CtrlErrors.
* Fix: one lock per `CtrlClient` around request/events (or call `wifi_details` from the tick and cache it).
* Test: UNIT against a fake datagram server with delayed replies, two threads, 1000 iterations -> no misattributed reply.

### N-8 Firewall: spoofed source port 3483 opens every high UDP port; resolved/LLMNR not in the documented stack (CONFIRMED)
* Code: `netrender.py:185` and `overlay/usr/share/hifi-bridge/nftables-base.nft`: `udp sport 3483 udp dport 1024-65535 accept`.
  The built rootfs enables `systemd-resolved` (sysinit.target.wants) with compiled-in `LLMNR=yes` (UDP/TCP 5355 responder on every link);
  DECISIONS #89 lists networkd/timesyncd/hostnamed/nftables only.
* Effect: any LAN host reaches every UDP listener >= 1024 (today at least resolved's LLMNR on 5355; anything added later) by sending from
  port 3483; LLMNR answers name queries.
* Fix: bind the bridge's discovery socket to a fixed local port and accept `udp sport 3483 udp dport <that port>` only (or rely on
  conntrack for unicast replies and measure); set `LLMNR=no` in a resolved drop-in and document resolved in #89/NETWORK.md.
* Test: tests/net fw_probe - packet with sport 3483 to a high port other than the discovery port is dropped; image check for the
  resolved drop-in.

### N-9 Backup import with scrypt parameters above `maxmem` -> HTTP 500 (CONFIRMED)
* Code: `ota/backup.py:177` accepts `n <= 2^16, r <= 16, p <= 4`; `_keys` (`:79-81`) uses `maxmem=64 MiB`, so e.g. `n=2^16, r=8` raises
  `ValueError: memory limit exceeded`, which `plan()` does not convert; `views_update.py:442-447` catches only `BackupError`.
* Repro: inline snippet in this review (`B.plan(B.parse(doc with n=65536, r=16))` -> ValueError).
* Fix: bound `128*r*n*... <= maxmem` in the parameter check, and map any `ValueError` from scrypt to `BackupError("invalid_file")`.
* Test: UNIT - such a file -> 303 with "invalid backup file" in DE/EN, nothing changed.

### N-10 Remaining wedge states in the update manager (CONFIRMED by code, PLAUSIBLE trigger)
* `ota/manager.py:472-498` `_verify` handles only RAUC errors; an `OSError` from `getsize`/`state.edit` (or a malformed `inspect` answer)
  leaves `_busy = "verifying"`; `finish_upload` (`:459-469`) raising `OSError` from `os.replace`/state write leaves `_busy = "uploading"`
  (the view's catch-all at `views_update.py:340-342` only covers the parse step). Both block every action until the bridge restarts.
* `request_reboot` (`:627-638`) sets `rebooting`; if `hifi-bridge-reboot.path` never fires, the state stays `rebooting` for this boot,
  no action is offered (`views_update.py:165-178`) and `update.js:14-23` reloads after 180 s into the same wait forever.
* Fix: `try/finally` that always clears `_busy` and records an error; a "restart did not happen" timeout (e.g. 5 min, same boot id) that
  returns to the previous phase with guidance.
* Test: UNIT - inject OSError in `_verify` and in `finish_upload` -> phase idle + error; `request_reboot` with a no-op helper and a fake
  clock past the timeout -> back to `installed` with a message.

### N-11 Translation gaps and guidance (CONFIRMED)
* Missing strings for reachable codes (both languages): `net.err.{unreadable, wrong_owner, too_large, not_a_regular_file, unsupported,
  bad_request}` (agent `read_strict`/`handle` problems, rendered raw by `views_network.problem_text`), `bak.error.{too_many_parts,
  field_too_large}` (multipart errors of the backup import, generic fallback). Agent errors `forbidden`/`too_large` go through
  `"document:" + error` (`views_network.py:327`) and are also shown raw.
* No next step in: `net.state.agent_unreachable` ("Network service not reachable"), `upd.error.rauc_unavailable`, `upd.error.generic`
  (shows a raw code). Suggest: "Restart the device; if this stays, use the network reset on the SD card (docs)" etc.
* Test: UNIT that enumerates every problem/error code the code can emit (netconfig, read_strict, agent replies, TransferError, UpdateError,
  BackupError) and asserts a DE and EN string exists.

### N-12 Neighbours' Wi-Fi names in the diagnostics bundle (CONFIRMED)
* `service.py:610` includes `network.diagnostics()`, whose `status` contains `scan.results` (`netmanager.py:348-350`: SSID, signal,
  security, frequency of every network in range); `privacy.scrub_data` only pseudonymises MAC addresses. Owners share bundles with support.
* Fix: replace the scan list by a count in diagnostics (keep it on the page).
* Test: UNIT - bundle after a scan contains no SSID from the scan list.

### N-13 growdata guard never effective (CONFIRMED)
* `overlay/.../hifi-bridge-growdata.service`: `ConditionPathExists=!/data/.grown` with `Before=data.mount`; the condition is checked on the
  read-only root's empty `/data` mount point (no `.grown` there - verified in the built rootfs), so the script runs on every boot
  (idempotent: `sfdisk --dump` only). `hifi-bridge-data.service` touching `/data/.grown` has no effect.
* Fix: drop the condition (documented idempotency) or check a marker that exists before the mount; keep the doc consistent.
* Test: tests/os unit-file check or systemd-analyze based assertion of the guard's path.

### N-14 Boot partition writable by the service user (PLAUSIBLE, defence in depth)
* `post-build.sh` fstab: `/boot vfat ... uid=990,gid=990,fmask=0177,dmask=0077`: `boot.scr`, `uboot.env`, `config.txt`, `cmdline.txt` and
  the firmware are writable by every process running as `hifi-bridge`. Today only `hifi-bridge-boot.service` (no network) gets
  `ReadWritePaths=/boot`; the main service and the health check have `ProtectSystem=strict`. Any future hifi-bridge process without that
  sandbox (a recovery helper, a debug shell, a mis-edited unit) has root-equivalent persistent control over the next boot.
* Fix: mount `/boot` root-owned and give the bridge only the provisioning/status files (separate small FAT partition, or a root oneshot
  that copies them); at least an image check that every hifi-bridge unit except the boot one has `ProtectSystem=strict` without `/boot`.
* Test: image check over all units running as `hifi-bridge`.

---

## Uncovered but testable branches (worth real tests)
1. Network pages and `/api/network` before `setup_complete` (N-1) - today every network test calls `finish_wizard` first.
2. `NetworkService` stale status vs `save()`; client timeout after the agent applied a trial (N-2, N-3).
3. Agent `status` latency while a tick runs slow system actions; concurrent `CtrlClient` use (N-3, N-7).
4. `UpdateManager._monitor` with `RaucError`/malformed properties; `_verify`/`finish_upload` with `OSError` (N-5, N-10).
5. `request_reboot` when the reboot never happens (same boot id after a timeout) (N-10).
6. `prepare_data` with a failing copy and with a restored `network.json`/`local_hostname` (agent sees the restored values) (N-4).
7. `backup.decrypt` at every accepted (n, r, p) corner: either works within `maxmem` or yields `BackupError` (N-9).
8. i18n coverage generated from all emitted codes (N-11).
9. Image check: redundant U-Boot env + two fw_env lines; resolved LLMNR off; growdata guard (N-6, N-8, N-13).
10. Firewall probe: spoofed sport 3483 to a non-discovery high port dropped (N-8).
11. Diagnostics bundle after a Wi-Fi scan contains no foreign SSID (N-12).
12. `reconcile` with `phase=installing`, same boot, RAUC idle and the install FAILED (primary unchanged): today recorded as
    `install_interrupted` with RAUC's `LastError` - assert the intended code/text (install_failed vs interrupted).

## Checked and found sound (no finding)
Agent socket: SO_PEERCRED uid check, 4 KiB request cap, ops without configuration payload, SHA-256 bound pending file read once,
`read_strict` (O_NOFOLLOW, owner, mode, size), full re-validation; rendered configs: SSIDs/PSKs hex/PMK only, country/IP/DNS from
`ipaddress`-validated values, interface names regex-checked; nftables default deny incl. forward, hotspot chain; WebUI: CSRF on all new
POSTs (streamed uploads check session + CSRF + Content-Length before reading), escaping of SSIDs/version strings, no passwords echoed;
OTA: HTTPS-only redirects, asset URL pinned to the official releases prefix, size/space checks, atomic fsynced state, flock shared with
the health check, signature (RAUC InspectBundle) + own compatible check, snapshot before install, D-Bus policy limited to root/hifi-bridge,
reboot helper takes no input; backup: scrypt + openssl via inherited fd + encrypt-then-MAC checked before decryption; flash tool:
strict helper request validation, re-enumeration + identity check, O_EXCL/lock + size check, hash-while-writing + cache-bypassing
read-back + invalidation on failure/cancel/EOF, token file 0600 and constant-time compare, polkit `auth_admin` for exactly the helper path,
owner sheet without trust key/home Wi-Fi password; image: empty provisioning slot, public-only keyring, development marking.

---

## Fixes (package 7b-1), 2026-11-19

Branch `wip/qa-fixes` (from develop @ fa82736). Scope: N-1, N-2, N-3, N-4, N-5, N-7, N-9, N-10, N-11, N-12. Not in scope (OS image, later
package): N-6, N-8, N-13, N-14. Every fix started with a test that reproduced the failure on the unchanged code (it failed), then the fix.
All evidence is UNIT/INTEGRATION/BROWSER in Docker with the SIMULATED radio, fake/real RAUC as noted - **nothing here ran on a Pi, a real
Wi-Fi radio or a real phone**.

| ID | Fix commit | What changed | Proving tests |
|---|---|---|---|
| N-1 | `78a9be7` fix(webui), `185c112` | `/settings/network` and everything below it reachable before the wizard is finished (`server.SETUP_REACHABLE`; session/CSRF/origin unchanged, every other page still waits). During setup the page leads back to the wizard and explains the next steps; the Lyrion step links to it; wizard pages show the confirm/undo banner of a pending change. DE/EN. PROVISIONING.md section 3 "First setup without a working home network", NETWORK.md section 1 | `test_webui_network.py`: `test_hotspot_only_onboarding_reaches_the_network_settings_before_the_wizard_is_finished` (hotspot active, owner password set, `setup_complete` false: pages + `/api/network` 200, join -> trial, banner in the wizard, Keep promotes `network.json`, other pages redirect), `test_a_mistyped_provisioned_wifi_password_can_be_fixed_before_the_wizard`, `test_network_settings_before_the_wizard_keep_auth_and_csrf_as_strict`, `test_fresh_device_without_owner_password_sends_network_pages_to_the_setup`, `test_the_wizards_lyrion_step_points_to_the_network_settings[en/de]`, `test_a_broken_network_service_never_breaks_the_wizard`; BROWSER `test_ui_browser.py::test_01` (phone viewport: wizard -> network page -> back, axe, no overflow) |
| N-2 | `e40f663` fix(net) | `NetworkService`: generation counter bumped when a `save()` ends; a status answer requested before that is not used for the pending-file sync | `test_netagent.py::test_a_status_answer_older_than_a_save_never_deletes_the_new_pending_file` |
| N-3 | `e40f663` fix(net) | Agent: `status` waits for the work lock at most 0.25 s, else serves the last consistent snapshot (published at the end of every locked step) with `busy: true`; reports a received-but-unapplied trial as `trial_request`. Client: `AgentUnavailable.sent`; a delivered request without answer keeps the pending file (next status decides by trial id), only an undelivered request or an agent that does not know the trial removes it; confirm/undo without answer return "unknown" (`None`) and keep the file. Page texts for "no answer in time" and "not reachable" (DE/EN). NETWORK.md section 4 item 6 | `test_status_answers_at_once_while_a_tick_runs_a_slow_system_action` (< 0.5 s during a blocked `hotspot()`), `test_client_timeout_after_the_agent_took_the_trial_keeps_the_file_and_the_next_poll_promotes_it`, `test_client_timeout_and_then_an_agent_restart_drops_the_file`, `test_a_request_that_never_reached_the_agent_drops_the_file`, `test_confirm_and_undo_timeouts_keep_the_pending_file_until_the_agent_answers`, `test_an_undo_that_never_reached_the_agent_drops_the_file`, `test_a_second_save_while_the_first_still_waits_wins_and_the_first_is_refused`, `test_agent_unreachable` (updated); `test_webui_network.py::test_an_agent_without_an_answer_tells_the_owner_what_to_do[en/de]` |
| N-4 | `e698c7e` fix(ota) | The owner-requested snapshot restore runs in the boot step (`bootfiles`, `hifi-bridge-boot.service`, which `hifi-netd` is ordered after); the boot unit drops `ProcSubset=pid` to read `rauc.slot=` (systemd exposure still 0.6); unknown slot -> request waits (`deferred`). `prepare_data` never raises: all-or-nothing copy, on error log + `restore_failed` (update and backup page, DE/EN) + request dropped. The appliance keeps a guarded no-op call. OTA.md section 6 | `test_ota_manager.py`: `test_the_boot_step_restores_the_snapshot_before_the_network_agent_reads_it` (boot step, then a real agent start: restored Wi-Fi and host name), `test_a_failing_restore_changes_nothing_records_the_error_and_drops_the_request`, `test_a_failing_restore_never_stops_the_start_even_if_nothing_can_be_written`, `test_a_restore_failing_in_the_middle_of_a_directory_copy_leaves_no_partial_copies`, `test_without_a_known_boot_slot_the_request_waits`; `test_appliance_updates.py::test_a_crashing_restore_step_never_keeps_the_webui_down`; `test_network_os.py::test_snapshot_restore_runs_in_the_boot_step_before_the_network_agent`; `test_webui_update.py::test_a_failed_snapshot_restore_is_shown_on_the_backup_and_update_pages[en/de]` |
| N-5 | `6a1af97` fix(ota) | Install monitor: any failure of a look at RAUC (RaucUnavailable, RaucError, unexpected exception) counts towards `rauc_gone_s`, then `install_interrupted`; malformed `Progress` shown as 0 %; failure to record the end is logged, the thread never dies silently | `test_a_rauc_error_reply_during_the_install_ends_as_interrupted[completed/properties]`, `test_a_short_rauc_error_burst_does_not_end_the_install`, `test_a_malformed_progress_value_never_stops_the_install[4 cases incl. 2-tuple]`, `test_an_unexpected_exception_in_the_monitor_ends_as_interrupted`, `test_the_monitor_logs_when_even_the_final_state_cannot_be_written` |
| N-7 | `e1289aa` fix(net), `185c112` (test hygiene) | `CtrlClient`: one lock per client around request/reply, ATTACH, the event drain and close | `test_netsys.py::test_one_control_client_used_by_two_threads_never_mixes_up_replies` (2 threads x 1000 requests, delayed replies, interleaved events; failed before) |
| N-9 | `135675a` fix(ota) | KDF bounds include OpenSSL's memory formula `128 r (N + p + 2) <= 64 MiB` and `N < 2^(16 r)`; any `ValueError` from scrypt -> `invalid_file` | `test_ota_backup.py`: `test_kdf_parameters_within_the_bounds_but_above_the_memory_limit_are_an_invalid_file[3]`, `test_n_must_be_below_2_to_the_16_r_as_scrypt_demands[2]`, `test_every_accepted_kdf_corner_runs_within_the_memory_limit[6]`, `test_a_scrypt_refusal_is_an_invalid_file`; `test_webui_update.py::test_import_with_kdf_parameters_above_the_memory_limit_is_an_invalid_file[en/de]` (303 + text, nothing changed) |
| N-10 | `6a1af97` fix(ota) | `_verify`/`finish_upload` always leave the transient phase (new `verify_failed`, `upload_failed`); `_set_error` never keeps a transient phase; a failed verification in the download thread is no unhandled exception; "rebooting" on the same boot for `REBOOT_WAIT_S` (5 min) returns to installed/switching with `reboot_timeout` guidance and the restart offered again (a real restart is still reconciled at the start). OTA.md sections 3 and 5 | `test_a_storage_error_while_verifying_returns_to_idle_with_an_error`, `test_a_malformed_inspect_answer_returns_to_idle_with_an_error`, `test_a_storage_error_while_finishing_the_upload_returns_to_idle`, `test_an_unrecordable_error_still_frees_the_manager`, `test_storage_errors_while_cleaning_up_never_keep_a_transient_phase`, `test_a_download_whose_verification_fails_does_not_kill_the_thread_silently`, `test_a_restart_that_never_happens_returns_to_the_previous_phase[installed/switching]`, `test_a_real_restart_is_never_mistaken_for_a_timeout`, `test_a_rebooting_state_without_a_restart_record_times_out_from_the_install`, `test_a_restart_request_with_an_incomplete_state_record`; `test_webui_update.py::test_a_restart_that_never_happens_offers_the_restart_again_with_guidance[en/de]` |
| N-11 | `1f585c3` fix(i18n) (+ texts in the N-3/N-4/N-10 commits) | DE/EN texts with a next step for `net.err.{unreadable, wrong_owner, too_large, not_a_regular_file, unsupported, bad_request, forbidden}`, `bak.error.{too_many_parts, field_too_large}`; next steps added to `upd.error.generic`, `bak.error.generic`, `upd.error.rauc_unavailable`; `net.agent_unreachable.help` | new `test_error_texts.py`: codes collected from the source by AST (netconfig reasons incl. f-strings, agent replies, UpdateError/_set_error/recorded codes, TransferError of downloads/uploads, BackupError, multipart errors of the import) must have DE and EN texts; generic/service errors must name a next step |
| N-12 | `e1c53fe` fix(net) | `NetworkService.diagnostics()` keeps only the number of networks seen in the last scan; the list stays on the page; own SSIDs stay | `test_netagent.py::test_the_diagnostics_bundle_never_contains_the_neighbours_wifi_names` |

Remaining / not changed here:
* N-6, N-8, N-13, N-14: OS image, out of scope of this package.
* N-3 residual: trial/confirm/undo still wait for the agent's work lock (they must be serialised with the state machine); behind a very
  slow action the owner sees "did not answer in time" and the next status reconciles. Status itself no longer waits.
* N-4 residual: in a setup without the boot unit (the appliance's fallback call, e.g. a hand-installed test Pi without `--boot-dir`)
  the agent still follows a restored `network.json` only at its next start.
* Hardware: the hotspot-only first setup on a real phone (does it stay on a hotspot without internet; gate item N-5 in NETWORK.md) and
  the timing of real `iw`/`systemctl` actions are unverified.

Suite results on `wip/qa-fixes` (2026-11-19, Docker only):

| Suite | Result |
|---|---|
| tests/unit (same runner as `scripts/run_unit.sh`, under coverage, final commit) | **890 passed** in 337 s (develop before this package: 818). The plain `scripts/run_unit.sh` one commit earlier: 889 passed + 1 warning (a test's fake socket thread), fixed in `185c112` |
| `scripts/run_net.sh` (real nft/dnsmasq/hostapd/wpa_supplicant/networkd/Avahi + live firewall/DHCP) | 16 passed, live checks PASS, exit 0 (`reports/evidence/network_realtools.txt`) |
| `scripts/run_ota.sh` (real RAUC 1.13 D-Bus service) | 12 passed, exit 0 (`reports/evidence/ota_realrauc.txt`) |
| `scripts/run_static.sh` | shellcheck, compile, `systemd-analyze verify`, actionlint: exit 0; exposure hifi-bridge 1.9, hifi-bridge-boot 0.6 (unchanged after dropping `ProcSubset=pid`), health 1.5 |
| `scripts/run_entry_tests.sh` | 90 passed |
| Browser (`tests/browser`, Chromium) | 11 passed; axe: `network-during-setup` and `lyrion` without violations (`reports/screenshots/axe-results.json`). Run with the commands of `scripts/run_browser.sh` in an **own compose project** (`COMPOSE_PROJECT_NAME=btbridge-qa7b`, removed afterwards), because the lab of the main checkout was running and `run_browser.sh`/`lab_up.sh` would have recreated it |

Line + branch coverage of the changed modules (unit suite, coverage.py; develop before -> after): `ota/manager.py` 99 % -> 99 % (0 missed
lines; the 2 partial branches are the pre-existing generator expressions in `install`/`_primary_bootname`), `ota/backup.py` 100 % -> 100 %,
`webui/views_update.py` 100 % -> 100 %, `net/netagent.py` 96 % -> 97 %, `net/netclient.py` 98 % -> 98 %, `net/netsys.py` 95 % -> 95 %,
`webui/views_network.py` 96 % -> 98 %, `webui/server.py` 92 % -> 92 %, `webui/views.py` 89 % -> 89 %, `bootfiles.py` 98 % -> 98 %,
`appliance.py` 94 % -> 94 %. Every remaining miss in these modules existed before this package (no new uncovered line or branch).
