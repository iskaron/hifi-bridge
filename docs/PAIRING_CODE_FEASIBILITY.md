# Owner-defined pairing code — feasibility (feature B; analysis only, NOTHING prototyped, NOTHING implemented)

Status 2026-11-25. Source: this repository's agent (`bridge/btbridge/adapters/bluez/adapter.py` l.76, 185–190:
`NoInputNoOutput` agent, authorises inside the 5-minute window only), Bluetooth Core (Secure Simple Pairing association model
table) and BlueZ's `Agent1` API from the maintainers' documentation as I know it. **Not verified on any device in this session**:
the statements about what the Pixel/iPhone do are expectations to be tested, marked *(to test)*. No pairing, no BlueZ
setting, no device was touched; the dev host has no Bluetooth (CLAUDE.md).

## 1. The request

A 4–6 digit code **chosen in the WebUI** and **typed on the phone** when a *new* phone pairs; blank = today's behaviour exactly.
Hard rule: never silently fall back to Just Works.

## 2. Why a phone-side static code is very probably not possible with modern phones

1. **BR/EDR pairing is SSP unless a side lacks it.** Secure Simple Pairing (Core 2.1+) is used whenever both controllers support it —
   every current phone and a BlueZ adapter with SSP on (default). The association model comes from the two **IO capabilities**
   (Core Vol 3 Part C 5.2.2.6): numeric comparison (both DisplayYesNo), passkey entry (one side keyboard, the other displays),
   out-of-band, **Just Works** (anything with NoInputNoOutput, and DisplayOnly with DisplayYesNo). Today the bridge says
   `NoInputNoOutput` → Just Works.
2. **In passkey entry the displaying device generates the 6-digit number** (random per pairing; BlueZ hands it to the agent in
   `DisplayPasskey(device, passkey, entered)` — the agent cannot choose it). So with SSP the phone user can only type a code that the
   *bridge displays and generated*, or the owner types the code the *phone shows* into the WebUI (`RequestPasskey`, capability
   `KeyboardOnly`/`KeyboardDisplay`). **A fixed, admin-chosen code that the phone user must type does not exist in SSP.**
3. **A fixed code exists only in legacy PIN pairing** (`Agent1.RequestPinCode`, 1–16 characters), which BlueZ uses only when the
   remote or the local controller has SSP **off**. That means turning SSP off on the Pi's controller (`btmgmt ssp off`/adapter
   power cycle; kernel-level, affects every pairing and possibly A2DP/AVRCP security expectations). Phones may still offer a PIN
   dialog for legacy accessories *(to test: Pixel, iPhone)* — but: a 4–6 digit PIN protects against brute force only weakly (the
   PIN is the only secret; an eavesdropper of the pairing can try all values offline), there is no MITM protection comparable to
   numeric comparison, Android/iOS may refuse or warn, and SSP-off is a global downgrade of the product's security. Newer
   Bluetooth specs (and some OS policies) discourage or forbid it. **Not recommended**, and unacceptable as an *invisible*
   fallback.

Conclusion: *do not implement "a code the phone user enters" as a promise*. What can be built honestly is below.

## 3. Options that are real

| Option | What the user sees | Security | Mechanism | Hardware-proof needed |
|---|---|---|---|---|
| **A. Owner approval in the WebUI** (recommended, smallest) | Phone pairs as today (Just Works); the WebUI shows *"<device name> wants to pair — Allow / Deny"* for 60 s; nothing is bonded before "Allow" | stops a stranger in range pairing during the open window; the owner sees the name | hold `RequestAuthorization`/`RequestConfirmation` until the WebUI answers; reject on timeout/deny | one Pixel + one iPhone: confirm BlueZ calls the agent for Just Works and that a held reply keeps the phone's dialog open |
| **B. Numeric comparison confirmed in the WebUI** | WebUI shows the 6-digit number; the phone shows its own; owner compares and taps Allow; guest taps *Pair* | MITM-protected, authenticated link key | agent capability `DisplayYesNo`; `RequestConfirmation(device, passkey)` answered from the WebUI. OPEN_QUESTIONS #102 | the same two phones: does each phone select numeric comparison against `DisplayYesNo` |
| **C. Passkey the owner reads from the phone** | the phone displays a random 6 digits, the owner types it into the WebUI | MITM-protected | capability `KeyboardOnly` → `RequestPasskey` | phone behaviour *(to test)*; awkward UX |
| **D. Legacy PIN with a fixed code** | phone asks for a PIN | weak (§2.3) | SSP off + `RequestPinCode` | everything; **not recommended** |

