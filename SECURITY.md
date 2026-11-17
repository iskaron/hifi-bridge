# Security policy

Hi-Fi Bridge is pre-release software (see the status note in README.md). There are no supported releases yet; fixes go to `develop`.

## Reporting a vulnerability
Please **do not open a public issue** for security problems. Use GitHub's private vulnerability reporting for this repository
("Security" tab -> "Report a vulnerability"). Include the affected component, steps to reproduce and the impact you expect.
You will get an acknowledgement as soon as possible; please give us reasonable time to fix the issue before disclosure.

## Scope and design documents
* Device security model, authentication, TLS and the local CA: `docs/SECURITY.md`, `docs/LOCAL_ACCESS_TLS.md`
* Update signing and key handling: `docs/UPDATE_TRUST.md` (no signing key is ever stored in this repository or in CI)
* First setup, provisioning and the CA trust proof: `docs/PROVISIONING.md`, `docs/TRUST_BOOTSTRAP.md`
* The interactive demo (`make demo`) is a local development tool: its ports bind to 127.0.0.1 by default, and its devices are simulated.
