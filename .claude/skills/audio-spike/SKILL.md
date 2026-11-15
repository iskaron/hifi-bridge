---
name: audio-spike
description: Start autonomous phase 1 Bluetooth-to-Lyrion audio feasibility prototype and produce a ChatGPT review handoff.
disable-model-invocation: true
---

Execute the complete major assignment described in `prompts/01_AUDIO_SPIKE.md`. First read `CLAUDE.md`, `AGENTS.md` and the linked product/acceptance documents. Work autonomously through independent implementation and testing, then produce `reports/HANDOFF_FOR_CHATGPT.md` and **stop before phase 2**. Be explicit about actual vs simulated hardware verification.

**Mandatory:** the workstation has no BT/Pi/audio hardware. Use Docker-only builds/tests, simulated Bluetooth source, real Docker LMS + headless Squeezelite. Never enumerate hardware or treat its absence as blocking. See `CLAUDE.md`.
