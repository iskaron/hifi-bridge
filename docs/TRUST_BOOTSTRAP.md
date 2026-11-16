# Out-of-band trust in the device certificate (P7, DECISIONS #57, #63)

Status: implemented and tested. The device side is `/trust/proof`; the flash-tool side is `hififlash.trust`; both sit on the shared
`btbridge.trustproof`. INTEGRATION covers real HTTP and real TLS on localhost, a LAN attacker that swaps the CA, the wrong key, replay and
the end of setup. **Not tested:** real phones, real home networks, and the GUI step that installs the CA into the OS trust store.

## 1. The contradiction that is removed
The earlier proposal said the flash tool would show the CA fingerprint. The CA is created **on the device at first boot**, and its key must
never leave it (DECISIONS #57). At flash time the fingerprint therefore **cannot exist**.

The fix: the flash tool does not know the fingerprint in advance. It **learns it afterwards over the network, authenticated by a secret it
placed on the card itself.**

## 2. Flow
1. **Flash time** (owner's computer): the flash tool generates a random 32-byte **trust key** and writes it into the provisioning file on
   the card, together with the setup code (docs/PROVISIONING.md). The trust key is never shown to anyone.
2. **First boot** (device): `hifi-bridge-boot.service` takes the trust key into the device's secrets and creates the CA (private key 0600
   in the data partition). It writes the fingerprint to `hifi-status.txt`.
3. **Verification** (flash tool, same session or later; the owner's computer on the same LAN):
   1. `GET http://<device>/trust/proof?nonce=<128-bit random hex>`.
   2. The device answers with its CA certificate (DER), its fingerprint, and
      `mac = HMAC-SHA256(trust key, "hifi-bridge-trust/1" \n nonce \n SHA-256(CA DER))`.
   3. The tool recomputes the mac with its trust key. **Only the real device knows the key**, so a LAN attacker who substitutes its own CA
      cannot produce a valid mac. An old answer fails because of the fresh nonce.
   4. The tool then opens `https://<device>/healthz`, trusting **only** this CA, with host-name checking. This proves that the HTTPS
      endpoint really uses a certificate from the authenticated CA.
4. The tool shows **"verified"** and the fingerprint. **GUI requirement (not implemented yet):** offer to install the CA on this computer
   and export it for phones (QR/file).
   The owner compares the fingerprint on the phone's trust page with the one the tool shows (verified), not with something received over
   plain HTTP.
5. **End:** the device deletes the trust key when the owner password is set. `/trust/proof` then answers 404.

## 3. Why this is sound, and its limits
* HMAC-SHA256 from the standard library. There is no own cryptography; the CA itself is made with OpenSSL (DECISIONS #50).
* The answer contains nothing secret. The endpoint is public, but it is useless without the key, and it exists only until setup is done.
* The trust key travels only on the physical card. Its residual-data risk is bounded by its short lifetime (docs/PROVISIONING.md §4). An
  attacker would need both the key (from the card) **and** a MITM position at the right moment.
* **Fallbacks without the flash tool:**
  * the fingerprint in `hifi-status.txt` (re-insert the card into any computer and read the file);
  * the device console.

  These are as strong as physical access, but the owner has to compare manually.
* **Not covered:** an attacker who already controls the device (out of scope, docs/LOCAL_ACCESS_TLS.md §1).

## 4. Evidence
| Check | Test |
|---|---|
| Genuine answer verifies; substituted CA, replay with another nonce, wrong key and wrong fingerprint are rejected | `tests/unit/test_trustproof.py` |
| Endpoint only while the trust key exists; 400 on a bad nonce; `Cache-Control: no-store` | same, real HTTP |
| Card → bootfiles → WebUI → tool verifies → HTTPS against only that CA → setup ends the key | `tests/flashtool/test_provision_and_trust.py` |
| A relaying LAN attacker that swaps the CA is detected ("could not prove") | same |
