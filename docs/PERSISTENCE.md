# Persistence and card layout (DECISIONS #120, #121)

**Status (2026-11-20, phase-2b final polish P1):** implemented and checked **without hardware**. Labels: **STATIC** (repository files,
`tests/os`), **UNIT** (`tests/unit`), **BUILD** (a real rpi3_64 image built and taken apart in Docker, `scripts/os_check_image.sh`;
nothing booted). Whether a running device writes only these paths is **HARDWARE-PENDING** (docs/HARDWARE_GATE_CHECKLIST.md G2 P4-5).

The single source of the numbers and of the allow-list is [`os/storage.json`](../os/storage.json). The image check
(`os/tools/image_check.py`, ids `persist.*` and `size.*`), the size report (`os/tools/size_plan.py`), the flash tool's minimum card size
and the tests read it; `tests/os/test_persistence_layout.py` fails when this document, the genimage configuration, the defconfigs or the
flash tool disagree with it.

## 1. The rule
**Persist only what must survive a reboot. Everything else belongs in RAM** (owner decision #121, binding).

Persistent: administrator credentials, device certificates and keys, Wi-Fi/network/LMS/MQTT configuration, Bluetooth bonds, the OTA and
rollback state that is required, boot and recovery metadata. Logs and diagnostics live in bounded RAM buffers and are gone after a reboot;
an explicitly configured external syslog server is the way to keep logs (section 6). An update may keep temporary files on the card while
it runs and must remove them reliably; power-loss safety and rollback take precedence over avoiding necessary writes. No audio
recording, no playback history, no unbounded log files.

What is writable on the image at all: the root file system is read-only (ext4 without journal, mounted `ro`, DECISIONS #101); `/var` is an
overlay whose upper layer is in `/run` (tmpfs); `/run` and `/tmp` are tmpfs. Only two places are on the card: the **data partition**
(`/data`, ext4, `commit=5`) with the bind mounts listed below, and the **boot FAT** (`/boot`).

## 2. Card layout (DECISIONS #120)
| Partition | Size | Content |
|---|---|---|
| (MBR + alignment) | 4 MiB | partition table; every partition starts on a 4 MiB boundary (SD erase blocks) |
| p1 boot FAT | **64 MiB** | firmware, DTBs, overlays, U-Boot + its two environment copies, `boot.scr`, licences, provisioning slot (4.6 MiB used) |
| p2 rootfs A | **384 MiB** | read-only root with kernel and modules (193.3 MiB used in the 384 MiB slot, measured; 156.8 MiB of files) |
| p3 rootfs B | **384 MiB** | empty on a fresh card; RAUC installs updates here (and back to A) |
| p4 data | **256 MiB**, grown to the rest of the card on the first boot | everything in section 3 |

Card image: 4 + 64 + 2 × 384 + 256 = **1092 MiB = 1.145 GB**. Technical target: a **nominal 2 GB card**. Real "2 GB" cards report less
than 2 × 10^9 bytes (typically 1.9–1.98 × 10^9), so `os/storage.json` treats **1.8 × 10^9 bytes (1716 MiB, 10 % below the label)** as the
usable size of such a card; the image must fit in it (image check `size.card-fits-2gb`) and the flash tool refuses only cards below it
(`disks.MIN_BYTES`, "smaller than 2 GB" / "kleiner als 2 GB"). On such a card the data partition grows to ~880 MiB. **Recommendation: a
16 GB microSD** (endurance and speed, not space; data then ~14 GiB) - never a requirement: no 16 GB minimum anywhere.

The slot size cannot change after the first flash (an update writes into the existing slots); 384 MiB leaves 191 MiB (50 %) for later
releases (image check `size.slot-headroom`: at most 80 %). Growth: `hifi-bridge-growdata.service` moves the end of p4 to the end of the card on every boot when there is room
(idempotent, review N-13), `x-systemd.growfs` grows the mounted ext4 online.

### 2.1 Initial data size - the arithmetic
The data partition must work even if the first-boot growth never happens (a card exactly the image size, a failed growth): it must hold
one update of a full slot plus the state.

| Item | MiB | Source |
|---|---|---|
| Largest update bundle | **192** | RAUC streaming is off (`# BR2_PACKAGE_RAUC_STREAMING is not set`), so a downloaded or uploaded bundle is stored completely on `/data` (`ota/bundle.raucb`, via `.part`/`.upload`, renamed - never two copies of the same bundle) while RAUC verifies and installs it. Measured (P1 build, 384 MiB slot): the development bundle is 73.6 MiB for a rootfs with 193.3 MiB in use (ratio 0.38; the empty part of the slot compresses to nothing - the bundle is as large as with the 256 MiB slot). A completely full 384 MiB slot at that ratio: 146 MiB. Budget: 2:1 = slot / 2 = 192 MiB. |
| Free-space margin of the update manager | **16** | `btbridge.ota.transfer.SPACE_MARGIN`: an upload/download is refused **before a byte is stored** unless the bundle plus 16 MiB fit (`ensure_space`) |
| State | **8** | settings, secrets, network, certificates, journals, setup code (< 100 KiB together), two pre-update snapshots (< 100 KiB each), update state, RAUC status, Bluetooth bonds of ~100 phones (a few KiB each), clock file |
| **Needed, available to the bridge** | **216** | the bridge runs as `hifi-bridge`; ext4's reserved blocks (5 %) are for root only and do not count |
| ext4 overhead at 256 MiB | 31 | measured with mke2fs 1.47.2 (`-b 4096 -i 65536`): 12.8 MiB reserved blocks, 16 MiB journal, ~2 MiB inode tables, bitmaps, descriptors and the reserved GDT blocks for online growth |
| **Available at 256 MiB (measured)** | **225** (built image: 225.8) | ≥ 216 → **256 MiB** (240 MiB would leave ~210 MiB: too little; the next 16 MiB step that fits) |

Without `-b 4096 -i 65536` mke2fs picks its "small" profile below 512 MiB: 1 KiB blocks (kept forever after the growth) and one inode per
4 KiB (65536 inodes = 16 MiB of inode tables for a few hundred files); 256 MiB would then leave 217 MiB. With one inode per 64 KiB a
256 MiB partition has 4096 inodes, a grown 2 GB card ~14 000 and a 16 GB card ~240 000 - far above the few hundred files in use.

The old size plan's 448 MiB (package 6, "2 × bundle + 256 MiB") assumed two bundles at once; the manager never holds two copies of one
bundle (a new upload over a staged one needs the free space for the new one, which the space check enforces). Built image: section 8.

## 3. Persistent write paths
Everything below `/data` that exists on a device. The fresh data partition contains only `lost+found` (image check
`persist.data-partition-empty`); `hifi-bridge-data.service` creates the four directories, the services create their files.

| Path (on the card) | Mounted at | Owner service | Content | Why it must persist | Size | Write frequency |
|---|---|---|---|---|---|---|
| `/data/hifi-bridge` | `/var/lib/hifi-bridge` | hifi-bridge, hifi-bridge-boot, hifi-bridge-health (hifi-netd reads) | see 3.1 | credentials, keys, network/LMS/MQTT configuration, OTA/rollback state | < 1 MiB; + one bundle (≤ 192 MiB) while an update is staged/installed | owner changes, session start/end, update steps |
| `/data/bluetooth` | `/var/lib/bluetooth` | bluetooth.service (BlueZ) | adapter settings, bonds (link keys) + attributes of paired phones, BlueZ's name cache | a paired phone reconnects after a reboot without pairing again | a few KiB per phone | pairing, connection, removal |
| `/data/timesync` | `/var/lib/systemd/timesync` | systemd-timesyncd | empty file `clock`, its modification time = last known time | no RTC on a Pi: after a reboot without network the clock starts at the last known time (TLS, certificates) | 0 bytes (one inode) | every 900 s (`SaveIntervalSec`, was 60 s) and at each NTP synchronisation |
| `/data/rauc` | (direct) | rauc.service (`data-directory`) | central slot status: install status, bundle version and hash per slot | OTA/rollback state | < 4 KiB | per installation |
| `/data/lost+found` | (direct) | e2fsck | ext4 recovery directory | file system repair after a power cut | 16 KiB | only during a repair |

### 3.1 Files of the bridge (`/var/lib/hifi-bridge`)
The complete list (`os/storage.json` → `bridge_files`; `tests/unit/test_persistence_allowlist.py` runs the boot step, the service, a
fault, a playback session journal and an update upload and fails on any other file).

| File | Content | Why | Written |
|---|---|---|---|
| `settings.json` | owner settings: device name, Lyrion server and player, MQTT, syslog target, language, labels of paired phones | configuration | on owner changes (only when a value changes) |
| `secrets.json` | owner password hash, setup-code hash, session key, stream token, MQTT password, flash-tool trust key, log salt | credentials | on owner changes |
| `settings.json.corrupt-*`, `secrets.json.corrupt-*` | a damaged file moved aside, defaults used | evidence for a repair | only after damage |
| `network.json` | Wi-Fi client, Ethernet/Wi-Fi addresses, setup hotspot, country | network configuration | on owner changes |
| `network.pending.json` | a network change on trial (Keep/Undo) | the trial and its undo survive a restart of the bridge | during a trial |
| `setup-code.txt` | the one-time setup code | first setup and password reset need it until the owner password exists | first start, password reset |
| `session.json` | the LMS item the bridge currently owns: player id + stream URL (no title, no artist) | after a crash or power cut the next start releases exactly that item at Lyrion (ownership semantics, SERVICE_SUPERVISION.md) | playback session start and end; removed when the session ends |
| `provision-journal.json`, `recovery-journal.json` | state of processing the provisioning file / a recovery request | exactly once across power cuts | once per provisioning / request |
| `certs/ca.key`, `certs/ca.crt` | device CA (the key never leaves the card) | device certificates (trust of the local HTTPS) | first start |
| `certs/server.key`, `certs/server.crt`, `certs/custom.key`, `certs/custom.crt`, `certs/state.json` | HTTPS certificate (own or owner-supplied) and its state | as above | issue/renewal, owner upload |
| `ota/state.json`, `ota/state.lock` | update state machine; empty lock file shared with the health check | OTA/rollback state | per update step |
| `ota/slots.json`, `ota/health.json` | version per slot (rollback offer); result of the last post-boot health check | OTA/rollback state | per update; once per boot |
| `ota/restore-request.json` | an owner-requested snapshot restore for the next start | owner action across the restart | on request |
| `ota/snapshots/A`, `ota/snapshots/B` | settings, secrets, network and certificates before an update (DECISIONS #93) | the owner can return to them after a rollback | per installation |
| `ota/bundle.raucb` | the update bundle | input of the pending/running installation (section 5) | per update |
| `ota/bundle.raucb.upload`, `ota/bundle.raucb.part`, `ota/bundle.raucb.part.json` | an upload / a download in progress | only while it runs (section 5) | during the transfer |

### 3.2 Writes to the boot FAT
| Path | Writer | Why | Frequency |
|---|---|---|---|
| `/boot/uboot.env` | U-Boot (boot counter), RAUC through `fw_setenv` (install, mark-good) | A/B boot selection and boot counter (boot/recovery metadata) | every boot (U-Boot decrements), every healthy boot (mark-good resets it), per update |
| `/boot/uboot-redund.env` | the same, alternating (review N-6) | power-cut safety of the boot selection | as `uboot.env` (OPEN_QUESTIONS #81: raw MMC sectors instead of FAT files) |
| `/boot/hifi-status.txt` | hifi-bridge-boot.service | offline status for the owner: name, CA fingerprint, setup code while needed, recovery/provisioning results | **only when its content changes** (was: every boot, because of the time stamps) |
| `/boot/hifi-provision.json` | hifi-bridge-boot.service | scrubbed after it was applied (it carried secrets) | once after flashing |
| `/boot/hifi-recovery` | hifi-bridge-boot.service | the owner's recovery request, removed once processed | only on a request |

## 4. Everything else is in RAM
| What | Where | Bound |
|---|---|---|
| system journal (all services, incl. the bridge's stdout) | `/run/log/journal` | `Storage=volatile`, `RuntimeMaxUse=16M`, `RuntimeMaxFileSize=2M` (`/etc/systemd/journald.conf.d/hifi-bridge.conf`; ~3 % of a Pi Zero 2 W's 512 MiB); no `/var/log/journal` |
| bridge log lines (WebUI, diagnostics) | process memory | last 600 lines |
| fault history (WebUI diagnostics, bundle) | process memory, mirrored to `/run/hifi-bridge/faults.jsonl` (tmpfs, `RuntimeDirectory=`, kept across a service restart) | last 200 faults; **was `/data/hifi-bridge/faults.jsonl`** (review L5), see DECISIONS #127 |
| remote-syslog queue | process memory | 1000 lines, oldest dropped and counted; never spooled to disk |
| now playing, phone name, MQTT state, status, CPU/temperature history, diagnostics bundle | process memory (the bundle is built per request) | fixed-size structures; no playback history (DECISIONS #124) |
| audio | ring buffer in memory between PipeWire capture and the HTTP stream | bounded ring; no PCM is ever written to a file |
| network runtime: wpa_supplicant/hostapd/dnsmasq configuration and sockets, DHCP leases, networkd files written by the agent | `/run/hifi-net`, `/run/systemd/network`, `/run/systemd/netif` | small |
| reboot request | `/run/hifi-bridge-ota` | one empty file |
| PipeWire/WirePlumber, Avahi, D-Bus, resolved, pstore archive, `/var/cache`, `/var/log`, `/var/tmp`, `/var/lib/*` except the bind mounts | `/var` overlay upper in `/run`, or `/run` | tmpfs (`/run`: 20 % of RAM) |
| temporary files (certificate signing requests, OpenSSL configs) | `/tmp` (tmpfs; `PrivateTmp=yes`) | removed after use |
| core dumps | **none**: `DefaultLimitCORE=0` for every service (`/etc/systemd/system.conf.d/hifi-bridge.conf`), `kernel.core_pattern=|/bin/false` (`/usr/lib/sysctl.d/90-hifi-bridge-coredump.conf`); systemd-coredump is not installed | - |
| Python bytecode | precompiled into the read-only root (`unchecked-hash` pycs); every Python unit sets `PYTHONDONTWRITEBYTECODE=1`; nothing is imported from a writable path | - |
| machine-id, random seed | not stored: `/etc/machine-id` stays empty (transient per boot; networkd DUID from the MAC, OPEN_QUESTIONS #77); systemd-random-seed is not installed (the Pi's hardware RNG and the kernel's jitter entropy seed the pool) | - |

The service user's home directory is `/var/lib/hifi-bridge` (= `/data/hifi-bridge`); no component writes into `$HOME` (no `~/.cache`,
no OpenSSL `RANDFILE`: not set in the image's `openssl.cnf`; checked by grep of the bridge for `expanduser`/`HOME`/`XDG_`).

## 5. Update staging: lifecycle and cleanup
| Event | What happens to the files on `/data/hifi-bridge/ota` |
|---|---|
| upload starts | free-space check **before a byte is stored** (bundle + 16 MiB, else `no_space`); streamed into `bundle.raucb.upload` |
| upload fails / is cancelled / the connection drops | `bundle.raucb.upload` removed at once (`abort_upload`) |
| upload complete | renamed to `bundle.raucb` (one copy), verified by RAUC; refused → removed |
| download | `bundle.raucb.part` + `.part.json`; cancel = pause (kept for a resume while the device runs); discard removes them |
| installation succeeds | `bundle.raucb` removed **before** the state says "installed" (no orphan after a power cut) |
| installation fails | `bundle.raucb` kept, phase `staged`: the owner can retry or discard |
| owner discards a staged bundle | removed |
| **bridge restart** (crash, watchdog) | `ota.manager.cleanup_staging(at_boot=False)` in `UpdateManager.resume()`: an interrupted upload and any bundle the persistent state does not reference (e.g. a verification that was cut off) are removed; a paused download stays |
| **boot** (also after a power cut in any step) | `btbridge.storage.boot_cleanup` in `hifi-bridge-boot.service`, before anything else reads the data: removes `.upload`, `.part`, `.part.json`, an unreferenced `bundle.raucb`, `state.json.tmp-<pid>`, `.health-probe`, half-written snapshot directories, and the leftovers of interrupted atomic writes of the whole data directory (`.<file>.tmp-<pid>[-<thread>]`, `<file>.tmp`, also below `certs/`), plus the fault file of earlier versions (`faults.jsonl`) |
| kept across a reboot on purpose | `bundle.raucb` while the state is `staged` (verified, waiting for the owner's install) or `installing` (RAUC reads it; after a power cut the state machine returns to `staged` so the owner can retry - power-loss safety first). OPEN_QUESTIONS #87 asks whether a staged bundle should rather be dropped at boot. |

Tests (UNIT, `tests/unit/test_persistence.py`): every transient file is removed at boot; a referenced bundle is kept in `staged` and
`installing`; an unreferenced one is removed; a bridge restart keeps a paused download and drops an interrupted upload; atomic-write
leftovers and the legacy fault file go, real files stay; a failing cleanup never stops the boot step. The existing OTA tests cover
upload failure/cancel, discard, success and failure (`tests/unit/test_ota_manager.py`).

## 6. External syslog
Already implemented (PRODUCT_SPEC "optionally secure remote syslog"; `bridge/btbridge/system/syslog.py`, settings `syslog_enabled`,
`syslog_host`, `syslog_port`, `syslog_ca_pem`): RFC 5424 over TLS (RFC 5425) with mandatory certificate and host-name verification, lines
already redacted (tokens, MAC pseudonyms), a bounded in-memory queue (1000 lines, oldest dropped and counted) and a background thread with
back-off. It **never spools to the card**. It forwards the bridge's own log; the journal of the other services (BlueZ, PipeWire, RAUC,
kernel) is not forwarded - a journald forwarder (e.g. `systemd-journal-upload` or an rsyslog/syslog-ng client reading the journal) would
make that possible without changing the rule; not built (OPEN_QUESTIONS #88).

## 7. Automated checks
| Check | Where | Label |
|---|---|---|
| `persist.journald-volatile` (`Storage=volatile`, `RuntimeMaxUse` ≤ 16M, `RuntimeMaxFileSize` set), `persist.no-persistent-journal-dir` | image check, `tests/os/test_persistence_layout.py` | BUILD / STATIC |
| `persist.coredumps-off` (`DefaultLimitCORE=0`, `core_pattern=|/bin/false`, systemd-coredump absent or `Storage=none`) | same | BUILD / STATIC |
| `persist.python-no-bytecode-writes` (every unit running python3 sets `PYTHONDONTWRITEBYTECODE=1`) | same | BUILD / STATIC |
| `persist.data-allowlist` (fstab bind mounts, directories created by `hifi-bridge-data.service`, RAUC `data-directory` = the allow-list) | same | BUILD / STATIC |
| `persist.unit-write-paths` (only the allowed units name `/data`, `/boot` or a bind target in `ReadWritePaths=`/`StateDirectory=`) | same, incl. all stock units in the built image | BUILD / STATIC |
| `persist.tmpfiles-no-persistent-paths` (no tmpfiles.d entry on `/data`, `/boot` or a bind target) | same | BUILD / STATIC |
| `persist.bridge-faults-in-ram`, `persist.ota-staging-cleanup`, `persist.timesync-save-interval` | same | BUILD / STATIC |
| `persist.data-partition-empty`, `size.layout`, `size.card-fits-2gb`, `size.data-holds-update`, `size.data-ext4-layout`, `size.slot-headroom` | image check; the data partition also from a real genimage run in `tests/os` | BUILD / STATIC |
| each `persist.*` check fails on a planted violation (10 cases) | `tests/os/test_persistence_layout.py` | STATIC |
| bridge writes only allow-listed files; RAM fault history; boot cleanup; status file without needless writes | `tests/unit/test_persistence*.py` | UNIT |
| layout numbers agree (storage.json, genimage, fragment, 3 defconfigs, flash tool), no "16 GB minimum" in docs/UI | `tests/os/test_persistence_layout.py` | STATIC |

## 8. Consequences of the bigger layout (BUILD, rpi3_64, 2026-11-20, commit 85b25d3)
Evidence: `reports/evidence/os_image_rpi3_64.md` (0 FAIL, 119 PASS, 20 INFO; secret scan 0 findings), `os_ota_rpi3_64.txt`,
`os_size_plan.md`.

| Effect | Before (package 6, 64/256/256/64) | Now (64/384/384/256) | Note |
|---|---|---|---|
| card image (apparent size) | 644 MiB | **1092 MiB** (1 145 044 992 bytes) | |
| card image (blocks on disk) | 180 MiB | **192 MiB** | genimage leaves the empty slot B and the free space of the file systems as holes (sparse file); the extra 12 MiB are the data partition's journal |
| rootfs in use | 185.1 MiB | 193.3 MiB | more ext4 inode tables in the bigger slot (mke2fs scales the inode count with the size; 7 k of 98 k inodes used); free 191 MiB |
| development bundle (RAUC) | 73.6 MiB | **73.6 MiB** (77 190 603 bytes) | squashfs stores the empty blocks of the slot image as sparse blocks: the bigger slot costs nothing in the bundle |
| flash time | write + read back 644 MiB | write + read back **1092 MiB** (~1.7 ×) | the flash tool writes every byte of the image incl. the empty slot B (needed for a correct read-back verification; OPEN_QUESTIONS #90). A compressed download (`.img.xz`) stays as small as before: the zeros compress away |
| write volume of an update | 256 MiB slot image | 384 MiB slot image | RAUC writes the whole slot image; whether it skips empty blocks is HARDWARE-PENDING (OPEN_QUESTIONS #90) |
| reproducibility | | `tests/os` builds two cards from different-age inputs with the new genimage configuration: byte-identical | a two-build comparison of the real image was not repeated (package 6 method, docs/IMAGE_BUILD.md §5); P2 rebuilds all boards |
