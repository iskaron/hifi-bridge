---
name: handoff
description: Produce an evidence-backed project milestone report for the owner's ChatGPT review.
disable-model-invocation: true
---

Read `docs/HANDOFF_TEMPLATE.md`, the active phase prompt, project changes and actual test outputs. Create/update `reports/HANDOFF_FOR_CHATGPT.md`. Explicitly distinguish real hardware, LMS integration, simulation and unknowns. Include test reproduction and requested owner decisions. Never claim tests or deployment not performed. If the major task has reached completion or a hard blocker, stop after the handoff; do not start another major phase.
