# Real Estate Pipeline & AI Insights

Solo portfolio project and a real home-buying tool for the Toulon/Var area. It scrapes ParuVendu listings and loads DVF transactions into Supabase (Postgres); later phases add a dashboard, AI investment insights grounded in DVF comparables, a one-way Notion sync and scheduling. Python 3.12, Poetry, SQLAlchemy + psycopg v3, pandas, pytest, Ruff, Pyright, GitHub Actions CI. Repo: `louis-lemoine-dev/real-estate-pipeline`. Notion holds the roadmap and the task write-ups; this repo holds the code and the context files.

## Start of every session
1. Read `docs/progress.md` (state and Next steps), then tell Louis in plain English where we are and what the next step is. No step numbers, function names or jargon in that recap.
2. Read on demand: `docs/decisions.md` (why choices were made: do not re-litigate them), `docs/parking-lot.md` (open items), `docs/tools-and-gotchas.md` (commands, communes, gotchas), `docs/project-context.md` (stable background), `docs/git-guide.md`.
3. Detailed rules are in `.claude/rules/`: `git-workflow`, `testing-and-quality`, `python-and-data`, `notion-and-docs`.

## How to work with Louis
- **Design before code.** For anything non-trivial, use Plan mode: propose the approach, state the trade-offs, wait for his confirmation, then build.
- **One step at a time, confirm before proceeding.** Give a decisive recommendation (he wants advice, not open-ended questions) and flag trade-offs explicitly instead of silently picking defaults.
- **Verify, don't assume.** Check library behavior, API behavior, versions, URLs, file contents and the roadmap's real structure with real tools. Say plainly what you checked and what you did not.
- **Ask when the scope is ambiguous:** "if I say 1.2, work on 1.2". Never guess which Notion item, task or file to touch.
- **Flag real consequences.** Louis defers to technical recommendations where he lacks experience, but engages when a decision has lasting effects (making the repo public, schema changes, anything hard to undo). Name those consequences before he decides.
- **Honest over agreeable.** Push back, with reasons, when something looks wrong. He verifies claims and asks "why" before accepting.
- **Teach directly** in genuinely new territory: why before how, no Socratic quizzing.
- **Louis is a beginner.** One thing at a time: one instruction per message, at most one question, plain words, and say what each step is for. When something goes wrong, first explain in plain terms what happened and that nothing is lost, then give the one next step. Never run two tasks or two branches in parallel.
- **Plan across dependencies** before implementing: note which sub-tasks are parallel and which sequential.
- When Louis must do something himself (restore the Supabase project, enter a passphrase, run a long command), state it as its own explicit numbered step, never buried in prose.
- He writes English with occasional French: reply in the language of his message.

## Commands (always through Poetry)
- Before every PR, all three: `poetry run pytest`, `poetry run ruff check .`, `poetry run pyright` (Ruff and Pyright project-wide, as CI runs them). Format before committing: `poetry run ruff format src tests scripts`.
- Scripts and scratch files: `poetry run python <script>`. Scratch files are `scratch/scratch_*.py` (gitignored).
- The DVF load writes to Supabase and takes about 75 minutes for all twelve communes: `poetry run python scripts/load_dvf.py [commune codes]`. Ask Louis to run it in his own terminal, with `caffeinate -i`.
- Project skills (`.claude/skills/`): `/new-task`, `/open-and-merge-pr`, `/mutation-check`, `/notion-task-page`. They run only when Louis types them.

## Hard rules
- Never work or commit on `main`. Before editing any file: `git status`, then `git switch -c <prefix>/<name>`, with a prefix from `.claude/rules/git-workflow.md`, from an up-to-date `main`, and show Louis the result.
- Never merge a PR without Louis's explicit confirmation in the current session. Never use `--admin`, never force-push `main`; force-push only your own feature branch, and only after Louis explicitly agrees. Never `git add -A` or `git add .` (name the paths: `git add src tests scripts docs`).
- Never read, print or commit `.env`; never commit `.vscode/settings.json`.
- This repo is public: no credentials, Supabase project ref, personal data or anything private in tracked files. Private notes live in `CLAUDE.local.md` (gitignored).
- Anything that writes to Supabase (migrations, load runs, scratch scripts that write) needs Louis's go at that moment, and scratch scripts must clean up after themselves. The Supabase MCP connector is read-only.
- Louis sets the Notion Status, Scope and Done fields himself: never change them.

## Order of work for every task (agreed with Louis, 20 Nov 2026)
One task = one branch = one PR, and only one branch is open at a time. Never start the next task, or open a second branch, before every step below is done.
1. **Code** on the task's branch. Run all the checks (pytest, ruff, pyright) and the mutation and real-system checks that apply.
2. **Notes, on the same branch, before the merge:** `docs/progress.md` (what was built and learned), `docs/decisions.md` for any decision made, `docs/parking-lot.md` for anything left open or resolved. Commit them with the code, so the PR carries both.
3. **PR:** push, open the PR, wait for green checks, summarise the diff for Louis in plain words, and merge only after his explicit yes (`/open-and-merge-pr`).
4. **Notion write-up:** draft it in the chat, Louis approves, then write it to the page (`/notion-task-page`).
5. **Only then** propose the next task, and say in one sentence what it is.

## Keeping the context current
- The notes of each task are written before its merge (step 2 above), the Notion write-up after it (step 4). Do not use the chat-context-handoff skill in this repo; this routine replaces it.
- Add a rule to this file or to `.claude/rules/` when Louis corrects the same thing twice. Keep this file under about 150 lines; notes meant only for humans go in HTML comments.

## Compact instructions
When compacting, keep: the current task and branch, the agreed design and decisions, commands already run with their real results, files changed so far, and anything Louis asked to remember. Re-read `docs/progress.md` afterwards.
