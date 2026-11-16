# Git and PR workflow

Louis's non-negotiable workflow for every task: branch → code → test → PR → CI green → squash-merge. The Git Guide (`docs/git-guide.md`) is authoritative for commands: check it rather than relying on memory.

## Branches
- Start every task with `git status` (clean tree, on `main`, up to date with `origin/main`), then `git switch -c <prefix>/<name>`. Use `git switch -c`, not `git checkout -b`; rename with `git branch -m`.
- Prefixes: `feat/` for new functionality, `fix/` for fixes, plus `chore/`, `refactor/`, `test/`, `docs/` as in the Git Guide. Name the branch after what it contains.
- One task = one branch = one PR. "One branch, multiple PRs" is mechanically broken with `gh pr merge --squash --delete-branch`, which deletes the branch.
- After switching, show Louis the `git status` result so he can see which branch you are on before any file is edited. If you find uncommitted work on `main`, stop and tell him.
- Throwaway scratch work needs no branch (scratch files are gitignored).

## Commits
- Message: lowercase, a type prefix (`feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`), says what and why rather than how, under 72 characters. Always `git commit -m "…"`.
- Stage by name: `git add src tests scripts docs`. Never `git add -A` or `git add .`. `.vscode/settings.json` can show as modified (picking the pyenv interpreter rewrites it): never commit it.
- Run `git status` before every commit. Commit before any mutation check, so `git restore` is safe.
- Amend only commits that are not pushed yet. Force-push only your own feature branch, and only if Louis agrees. Never force-push `main`.

## Pull requests and merging
- Push with `git push -u origin <branch>` and open the PR with `gh pr create --base main`. Title in commit style; the body says what changed, why, and how it was checked, including any real-system check.
- Wait for the checks: `gh pr checks <n> --watch`. Then read `gh pr diff <n>` and confirm it contains only the intended files.
- Merge only after Louis has explicitly confirmed in this session: `gh pr merge <n> --squash --delete-branch`. Never `--admin`.
- If the merge is refused despite green checks because `main` moved: `gh pr update-branch <n>`, wait for the checks again, then merge.
- `--delete-branch` also switches to `main` and pulls. If an SSH error appears after the merge, the PR may still have merged: run `git pull origin main`, then `git status` and `git log --oneline -3`.
- `main` is protected on GitHub: three required checks (Ruff, Pyright, pytest), `enforce_admins`, no force pushes, branch must be up to date, 0 mandatory approvals. Never try to get around it.
- Pushing needs the SSH key unlocked (ssh-agent or the macOS Keychain). If a push stops at a passphrase prompt, tell Louis and let him unlock it.
