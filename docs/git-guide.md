# Git Guide v2 — Complete Practical Reference

Created by: Louis Lemoine
Created time: April 26, 2026 8:59 AM
Category: 4.2 Technical Skills (https://app.notion.com/p/4-2-Technical-Skills-2de50849ad7e80ae8390d75f075006ab?pvs=21)
Last edited by: Louis Lemoine
Last updated time: August 28, 2026 2:53 PM
Status: Validated
Topic: Coding
Type of Document: Documentation

# 1. The Core Mental Model

Git is a **version control system**. It tracks every change you make to your files over time, so you can always go back.

Three things to keep straight:

- **Git** = runs on your machine. All history is local.
- **GitHub** = hosts your repository online. It only updates when you explicitly push.
- **Git is not automatic**. You decide when to stage, commit, and push.

The flow in one line:

```
Edit files → stage → commit → push to GitHub
```

---

# 2. The 3 Git Environments

This is the most important concept in Git.

## 2.1 Working Directory

Your actual project folder on disk. When you edit, create, or delete a file, the change happens here. Git sees it, but hasn't recorded it yet.

## 2.2 Staging Area

The preparation zone for your next commit. You explicitly choose what goes in.

```
git add FILE       # stage one file
git add .          # stage all changes
```

Think of it as: *"These are the changes I want to include in my next snapshot."*

## 2.3 Commit History

A commit is a permanent snapshot. Once committed, it lives in Git history forever (unless you explicitly rewrite it).

```
git commit -m "Your message here"
```

## 2.4 The Full Flow

```
Working Directory → git add → Staging Area → git commit → History → git push → GitHub
```

---

# 3. One-Time Machine Setup

Run these once on any new machine:

```
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
git config --global init.defaultBranch main
```

---

# 4. Repository Setup

## Starting a new project from scratch

```
git init
git add .
git commit -m "Initial commit"
git remote add origin git@github.com:username/repo.git
git push -u origin main
```

The `-u` flag sets up tracking so future pushes only need `git push`.

## Starting from an existing GitHub repo

```
git clone git@github.com:username/repo.git
```

This downloads the full project and connects it to GitHub automatically.

---

# 5. Day-to-Day Commands

## Check what's going on

```
git status          # your main dashboard — run this constantly
git diff            # see exact line-by-line changes not yet staged
git log --oneline   # compact commit history, one line per commit
git log             # full detailed history
```

## The daily loop

```
git status
git add .
git commit -m "Describe what changed"
git push
```

---

# 6. Branching

## What a branch is

A branch is an independent line of development. `main` is your stable branch. Feature branches are temporary workspaces where you develop without touching `main`.

**Key insight**: `main` is never touched directly when working in a team — or when working properly alone.

## Branch commands

```
git branch                  # list all local branches (* = current)
git switch -c BRANCH_NAME   # create AND switch to a new branch (preferred)
git switch BRANCH_NAME      # switch to an existing branch
git branch -d BRANCH_NAME   # delete a branch (safe — only works if merged)
git branch -D BRANCH_NAME   # force delete (even if not merged)
```

## Rename current branch

```
git branch -m NEW_NAME
```

---

# 7. The Full Professional Workflow (Branch + PR)

This is how real teams work. You should use this for every non-trivial change.

## Steps 1–3 (same regardless of approach)

### Step 1 — Start a feature branch

```
git switch -c feature/my-feature
```

### Step 2 — Work and commit

```
git add .
git commit -m "feat: add my feature"
```

### Step 3 — Push the branch to GitHub

```
git push -u origin feature/my-feature
```

GitHub will show a link to create a Pull Request directly in the terminal output.

## 7.a — Via GitHub browser

### Step 4 — Open a Pull Request on GitHub

Go to GitHub → your repo → click **Compare & pull request**. CI runs automatically. Review the diff. When happy, click **Merge pull request**.

### Step 5 — Delete the branch on GitHub

Click **Delete branch** on GitHub after merging. This keeps the remote clean.

### Step 6 — Sync your local machine

This step is easy to forget — always do it after merging:

```jsx
git switch main                      # go back to main
git pull origin main                 # download the merged changes
git branch -d feature/my-feature     # delete the local branch
```

After this, your local `main` matches GitHub exactly.

## 7.b — Via GitHub CLI (gh) — recommended

Requires the GitHub CLI to be installed (`brew install gh`) and authenticated (`gh auth login`). This approach never requires opening a browser.

### Step 4 — Open a Pull Request from the terminal

```jsx
gh pr create --title "feat: my feature" --body "Description of what this PR does." --base main
```

### Step 5 — Merge, clean up, and sync in one command

```jsx
gh pr merge --squash --delete-branch
```

`--squash` combines all branch commits into a single clean commit on main. `--delete-branch` automatically handles everything that 7.a does manually in steps 5 and 6: deletes the remote branch, deletes the local branch, switches you back to main, and pulls the merged commit. No manual cleanup needed.

---

# 8. Fixing Mistakes

This is the section the original guide was missing. Git is designed to make mistakes recoverable.

## 8.1 "I staged something I didn't want to"

```
git reset              # unstage everything, keep your file changes
git reset FILE         # unstage one specific file
```

## 8.2 "I want to fix my last commit (before pushing)"

Use `--amend` to update the most recent commit in place:

```
# Fix the files first, then:
git add .
git commit --amend --no-edit    # keeps the same commit message
git commit --amend -m "New message"  # changes the message too
```

⚠️ Only use amend on commits that have NOT been pushed yet.

## 8.3 "I amended a commit that I already pushed"

If the commit is already on GitHub (e.g. on a feature branch), you need to force-push:

```
git push --force
```

⚠️ Only safe on YOUR OWN feature branches. Never force-push to `main`.

## 8.4 "I want to undo my last commit entirely"

```
git reset --soft HEAD~1    # undo commit, keep changes staged
git reset HEAD~1           # undo commit, keep changes in working dir
git reset --hard HEAD~1    # undo commit AND discard all changes (destructive)
```

`HEAD~1` means "one commit before the current one". `HEAD~2` means two commits back.

## 8.5 "I want to discard all local changes in a file"

```
git checkout -- FILE
```

This restores the file to how it was at the last commit. Irreversible.

## 8.6 "I want to directly edit a file instead of using echo"

Always prefer opening the file in your editor and editing it manually over terminal tricks like `echo`. It's clearer, safer, and gives you full control over the result.

---

# 9. Remote / GitHub Commands

```
git remote add origin URL       # link local repo to GitHub
git remote -v                   # verify remote connection
git push origin BRANCH          # push a branch to GitHub
git push -u origin BRANCH       # push and set up tracking
git push                        # push (when tracking is already set)
git push --force                # force push (rewrites remote history — use carefully)
git pull origin BRANCH          # download and merge remote changes
git pull                        # pull (when tracking is already set)
```

---

# 10. Reading a Diff

When you open a PR or run `git diff`, you see:

```
- VS Code + Cursor         ← red: line that was removed
+ VS Code + Cursor# oops   ← green: line that was added
```

Lines starting with `-` were in the original. Lines starting with `+` are new. Lines with neither are unchanged context.

Always read the diff before merging a PR.

---

# 11. Commit Message Best Practices

A good commit message explains *what* changed and *why*, not *how*.

```
feat: add GitHub API integration
fix: correct SSH key path in config
chore: update README with setup instructions
refactor: extract data parsing into separate module
test: add unit tests for math_utils
```

Convention: use lowercase, start with a type prefix, keep it under 72 characters.

---

# 12. Complete Cheat Sheet

## Machine setup (once)

```
git config --global user.name "Name"
git config --global user.email "email"
git config --global init.defaultBranch main
```

## New project (once per project)

```
git init
git add .
git commit -m "Initial commit"
git remote add origin git@github.com:user/repo.git
git push -u origin main
```

## Daily loop

```
git status
git add .
git commit -m "message"
git push
```

## Feature branch workflow

### Option A — Via GitHub browser

```jsx
git switch -c feature/name       # start
git add . && git commit -m ""    # work
git push -u origin feature/name  # push
# → open PR on GitHub → merge → delete branch on GitHub
git switch main                  # sync local
git pull origin main
git branch -d feature/name
```

### Option B — Via GitHub CLI (recommended)

```jsx
git switch -c feature/name       # start
git add . && git commit -m ""    # work
git push -u origin feature/name  # push
gh pr create --title "" --body "" --base main   # open PR
gh pr merge --squash --delete-branch            # merge + full cleanup
```

`gh pr merge --squash --delete-branch` handles merge, remote branch deletion, local branch deletion, switch to main, and pull — all in one command.

## Fixing mistakes

```
git reset                     # unstage everything
git commit --amend --no-edit  # fix last commit
git push --force              # force push after amend (own branch only)
git reset --soft HEAD~1       # undo last commit, keep changes
git reset --hard HEAD~1       # undo last commit, discard changes
```

## Useful inspection

```
git status
git diff
git log --oneline
git branch
```

---

# 13. Simple Rules to Remember

1. `git status` is your best friend — run it constantly
2. `main` is sacred — never work directly on it
3. One branch = one feature or fix
4. Commit often, with clear messages
5. Always read the diff before merging

6a. After merging a PR via browser: switch main → pull → delete local branch

6b. After merging a PR via gh CLI: `gh pr merge --squash --delete-branch` handles everything automatically — no manual cleanup needed

1. `--amend` and `--force` are safe on your own branches, dangerous on shared ones
2. When in doubt, open the file in your editor — don't use terminal tricks

---

# 14. Additions (8 November 2026)

Added after the guide was validated. The Notion copy of the guide does not have these yet.

1. **Merge refused with green checks?** `main` moved and branch protection requires the branch to be up to date: `gh pr update-branch <number>`, wait for the checks again, then merge. Never use `--admin`.
2. **After a merge, sync local `main`.** If the terminal shows an SSH error after `gh pr merge`, the PR may still have merged: `git pull origin main`, then `git status` and `git log --oneline -3`.
3. **Stage by name, not with `-A`:** `git add src tests` (and `scripts`, `docs` when they changed). Editor noise such as `.vscode/settings.json` must never be committed.
4. **Always `git commit -m "message"`.** Without `-m`, git opens vim: `Esc` then `:wq` saves, `Esc` then `:q!` aborts.
5. **Branch prefixes:** `feat/`, `fix/`, `chore/`, `refactor/`, `test/`, `docs/`. One fix = one branch = one PR.
6. **Switch to the feature branch before any code is written** (`git status`, then `git switch -c <name>` from an up-to-date `main`).
7. **Quote globs in zsh** (`--include="*.py"`), and on macOS use `sed -i ''`.
8. **With Claude Code:** Claude may run these commands itself in Manual mode, with Louis approving each one; merging happens only after Louis's explicit confirmation. See `CLAUDE.md`.
