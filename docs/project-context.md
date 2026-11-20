# Project context

_Stable background: why the project exists, the environment, and how Notion and GitHub divide the work. For the current state see `progress.md`._

## Purpose
- A diff-aware pipeline that scrapes ParuVendu listings and DVF transactions into Supabase, generates AI-backed investment insights grounded in real historical comparables for listings that match fixed criteria, and syncs them into Louis's existing Notion investment-triage workflow.
- It serves two goals at once: a working home-buying tool for the Toulon/Var area, and a portfolio piece demonstrating a real, closed-loop, AI-augmented data engineering system. Work is portfolio-facing throughout, so deliverables are professional and the methodology is serious.
- Louis's wider aim is to build modern AI-powered software independently, with agentic tools, MCP, Supabase, FastAPI and the Anthropic API. Solo project.

## How Notion and GitHub divide the work
- GitHub is the source of truth for implementation. Notion is the project-management and documentation layer.
- The pipeline has its own dedicated Notion roadmap (8 phases, 25 steps, 86 tasks, V1/V2 scoped), with a pointer entry on the broader Python & Dev Roadmap, which is a 6-phase learning roadmap that works as an index (projects are numbered X.Y, tasks X.Y.Z). Each large project gets its own dedicated Notion database. FastAPI is a standalone learning project (3.1 on the Dev Roadmap), not part of this pipeline.
- V2 items are real numbered tasks with a Scope tag in the roadmap.

## Related work
- **GitHub Analyzer CLI** (complete): a typer/rich CLI that fetches repository metadata, commits, PRs and branches from the GitHub REST API; used as a portfolio case study.
- The portfolio itself lives in Notion (recruiter-facing presentation layer); GitHub holds the code.

## Planned schedules (Phase 7)
- ParuVendu daily (scrape → diff → insight → Notion) and DVF semi-annual, aligned to DGFiP's April/October publication calendar, via GitHub Actions. GitHub Actions Secrets handling is deferred to that phase.

## Dev environment
- macOS on Apple Silicon, zsh, Homebrew, pyenv (Python 3.12.4), pipx, Poetry, Ruff, Pyright, pytest, VS Code with the Claude Code extension, GitHub with SSH authentication, GitHub Actions CI. GitHub handle: `louis-lemoine-dev`.
- Package name is `real_estate_pipeline` (`src/` layout with `paruvendu/`, `dvf/` and `common/` subpackages).
- ParuVendu search URL (Toulon area): `https://www.paruvendu.fr/immobilier/vente/toulon/`.
- HackerRank is used for Python practice alongside each roadmap phase.

## Starting another Python project (checklist)
This repo already follows it. Order matters: the `.gitignore` comes before any `poetry add`, so `.venv/` is never staged.
1. `mkdir` and `cd` into the project folder.
2. `git init`.
3. Create `.gitignore` immediately, covering `.venv/`, `__pycache__/`, `*.pyc`, `.env`, `.ruff_cache/`, `.pytest_cache/`, `.mypy_cache/`, build artifacts, `.DS_Store`.
4. `gh repo create --source=. --remote=origin`.
5. `poetry init --python "^3.12" -n`, then correct `requires-python` from caret syntax to `>=3.12` (PEP 440).
6. `poetry add --group dev ruff pyright pytest`.
7. Add the Ruff config block to `pyproject.toml`.
8. Confirm the Ruff VS Code extension is installed.
9. Create `.vscode/settings.json`: `defaultInterpreterPath` pointing at `.venv`, Ruff as the default formatter, `formatOnSave`, `codeActionsOnSave` with fixAll and organizeImports.
10. Verify by hand that the VS Code interpreter picker shows the project's own Poetry `.venv`, not the global Python.
11. Commit the baseline as `chore: initial project setup` on `main`, before any branching.
12. `git push -u origin main`.

Only after that baseline exists is a feature branch created for the first real task. CI (Ruff, Pyright, pytest) is set up from the very start of every new project, not deferred. This pipeline's CI was deferred by its original roadmap design, which is acknowledged as not the preferred pattern.
