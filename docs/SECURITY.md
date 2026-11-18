# Security model (phase 2a; software-verified, not penetration-tested)

Scope: the owner WebUI, the MQTT integration, the live stream and local data on the device. Evidence labels as in `docs/ACCEPTANCE.md`.
Nothing here has been reviewed by an external security tester.

## Assets and principals
* **Owner:** the single WebUI account, protected by a password chosen during setup. There is no user name and no default password.
* **Guests:** they only use Bluetooth. They never see the WebUI, Lyrion or Home Assistant.
* **Secrets** in `<data>/secrets.json` (mode 0600, separate from `settings.json`):
  * admin password hash (PBKDF2-SHA256);
  * setup-code hash;
  * session key;
  * stream token;
  * MQTT password;
  * trust key (flash tool, until the setup) and the log-pseudonym salt (review M10).
  Unknown keys written by a newer version are kept verbatim (OTA rollback). An unreadable file (permissions, I/O error) is an error,
  never a silent reset; only undecodable content is moved aside as corrupt (review M8).

## Controls (with tests)
| Area | Control | Evidence |
|---|---|---|
| First start | A random 12-character setup code. With provisioning (flash tool or manual bootstrap file, docs/PROVISIONING.md) the owner already knows it and the device stores only its hash; otherwise the device generates one (`setup-code.txt` 0600, and in `hifi-status.txt` on the boot partition until the owner password is set). It is invalid after use. Recovery issues a new code, only via console or the boot partition, never over the network. | UNIT `test_auth.py`, `test_provisioning.py`, HTTP `test_webui.py` |
| Passwords | PBKDF2-HMAC-SHA256 with a random salt; 200k iterations (to be re-measured on a Pi Zero 2 W); at least 10 characters plus simple-pattern checks; constant-time comparison. | UNIT |
| Brute force | Per-client exponential back-off after 5 failures, for login, setup code and the old password of a password change. Behind a trusted proxy the client is the rightmost `X-Forwarded-For` hop that is not a trusted proxy (the leftmost entry is client-controlled); IPv6 clients are keyed by /64. A global budget (50 failures, then up to 60 s for everyone, refilled after 15 quiet minutes) limits guesses spread over many addresses. | UNIT + HTTP (`test_review_access.py`) |
| Sessions | Random 256-bit id held only in server memory; 8 h idle and 30 days absolute, measured on the monotonic clock (a Pi has no RTC; the NTP jump after boot must not end sessions); all sessions end on a password change. | UNIT + HTTP |
| Cookies | `HttpOnly; SameSite=Strict; Path=/`, plus `Secure` with TLS. | HTTP test |
| CSRF | A token per session on every authenticated POST. On every POST a real `Origin` must match the request's host (`Host`, or the forwarded host behind a trusted reverse proxy). `Origin: null` is accepted only if `Sec-Fetch-Site` says same-origin, which also blocks login CSRF. | HTTP tests, 5/5 security mutants killed; real Chromium |
| Browser hardening | Strict CSP (no inline script or style, `frame-ancestors 'none'`, `form-action 'self'`); `nosniff`; `X-Frame-Options DENY`; `Referrer-Policy same-origin`; `no-store`; COOP only over TLS. | HTTP + real-browser test (no CSP violations) |
| Input/output | HTML-escaped output; request body ≤ 64 KiB, checked before it is read (one plain decimal `Content-Length`, else 400; larger 413; chunked 411); strict settings schema (unknown keys rejected, all-or-nothing updates incl. the MQTT password); static files without path traversal; no open redirect (`/\host`, `//host`, control characters and foreign Referer paths are refused; no CR/LF in `Location`). Error pages in German and English. | UNIT + HTTP (`test_review_webui.py`) |
| Connections | WebUI: TLS handshake in the connection's own thread, 10 s per connection, at most 64 concurrent (more get 503). Stream: 10 s to read the request, 60 s per blocked write, at most 16 concurrent. One idle peer can no longer block HTTPS or pin threads. | HTTP (`test_review_webui.py`, `test_review_stream.py`, `test_review_access.py`) |
| Diagnostics | The status, logs and diagnostics bundle never contain the stream token, password hash, session key or MQTT password. | UNIT + HTTP |
| Personal data in logs | MAC addresses in log lines, `faults.jsonl`, the remote syslog, journald (BlueZ adapter) and the diagnostics bundle are replaced by `dev-<8 hex>` (SHA-256 with a per-device salt; stable on one device, not linkable across devices). The bundle leaves out device labels and phone names. The owner's own WebUI and MQTT payloads are unchanged (see OPEN_QUESTIONS proposal in the review report). | UNIT (`test_review_service.py`) |
| Stream token | Redacted in all bridge logs; rotatable; old tokens stay redacted. It does travel through LMS and players by design (OPEN_QUESTIONS #11). | UNIT + phase-1 tests |
| Audio privacy | A missing PipeWire node never falls back to another source such as a microphone (`node.dont-fallback`/`dont-reconnect`, watchdog). | INTEGRATION, real PipeWire |
| MQTT | **Read-only by default** (DECISIONS #36): pairing, playback and test tone are each enabled separately; disabled controls appear only as read-only sensors and their command topics are not subscribed (and re-checked per message). Broker TLS always verifies (system CAs or owner CA), username/password as a secret. | INTEGRATION real Mosquitto + REAL Home Assistant 2026.11.1 |
| HTTPS | Per-device local CA, name-constrained to local names/private IPs (a forged public-name certificate fails verification), 397-day server certificates with automatic renewal, own certificates, reverse-proxy mode with explicit trusted proxies, HTTPS-only with lock-out protection; concept: docs/LOCAL_ACCESS_TLS.md | UNIT/INTEGRATION real openssl + TLS; BROWSER WebKit/Chromium |
| DNS rebinding | Host allow-list (mDNS name, its bare form for a router search domain, extra names - also single labels -, localhost, IP literals) checked before any processing -> 421 page (DE/EN) with links to the device's names and the address actually used. A single label cannot be a public domain, so the bare names do not weaken the protection. | UNIT/HTTP |
| HSTS | **Off by default** (DECISIONS #48): no `Strict-Transport-Security` in any mode; static guard against accidental introduction | UNIT/TLS |
| Remote syslog | Optional; TLS only (RFC 5425) with mandatory verification incl. host name; redacted lines; bounded non-blocking queue; a wrong CA delivers nothing | UNIT + INTEGRATION real rsyslog |
| Recovery | Password reset / factory reset only locally (console, or a request file on the boot partition = physical access); never via WebUI/MQTT; factory reset removes only the bridge's own known files (incl. `faults-respawn.log`); a request runs exactly once and survives power cuts (journal); the CLI acts as the owner of the data directory (root drops to it, other users are refused), so it never leaves root-owned files the service cannot read | UNIT (power cut injected at every step; root case in a root container) |
| Provisioning secrets on FAT | strict validation without echoing values; journaled apply; in-place scrub; short-lived secrets; no hotspot without an owner-known password; evaluation of residual data in docs/PROVISIONING.md §4 | UNIT + INTEGRATION (genimage card, mtools) |
| CA trust bootstrap | the device CA never leaves the device; the flash tool authenticates it with a one-time trust key from the card (HMAC over a nonce), then checks HTTPS against only that CA; the key ends with the setup (docs/TRUST_BOOTSTRAP.md) | INTEGRATION incl. a CA-swapping LAN attacker |
| Public routes | exactly 11 unauthenticated method+path pairs, pinned by a test (a misplaced decorator is caught) | UNIT |
| Boot-partition writer | only `hifi-bridge-boot.service` may write `/boot`; no network (`PrivateNetwork=yes`); systemd exposure 0.6 | STATIC |
| Update signatures | explicit trust choice per image; public CAs only in the keyring; release key never in CI (offline re-signing); codeSigning purpose required (docs/UPDATE_TRUST.md) | real RAUC 1.13 (signatures only; no install) |
| Flash tool | only removable non-system disks; typed confirmation; identity re-check; signed manifest; read-back verify; invalidation on failure (docs/FLASH_TOOL.md) | UNIT with simulated cards; no host disk is ever touched |

## Known gaps and decisions for later (see OPEN_QUESTIONS)
* **HTTPS:** implemented (local CA / own certificate / proxy). Until the owner has installed the CA and enabled HTTPS-only, plain HTTP stays
  available and a LAN attacker could sniff a session cookie used over HTTP. The CA must be authenticated out of band: the flash tool does it
  automatically (docs/TRUST_BOOTSTRAP.md; GUI still to come), otherwise compare the fingerprint in `hifi-status.txt`. No HSTS (DECISIONS #48).
* **MQTT command trust:** only relevant once a control is explicitly enabled; then anyone allowed by the broker ACL can use it (documented in the UI).
* **No account lock-out** beyond the back-off, and no audit log of owner actions beyond the RAM log.
* **CA key on the device:** root on the device can mint certificates - but only for local names/private IPs (name constraints). A new CA
  (e.g. after a reset) must be re-installed on every client.
* No external review or penetration test has been done.
