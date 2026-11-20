# Tools, commands and gotchas

_Last updated: 8 November 2026. Reference material: tooling in use, commands, and hard-won gotchas. Principles and rules are in `CLAUDE.md` and `.claude/rules/`._

## The twelve communes (Métropole Toulon Provence Méditerranée)

| Code | Commune | API count |
|---|---|---|
| 83034 | Carqueiranne | 3,159 |
| 83047 | La Crau | 3,843 |
| 83062 | La Garde | 6,080 |
| 83069 | Hyères | 18,108 |
| 83090 | Ollioules | 3,023 |
| 83098 | Le Pradet | 3,080 |
| 83103 | Le Revest-les-Eaux | 773 |
| 83126 | La Seyne-sur-Mer | 17,859 |
| 83129 | Six-Fours-les-Plages | 13,529 |
| 83137 | Toulon | 47,296 |
| 83144 | La Valette-du-Var | 6,320 |
| 83153 | Saint-Mandrier-sur-Mer | 2,499 |

Defined once in `dvf/orchestrator.py` (`DEFAULT_COMMUNE_CODES`); the runner accepts only these.

## Commands

- Checks, all three before every PR: `poetry run pytest`, `poetry run ruff check .`, and project-wide `poetry run pyright` (matches CI; a scoped run hides errors). Ruff: line length 100, rules E/F/I/UP/B/SIM, target py312.
- Load DVF into Supabase: `poetry run python scripts/load_dvf.py [commune codes]` (no codes = all twelve; about 75 minutes; use `caffeinate -i` so the Mac does not sleep). Safe to re-run: unchanged rows are left alone.
- Scrape ParuVendu into Supabase (Toulon, 5 pages): `poetry run python scripts/scrape_paruvendu.py --max-pages 5 [--dry-run]`. The real run writes to the database (ask Louis first); `--dry-run` writes nothing. Safe to re-run. About 15 seconds.
- Apply a migration: `poetry run python scripts/apply_migration.py db/migrations/<file>.sql`. The script splits the file on every `;`, so a semicolon inside a migration comment can break it (a `;` at the very end of a comment line is harmless).
- Scratch scripts: `scratch/scratch_*.py` (gitignored, never collected by pytest), run with `poetry run python`, read-only unless they write and clean up after themselves.

## Tools and resources

- **Supabase:** project ref in `CLAUDE.local.md` (the repo is public, so it stays out of tracked files), region `eu-central-2` (Zurich), Session pooler. MCP connector scoped read-only with the custom URL `https://mcp.supabase.com/mcp?project_ref=<project-ref>&read_only=true` (the directory connector grants org-wide access with no way to restrict it). It supports `list_tables` (`verbose=True` for full column metadata) and `execute_sql` for SELECT queries; it cannot write. Writes go through the project's own committed scripts.
- **Notion MCP tools:** `notion-search`, `notion-fetch` (page ID or full URL), `notion-update-page` (`replace_content` replaces the whole body; `insert_content` with `position: {type: end}` appends), `notion-create-pages`, `notion-query-data-sources`, `notion-move-pages`, `notion-update-data-source`.
- **GitHub CLI (`gh`):** repo creation, PR management, branch protection, run log inspection.
- **CI:** three parallel jobs (Ruff, Pyright, pytest) on push and PRs to `main`; `actions/checkout@v7`, `actions/setup-python@v7`, `actions/cache@v5`. Branch protection on `main`: all three checks required, `enforce_admins: true`, no force pushes, branch must be up to date, 0 mandatory approvals.
- **DVF+ API:** `apidf-preprod.cerema.fr`, a beta/preprod endpoint with real unreliability. `fields=all` is required for the room-count bucket fields; `http://` to `https://` normalisation is needed on pagination links.
- HackerRank is used for Python practice alongside each roadmap phase.

## Technical gotchas

