## Mandatory development environment: hardware-free Docker ONLY

**Authoritative owner clarification (2026-11-15):** Claude Code runs on a separate development machine with **no Raspberry Pi, Bluetooth adapter, speakers, physical audio interface, or live Lyrion installation**. This is expected and **must not be treated as a blocker**.

- **DO NOT** search for, enumerate, probe, or request host Bluetooth adapters (`bluetoothctl`, `hciconfig`, `btmgmt`, `rfkill`, `/sys/class/bluetooth`, `lsusb` for BT), physical sound devices (`aplay -l`, `/dev/snd`), Raspberry Pi boot firmware, local network LMS players or paired phones. No host hardware discovery or device passthrough. Do not suggest connecting hardware as a prerequisite for phase 1.
- **DO NOT** install packages, services or system dependencies on the development host. Host prerequisite is only Docker/Compose and lightweight shell/build wrappers (where available). All compilation, unit testing, test audio generation, LMS, software Squeezebox players, integration tests and diagnostics run **inside disposable Docker containers**. If a necessary container permission is missing, report that exact limitation, not missing Bluetooth hardware.
- Use **synthetic audio fixtures and a simulated A2DP/AVRCP adapter** feeding the same interface as a future real BlueZ/PipeWire adapter. No real radio is needed to develop and exercise the transport, state machine, timing, buffer policies and Lyrion ownership semantics.
- Run a **real Lyrion Media Server in Docker** and one or more **software players (e.g. headless Squeezelite) in Docker** to validate actual integration, including synchronized playback when feasible. Headless players may consume/discard PCM or capture output for quantitative analysis, without sound hardware.
- Distinguish evidence: simulated Bluetooth + real Docker LMS/player integration is **software integration verified**, not real Android/iPhone Bluetooth or audible physical hardware verified. Mark radio/phone AVRCP behavior, actual speaker timing, Wi-Fi coexistence and physical Pi boot as **deferred hardware validation**, not as failed or blocking phase-1 requirements.
- Collect software-measurable performance (stream readiness, service setup, HTTP transfer, player commands, buffer continuity, CPU/memory inside containers), document measurement points and reproducible scripts. Do **not** fabricate acoustic/playback-to-ear latency or claim 'tested on Pi'.
- Phase 1 can be delivered and reviewed as **software-only feasibility** even though real-hardware proof remains outstanding. Surface true architectural feasibility blockers, continue independent work otherwise, and produce the normal Handoff for ChatGPT.


# Claude Code project instructions — Bluetooth Audio Bridge

## Mission

Make a real, reliable Bluetooth-to-Lyrion audio bridge. **From a guest's perspective it is an ordinary Bluetooth speaker:** discover/pair during an owner-enabled pairing window, connect, press Play in *any* ordinary phone audio application, hear audio on the selected system. No phone app, Lyrion UI or Home Assistant skill required for guests. The system owner handles setup and pairing from a mobile-first local UI.

## Authoritative documents and precedence

1. User's latest explicit instructions in the current Claude Code session.
2. `docs/PRODUCT_SPEC.md` and `docs/DECISIONS.md` — **accepted product decisions**.
3. `docs/PLAYBACK_STATES.md` and `docs/ACCEPTANCE.md` — intended behavior and acceptance tests.
4. Phase-specific task file in `prompts/` — **scope and stop criteria**.
5. This `CLAUDE.md` and `AGENTS.md` — delivery conventions.

When docs seem inconsistent, prioritize guest audio UX and avoid undesired control of unrelated Lyrion music. Record conflicts in `docs/OPEN_QUESTIONS.md` and choose the smallest reversible implementation that preserves accepted requirements. Do not silently change product behavior.

## Two major tasks, not hourly handoffs

- **Phase 1 `audio-spike`:** prove or disprove the actual audio transport and user experience as far as the available environment permits. Implement, test and optimize autonomously with reasonable effort. Measure latency instead of imposing an arbitrary time target. Work through independent research, unit tests, simulated/LMS integration and hardware tests *when a test rig actually exists*. Prepare `reports/HANDOFF_FOR_CHATGPT.md` once the phase has a defensible result or a real blocker. **Then stop.**
- **Phase 2 `build-product`:** only after the owner approves phase 1. Implement the complete appliance autonomously in coherent increments, including Buildroot, setup, UI, MQTT, OTA, flash/recovery and documentation. Do not ask approval at every sub-task; continue work that is not blocked and validate every milestone. Handoff only at major completion or real user-dependent blocker.

Never claim a phase is complete if mandatory acceptance tests have not run. Instead mark it **partial/unverified** and describe exactly what's needed.

## Autonomy and safety boundaries

