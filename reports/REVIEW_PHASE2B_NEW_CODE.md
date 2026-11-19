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
