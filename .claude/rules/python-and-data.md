---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
  - "scripts/**/*.py"
  - "db/**/*.sql"
---

# Python and data rules

## Database access
- Direct Postgres through SQLAlchemy + psycopg v3, not `supabase-py`. Use the shared `get_engine()` and `run_in_transaction()` in `common/db.py`: reuse them, never create new engines or retry loops. The DB retry tuning is deliberately separate from the DVF+ client's.
- The connection is the Supabase Session pooler (port 5432). Credentials live in `.env` (gitignored; `.env.example` is committed, redacted). Never read or print `.env`.
- Write paths: one transaction, all-or-nothing. Retry only transient connection errors (`OperationalError`); let `IntegrityError` fail fast after logging context. Validate input before writing, and write an explicit allow-list of columns, never a drop-list. Nothing deletes listings or transactions (delisting only marks).
- Never send raw SQL containing a literal `%` through pandas under psycopg: use `text()` or a SQLAlchemy select.

## Schema and types
- Migrations are numbered files in `db/migrations/`, applied with `poetry run python scripts/apply_migration.py db/migrations/<file>.sql`, never from the Supabase dashboard. The script splits on every `;`, so no semicolon inside a migration comment (a `;` at the very end of a comment line is harmless). Row-level security is on with no policies; the Data API is disabled.
- Every type traces back to the real source of the field. `numeric` for money and surfaces (avoids rounding drift), except ParuVendu `price` and the price-event columns, which are `integer` (whole euros at the source); `text` for identifiers that merely look numeric; `bigint` for `transactions.id_mutation`.
- A NOT NULL on a source-derived column is a claim about the source: check it against the full dataset, not a 200-row sample (the null-price lesson).
- The `transactions` table stores only the 13 source fields. Derived values (`price_per_m2`, `is_single_residential_unit`, `is_market_transaction`) are computed in SQL by the consumers.

## pandas
- NaN to NULL: cast to object before `.where(pd.notna(df), None)`, because a float64 column keeps NaN otherwise. Convert numpy values to plain Python types before they reach the driver.
- Never use `is True` / `is False` on pandas values (use truthiness): `numpy.bool_` is not `bool`.
- `Protego.can_fetch` takes `(url, useragent)`, the reverse of `urllib.robotparser`.

## DVF and ParuVendu
- The DVF+ API is beta/preprod and flaky, so retries are essential. A commune is fetched completely or the call fails (`IncompleteFetchError`); `allow_partial=True` is for sampling only. First request: 65 s timeout and 5 attempts; later pages: 15 s and 3 attempts. `fields=all` is required.
- `commune_code` is the mutation's primary commune (`l_codinsee[0]`), not the commune that was queried. Mutations with no price are kept (`price` is nullable). `rooms` is set only for a single residential unit (exactly one bucket = 1, all others 0, and `nblocmut == 1`); 5 means "5 or more".
- ParuVendu: robots.txt is respected (Protego). A "modified" listing means a price change only. Delisting needs two consecutive misses plus the run-level sanity gate.
