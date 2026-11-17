# Repository, CI and release channel (DECISIONS #69, #70)

## 1. Remotes
| Remote | URL | Role |
|---|---|---|
| `origin` | `git@github.com:iskaron/hifi-bridge.git` (https://github.com/iskaron/hifi-bridge) | **final platform**: code, GitHub Actions CI, GitHub Releases. **Private** until the owner explicitly releases it |
| `gitlab` | `https://git.cativa.net/hifi-bridge/hifi-bridge.git` | former central server (2026-11-17, #69); kept, not pushed to without approval |

Branches: `develop` (development; agents may push it automatically after tests and a secret check) and `main` (pushed only with the
owner's approval; no merge from `develop` without approval). No tags, no releases, no force-push, no history rewrite. Git identity
`Oliver Meyer <olm@cativa.net>` (#22). Licence intent AGPL-3.0-only (#13).

## 2. Migration record (2026-11-17)
1. `origin` (GitLab) renamed to `gitlab`; GitHub added as `origin`.
2. Checks before the push:
   * SSH authentication to GitHub works (`ssh -T git@github.com` → authenticated as `iskaron`).
   * The repository existed and was **empty** (`git ls-remote origin` returned no refs), so there was nothing to reconcile.
   * **Private:** anonymous requests to `https://github.com/iskaron/hifi-bridge`, `https://api.github.com/repos/iskaron/hifi-bridge`
     and anonymous `git ls-remote` over HTTPS all fail (404 / refused) while SSH access works.
   * **Secrets:** Gitleaks v8.24.3 (Docker, no network, read-only mount) over the complete history of all branches: no leaks
     (`scripts/run_secrets.sh`). A pattern search found only test values (a dummy `AAAA` key in a test, lab-only passwords of the Docker
     lab, invented Wi-Fi/hotspot test passwords) and no key files ever committed.
3. `main` and `develop` pushed with their full, unchanged history; upstreams set to `origin/main` and `origin/develop`.

## 3. CI (GitHub Actions, `.github/workflows/ci.yml`)
* Every job calls the same Docker script as a local run (`scripts/run_*.sh`); nothing besides Docker is installed on the runner.
* Jobs: static (shellcheck, compile, systemd-analyze, actionlint), unit, integration + outages (real LMS/Squeezelite/Mosquitto), PipeWire
  0.3.65/1.4.2, hwkit, mDNS against a real Avahi, OS + flash tool (Buildroot defconfig/licence checks, real RAUC host tool, genimage card
  layout, simulated cards), security (bandit + **full-history Gitleaks**, `fetch-depth: 0`), Python 3.14, syslog over TLS, coverage,
  Home Assistant, browser (Chromium/WebKit).
* Actions on Node 24 (`actions/checkout@v7`, `actions/upload-artifact@v7`); checkout does not persist credentials; `permissions:
  contents: read`.
* **No secrets in CI.** Tests generate their keys per run; release keys never go into GitHub (docs/UPDATE_TRUST.md §4).
* Documentation-only pushes (`*.md`, `docs/`, `reports/`, `measurements/`) do not start a run (private-repository minutes,
  OPEN_QUESTIONS #44).
* What CI can never show: anything in docs/HARDWARE_GATE_CHECKLIST.md. Green CI is software evidence only.

## 4. Releases and the official OTA source
* GitHub Releases of `iskaron/hifi-bridge` is the release platform and the **fixed official OTA source** (`update-trust.json` →
  `official_source`, docs/UPDATE_TRUST.md §4a).
* Releases are created **only by the owner**, by hand, after the offline re-signing. No workflow creates tags or releases, and none has
  access to a signing key.
* While the repository is private, devices cannot fetch releases (no tokens on devices): OPEN_QUESTIONS #45, gate G4 K-3.

## 5. API access for agents
A fine-grained token for `iskaron/hifi-bridge` lives in `secrets/github-token` (git-ignored, mode 0600). `scripts/gh_api.sh` uses it for
the GitHub REST API without printing it or putting it on a command line. Rules and the probed permissions: CLAUDE.md, "GitHub access
token". The token never enters commits, images, artifacts, reports or CI configuration, and it does not widen any approval limit.

## 6. Found by replaying the CI jobs in a fresh clone (2026-11-17)
A fresh clone has no `.lab/` and no Buildroot checkout, like a GitHub runner. Replaying the jobs there found:
* `scripts/os_fetch_buildroot.sh`: `mkdir -p .lab/buildroot && cd .lab/buildroot` - when `.lab/` had been created by Docker as root, the
  `mkdir` failed without stopping the script (`set -e` ignores failures inside `&&` lists), and the Buildroot fetch then ran in the
  **project** repository (it became shallow; a checkout of Buildroot over the project tree was only prevented by local changes).
  Fixed: separate statements plus a guard that refuses to run unless the directory is the top level of its own repository.
* `scripts/lab_up.sh` now creates `.lab/` as the calling user before Docker can create it as root.
