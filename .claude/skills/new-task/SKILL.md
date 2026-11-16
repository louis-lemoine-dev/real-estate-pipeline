---
name: new-task
description: Start a new task safely: update main and create the branch for it.
disable-model-invocation: true
argument-hint: <prefix>/<short-name>
---

Start a new task on a fresh branch. The branch name is: $ARGUMENTS

1. Check the name. The prefix must be one of feat/, fix/, chore/, refactor/, test/, docs/ (see .claude/rules/git-workflow.md). If the name is missing or has another prefix, stop and ask me.
2. Run `git status`. If there are uncommitted changes, stop and tell me what they are. Do not stash or discard anything.
3. Run `git switch main` and `git pull`.
4. Run `git switch -c $ARGUMENTS`.
5. Run `git status` and show me the output, then say in one plain sentence what the branch is for. One task = one branch = one PR.
