# Flash and provisioning tool (P9, DECISIONS #59)

Status (phase 2b, package 5): **software complete, hardware-unverified.** The PySide6 GUI, the privileged write helper and the platform
disk adapters exist and are tested **only with simulated cards (regular files), recorded tool outputs and injected fakes** of the system
calls. Nothing in this repository has enumerated or written a real disk, elevated a real process or run on Windows or macOS. Those
checks are the hardware gate G3 (docs/HARDWARE_GATE_CHECKLIST.md, F-1…F-6).

Run (Docker only): `scripts/run_flashtool.sh test | coverage | screenshots | package`.

## 1. Architecture
```
GUI  hififlash.gui (PySide6, runs as the normal user; refuses to start as root)
 │   wizard: board → image (download+verify | custom) → card → device setup → typed confirmation → write/verify → owner sheet + P7
 │   no write logic: every check below is the core's, every write goes through the helper
 ├─ core (pure Python, Qt-free, tested)
 │   ├─ boards      Pi Zero 2 W / 3 B+ / 4 ↔ the manifest's "compatible" (same strings as the Buildroot defconfigs)
 │   ├─ catalog     GitHub-Releases JSON from a configurable source; streamed, resumable download (Range/If-Range) with a cache
 │   ├─ keystore    official keys (shipped, empty until the release key exists) + custom keys (only after explicit addition)
 │   ├─ images      signature by a trusted key → board → size + SHA-256 of raw/.xz/.gz images
 │   ├─ disks       lsblk / diskutil / PowerShell parsers; safety policy; typed confirmation; identity
 │   ├─ provision   per-device secrets; the document is validated by the device's own btbridge.provisioning
 │   ├─ trust       P7: authenticate the device CA with the trust key; then HTTPS against only that CA
 │   └─ writer      stream → hash → flush → read back → slot → read back; any failure after the start invalidates the card
 │
 │   JSON lines (stdio with pkexec | token-authenticated loopback connect-back on macOS/Windows | in-process pipes when simulated)
 ▼
HELPER  hififlash.helper (elevated, no Qt, ONE request per run)
     protocol.validate_request → re-enumerate → same identity → full policy → prepare (unmount/lock) → exclusive open → writer → finish
     platforms/: linux (lsblk, umount, O_EXCL) · darwin (diskutil, /dev/rdiskN, F_NOCACHE) · windows (PowerShell, FSCTL_LOCK/DISMOUNT,
                 \\.\PhysicalDriveN, NO_BUFFERING) · simulated (regular files only) - every system call injectable
```
Shared with the device: `btbridge.provisioning` (format and validation), `btbridge.net.netconfig` (Wi-Fi/hotspot/country validators used by
the GUI's form), `btbridge.config.schema` (device name, host name), `btbridge.trustproof` (HMAC proof). Nothing is duplicated.

Command line for test day and development (**no write command**): `python3 -m hififlash provision | verify-device | verify-image`.

## 2. Safe disk selection (`hififlash.disks`)
A disk is offered only if **all** of these hold. Every refusal has a reason code that the GUI shows next to the disk (it stays visible,
greyed out and not selectable):
* whole disk, removable or USB/SD/MMC transport (`not_removable`);
* not the system or boot disk, nothing mounted at `/`, `/boot`, `/boot/efi`, `/usr`, `/var`, `/home`, `C:\`, `/System/Volumes/Data`, no
  active swap (`system_disk`, `system_mount`). **Review M11:** mounts are collected from the **whole device tree** (partitions and the
  LUKS/LVM/RAID devices below them), so a system on a USB SSD with root on LUKS+LVM is the system disk even without a separate `/boot/efi`;
  on macOS the mount points of APFS volumes (which live on synthesized container disks) are attributed to the physical disk that holds the
  container's physical store, and the disk behind `/` (`diskutil info -plist /`) is always the system disk;
* no open crypt/LVM/RAID device on it (`in_use_by_volume_manager`): unmounting would not release it, so it is refused;
* writable (`read_only`); at least the usable size of a nominal 2 GB card (1.8 × 10^9 bytes, `os/storage.json`, DECISIONS #120; message
  "smaller than 2 GB" / "kleiner als 2 GB") and at least the image (`too_small_for_product`, `smaller_than_image`). 16 GB is recommended,
  never required. The tool writes and reads back the whole image (1092 MiB, including the empty slot B), so a card takes ~70 % longer
  than with the old 644 MiB layout;
* at most 256 GB unless the user ticks "also allow unusually large disks" (`unusually_large_confirm_explicitly`).

A disk with ordinary mounted partitions (for example `/media/user/BOOT`) is offered; the GUI says that its volumes will be unmounted, and
the helper unmounts them and **re-checks that nothing is mounted any more** before opening the disk.

Then: the **typed confirmation** (the disk's short name, e.g. `sdb`, `disk4`, `PhysicalDrive3`) and the **identity re-check** (path + size +
serial), done three times: by the GUI right before elevation, by the helper after elevation against its own fresh enumeration, and by the
writer against the opened device (the device must report the listed size).

Platform commands: `lsblk --json --bytes …`; `diskutil list -plist physical`, `diskutil info -plist <disk>`, `diskutil list -plist`,
`diskutil info -plist /`; PowerShell `Get-Disk | ConvertTo-Json`. Only the owner's machine runs them. **Tests feed recorded outputs and fail
if any process is started** (`no_subprocess` guard in `tests/flashtool` and `tests/flashtool_gui`).

## 3. Images: catalogue, download, verification
* **Source:** GitHub Releases of `iskaron/hifi-bridge` (DECISIONS #70), configurable in Settings (a mirror with the same JSON works). Per
  image `<base>` (`hifi-bridge-<compatible>-<version>.img`) a release carries `<base>[.xz|.gz]`, `<base>.manifest.json` and
  `<base>.manifest.json.sig`. The repository is **private today**: the default source answers 404, and the GUI says "not found or private –
  use your own image or set another source". Asset names from the network must be plain file names; JSON and manifests are size-limited;
  asset URLs other than http(s) are ignored.
* **Order:** manifest + signature first. An image whose manifest is not signed by a trusted key is refused **before** it is downloaded.
  Then the image is streamed into the cache as `<name>.part` (resumable, cancel keeps the partial file), then its size and SHA-256 are
  checked against the signed manifest. The writer hashes again while writing.
* **Trusted keys:** official keys ship in `hififlash/keys/` (empty until the offline release key exists, G4 K-1 – until then an official
  download is shown as "not from a trusted publisher"). A custom publisher key (self-built images) is trusted only after the user adds it
  in Settings: the SHA-256 fingerprint is shown and must be confirmed; such images are labelled **CUSTOM publisher** everywhere (image
  page, confirmation). Only ECDSA P-256 keys are accepted.

## 4. Privilege separation and the helper protocol
* The GUI never runs as root (it refuses). Only `hififlash-helper` is elevated, once per write: Linux `pkexec <helper> --stdio` with a
  polkit action that allows exactly that executable (`auth_admin`, not cached); macOS `osascript … do shell script "<helper> --connect …"
  with administrator privileges`; Windows `ShellExecuteExW("runas")` with a `requireAdministrator` manifest on the helper only.
* **Connect-back (macOS/Windows):** elevation does not keep stdio, so the GUI listens on 127.0.0.1 (random port) and writes a fresh 256-bit
  token into a 0600 file in a 0700 temporary directory. The helper reads it, connects and sends it first; any other connection is dropped.
  The token never appears on a command line.
* **Protocol** (`helper/protocol.py`): JSON lines, ≤ 64 KiB each. Exactly one request:
  `{"v":1,"op":"write","disk":{path,name,size,serial},"allow_large":bool,"image":"/abs/path","manifest":{…},"provisioning":"<base64>"|null}`,
  then only `{"op":"cancel"}`; EOF from the GUI also cancels. Strict validation: unknown fields, a path that is not a **whole disk of this
  platform** (`/dev/sd[a-z]`, `/dev/mmcblkN`, `/dev/diskN`, `\\.\PhysicalDriveN`; partitions, loop/NVMe devices, files and `rdisk` nodes
  are refused), a relative or device image path, an invalid manifest, or provisioning data that is not a valid, non-empty document of
  exactly the slot size → `bad_request`, nothing touched.
* **After validation** the helper enumerates again with its own adapter, requires the same identity, applies the full policy, prepares the
  disk (unmount / `diskutil unmountDisk` incl. APFS containers / lock+dismount volumes), opens it exclusively and checks the reported size.
  Errors: `disk_gone`, `disk_changed`, `disk_refused` (+ reasons), `unmount_failed`, `open_failed`, `image_unreadable`, `write_failed`,
  `cancelled`, `internal`; the GUI adds `elevation_refused`, `helper_died`, `protocol`. Locks are always released (`finish`).
* **What the helper does not decide:** which image. The user chose it, the GUI verified it; the helper verifies only that the bytes match
  the manifest it was given (and invalidates the card otherwise). The helper reads the image file as root/administrator – acceptable
  because the caller has just authenticated as an administrator (no privilege gain); it refuses device and non-regular files.

## 5. Write and verify
1. Refuse before writing: identity changed, image larger than the card, provisioning not slot-sized.
2. Stream in 4 MiB chunks with SHA-256 on the fly; size and hash must match the manifest.
3. Flush, then **read back** and hash again; write the provisioning slot and read it back.
4. **Any failure or cancel after the start invalidates the card** (first 4 MiB zeroed); if even that fails the message says so.
5. **Review M12 – the read-back must bypass the OS cache on real devices**, otherwise a fake-capacity card is "verified" from RAM.
   `BlockTarget.flush` = sync + drop the cache: Linux `fsync` + `BLKFLSBUF` + `POSIX_FADV_DONTNEED` (the disk is opened with `O_EXCL`);
   macOS the raw node `/dev/rdiskN` with `F_NOCACHE`, `fsync` + `DKIOCSYNCHRONIZECACHE`; Windows `FILE_FLAG_NO_BUFFERING |
   FILE_FLAG_WRITE_THROUGH` with sector-aligned I/O (read-modify-write for the unaligned tail and the slot). A test with a page-cache fake
   in front of a fake-capacity card shows that the read-back detects it **only because** the cache is dropped. All read-back evidence in
   this repository is **SIMULATED** (regular files and fakes); real cards are G3 F-3/F-5.

## 6. Per-device data and the owner sheet
| Data | Generation | Owner sheet | On the card |
|---|---|---|---|
| Setup code | 12 symbols, 31-symbol alphabet (`secrets`) | yes | yes (until scrubbed) |
| Setup hotspot SSID / password | `Hi-Fi Bridge XXXX` / 20 symbols (~99 bits) or the owner's own (12–63 printable) | yes | yes |
| Trust key | 32 random bytes | **never** | yes (until scrubbed) |
| Home Wi-Fi SSID / password (optional) | entered by the owner | SSID only, **never the password** | yes (PROVISIONING §4) |
| Country (required, no guessing), device name, network name (optional) | entered | yes | yes |

The form uses the device's validators (`netconfig.ssid_ok / wifi_psk_ok / hotspot_psk_ok / country_ok`, `schema` for names); the final
document passes `btbridge.provisioning.validate`. New secrets are generated each time the setup page is left. The owner sheet (DE/EN) is
shown after writing and can be printed or saved as PDF/text (files 0600). The trust key stays in memory only until the device is verified
and is then discarded.

## 7. After writing: setup instructions and P7
The done page shows the owner sheet and the first-start steps (insert, power, `https://<name>.local`, setup hotspot `10.42.0.1`, setup code,
trust). **Verify device:** `GET http://<address>/trust/proof` with a fresh nonce, HMAC check with the trust key, then `https://<address>`
with **only** that CA and host-name checking (docs/TRUST_BOOTSTRAP.md). Only then the fingerprint is shown as verified, "Save certificate"
(PEM, for installing on computers and phones) and "Open the device's web page" are enabled. The system browser does not trust the CA by
itself: the owner installs the saved certificate or compares the fingerprint. Fallback without the tool: the fingerprint in
`hifi-status.txt` on the card.

