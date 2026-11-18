# Phase 2b work state (resume point)

Read this first after an interruption. Updated by the coordinator at every package boundary. Details per package: `reports/PROGRESS_PHASE2B.md`.

## Strategy (owner instruction 2026-11-18, after the rate-limit interruption)
* At most **one subagent at a time**; each package is finished completely (implementation, tests, review, docs, commit, merge into
  `develop`, push after tests + secret scan) before the next starts.
* Sonnet for regular implementation, Opus for complex architecture/security work.
* Order: 1 review fixes → 2 demo/entry points → 3 network → 4 OTA → 5 flash tool GUI → 6 Buildroot images (3 boards) → 7 final QA + handoff.

## Saved work of the interrupted parallel streams (2026-11-18, nothing discarded)
All five parallel agents stopped at ~14:20 on the account rate limit. Their uncommitted changes were committed as `chore(wip)` commits;
the branches were renamed to `wip/<package>`. Worktrees stay in `.claude/worktrees/` (locally git-excluded).

| Package | Branch | Worktree | State when interrupted |
|---|---|---|---|
| 2 entry/demo | `wip/entry` | `.claude/worktrees/agent-a4175611437335915` | loudness commit done (rooms at volume 75, louder generated audio, monitor gain + soft limiter); WIP: `scripts/lib/`, image pins (`docker/image-pins.txt`, `scripts/image_pins.sh`), `Dockerfile.entry`, changes to every Dockerfile/run script. A test stack `hifi-entry-demo-*` was left running by this stream |
| 3 network | `wip/network` | `.claude/worktrees/agent-a3fbaad627f328c4b` | 3 commits: `network.json` schema + renderers, state machine (Wi-Fi XOR hotspot), root network agent + Linux adapter + bridge client. Missing: WebUI, OS integration, firewall/timesync/hostname, docs, browser tests |
| 4 OTA | `wip/ota` | `.claude/worktrees/agent-a018edf388402fff7` | 2 commits: RAUC D-Bus client + simulated RAUC service, update check + streamed transfers; WIP: manager, health check, `Dockerfile.ota`, tests. Missing: WebUI, backup/restore, OS integration, docs |
| 5 flash tool | `wip/flashtool` | `.claude/worktrees/agent-aaa8ce811404c9b76` | 2 commits: board table, publisher key store, flash tool image, catalogue, resumable downloads, verification; WIP: privileged helper, platform adapters, review fixes M11 (recursive mount walk). Missing: GUI, packaging, M12, docs |
| 6 Buildroot | `wip/os` | `.claude/worktrees/agent-a9e0212576b4defc8` | 4 commits: read-only root, scripted dev builds (`scripts/os_build.sh`), secret scanner, firmware licence notices, `docs/IMAGE_LICENSING.md`; WIP: reproducibility fix of the data ext4 (fixed UUID/hash seed), `os/tools/size_plan.py`. **Pi 3 B+ build completed** (`.lab/br-out/rpi3_64`, 9 GB); Zero 2 W and Pi 4 not built yet |
| 1 review fixes | – | – | the fix agent had not changed anything yet; restarted from `develop` |

Review findings for package 1: `reports/REVIEW_PHASE2B_EXISTING_CODE.md`; repro scripts in `.lab/review-repro/` (not versioned).

## Package status
| # | Package | Status | Merge commit on develop |
|---|---|---|---|
| 1 | Review fixes (H1-H6, M1-M10, L1-L12; M11/M12 belong to package 5) | in progress | |
| 2 | Demo and entry points | pending (resume `wip/entry`) | |
| 3 | Network management | pending (resume `wip/network`) | |
| 4 | OTA update management | pending (resume `wip/ota`) | |
| 5 | Flash tool GUI | pending (resume `wip/flashtool`) | |
| 6 | Buildroot images | pending (resume `wip/os`; rebuild all three boards after packages 3/4 are merged) | |
| 7 | Final QA, coverage, screenshots, handoff | pending | |

## Notes
* Never touch the owner's demo (`hifi-demo`) or the shared lab (`btbridge-lab`) except through the documented scripts by the coordinator.
* An unnamed container `sweet_lovelace` (alpine `nc` stub on port 8000, created 2026-11-18 09:29) was not started under any project name
  prefix of this package and is left untouched; ask the owner if it is unknown to them.
* The 13 modified screenshots in `reports/screenshots/` (uncommitted, from an earlier test run) are left as they are until package 7
  regenerates the curated set.