- You may research, write/change local project files, add tests, run safe local test commands and launch **isolated disposable test systems** as needed. Check tools available before choosing implementation.
- **Never** modify a production Lyrion server, Home Assistant instance, live Raspberry Pi, physical player or home network without separate explicit authorization. Do not assume any live credentials, hosts or access are available.
- Do not publish a release, create a tag, deploy, flash media, change production configs or run destructive cleanup without explicit authorization. You may use read-only Git commands.
- **Git and the central repository (owner decisions 2026-11-17, DECISIONS #69/#70):** remote `origin` = `git@github.com:iskaron/hifi-bridge.git` (GitHub, **private** until the owner explicitly releases it; final platform for code, CI and releases). The former GitLab server stays as remote `gitlab` (`https://git.cativa.net/hifi-bridge/hifi-bridge.git`); do not push there without approval. Development commits on `develop` (Conventional Commits, repository-local identity `Oliver Meyer <olm@cativa.net>`) may be made and **pushed to `origin/develop` automatically**, after the tests relevant to the change have passed and the pushed commits have been checked for keys, tokens and passwords. Still **not** allowed without separate approval: pushing `main` or any other branch, merging `develop` into `main`, force-push, tags, releases, rewriting pushed history, changing the remote or server settings. If `origin/develop` contains commits that are not local, stop and report instead of merging or overwriting. After a push, check the GitHub Actions run (`.github/workflows/ci.yml`) when access is available and fix CI failures. Never change the repository's visibility, publish a GitHub Release or put signing keys into GitHub secrets.
- **GitHub access token (owner authorization 2026-11-17):** a fine-grained personal access token for `iskaron/hifi-bridge` is stored in
  `secrets/github-token` (relative to the repository root), one line `GITHUB_TOKEN=github_pat_...`. The file is git-ignored
  (`**/secrets/**`) and must keep mode **0600** (directory 0700).
  * **Use:** the GitHub REST API and the GitHub CLI (`gh`, only inside Docker; nothing is installed on the host) for work already authorized
    here: monitoring GitHub Actions runs, analysing and fixing CI failures (then commit + push to `develop` as above), reading issues, pull
    requests and releases. Preferred tool: `scripts/gh_api.sh /repos/iskaron/hifi-bridge/...` (reads the file, passes the header to curl
    via stdin). For `gh` in a container pass it as `-e GH_TOKEN` from an exported variable, never as `GH_TOKEN=value` on the command line.
  * **Never** print, echo, log or `cat` the value; never pass it as a command-line argument (visible in the process list); never write it
    into commits, Docker images or build contexts, test fixtures or artifacts, `reports/` (handoffs), CI configuration or GitHub secrets,
    URLs or remote configurations. Test containers bind-mount the repository: tests must never read `secrets/`, and bundles/archives must
    list their files explicitly (as `scripts/hwkit_bundle.sh` does).
  * **Permissions** (probed 2026-11-17 with read requests only): metadata, actions, contents, issues and pull requests are readable;
    checks, statuses, administration, secrets and webhooks are not granted. Write permissions were not probed. If an action fails for a
    missing permission, report the exact permission (the API's `x-accepted-github-permissions` header) instead of working around it.
  * The token changes **none** of the approval limits above: no `main` push, merge into `main`, release, tag, publication, visibility or
  security-relevant repository setting (branch protection, secrets, webhooks, Actions permissions, collaborators) without the owner.
- Preserve local user files and uncommitted changes. No secret tokens in logs, test artifacts, URLs shared publicly or reports. Never embed reusable passwords in public images or configs.
- Minimize additional dependencies and complexity; avoid unproven claims about Linux/Bluetooth/LMS APIs. Research and verify uncertain compatibility.
- Prioritize meaningful tests and runnable reproduction scripts over prematurely elaborate UI.
- Use meaningful project boundaries/interfaces so a future Docker runtime can be supported without duplicating the core. **Do not implement Docker deployment in phase 1 or claim it is a v1 requirement.**
- Write source/tests/docs in clear English for international development; user-facing WebUI must support German and English.
- Follow TDD where practical; CI and automated tests are required, but 100% line coverage cannot replace real hardware tests. No fake green tests or mocked results passed off as integration tests.

## Handoff contract

At the end of the assigned major task, write/update `reports/HANDOFF_FOR_CHATGPT.md` using `docs/HANDOFF_TEMPLATE.md`. Include run and measurement commands, exact devices/player versions when known, evidence paths, changed files, tests and **real outputs**, limitations, tradeoffs, unverified items, blockers, next steps and owner decisions needed. Give the owner a concise copyable summary so they can hand this report to ChatGPT. **Stop rather than start a new major phase.**
