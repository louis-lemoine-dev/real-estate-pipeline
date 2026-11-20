# Decisions log

_Last updated: 8 November 2026. Decisions and their reasons, so they are not re-litigated. Older entries keep their original wording; later evidence is added in italics. The working rules written for the claude.ai chat workflow are archived in `archive/claude-ai-working-conventions.md`; the current rules are in `CLAUDE.md` and `.claude/rules/`._

## Decisions log

**Project and architecture**
- **Dedicated per-project roadmap, not tasks-in-the-Dev-Roadmap** — standing pattern for any large, multi-phase project.
- **Supabase (Postgres) over DuckDB** — hosted, network-accessible store needed for later networked/agent access.
- **FastAPI dropped from this project's scope** — kept as standalone learning project 3.1 on the Dev Roadmap.
- **Diff-aware ingestion replaces delete-and-replace** — avoids redundant AI calls, enables price-change tracking.
- **Price-change history logged (V1) via an event table; exploiting it is V2.**
- **Two independent schedules** — ParuVendu daily, DVF twice-yearly aligned to DGFiP's publication dates.
- **Fixed investment criteria as code-level config** — studio, Toulon Centre, ≤3000€/m², filtered before any AI call.
- **AI insights must be grounded in retrieved DVF comparables** — model selection is its own explicit task.
- **Notion sync is one-way (Supabase → Notion) for V1.**
- **CI from the start; repository public** for branch protection on the Free tier (checked for secrets first).
- **Region/connection choices optimize for the developer's location and constraints** — Zurich over Paris, Session pooler over Direct. Direct Postgres over `supabase-py`.
- **Migrations are versioned numbered files applied via a committed script**; RLS on with no policies.
- **Every schema/type choice traces back to the real source of each field**; ParuVendu prices are `integer` (whole euros), DVF money `numeric`. A NOT NULL on a source-derived column is a claim about the source and needs the full dataset behind it, not a 200-row sample (lesson from the null-price fix).

**Ingestion and data integrity (Phase 3.3–3.4)**
- **"Modified" means price only**; field-level history is a separate V2 design.
- **Delisting needs two safeguards:** a second consecutive miss (elapsed-time based, assumes a roughly daily cadence — a never-triggered run could wrongly cross the threshold; an exact miss-counter column was deferred) and a run-level sanity gate that stops a collapsed scrape.
- **`delisted_at` semantics:** the day the run concluded the listing was gone; recorded once only; cleared when the listing returns; reinstatements are counted and logged. Nothing is ever deleted.
- **Update every listing seen again**, not only modified ones, so `last_seen_at` stays fresh; the price-only rule decides only whether an *event* is logged.
- **Price-change event first, in the same transaction as the update** — the old price exists nowhere else; atomicity proven by test.
- **Validate scraped columns where provenance is known** (inside `classify_listings`, against `LISTING_COLUMNS`), and **write an explicit allow-list of columns** (not a drop-list).
- **Shared plumbing built at the second/third use:** `get_engine()` with `pool_pre_ping`, `run_in_transaction()` retry helper. DB retry tuning (1s/2s/4s) is deliberately separate from the DVF+ API client's.
- **Classification stays truthful; the write step decides** whether anything needs doing.

