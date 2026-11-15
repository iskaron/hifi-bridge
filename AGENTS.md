## Non-negotiable environment constraint

No physical audio/Bluetooth/Pi hardware exists on the development host. Run **all builds/tests and LMS/Squeezelite simulations in Docker only**; never probe local devices or require actual Bluetooth in phase 1. Follow the full contract in `CLAUDE.md`.

# Agent instructions

Read `CLAUDE.md`, then all documents in `docs/` that apply to the current phase. The visitor must experience an ordinary Bluetooth speaker, not a Lyrion configuration project. Treat `docs/PRODUCT_SPEC.md` and `docs/DECISIONS.md` as accepted requirements, not ideas to override silently.

The project has two gated major tasks: `prompts/01_AUDIO_SPIKE.md` first; `prompts/02_FULL_PRODUCT.md` only after explicit owner approval. Implement and test independently within scope. No routine stop after each small step. Never falsely report physical measurements or hardware compatibility. Handoff at true phase completion/blocker via `reports/HANDOFF_FOR_CHATGPT.md`.

Do not change production devices or settings. No publication, flash, push, tag, add or commit without explicit approval. Source should be portable and documented. See `CLAUDE.md` for full safety and reporting rules.
