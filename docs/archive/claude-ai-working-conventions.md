# Archived: working conventions from the claude.ai chat workflow

_Written for the workflow where Claude (in a chat) handed code over as patches and Louis ran every command. Kept for the record: it is superseded by `CLAUDE.md` and `.claude/rules/` for Claude Code, where Claude edits files and runs commands itself (with Louis's approval). The rules that still apply are carried over there._

**Working conventions (standing)**
- Design before code; one step at a time, confirm before proceeding. Instructions to Louis should be short, one step at a time (a long block of commands was hard to follow).
- **Switch to the feature branch BEFORE any code is handed over.** Branch → commit → PR → merge for every task; `git status` at the start of each task. Branch names: `feat/…` for features, `fix/…` for fixes and chores; throwaway scratch work needs no branch. One fix = one branch = one PR.
- Verify claims with real tools, never from memory; check the roadmap's actual contents before assuming structure (a whole step, 3.4, was once missed). Say plainly what was and was not checked.
- Code is checked against the project's real config: `poetry run pytest`, `poetry run ruff check src/ tests/`, and **project-wide** `poetry run pyright` (matches CI; a scoped run hides errors). Ruff config: line length 100, rules E/F/I/UP/B/SIM, target py312. VS Code formats on save, so files handed over are pre-formatted with the same Ruff settings so `git diff` shows only real changes.
- Code is handed over as a `.patch` file applied with `git apply --check` then `git apply`, plus new files saved by hand; the patch is tested in a sandbox against reconstructed originals first.
- **Tests are mutation-checked by hand:** commit first (so `git restore` is safe), break one line on purpose in VS Code, run `poetry run pytest -q`, undo with `git restore <file>`, confirm `git status` is clean. A test that passes for any value of a parameter does not protect it (a surviving mutant in PR #27 proved this).
- Write-path and API changes get a manual check on the real system before merging, using throwaway `scratch/scratch_*.py` scripts (gitignored, named so pytest never collects them; read-only unless they write and clean up after themselves).
- Before merging: `gh pr checks <n> --watch` until green, then read `gh pr diff <n>`, then `gh pr merge <n> --squash --delete-branch`; never `--admin`.
- Status recaps ("where are we") in plain English: no step numbers, function names or jargon.
- Notion task pages follow Context → What was done → What was learned; later corrections are appended as "Amendment (date, PR #n)" after a `---`. Louis sets Status and Scope fields himself.
- **VS Code/Poetry:** if VS Code says no interpreter is selected, pick the Poetry one (`.venv`), not the bare pyenv 3.12.4; picking pyenv rewrites `.vscode/settings.json`, a tracked file — never commit it (use `git add src tests`, not `git add -A`, when it shows as modified). The VS Code terminal cannot read `~/Downloads` ("Operation not permitted"); use Apple Terminal for `git apply ~/Downloads/<patch>`.
