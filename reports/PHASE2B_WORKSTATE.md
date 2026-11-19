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
| 6 Buildroot | `wip/os` | `.claude/worktrees/agent-a9e0212576b4defc8` | merged into develop (18 commits); all three boards built; worktree can be removed |
| 1 review fixes | – | – | the fix agent had not changed anything yet; restarted from `develop` |

Review findings for package 1: `reports/REVIEW_PHASE2B_EXISTING_CODE.md`; repro scripts in `.lab/review-repro/` (not versioned).

## Package status
| # | Package | Status | Merge commit on develop |
|---|---|---|---|
| 1 | Review fixes (H1-H6, M1-M10, L1-L12; M11/M12 belong to package 5) | **done** (CI run 44350223892: 14/14 green) | `618e395` (+ `649fc32` test fixes) |
| 2 | Demo and entry points | **done** (CI run 44372722429: 14/14 green) | `46483a0` |
| 3 | Network management | **done** (CI run 44420917417: 15/15 green) | `926e945` |
| 4 | OTA update management | **done** (CI run 44483643387: 16/16 green) | `e8a95ff` |
| 5 | Flash tool GUI | **done** (CI run 44504983152: 17/17 green) | `1e8c7c5` |
| 6 | Buildroot images | merged; full run + CI pending | 3 boards BUILD-VERIFIED, 89/89 image checks each |
| 7 | Final QA, coverage, screenshots, handoff | in progress: 7a review done (`reports/REVIEW_PHASE2B_NEW_CODE.md`, 14 findings); 7b-1 app fixes merged; next 7b-2 OS fixes N-6/N-8/N-13/N-14 (one-board rebuild), 7b-3 coverage + PipeWire trixie harness flake + local mDNS + demo, 7c docs/screenshots/final run/handoff | package 6 CI 16/17 (HA test race fixed) |

## Notes
* Never touch the owner's demo (`hifi-demo`) or the shared lab (`btbridge-lab`) except through the documented scripts by the coordinator.
* An unnamed container `sweet_lovelace` (alpine `nc` stub on port 8000, created 2026-11-18 09:29) was not started under any project name
  prefix of this package and is left untouched; ask the owner if it is unknown to them.
* The 13 modified screenshots in `reports/screenshots/` (uncommitted, from an earlier test run) are left as they are until package 7
  regenerates the curated set.
