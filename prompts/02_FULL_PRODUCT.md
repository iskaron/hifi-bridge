# PHASE 2 — Autonomous full appliance implementation (REQUIRES OWNER APPROVAL)

Do not execute unless the owner explicitly authorized phase 2 after review of phase 1 audio handoff.

You are lead engineer for Bluetooth Audio Bridge. Read `CLAUDE.md`, all relevant `docs/`, the real phase-1 implementation and `reports/HANDOFF_FOR_CHATGPT.md`. Keep existing tested audio architecture unless evidence mandates a change and document any accepted product conflicts.

**Goal:** develop a production-quality, portable Buildroot-based Raspberry Pi appliance with secure setup, stable Bluetooth/Lyrion integration, mobile-first Hi-Fi & elegant WebUI, international Wi-Fi setup, optional MQTT/Home Assistant, signed A/B OTA+rollback, recovery/flash workflow, documentation and CI. Keep future Docker as an architectural possibility, not a deliverable.

**Autonomy:** Work through independent vertical slices without waiting for review after each stage. Create a task plan and continue through the next independently feasible item. Make reasonable, reversible technical implementation decisions and test every slice. When external hardware is unavailable, implement/test what is possible and mark physical claims UNVERIFIED. Parallelize independent reviews or investigations with subagents if helpful; main agent remains accountable for integration. **Do not change product requirements silently.**

**Recommended slices:** audio core solidification -> setup/network and state persistence -> WebUI owner UX -> MQTT/HA -> immutable Buildroot hardware integration -> image/flash tooling -> signed A/B+rollback -> hardware coverage/resource/storage tests -> packaging/security/docs/release readiness. Choose a better technical order if evidence supports it.

**No automatic external effects:** no production deploy, Pi flash, push of anything other than development commits on `develop` (CLAUDE.md, DECISIONS #69/#70), tag, publication, new public release, or modification of real servers/networks unless separately authorized. Do not set a guessed SD size minimum. Do not require Internet for playback, setup or routine operation. No automatic OTA installation.

**Deliver at major completion/blocker:** `reports/HANDOFF_FOR_CHATGPT.md` with exact evidence, tests and gaps, plus clear run/build/flashing instructions and unresolved approval items; then stop. A partial but truthful handoff is better than claiming supported hardware or safety-critical rollback works without measurements.
