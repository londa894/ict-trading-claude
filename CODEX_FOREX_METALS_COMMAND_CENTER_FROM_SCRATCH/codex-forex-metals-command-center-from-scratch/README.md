# Codex Rebuild Package

Use this package to rebuild the Forex & Metals ICT/SMC Trading Command Center from scratch in Codex.

Files:
- `CODEX_MASTER_BUILD_SPEC.md` — all 19 steps consolidated as the source of truth.
- `CODEX_START_PROMPT.txt` — first prompt to paste into Codex.

Workflow:
1. Create a new empty repository/project in Codex.
2. Add these files at the repo root.
3. Paste `CODEX_START_PROMPT.txt` into Codex.
4. Let Codex complete Phase 0 only.
5. Verify tests/build.
6. Then tell Codex: `Continue to Phase 1 using the master spec.`
7. Repeat one phase at a time.

Do not provide the old generated Phase 5 repository if the goal is a clean rebuild.
