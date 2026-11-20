# Hardware test procedure (the concrete sequence for the first hardware day)

**Status: prepared, NOT executed.** This is gate **G1** of docs/HARDWARE_GATE_CHECKLIST.md. Everything needed is in `hwkit/` (runbook `hwkit/README.md`). The test IDs come from
`docs/HARDWARE_VALIDATION_PLAN.md`. Every result entered with the kit is labelled `REAL-HARDWARE`, and the kit refuses anything else.

## 0. Bring
* Raspberry Pi 3 B+ (reference), power supply, microSD card (16 GB recommended; also one nominal 2 GB card for P4-1, DECISIONS #120), Ethernet cable. Optional: Pi Zero 2 W for E5.
* One Android phone and one iPhone, with a music app, a messenger, navigation and YouTube installed.
* One target player: Squeezelite on a PC or Pi, or a real Squeezebox (note model and firmware).
* Access to a **test** LMS, or the owner's LMS used read-only until F4 (plan §0).
* For E2/F3: a USB microphone on the Pi, placed about 30 cm in front of the speaker.

## 1. Prepare (about 45 min)
1. On the development machine (Docker): run `scripts/hwkit_bundle.sh`. It produces `dist/hwkit-bundle-<rev>.tar.gz` and `dist/hwkit-tracks/`.
2. Copy the `.m4a` test tracks to both phones.
3. Flash a stock **Raspberry Pi OS Lite** (not the appliance image) and copy the bundle to the Pi.
4. On the Pi: `sudo sh hwkit/pi-setup/setup_test_pi.sh --i-am-on-the-test-pi`, then reboot.
5. On the Pi: `PYTHONPATH=$PWD/bridge:$PWD/hwkit python3 -m hwkit preflight`. Fix every **FAIL** before you continue. Each check prints
   its own fix. It also shows which capture tool the bridge would use there (`pw-cat --raw` on PipeWire ≥ 1.x, otherwise `parec`).
6. Start the bridge on the Pi: `python3 -m btbridge.appliance --data ~/bridge-data --source bluez --boot-dir /boot/firmware`
   (Raspberry Pi OS: Avahi runs already, so the name is published through it; `hifi-status.txt` on the boot partition shows the CA fingerprint.
   `/boot/firmware` belongs to root: without write access the bridge reports "boot files NOT processed" and starts anyway; the console log
   then has the setup-code path and the fingerprint). Open `http://<pi>:8080/`, enter the setup
   code from `~/bridge-data/setup-code.txt`, and finish the wizard with the test LMS and the test player.
7. Create the results file: `python3 -m hwkit results new results.json --board "<model rev>" --operator <name> --phone "<model / OS>" ...`,
   then `python3 -m hwkit env -o env.json`.

## 2. Run, in this order (about 4 h; `python3 -m hwkit guide results.json --address <phone MAC> [--mic <source>]`)
| Block | IDs | Why first | Time |
|---|---|---|---|
| Bring-up | A1, A2, A3, A4, A5 | Without a working capture node nothing else is meaningful. A3/A4 settle the open PipeWire questions. | 45 min |
| Pairing | B1, B2, B3, B4, B6, B7 | Guest experience and the single-source policy | 40 min |
| Media traces | C1 (Android), C2 (iPhone) | Notification vs. music: the main open software risk | 60 min |
| Volume | D1, D2 | Absolute volume per phone | 20 min |
| Audio quality and timing | E1 (30 min capture), E2 (10 × play/pause with mic) | First real numbers | 50 min |
| Players | F1, F2 (each real Squeezebox) | Compatibility list, ICY decision | as needed |
| Guest | G1 | The product goal | 15 min |
| Owner access | H1, H2, H3 (H4 if time) | name resolution, trusted HTTPS, home-screen shortcut on real phones | 30 min |

The rest (A6, A7, B5, C3, D3, E3–E6, F3–F5) goes on a second day or follows the decisions from day one.

**Stop rules:**
* Stop and report if A3/A4 fail, i.e. no capturable phone node exists. The capture design would need to change; fallback candidates are
  BlueZ `MediaEndpoint1` with in-process SBC decoding, or `pw-cat`.
* Stop and report if B4 shows that a second phone evicts the first.
* Never test against the production LMS beyond read-only observation without the owner's explicit consent (plan §0, F4).

## 3. Analyse (development machine, Docker)
```bash
docker run --rm -v "$PWD":/work -w /work btbridge-hwkit:dev python3 -m hwkit analyze <capture dir>
docker run --rm -v "$PWD":/work -w /work btbridge-hwkit:dev python3 -m hwkit replay <trace.jsonl>
docker run --rm -v "$PWD":/work -w /work btbridge-hwkit:dev python3 -m hwkit latency <trace.jsonl> <capture dir>
python3 -m hwkit results check results.json && python3 -m hwkit results report results.json > REPORT.md
```

## 4. Bring back (for the next handoff)
`results.json`, `env.json`, `REPORT.md`, and `sessions/**` (traces, captures, `pw-dump`). Traces are anonymised by default. Do not include setup
codes, passwords or the stream token. Real traces become regression fixtures in `tests/fixtures/traces/`, labelled REAL-HARDWARE.
