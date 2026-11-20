# First-setup provisioning (P6, DECISIONS #56, #62)

Status: format, validation, device-side processing and the flash tool's generator are implemented and tested (UNIT; INTEGRATION with a
real genimage card layout, mtools reading back through FAT, real HTTP/TLS). The network service that starts Wi-Fi or the hotspot from `network.json` is implemented (docs/NETWORK.md; UNIT,
INTEGRATION with the real network tools in Docker). **Not tested:** the real vfat kernel driver on a Pi, a real SD card, and the network
service on a real Wi-Fi radio.

## 1. Rule
**There is no setup hotspot without an individual password that the owner knows.** The password comes from the provisioning file, which
is created either by the flash tool (generated and shown to the owner) or by the owner by hand. Without a valid provisioning file the device
**never opens a hotspot**. It is then reachable over Ethernet only, and the one-time setup code is in `hifi-status.txt` on the boot
partition. There is no shared or factory password anywhere.

## 2. The file
`hifi-provision.json` in the root of the FAT boot partition, format `hifi-bridge-provision/1`, UTF-8 JSON. The image contains it as a
4096-byte placeholder (`{"format":"hifi-bridge-provision/1","state":"empty"}` padded with spaces). This lets the flash tool write it in
place on the raw card at the offset given in the image manifest, without mounting the file system.

| Field | Required | Rule |
|---|---|---|
| `format` | yes | `hifi-bridge-provision/1` |
| `setup_code` | yes | 12 characters from `ABCDEFGHJKMNPQRSTUVWXYZ23456789` (dashes and spaces ignored) |
| `trust_key` | no (flash tool: yes) | 32 random bytes, base32 without padding (P7, docs/TRUST_BOOTSTRAP.md) |
| `device_name`, `local_hostname` | no | same rules as the settings |
| `country` | when `hotspot` or `wifi` is present | ISO 3166 alpha-2, upper case, not `00` (regulatory domain) |
| `hotspot` | no | `{"ssid": 1–32 bytes, "psk": 12–63 printable ASCII}` |
| `wifi` | no | `{"ssid": 1–32 bytes, "psk": 8–63 printable ASCII or 64 hex}` |
| `created_by` | no | ≤ 64 characters |

Unknown fields are rejected (this catches typos). Problems are reported **by field and reason only, never with values**, because values
can be secrets.

### Manual bootstrap (without the flash tool)
1. Open the boot partition of the freshly written card on a computer (it is FAT; every OS can read it).
2. Replace the content of `hifi-provision.json`, for example:
   ```json
   {"format": "hifi-bridge-provision/1", "setup_code": "K7QM-2XPA-RT9H", "country": "DE",
    "hotspot": {"ssid": "Hi-Fi Bridge Setup", "psk": "choose-at-least-12-chars"}}
   ```
   Choose your own setup code from the alphabet above. You can check the file first:
   `python3 -m btbridge.provisioning check < hifi-provision.json`.
3. Eject the card properly, insert it into the Pi and power it on.

## 3. What the device does (`btbridge.bootfiles`, unit `hifi-bridge-boot.service`, before the bridge starts)
1. Validate strictly. If invalid, apply **nothing**, overwrite the file anyway (it may contain secrets), and report the field problems in
   `hifi-status.txt`.
2. Apply under a journal in the data partition (`provision-journal.json`): `applying` → apply → `applied` → scrub → `done`.
   * The setup code becomes the device's setup code. Its plaintext is **not** stored, because the owner already has it. If an owner password
     already exists, the code is ignored and the status says so; use recovery (P8) instead.
   * The trust key is stored as a device secret until the owner password is set.
   * `country`, `hotspot` and `wifi` go to `network.json` (mode 0600) in the data partition, for the network agent `hifi-netd`
     (docs/NETWORK.md). The file format is `hifi-bridge-network/1` (`btbridge.net.netconfig`); the provisioning output is a valid subset,
     Ethernet and Wi-Fi addresses default to DHCP / automatic IPv6. Re-provisioning replaces the whole network part.
3. **Scrub:** overwrite the whole file **in place** with the placeholder (same length), then `fsync` the file and the directory. The name
   stays, so the slot remains.

