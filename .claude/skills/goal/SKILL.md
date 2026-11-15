---
name: goal
description: Launch an explicitly named major project goal such as audio-spike, build-product, or handoff.
argument-hint: "audio-spike | build-product | handoff"
disable-model-invocation: true
---

The owner selected project goal: **$ARGUMENTS**.

- For **audio-spike**, execute all instructions in `prompts/01_AUDIO_SPIKE.md` with `CLAUDE.md` and applicable docs; complete and hand off; stop before phase 2.
- For **build-product**, verify the owner has explicitly approved phase 2 and execute `prompts/02_FULL_PRODUCT.md`; major handoff at the end.
- For **handoff**, generate `reports/HANDOFF_FOR_CHATGPT.md` using `docs/HANDOFF_TEMPLATE.md`, without launching a new phase.
- For an unknown, missing or ambiguous goal, do **not** guess or run tasks; explain the three available goal names.

Within the selected scope, work autonomously on independent subtasks; do not ask for hourly reviews. All safety and product requirements in `CLAUDE.md` apply.
