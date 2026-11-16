# Progress log — software stabilisation and phase-2b preparation

Labels: UNIT, INTEGRATION, SIMULATED, STATIC, REAL-HARDWARE (none here). Newest entries at the bottom.

## 2026-11-16
- Docker (read-only check): data filesystem 196 G, 85 G used, 102 G free; images 53 GB (82), build cache 8.9 GB; 4 CPUs, 22.9 GiB RAM.
  Baseline: reports/evidence/docker_baseline_stabilisation.txt.
- All 7 project images rebuilt from scratch (scripts/build_images.sh: --pull --no-cache; sizes + base digests in reports/evidence/images.txt);
  new btbridge-static image (systemd-analyze, shellcheck) so static checks need no apt at check time.
- Removing my own superseded images (by ID) was denied by the permission system - not worked around. Owner-run helper:
  scripts/cleanup_own_images.sh (dry run by default; finds only dangling images built from this project's Dockerfiles + the duplicate
  HA "stable" tag). Space is not a blocker (102 GB free).
- Baseline full run with fresh images (reports/evidence/full_run_stabilisation_baseline.log): static 0 (systemd-analyze verify OK,
  exposure 1.9), unit 221, integration 54, outages 0/0, PipeWire trixie 10, hwkit 32, mDNS 7/7, HA 7/7, browser 5; PipeWire bookworm
  1 FAILED (first capture = only silence).
- Root cause analysis: NOT the rebuild (old image fails 1 in 3 too) - the SIMULATED node (module-pipe-source on PipeWire 0.3.65)
  sometimes delivers only silence for a whole session. Two harness fixes: a writer stays attached to the FIFO from before the module
  loads; readiness check through the bridge's own capture supervisor with up to 3 module reloads (counted in the results).
  6/6 bookworm runs then green (1 needed a reload), trixie 2/2 green. Product capture path unchanged; target PipeWire is 1.2.8.
- Buildroot research (sparse clone of 2026.02.x LTS, commit 679b9ea): versions/licences of all needed packages; key finding - no
  `parec` without the full pulseaudio package, so the appliance needs `pw-cat --raw`; Python 3.14 on the image. docs/PHASE2B_PLAN.md.
