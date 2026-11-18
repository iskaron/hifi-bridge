# Local access and HTTPS – concept and implementation (DECISIONS #35)

Status: implemented hardware-independently and tested (UNIT/INTEGRATION; real TLS clients, real Avahi, real WebKit/Chromium). Not tested on a Pi,
real phones' browsers or real home networks.

## 1. Goals and non-goals
* The owner reaches the device under a **stable name** (`hifi-bridge.local`) and over **HTTPS** without a public domain, cloud or certificate
  authority on the internet.
* **No silent TLS exceptions.** No component of the product or of its tests disables certificate verification.
* **mDNS creates a name, not trust.** Anyone on the LAN can answer mDNS queries. Trust comes only from a certificate the owner has knowingly
  accepted.
* Not a goal: protection against an attacker who already controls the device (root on the Pi).

## 2. Names
| Mechanism | Implementation | Evidence |
|---|---|---|
| mDNS host name `<local_hostname>.local` (default `hifi-bridge`) | **Avahi preferred (DECISIONS #49):** `net/avahi.py` publishes an own entry group via the system's avahi-daemon (D-Bus; no root needed; collision -> next name; `Free` = goodbye). Built-in stdlib responder (`net/mdns.py`) only when no Avahi daemon is reachable (`mdns_backend=auto`) or explicitly chosen | real Avahi peer: built-in 7/7 and Avahi backend 6/6 (`scripts/test_mdns.sh`) |
| DNS-SD services `_https._tcp` and `_http._tcp` | instance "Hi-Fi Bridge (<device name>)", shortened on a character boundary (…) to the 63-byte DNS label limit (a 32-character Cyrillic name is 64+ bytes) | Avahi browse; UNIT `test_review_access.py` |
| Extra names (reverse proxy, local DNS) | setting `extra_hostnames` (single labels allowed) | unit tests |
| Bare device name `http://hifi-bridge/` (router DNS search domain) | always accepted by the Host check (not in the certificate: outside the CA's name constraints) | UNIT |
| `--no-mdns` | when the OS publishes the name with avahi instead | – |

A conflict-renamed device (for example `hifi-bridge-2.local`) automatically gets the new name in its certificate.

The publication is checked every 60 s: if avahi-daemon restarted (its entry groups vanish with it), the bridge publishes again and keeps
retrying while the daemon is down. Any mDNS error degrades to "unavailable" instead of stopping the start. The built-in responder runs one
re-probe at a time and, after 15 conflicts within 10 s, waits 5 s before each further probe (RFC 6762 §8.1).

## 3. Certificates
**Mode `local_ca` (default):**
* **Per-device CA:** ECDSA P-256, valid 10 years, `pathlen:0`, and **name-constrained (critical)** to `local`, `home.arpa`, `lan` and `internal`
  plus private, link-local and loopback IP ranges.
  * Even with the CA key, nobody can issue a certificate that clients accept for an internet name. This is tested: a forged certificate
    for `www.example.com` fails with "permitted subtree violation".
  * The CA key is a 0600 file on the device and never leaves it.
* **Server certificate:** ECDSA P-256, valid 397 days (within browser limits), `serverAuth` only. Its SANs are the mDNS name, the extra names
  (only those inside the constraints) and the current private IP addresses.
* **Renewal:** automatic 30 days before expiry, when the name or address set changes (checked every 60 s), when the CA changes (also a
  regenerated CA with the same subject: its fingerprint is recorded at issue time), or when key and certificate do not match. A **new**
  TLS context is built and swapped in; the HTTPS listener reads it per connection and runs each handshake in the connection's own thread
  with a 10 s timeout, so new handshakes use the new certificate, open connections are not interrupted, and one idle peer cannot block
  other clients. In proxy mode there is no context and the HTTPS listener refuses every handshake.
* **Power-cut safety:** keys and certificates are written to temporary files, fsynced and renamed (key first), then the directory is
  fsynced. A CA that does not parse or whose key does not match (for example after a power cut during the very first boot) is replaced by
  a new CA, logged as a fault; its new fingerprint is in `hifi-status.txt`, and every client must trust the new CA again
  (docs/TRUST_BOOTSTRAP.md). A missing or unusable CA never stops the start; `hifi-status.txt` is written anyway.

**Mode `custom`:** the owner's own certificate (with chain) and key (PEM).
* The device rejects a key that does not match, an expired certificate, and a certificate that does not cover the device name.
* Typical uses are a certificate from the owner's own PKI, or a public certificate for an internal name obtained via DNS-01.

**Mode `proxy`:** a reverse proxy (Caddy, nginx, Traefik, …) terminates TLS.
* `X-Forwarded-For/-Proto/-Host` are honoured **only** from addresses listed in `trusted_proxies`.
* The Host check also applies to the forwarded host.

## 4. Trust setup (owner, once per client device)
1. Open `http://hifi-bridge.local:<port>/trust`. This page is reachable over HTTP so that setup can begin.
2. **Authenticate the CA out of band** (docs/TRUST_BOOTSTRAP.md):
   * **With the flash tool:** it verifies the device's CA automatically with the one-time trust key it put on the card, and shows the
     verified fingerprint. Compare the phone's trust page against that value.
   * **Without it:** compare the fingerprint with `hifi-status.txt` on the boot partition (or the console).
   * Over plain HTTP an attacker on the LAN could swap the download. The out-of-band check is what makes the trust decision sound.
   * Never accept a browser warning without this check.
3. Install the CA. The trust page gives the steps for iOS (profile plus "full trust"), Android (CA certificate, DER file), Windows (Trusted Root
   store), macOS (Keychain, "Always Trust") and Linux/Firefox (system store or Firefox's own store).
4. Open `https://hifi-bridge.local:<https port>/`. The lock must appear without a warning.
5. Optional: enable **HTTPS only**. This can only be switched on **from an HTTPS session**, which proves the client trusts the certificate and
   prevents locking yourself out. After that, HTTP answers with a 308 redirect, except `/trust`, `/ca.crt`, `/ca.der` and `/healthz`.

**No HSTS (DECISIONS #48):** not for `hifi-bridge.local`, a captive portal or local certificates; possibly later as an option for deliberately
configured HTTPS installations. A broken certificate together with HSTS could lock the owner out of a LAN device. Regression-tested.

## 5. DNS rebinding
Every request is checked **before** any processing. The Host header must be one of:
* the mDNS name (configured or conflict-renamed), also without `.local`;
* an extra name;
* `localhost`;
* an IP literal (IP literals cannot be rebound).

A single-label name cannot be a public domain, so accepting the bare names keeps the protection. Anything else gets **421 Misdirected
Request**, as a page in German and English with links to the device's names and to the address the browser actually used. Session cookies are `SameSite=Strict` and every POST is origin-checked (docs/SECURITY.md).

## 6. Evidence
| Check | Label |
|---|---|
| CA name constraints, forged public-name certificate fails, renewal reasons, custom-certificate validation | UNIT, real openssl 3.0 |
| Real TLS handshakes with hostname verification against the device CA; `Secure` cookies only over HTTPS; HTTPS-only redirect and lock-out protection; trusted vs. untrusted proxies; switching between local CA and own certificate | INTEGRATION (Python `ssl`, real HTTP servers) |
| WebKit loads `https://hifi-bridge.local:8443/` **after** the CA is installed in the system store; Chromium refuses with `ERR_CERT_AUTHORITY_INVALID` | BROWSER (real WebKit/Chromium) |
| mDNS interoperability incl. goodbye: after a clean stop the name disappears from the Avahi peer within ≤ 4.5 s (measured, incl. the 3 s resolve timeout; RFC 6762 allows 1 s cache retention; without goodbye it would stay for the 120 s TTL) | INTEGRATION (real Avahi) |
| Real phones' browsers and OS trust stores, real home routers and mDNS reflectors | **UNTESTED** (hardware plan) |
