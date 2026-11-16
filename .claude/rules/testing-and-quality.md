# Testing and quality

## Before every PR
- Run all three: `poetry run pytest`, `poetry run ruff check .`, `poetry run pyright`. Ruff and Pyright run project-wide, as CI runs them; a scoped run hides errors. Ruff: line length 100, rules E/F/I/UP/B/SIM, target py312.
- Run `poetry run ruff format src tests scripts` before committing, so `git diff` shows only real changes (Claude's edits do not trigger VS Code's format-on-save).
- Report what was run and the real result (counts, errors), never just "tests pass".

## Writing tests
- Pure data transforms are tested without a database. Database behavior is tested against in-memory SQLite with a hand-built table that mirrors the real one (stand-ins: `Integer` for `BigInteger` keys, `Float` for `numeric`, JSON for arrays).
- SQLite hides things the real database shows: NaN is bound as NULL, there is no `Decimal`, no arrays, no `BigInteger` key. Cover those with a pure test plus a real-system check.
- Build test data through the real cleaning and classification functions so the dtypes match production.
- Test fakes copy the real message. The DVF first-request failure is `502 Server Error: Proxy Error`, not the 504 once assumed.
- A test that passes for any value of a parameter does not protect it. Test each column and branch that matters (a revised-row test once changed only the price, so a loader comparing only the price would have passed).
- Check the real test layout before assuming it: `testpaths = ["tests"]`, and `scratch_*.py` files are never collected.

## Mutation checks (by hand, no tool)
For tests that protect logic. Commit first, so `git restore` is safe. Change one line on purpose, run `poetry run pytest -q`, confirm at least one test fails, undo with `git restore <file>`, confirm `git status` is clean. Report killed or survived for each mutant. A survivor is a test gap: fix the test and run it again. Skip for wording-only or documentation changes, and say so.

## Real-system checks
- Write-path and API changes get a manual check on the real system before merging, with a throwaway `scratch/scratch_*.py` script. It is read-only unless it writes; if it writes, it must refuse to start when its target rows already exist and must clean up in a `finally` block.
- Verify the result afterwards through the read-only Supabase connector (`execute_sql`).
- Say plainly what was checked on the real system and what was not.