## 8. Design, languages, accessibility
* "Hi-Fi & elegant" (DECISIONS #9): the **same colour tokens as the WebUI** (`app.css`, checked by a test), serif display type, the same
  logo file; light, dark and "like the system" (follows `QStyleHints.colorScheme` live).
* German and English: `gui/i18n/{en,de}.json`; tests require the same keys and placeholders, every used key to exist and no unused key.
  The language switches live.
* Accessibility (tested): every control has an accessible name or a buddy label; keyboard-only operation (Space/Tab, focus order: page →
  Next → header); WCAG contrast ≥ 4.5:1 for text tokens on every background in both themes, ≥ 3:1 for the focus ring. Not tested: real
  screen readers (NVDA, VoiceOver, Orca).
* A **SIMULATION** badge is shown whenever the tool runs with `--simulate` (all screenshots).

## 9. Packaging (`flashtool/packaging/`)
| OS | What exists | Executed here? |
|---|---|---|
| Linux | PyInstaller **one-folder** spec (GUI + Qt-free helper), licence collection, `.desktop`, polkit policy `io.github.iskaron.hifibridge.flash.policy`, `install.sh` (DESTDIR-aware), tarball + SHA-256 | **built and smoke-tested in Docker** (`scripts/run_flashtool.sh package`): GUI `--self-test` offscreen, the frozen helper writes a simulated card over stdio, `install.sh` into a scratch DESTDIR. Not run on a desktop, polkit prompt not exercised |
| Windows | same spec (`uac_admin`, `windows/hififlash-helper.manifest`), Inno Setup script `windows/installer.iss` (signing placeholder), ctypes bindings `platforms/win32api.py` | **prepared only** – no Windows runtime; not compiled, not signed |
| macOS | same spec with `BUNDLE` (.app), `macos/build.sh` (build + offscreen smoke tests on a Mac), `macos/sign_and_notarize.sh` (inside-out Developer ID signing, hardened runtime, notarytool, staple, `.dmg`), empty `macos/entitlements.plist`; manual GitHub Actions workflow `flashtool-macos.yml` (DECISIONS #126, docs/MACOS_SIGNING.md) | **prepared only** – no macOS runtime; workflow never run; not built, not signed, not notarised |

No signing key or certificate is in the repository; signing is an owner task (G3 F-6). macOS: Developer ID + notarisation on GitHub
Actions with the owner's certificate in the protected environment `macos-signing` (setup guide docs/MACOS_SIGNING.md); Windows: open. AppImage/Flatpak were not chosen: the tarball +
installer keeps pkexec's exact-path policy working (an AppImage mounts at a random path).

## 10. Tested vs. not tested
| Statement | Evidence |
|---|---|
| Policy offers only the SD card in recorded lsblk/diskutil/PowerShell outputs, incl. LUKS+LVM root, swap, APFS boot disk (M11) | UNIT, no command executed |
| Catalogue/download/resume/cancel/errors; signature/board/hash checks; custom publishers | UNIT/INTEGRATION (loopback HTTP) |
| Helper protocol validation, post-elevation checks, every error code, cancel and GUI-EOF invalidation | UNIT/INTEGRATION (real server + client) |
| Linux/macOS/Windows adapters end to end through the helper (unmount, exclusive open, cache drop, lock/dismount, alignment) | UNIT with recorded outputs and fakes; the "device" is a regular file – **SIMULATED** |
| Read-back detects a fake-capacity card behind a page cache (M12) | UNIT, **SIMULATED** |
| Whole wizard incl. refusals, swap, removal, failure, cancel, refused elevation, close while writing, owner sheet, P7 | GUI (pytest-qt, offscreen) on simulated cards |
| DE/EN completeness, WebUI tokens, contrast, accessible names, keyboard | GUI |
| Linux package builds and starts; frozen helper writes a simulated card | PACKAGE smoke test (Docker) |
| Real disks, real elevation (pkexec/osascript/UAC), Windows/macOS builds, screen readers, real devices for P7 | **NOT TESTED** – G3 F-1…F-6 |
