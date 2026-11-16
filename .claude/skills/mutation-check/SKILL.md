---
name: mutation-check
description: Hand mutation check: break one line on purpose and confirm a test fails, then restore and verify the tree is clean.
disable-model-invocation: true
argument-hint: <file or function to check>
---

Verify that our tests really protect this code by breaking it on purpose. Target: $ARGUMENTS

Follow the mutation-check rules in .claude/rules/testing-and-quality.md.

1. Run `git branch --show-current` (must not be main) and `git status` (must be clean). If there are uncommitted changes, stop: the work must be committed first so `git restore` can undo the mutation safely.
2. Read the target and choose ONE meaningful line to break (a condition, a comparison, a constant, a return value, a filter). Tell me which line and how you will break it, and wait for my OK.
3. After my OK, make exactly that one change.
4. Run `poetry run pytest -q` and show the real output.
5. Expected result: at least one test fails. Say which test caught it. If everything still passes, the tests do not protect that line: say so clearly and propose a test to add (do not add it without my OK).
6. Undo with `git restore <file>`.
7. Run `git status` and show it. It must be clean. If it is not, say so and fix it before anything else.
8. Run `poetry run pytest -q` once more to confirm the suite is green again.
9. Summarise in plain English: what was broken, which test failed, restored and clean.
One mutation per run. If I want more, I will run the skill again.