### First setup without a working home network (review N-1, package 7b-1; wizard network step since DECISIONS #122)
A card written **without** the home Wi-Fi password (recommended in section 4) or with a mistyped one, and no Ethernet: the device opens
its setup hotspot (name and password from the card; `hifi-status.txt` names it and `http://10.42.0.1/`).
1. Join the hotspot with the phone, open `http://10.42.0.1/`, enter the setup code and choose the owner password (as always).
2. The wizard continues with its **network step** (docs/NETWORK.md section 12): choose the home Wi-Fi from the list (or enter a hidden
   network's name), type its password. Above *Connect* the step explains what happens next and shows the address to open afterwards
   (`http://<local_hostname>.local/`). A mistyped password from the card is shown as "last problem" and corrected here.
3. *Connect*: the hotspot closes and the page loses its connection. Connect the phone to the home Wi-Fi, open the shown address (or the
   one the router lists), log in: the wizard opens at the network step with "Keep the new network settings?" - press **Keep** within
   3 minutes; the wizard continues (Bluetooth name, Lyrion server, player).
4. If the device cannot join (wrong password, network not found) the change is undone after about a minute, or after 3 minutes without
   *Keep*; the hotspot returns. Rejoin it and reload `http://10.42.0.1/`: the step shows the reason - try again, or skip the step.
With Ethernet the step only confirms "Connected via Ethernet as <address>" and offers Wi-Fi as an option. **Settings → Network** stays
reachable before the wizard is finished (DECISIONS #105), e.g. for fixed addresses.

| Aspect | Finding | Measure |
|---|---|---|
| **Residual data in the file system** | Deleting a file on FAT only marks the directory entry; the clusters keep the data. | We do **not** delete; we overwrite the same bytes in place. On vfat an in-place write of the same length does not reallocate clusters. **Assumption, to be verified on a Pi** (gate item P6-3): read the raw card afterwards and search for the secret. |
| **Residual data in the card's flash (FTL)** | SD cards remap blocks internally (wear levelling); overwritten data may physically survive and could be read with lab equipment. Software cannot prevent this. | Keep the secrets **short-lived**: the setup code and the trust key are worthless once the owner password is set; the hotspot password only protects the setup hotspot; the owner can change it. The home Wi-Fi password is the only long-lived secret. **GUI requirement (not implemented yet):** the flash tool must say so and offer to leave it out, to be entered later in the WebUI over HTTPS (**Settings → Network**, docs/NETWORK.md). |
| **Hand-edited files** | Editors may write a new file and delete the old one, which leaves the old clusters behind. | Documented in §2; the manual path is for owners who accept it. The flash tool writes in place. |
| **Takeover** (someone else gets the card or the device first) | Physical access to the card equals authority by design (P8). A person holding the card before the owner can read the setup code. | The owner flashes the card themselves. After setup, the provisioning secrets are useless. Recovery requires physical access again. |
| **Network takeover during setup** | A LAN attacker cannot read the card. The hotspot is WPA2/WPA3 with an individual password. | No open hotspot ever. |
| **Failure: power cut while applying** | Journal state `applying` → the next boot applies everything again (idempotent). | UNIT test. |
| **Failure: power cut while scrubbing** | The file is partly overwritten → invalid → the journal says `applied` → only the scrub is finished. | UNIT test. |
| **Failure: invalid file** | Nothing applied; the file is still scrubbed; the problems are reported. | UNIT test. |
| **Failure: provisioning never processed** (e.g. wrong card) | The file remains on the card with its secrets. | **GUI requirement (not implemented yet):** tell the owner to boot the card promptly; the trust verification (P7) also confirms that the device has applied it. |
| **Status file** | `hifi-status.txt` never contains provisioned secrets. It contains a **generated** setup code only while no owner password exists. | UNIT test scans the status for every secret. |

## 5. Open
* Gate items: in-place overwrite on real vfat and a real card (P6-3); the hotspot with a per-device password on the on-board Wi-Fi (P6-1);
  "client Wi-Fi OR hotspot, never both" (P6-2).
* The network service that consumes `network.json` is implemented (docs/NETWORK.md); its hardware items are N-1 … N-11 there.
* Recovery: a boot-partition request `network-reset` forgets the client Wi-Fi and every fixed address but keeps the country and the
  hotspot credentials (docs/NETWORK.md section 10).
