# Progress log - phase 2b: software completion and hardware readiness

Labels: UNIT, INTEGRATION, SIMULATED, STATIC, BUILD (built in Docker, never booted), REAL-HARDWARE (none here). Newest entries at the bottom.
Scope: owner work package of 2026-11-18 (DECISIONS #73/#74).

## 2026-11-18
- Baseline: `develop` = `origin/develop` = `486dfee`. Host (read-only): 4 CPUs, 22 GB RAM (~11 GB available), Docker data filesystem 109 GB
  free, /home 185 GB free; Docker Engine 29.1.3, Compose 2.40.3. The owner's demo (`hifi-demo`) and the shared lab (`btbridge-lab`) are
  running and are left untouched by the work streams.
- Work split into five parallel streams, each in its own git worktree, merged into `develop` by the coordinator:
  1. Buildroot images (Pi Zero 2 W, 3 B+, 4), SBOM, licences incl. Broadcom firmware, image secret scan - one board build at a time
     (`--cpus=3 --memory=8g`), outputs in `.lab/br-out/`.
  2. Network management (Ethernet/Wi-Fi, IPv4/IPv6, Wi-Fi XOR hotspot fallback, firewall, hostname, time sync, recovery).
  3. OTA/update management on RAUC (check, signed upload/download, progress, A/B, health check, rollback, backup/restore).
  4. Flash tool GUI (PySide6, platform disk adapters, privileged helper, packaging preparation, LGPL).
  5. Entry points and demo (Docker/Compose minimum versions + pre-flight check, automatic image builds, progress output, demo loudness,
     clean-environment test, Lyrion isolation regression tests, pinned images).
