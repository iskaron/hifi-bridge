# Flash and provisioning tool (P9, DECISIONS #59)

Status: **core logic only** (`flashtool/hififlash`), tested with recorded tool outputs and **simulated cards (regular files)**. There is no
GUI yet, no installer, and **no code path that writes a real disk**. The development machine never runs disk-enumeration commands and
never writes a host disk (CLAUDE.md).

## 1. Architecture
```
GUI (PySide6, LGPL-3.0, planned)      - wizard, DE/EN, light/dark, "Hi-Fi & elegant"; no logic of its own
 └─ hififlash (pure Python, tested)
     ├─ disks       parse lsblk / diskutil / PowerShell output; safety policy; typed confirmation; identity re-check
     ├─ manifest    image manifest; ECDSA P-256 signature check against trusted publisher keys (python-cryptography)
     ├─ writer      stream (raw/.xz/.gz) -> write -> hash -> flush -> read back -> provisioning slot -> verify; cancel; invalidate
     ├─ provision   per-device secrets (setup code, hotspot password, trust key); owner sheet DE/EN
     ├─ trust       P7: authenticate the device CA with the trust key; verify HTTPS against only that CA
     └─ fatslot     locate the provisioning slot inside the image's FAT (FAT12/16/32, long names) - used at image build time
 shared with the device: btbridge.provisioning (format + validation), btbridge.trustproof (HMAC proof)
```

Command line (test day, development; **no write command exists**): `python3 -m hififlash provision | verify-device | verify-image`.

## 2. Safe disk selection (`hififlash.disks`)
A disk is offered only if **all** of these hold. Every refusal has a reason code that the GUI shows:
* whole disk, removable or USB/SD/MMC transport (`not_removable`);
* not the system or boot disk, nothing mounted at `/`, `/boot`, `C:\`, `/System/Volumes/Data` … (`system_disk`, `system_mount`);
* writable (`read_only`);
* at least `MIN_BYTES` (1 GB, to be confirmed by measurement) and at least the image size;
* at most `MAX_BYTES` (256 GB) unless the user explicitly confirms a "large disk" (`unusually_large_confirm_explicitly`). A 2 TB "USB
  disk" is almost certainly not an SD card.

Then:
* **Typed confirmation:** the user types the disk's short name (`sdb`, `disk4`, `PhysicalDrive3`). A single click is never enough.
* **Identity re-check** directly before writing (path + size + serial). A swapped card is refused, and nothing is written.

Platform commands (`COMMANDS`): `lsblk --json --bytes …`, `diskutil list -plist physical` plus `diskutil info -plist`, and PowerShell
`Get-Disk | ConvertTo-Json`. Only the GUI on the owner's machine runs them. Tests feed recorded outputs and **fail if any subprocess is
started**.

## 3. Write and verify (`hififlash.writer`)
1. Verify the manifest signature (official key built in; a key for self-built images only after explicit addition, shown as "custom
   publisher").
2. Refuse before writing: the identity changed, the image is larger than the card, or the provisioning data does not fit the slot.
3. Stream in 4 MiB chunks with SHA-256 on the fly. Size and hash must match the manifest (catches a damaged or modified download).
4. Flush, then **read back** and hash again (catches fake-capacity cards and write errors).
5. Write the provisioning document at the manifest's slot offset and read it back.
6. **Any failure or cancel after writing started invalidates the card:** the first 4 MiB are zeroed, so a half-written image never boots.
   If even that fails, the message says the card must not be used.

A real block-device target, needed for the GUI, must additionally:
* unmount the card's partitions;
* open the device exclusively (`O_EXCL` / `FSCTL_LOCK_VOLUME` / `diskutil unmountDisk` + `/dev/rdiskN`);
* drop privileges where possible;
* re-check the identity after elevation.

This is **not implemented** in this package.

## 4. Per-device data (`hififlash.provision`)
| Data | Generation | Shown to the owner | On the card |
|---|---|---|---|
| Setup code | 12 symbols from a 31-symbol alphabet (`secrets`) | yes (owner sheet) | yes (until scrubbed) |
| Hotspot SSID / password | `Hi-Fi Bridge XXXX` / 20 symbols (~99 bits) or the owner's own (≥ 12 characters) | yes | yes |
| Trust key | 32 random bytes | **no** | yes (until scrubbed) |
| Home Wi-Fi (optional) | entered by the owner | no (the owner knows it) | yes (long-lived secret, see PROVISIONING §4) |

## 5. Licences and packaging (to do with the GUI)
* **PySide6 / Qt: LGPL-3.0.** Link dynamically; ship the licence texts and a notice that Qt can be replaced. Do not use static freezing
  that prevents relinking. With PyInstaller "one-folder" builds the Qt libraries stay replaceable; document this.
* **python-cryptography:** Apache-2.0/BSD; it bundles OpenSSL (Apache-2.0).
* Installers: Windows (signed MSI/EXE), macOS (signed + notarised app), Linux (AppImage or Flatpak). Code-signing certificates are an owner
  decision and cost.
* **Privileges:** writing raw disks needs admin or root. Elevate a minimal helper only for the write step (GUI unprivileged).

## 6. Tested vs. not tested
| Statement | Evidence |
|---|---|
| Policy offers only the SD card in realistic lsblk/diskutil/PowerShell outputs; refusal reasons; typed confirmation; identity | UNIT, no command executed |
| Write + read-back + slot; xz/gz; cancel; I/O error; fake card; tampered image; swapped card; device nodes refused | UNIT on simulated cards |
| A real genimage card image → simulated card → provisioning readable through FAT (mtools) | INTEGRATION (`tests/os`) |
| Manifest signed with the openssl CLI is verified by python-cryptography; other key, modification and RSA are refused | UNIT |
| P7 flow incl. attacker | INTEGRATION (real HTTP/TLS) |
| Writing real SD cards on Windows/macOS/Linux, GUI, installers, privilege elevation | **NOT TESTED / NOT IMPLEMENTED** – gate items F-1…F-6 |
