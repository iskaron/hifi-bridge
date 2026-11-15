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
| mDNS host name `<local_hostname>.local` (default `hifi-bridge`) | own stdlib responder (`net/mdns.py`): probing, conflict resolution (`-2`, `-3`, …), announcements, goodbye on SIGTERM; joins the group on every interface | real Avahi 0.8: resolve, browse `_https._tcp`, conflict, goodbye (7/7, `scripts/test_mdns.sh`) |
| DNS-SD services `_https._tcp` and `_http._tcp` | instance "Hi-Fi Bridge (<device name>)" | Avahi browse |
| Extra names (reverse proxy, local DNS) | setting `extra_hostnames` | unit tests |
| `--no-mdns` | when the OS publishes the name with avahi instead | – |

A conflict-renamed device (for example `hifi-bridge-2.local`) automatically gets the new name in its certificate.

## 3. Certificates
**Mode `local_ca` (default):**
* **Per-device CA:** ECDSA P-256, valid 10 years, `pathlen:0`, and **name-constrained (critical)** to `local`, `home.arpa`, `lan` and `internal`
  plus private, link-local and loopback IP ranges.
  * Even with the CA key, nobody can issue a certificate that clients accept for an internet name. This is tested: a forged certificate
    for `www.example.com` fails with "permitted subtree violation".
  * The CA key is a 0600 file on the device and never leaves it.
* **Server certificate:** ECDSA P-256, valid 397 days (within browser limits), `serverAuth` only. Its SANs are the mDNS name, the extra names
  (only those inside the constraints) and the current private IP addresses.
* **Renewal:** automatic 30 days before expiry, when the name or address set changes (checked every 60 s), or when the CA changes. The TLS
  context is reloaded in place: new handshakes use the new certificate, and open connections are not interrupted.

**Mode `custom`:** the owner's own certificate (with chain) and key (PEM).
* The device rejects a key that does not match, an expired certificate, and a certificate that does not cover the device name.
* Typical uses are a certificate from the owner's own PKI, or a public certificate for an internal name obtained via DNS-01.

**Mode `proxy`:** a reverse proxy (Caddy, nginx, Traefik, …) terminates TLS.
* `X-Forwarded-For/-Proto/-Host` are honoured **only** from addresses listed in `trusted_proxies`.
* The Host check also applies to the forwarded host.

## 4. Trust setup (owner, once per client device)
1. Open `http://hifi-bridge.local:<port>/trust`. This page is reachable over HTTP so that setup can begin.
2. **Compare the SHA-256 fingerprint** with the fingerprint shown on the device itself. Until the flash tool exists (phase 2b), "on the device
   itself" means the console or log output of the appliance.
   * Over plain HTTP an attacker on the LAN could swap the download. The out-of-band comparison is what makes the trust decision sound.
   * Never accept a browser warning without this check.
3. Install the CA. The trust page gives the steps for iOS (profile plus "full trust"), Android (CA certificate, DER file), Windows (Trusted Root
   store), macOS (Keychain, "Always Trust") and Linux/Firefox (system store or Firefox's own store).
4. Open `https://hifi-bridge.local:<https port>/`. The lock must appear without a warning.
5. Optional: enable **HTTPS only**. This can only be switched on **from an HTTPS session**, which proves the client trusts the certificate and
   prevents locking yourself out. After that, HTTP answers with a 308 redirect, except `/trust`, `/ca.crt`, `/ca.der` and `/healthz`.

There is no HSTS yet. A broken certificate together with HSTS could lock the owner out of a LAN device that has no other way in. This is a
deliberate, reversible choice (OPEN_QUESTIONS #28).

## 5. DNS rebinding
Every request is checked **before** any processing. The Host header must be one of:
* the mDNS name (configured or conflict-renamed);
* an extra name;
* `localhost`;
* an IP literal (IP literals cannot be rebound).

Anything else gets **421 Misdirected Request**. Session cookies are `SameSite=Strict` and every POST is origin-checked (docs/SECURITY.md).

## 6. Evidence
| Check | Label |
|---|---|
| CA name constraints, forged public-name certificate fails, renewal reasons, custom-certificate validation | UNIT, real openssl 3.0 |
| Real TLS handshakes with hostname verification against the device CA; `Secure` cookies only over HTTPS; HTTPS-only redirect and lock-out protection; trusted vs. untrusted proxies; switching between local CA and own certificate | INTEGRATION (Python `ssl`, real HTTP servers) |
| WebKit loads `https://hifi-bridge.local:8443/` **after** the CA is installed in the system store; Chromium refuses with `ERR_CERT_AUTHORITY_INVALID` | BROWSER (real WebKit/Chromium) |
| mDNS interoperability | INTEGRATION (real Avahi) |
| Real phones' browsers and OS trust stores, real home routers and mDNS reflectors | **UNTESTED** (hardware plan) |