**DVF fetch and data (PRs #25–#27)**
- **Mutations with no price are kept** (price nullable), consistent with the cleaning rule that no rows are dropped; only a null value becomes `None`, an absent key still raises. `is_market_transaction` False for such rows means "unusable for price analysis".
- **A commune is fetched completely or the call fails.** The page ceiling is derived from the API's `count`, not a fixed number; a hit ceiling with pages pending, a missing `count`, or a distinct-row count different from `count` raises `IncompleteFetchError`. Silent truncation is the worst failure because everything downstream (comparables for the AI step) trusts the table.
- **No automatic re-walk of a commune on a count mismatch in V1.** A re-walk costs minutes; measure how often mismatches happen first. The loader will process one commune at a time and is repeatable. *(Measured 3–4 Nov 2026: no count mismatch in 24 full walks, so still no re-walk.)*
- **`allow_partial=True` is the only way to get a partial fetch**, an explicit opt-in for sampling.
- **First request: 65 s timeout, 5 attempts; later pages: 15 s, 3 attempts.** Chosen from timing tests; the explanation for the first-request slowness is unproven and the code comment says only what was observed. *(Evidence from two full loads: no commune exhausted its attempts, but Hyères needed 4 of 5; revisit before scheduling in Phase 7.)*

**DVF load design (agreed by Louis on 3 Nov 2026; built in PR #28, see "DVF load as built" below)**
- **One commune at a time:** fetch → parse → clean → validate → upsert in its own transaction, so a failed commune costs only itself and a re-run is safe. A mutation fetched through two communes is the same row both times (`commune_code` is `l_codinsee[0]`, not the commune queried), so the second upsert finds it unchanged.
- **Upsert on `id_mutation`, updating an existing row only when it actually differs;** report inserted / updated / unchanged. The load never deletes a mutation.
- **Batches of about 1,000 rows** (13 columns × 1,000 = 13,000 bind parameters, far below Postgres's limit of 65,535 per statement).
- **Validate before writing:** expected columns defined once next to the `Transaction` dataclass (like `LISTING_COLUMNS`), no nulls in the NOT NULL columns, no duplicate `id_mutation` in the input; write an explicit allow-list of columns.
- **NaN becomes a real NULL** for `price` and `rooms` (object cast; the 3.3.2 lesson).
- **The three derived columns are not stored in V1.** The table stays a faithful copy of the 13 source fields; Phases 4–5 compute `price >= 2`, `price / surface_bati` and `rooms IS NOT NULL` in SQL, and a view can expose them later without storing them. Cost: the logic exists twice (pandas and SQL). Revisit only if queries get slow.
- **Settled in PR #28:** inserted / updated / unchanged are counted by reading the stored rows, comparing, then writing, inside one transaction (see "DVF load as built").

**DVF load as built (PRs #28–#30, 3–4 Nov 2026)**
- **Read → compare → write in one transaction, not a Postgres `ON CONFLICT` upsert.** The counts (inserted / updated / unchanged) are exact by construction, and the identical code runs on the in-memory SQLite the tests use. A Postgres-only counting trick (`RETURNING (xmax = 0)`) could not run there, so the tests would have exercised a different statement than production. Cost: one extra read per batch of 1,000 rows, with no race risk because there is a single writer.
- **The comparison normalises both sides:** Decimal (Postgres `numeric`) and float become a float rounded to 6 decimals; None and NaN become None. Without it every reload would flag every row as changed. The rounding is insurance (a float round-trips exactly through `numeric`) and is pinned by a test.
- **Plain Python types reach the driver** (`rooms` as `3`, not `3.0`; a non-whole room count is refused instead of truncated), and NaN becomes a real NULL. SQLite hides this (it binds NaN as NULL by itself), so only a pure test protects it.
- **A mutation returned by two communes is stored once;** the second commune reports it as unchanged (216 cases in the full load). Each row carries its primary `commune_code`.
- **Runner:** one commune per transaction; a failed commune is recorded (with its code and error text, cut at 500 characters) and the run continues; the exit code is 1 if any commune failed; an empty fetch is skipped; only the twelve known communes are accepted; Ctrl-C still stops the run. Louis confirmed "continue after a failure" on 4 Nov 2026.
- **Repeatability is proven by a second full run** (0 inserted, 0 updated, 125,569 unchanged), not by tests alone.
- **Test fakes copy the real message:** the real first-request failure is 502 Proxy Error, not the assumed 504 (PR #30).
- **Data rules to apply in Phases 4–5:** `price >= 2` (leaves out 615 rows, 0.49%); `price / surface_bati` only where `surface_bati > 0`; group by `commune_code` (the primary commune); decide what to do with the 49 rows whose primary commune is outside the twelve.

**Working with Claude Code (decided 8 Nov 2026)**
- **Move from claude.ai chats to Claude Code in the VS Code extension** (Louis, 8 Nov 2026), to cut the copy-paste loop between chat, editor and terminal. Context lives in repo files (`CLAUDE.md`, `.claude/`, `docs/`); the claude.ai Project stays as an archive.
- **Sessions start in Manual permission mode**, and Plan mode is used for design ("design before code"). Claude Code is never launched from the home directory.
- **Merge rule (Louis, 8 Nov 2026):** Claude may run `gh pr merge`, but only after Louis has explicitly confirmed in that session. This is enforced with an `ask` permission rule so a prompt always appears, not only by a written rule.
- **Notion stays the roadmap and documentation layer**, reached through MCP. Louis still sets Status and Scope himself.
- **Implemented (PR chore/claude-code-settings):** deny rules for `--admin`, `git add -A`/`.` and `.env`/`.env.local`, an `ask` rule for force-push (own feature branch only, after Louis's explicit agreement; never `main`), and a hook that blocks commits and pushes on `main`. They live in `.claude/settings.json` plus `.claude/hooks/guard-git.sh`. `.env.example` stays readable.
- **Bash permission rules are not a security boundary** (other spellings such as `git -C . add .` slip past pattern matching), which is why the hook exists and matches the command text itself. GitHub branch protection (required checks, `enforce_admins`, no force pushes) stays the real backstop.

**Market statistics, step 4.1 (decided 20 Nov 2026, built in `analysis/market.py`)**
- **Which sales count.** Always `price >= 2` and `surface_bati > 0`, and only ordinary resales: nature code 1 and not flagged new-build (VEFA). New-build, expropriations, land-for-building sales, auctions and exchanges (codes 2 to 6) are never counted (Louis: "I don't want non-ordinary sales"). A row must satisfy both the `is_vefa` flag and the nature code; the two agree on all 125,353 stored rows, and if they ever disagree the row is left out rather than counted on a guess.
- **New-build is left out of V1 (Louis, 20 Nov 2026).** Louis first asked for new-build stats kept apart from resale, then dropped them after seeing the data. Checked on 20 Nov 2026: new-build sales continue at 300 to 1,000 a year in the twelve communes, but since 2019 almost all are the "new-build, no description" type (code 101) with no built surface, so no price per m2 can be computed. Sales with a usable surface fell from about 500 a year in 2014-2018 to about 10 a year since 2023 (Toulon: 1 in 2025). Two segments in V1: `apartment` (code 121) and `house` (111). Multi-unit and mixed types (122, 112, 120, 151, 152) and commercial (14) stay out; building-level analysis is 4.1.4 (V2). A count of new-build sales per year (needs no surface) could be added later if wanted.
- **Segment on `property_type_code`, not on `rooms` / `is_single_residential_unit`.** Only 28,376 of the 65,495 usable resale apartment sales have `rooms` filled (43%), so the rooms-based flag would throw most of them away.
- **Filter in SQL, compute the median in pandas.** SQLite, which the tests run on, has no percentile function, so a SQL median could not be unit-tested. pandas' median and Postgres' `percentile_cont(0.5)` both interpolate linearly, and the volumes are small (at most about 30,000 rows per commune and segment). Cost: the `price >= 2` rule exists in the cleaning layer and in the query (already accepted above).
- **The median is never hidden for a low count** (Louis, 20 Nov 2026). Every result carries its sale count and a `low_sample` flag (below 30 sales by default); the caller decides how to display it. Only a segment with zero sales has no median to give. Planned for 4.1.1 and 4.1.3.
- **Period, threshold and window are parameters** the dashboard can change (Louis, 20 Nov 2026): yearly by default (quarterly suits the big communes only: Saint-Mandrier has about 7 house sales a quarter), a 30-sale threshold, and a 12-month comparable window. The window is anchored on the latest sale date in the data, not on today: the data ends on 31 Dec 2025, so a window counted back from today would be almost empty. The window is never widened automatically, so a caller always knows exactly what the comparables are.
- **Only the twelve known communes can be queried.** This settles the 49 rows whose primary commune is outside the twelve: they stay stored but are never returned.