- Pyright `exclude` replaces the defaults entirely rather than extending them: re-add standard defaults (e.g. `.venv`) explicitly, or the type checker hangs crawling the venv.
- Never use `is True` / `is False` against pandas values: use truthy / `not` checks (`numpy.bool_` is not Python's `bool`).
- `gh api -f` always serialises as strings: use `gh api --input -` with a heredoc for booleans and integers.
- `Protego.can_fetch` takes `(url, useragent)`, the reverse of `urllib.robotparser`'s `(useragent, url)`.
- `rooms` from DVF's bucketed fields needs exactly one bucket = 1, all others = 0, AND `nblocmut == 1`; otherwise `None`. A value of 5 means "5 or more".
- `.where(pd.notna(df), None)` silently leaves NaN in a float64 column: cast to object first so a missing value reaches Postgres as NULL (SQLite binds NaN as NULL by itself, so only a pure test catches a leak).
- A literal `%` in a raw SQL string breaks under psycopg; wrap it in `text()`.
- The ParuVendu Toulon search has exactly 5 pages; page 6 and beyond are HTTP 404, and the scraper stops the whole run on any error. Use `--max-pages 5` (re-check by probing if the site changes).
- Python reuses cached compiled code when a file keeps the same size and mtime (whole seconds): a hand mutation check can wrongly report a same-size mutant as killed. Delete the `.pyc` files and set `PYTHONDONTWRITEBYTECODE=1` for mutation runs.
- pytest collects any `*_test.py` / `test_*.py` it finds and runs top-level code on import: hence `testpaths = ["tests"]` (PR #23) and the `scratch_*.py` naming.
- Small samples do not generalise: conclusions drawn from 6–7 items (e.g. the `lazyload_bloc` filter) can be wrong at full scale, and a NOT NULL on a source-derived column needs the full dataset behind it, not 200 rows (the null-price lesson, PR #25).
- Free-tier Supabase pauses after about a week of inactivity (it happened twice) and must be restored from the dashboard. Data and schema come back intact, but while it is paused or still loading the read-only connector fails with "Database is possibly not accessible at the moment", and a query run too early can look like "no tables".
- Merging can be refused with green checks if `main` moved: `gh pr update-branch <number>`, then re-check and merge (never `--admin`).
- Notion `alter column` drops `COMMENT` annotations: field descriptions must be re-added in the UI afterwards.
- VS Code/Poetry: if VS Code says no interpreter is selected, pick the Poetry one (`.venv`), not the bare pyenv 3.12.4. Picking pyenv rewrites `.vscode/settings.json`, a tracked file: never commit it (use `git add src tests`, not `git add -A`).

## Terminal and macOS gotchas (learned 3–8 Nov 2026)

- zsh: an unquoted glob such as `--include=*.py` fails with "no matches found" and searches nothing, which looks like a clean result. Quote it (`--include="*.py"`) and check that the command actually ran.
- macOS `sed` needs an empty backup suffix: `sed -i '' 's/old/new/g' file`.
- `git commit` without `-m` opens vim. Leave it with `Esc` then `:wq` (save) or `:q!` (abort). Always use `git commit -m "message"`.
- After `gh pr merge --squash --delete-branch`, an SSH drop can leave the local `main` behind even though the PR merged: run `git pull origin main`.
- Pushing needs the SSH key unlocked (passphrase prompt otherwise); under Claude Code it must be loaded in ssh-agent or the macOS Keychain.
- The Claude Code guard hook (`.claude/hooks/guard-git.sh`) reads the whole command text, so a command line containing phrases like the forbidden `git add` forms or the admin merge flag is blocked even inside a commit message or PR text. Put such text in a file (`git commit -F`, `gh pr create --body-file`).

## Notion gotchas

- Page content is stored with inline markup (`<b>`, `<code>`): an `old_str` for `update_content` must match the stored markup, not the plain-text rendering. Fetch the page first to see its literal form.
- For an authoritative list of steps, query the roadmap database (`notion-query-data-sources` on its `collection://` URL with `SELECT "Step", "Status", ... WHERE "Phase" = '...'`). Relying on `notion-search` or a parent page's children once hid a whole step (3.4).
