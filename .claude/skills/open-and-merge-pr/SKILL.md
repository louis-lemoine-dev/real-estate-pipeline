---
name: open-and-merge-pr
description: Run the three checks, push, open the PR, wait for CI, show the diff, then wait for my explicit go before merging.
disable-model-invocation: true
argument-hint: [pr title]
---

Open a pull request for the current branch and, only with my explicit confirmation, merge it. PR title (optional): $ARGUMENTS

Follow .claude/rules/git-workflow.md and .claude/rules/testing-and-quality.md.

1. Run `git branch --show-current`. If it is main, stop: we never work on main.
2. Run `git status`. If there are uncommitted changes, stop and show me; do not commit for me unless I say so.
3. Run the checks one by one and show the real results: `poetry run ruff check .`, `poetry run ruff format --check src tests scripts`, `poetry run pyright`, `poetry run pytest`. If any fails, stop and explain; do not push.
4. Push with `git push -u origin HEAD`.
5. Write the PR description to a file in the scratchpad (what changed and why, in plain English), then `gh pr create --base main --title "<title>" --body-file <file>`. If no title was given, derive one from the commits, lowercase type prefix, under 72 characters.
6. Run `gh pr checks <number> --watch` and report the result.
7. Run `gh pr diff <number>` and summarise it for me in plain English: files touched, anything surprising.
8. STOP here and ask me for an explicit confirmation to merge. Never merge on my earlier approval of other things. A confirmation counts only if I give it in this session after seeing the diff and green checks.
9. After I confirm: `gh pr merge <number> --squash --delete-branch`. Never use the admin override. If main moved and the PR is behind, run `gh pr update-branch <number>`, wait for the checks again, and ask me again.
10. Afterwards: `git switch main`, `git pull`, `git status`, `git log --oneline -3`. Then remind me that docs/progress.md needs updating and that the Notion write-up (/notion-task-page) is still to do.