Strict mode rule for A–C: when the owner enabled "require approval/confirmation", the agent **never auto-accepts**; a phone that
negotiates something the agent cannot hold (e.g. a headset-style NoInputNoOutput that BlueZ completes without calling the agent) must be
rejected with a visible reason *"this device cannot be confirmed"*, not accepted. Whether BlueZ calls the agent in every case
is exactly what the prototype must establish.

## 4. Requirements mapping

| Requirement | Plan |
|---|---|
| blank = unchanged | default `pairing_mode = open` keeps today's agent byte-for-byte; a test pins it |
| affects new pairing only | the policy lives in the agent callbacks for *unbonded* devices; `Device1.Trusted/Paired` devices reconnect without agent calls; test: bonded device reconnects with the policy on |
| exclusive one-phone policy, 5-min window | unchanged; approval timeout (60 s) < window; closing the window cancels pending requests |
| no playback interruption | pending approval is only pairing-side; no change to the audio path; test: stream continues during a request |
| delete bond → immediate disconnect | unchanged code path (already specified in DECISIONS) |
| secret handling | A/B: no stored secret at all (one-time number in memory, never logged). D would store a PIN: hashed with a per-device salt, never logged |
| attempt limit | max 3 denied/expired requests per window, then the window closes; per-address back-off |
| first-run wizard, backup/restore, factory reset | mode is a normal config key: wizard default `open`; included in backup (no secret); reset → `open` |
| logs | agent logs events (`pair_request`, `allowed`, `denied`, `timeout`), never the passkey |

## 5. Operator-run feasibility prototype (not run; to be commissioned)

Tightly scoped, on the hardware-test Pi only, by the operator, one phone at a time, **no existing bond touched** (use a phone that is
not currently paired or a fresh adapter alias; remove only that test bond on the Pi):

1. `btmon` running on the Pi; bridge pairing window opened from the WebUI.
2. Variant 1: agent capability as today (`NoInputNoOutput`). Pair the Pixel. Record from `btmon`: *IO Capability Request/Response*,
   resulting association model, whether `RequestAuthorization`/`RequestConfirmation` reached the agent.
3. Variant 2: `DisplayYesNo` (B). Same recording; does the phone show a number; does the Pi receive `RequestConfirmation(passkey)`.
4. Variant 3: `KeyboardOnly` (C): does the phone display a passkey and the Pi receive `RequestPasskey`.
5. Variant 4 (D): *only if the owner explicitly asks*: SSP off, `RequestPinCode`. This changes the Pi controller's security state
   for the experiment's duration and must be reverted; I would rather not run it.
6. Repeat 2–4 with the iPhone. Output: a table phone × variant → association model, dialog text, success. Acceptance of the feature
   depends on it.

## 6. Recommendation

1. Treat feature B as **separate from G1-S**; do not touch pairing while the audio problem is open.
2. Implement **option A** (owner approval in the WebUI), with option B as the "stricter" mode, after the prototype table shows both phones
   behave; both are testable hardware-free with the existing fake BlueZ agent harness (`testkit/btkit/fake_bluez.py`).
3. Do not ship a static phone-side code (option D). If the owner insists, record the decision in DECISIONS with the downgrade stated.
4. Operator acceptance test (after implementation): pair a new phone with the mode on → no bond until "Allow"; deny/timeout → no bond
   and the phone reports failure; a bonded phone reconnects without any prompt; delete the bond → immediate disconnect; window closes at 5 min.
