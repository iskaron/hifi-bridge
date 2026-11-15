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
  * MQTT password.

## Controls (with tests)
| Area | Control | Evidence |
|---|---|---|
| First start | A random 12-character setup code: on the appliance from the flash tool (phase 2b), in development from `setup-code.txt` (0600). Only its hash is stored, and it is deleted after use. Recovery issues a new code, but only via console or flash tool, never over the network. | UNIT `test_auth.py`, HTTP `test_webui.py` |
| Passwords | PBKDF2-HMAC-SHA256 with a random salt; 200k iterations (to be re-measured on a Pi Zero 2 W); at least 10 characters plus simple-pattern checks; constant-time comparison. | UNIT |
| Brute force | Per-client exponential back-off after 5 failures, for both login and setup code. | UNIT + HTTP |
| Sessions | Random 256-bit id held only in server memory; 8 h idle and 30 days absolute; all sessions end on a password change. | UNIT + HTTP |
| Cookies | `HttpOnly; SameSite=Strict; Path=/`, plus `Secure` with TLS. | HTTP test |
| CSRF | A token per session on every authenticated POST. On every POST a real `Origin` must match `Host`. `Origin: null` is accepted only if `Sec-Fetch-Site` says same-origin, which also blocks login CSRF. | HTTP tests, 5/5 security mutants killed; real Chromium |
| Browser hardening | Strict CSP (no inline script or style, `frame-ancestors 'none'`, `form-action 'self'`); `nosniff`; `X-Frame-Options DENY`; `Referrer-Policy same-origin`; `no-store`; COOP only over TLS. | HTTP + real-browser test (no CSP violations) |
| Input/output | HTML-escaped output; request body ≤ 64 KiB; strict settings schema (unknown keys rejected, all-or-nothing updates); static files without path traversal; no open redirect. | UNIT + HTTP |
| Diagnostics | The status, logs and diagnostics bundle never contain the stream token, password hash, session key or MQTT password. | UNIT + HTTP |
| Stream token | Redacted in all bridge logs; rotatable; old tokens stay redacted. It does travel through LMS and players by design (OPEN_QUESTIONS #11). | UNIT + phase-1 tests |
| Audio privacy | A missing PipeWire node never falls back to another source such as a microphone (`node.dont-fallback`/`dont-reconnect`, watchdog). | INTEGRATION, real PipeWire |
| MQTT | **Read-only by default** (DECISIONS #36): pairing, playback and test tone are each enabled separately; disabled controls appear only as read-only sensors and their command topics are not subscribed (and re-checked per message). Broker TLS always verifies (system CAs or owner CA), username/password as a secret. | INTEGRATION real Mosquitto + REAL Home Assistant 2026.11.1 |
| HTTPS | Per-device local CA, name-constrained to local names/private IPs (a forged public-name certificate fails verification), 397-day server certificates with automatic renewal, own certificates, reverse-proxy mode with explicit trusted proxies, HTTPS-only with lock-out protection; concept: docs/LOCAL_ACCESS_TLS.md | UNIT/INTEGRATION real openssl + TLS; BROWSER WebKit/Chromium |
| DNS rebinding | Host allow-list (mDNS name, extra names, localhost, IP literals) checked before any processing -> 421 | UNIT/HTTP |

## Known gaps and decisions for later (see OPEN_QUESTIONS)
* **HTTPS:** implemented (local CA / own certificate / proxy). Until the owner has installed the CA and enabled HTTPS-only, plain HTTP stays
  available and a LAN attacker could sniff a session cookie used over HTTP. The CA fingerprint must be compared out of band; the flash tool
  that will show it is phase 2b. No HSTS yet (OPEN_QUESTIONS #28).
* **MQTT command trust:** only relevant once a control is explicitly enabled; then anyone allowed by the broker ACL can use it (documented in the UI).
* **No account lock-out** beyond the back-off, and no audit log of owner actions beyond the RAM log.
* **CA key on the device:** root on the device can mint certificates - but only for local names/private IPs (name constraints). A new CA
  (e.g. after a reset) must be re-installed on every client.
* No external review or penetration test has been done.
