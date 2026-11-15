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
| MQTT | Optional; username/password and TLS are supported; the password is stored as a secret. Commands are limited to pairing, bridge audio and the test tone, and the test tone refuses on a busy target. | INTEGRATION, real Mosquitto |

## Known gaps and decisions for later (see OPEN_QUESTIONS)
* **HTTPS:** the WebUI supports TLS when a certificate is supplied (`--tls-cert/--tls-key`), but certificate provisioning is phase 2b. Plain HTTP on the
  LAN means a network attacker can sniff the session cookie.
* **MQTT command trust:** anyone allowed to publish to `<base>/<id>/cmd/#` on the broker can open the pairing window or switch bridge audio. Broker ACLs
  are the owner's responsibility; this is documented in the UI help.
* **No account lock-out** beyond the back-off, and no audit log of owner actions beyond the RAM log.
* **DNS rebinding:** cookies are `SameSite=Strict` and every POST is origin-checked. GET pages of an authenticated session cannot be read cross-origin
  (same-origin policy), but no `Host` allow-list is enforced yet.
* No external review or penetration test has been done.
