# Open questions & parking lot

_Last updated: 8 November 2026. Items resolved since the 3 November version are listed at the end, with the outcome._

## Open

### New since 4 November 2026
- **Notion fields for Louis:** set Status/Done on 3.5.1–3.5.3 and step 3.5 (this closes Phase 3), and the Scope of 3.5.0, the only Phase 3 item without one.
- **The first-request retry budget is thin.** Hyères needed 4 of its 5 attempts in the second full load. A failed commune is recorded and can be re-run with the command the report prints, so a failure costs time, not data; consider raising the budget before scheduling (Phase 7).
- **`clean_transactions([])` raises `KeyError: 'price'`** (an empty list gives a DataFrame with no `price` column). The runner skips an empty fetch, so it is harmless today; decide whether to fix it at the source.
- **`transactions` has no address or neighbourhood, only `commune_code`.** "Toulon Centre" comparables (Phase 5) cannot be narrowed below the commune of Toulon (47,213 rows) with today's data. Check whether the DVF+ API offers a finer field before deciding; otherwise document the approximation.
- **49 rows (0.04%) have a primary commune outside the twelve** (17 other communes). Decide in Phases 4–5 whether to keep or drop them.
- **Unexplained, harmless:** one attempt (Six-Fours, second full load) logged a 65 s read timeout after only about 31 s.
- **No real DGFiP/Cerema refresh observed yet.** Counts were identical on 24 Oct, 3 Nov and 4 Nov. The first real refresh will show how often an existing mutation is revised (the update branch of the loader has met no real revision).
- **Claude Code setup tasks** are listed in `progress.md` (Next steps). The terminal `claude` command is v2.1.159 and outdated; the VS Code extension updates itself. Update the terminal one only if it will be used (`claude update`, or `brew upgrade claude-code` on Homebrew).

### Carried over
- **Nothing orchestrates the daily/semi-annual run end to end** (Phase 7). The DVF refresh is a manual command for now (`poetry run python scripts/load_dvf.py`, about 75 minutes). When the whole flow is orchestrated, a loader of stored listings must include already-marked ones (else a returning listing looks new and its insert fails on the existing id), and must not send raw SQL containing `%` through pandas under psycopg (use `text()` or a SQLAlchemy select).
- Semi-annual DVF refresh idea: compare each commune's reported `count` before refetching it, to skip unchanged communes (parked for Phase 7).
- Filter delisted listings out of the dashboard and the AI analysis (Phases 4–5); decide what happens to the corresponding Notion entry (Phase 6). Phases 4–5 must also exclude null-price DVF rows from price aggregates and comparables (rule in `decisions.md`).
- Tune the two-misses threshold using the reinstated counts once real runs exist; consider an exact miss-counter if the daily-cadence assumption ever breaks.
- V2: exploit price-change history as an investment signal (repeated drops → lower-offer indicator); full field-level change history for listings; two-directional Supabase ↔ Notion sync.
- Nationwide commune resolution via `geo.api.gouv.fr`.
- Building/multi-unit price analysis via DVF's bucketed fields.
- Multi-commune/multi-parcel DVF mutations labelled as single-unit sales — root cause unresolved (`idmutation=141626`). `commune_code` is `l_codinsee[0]` (the primary commune), so it does not depend on which commune was queried. New data: 216 mutations were returned by two communes in the full load, and 49 rows have a primary commune outside the twelve.
- `price_per_terrain_m2` for land-only ParuVendu listings.
- `has_asterisk` meaning, still unresolved.
- Migration-tracking table (`schema_migrations`) — deferred until a second migration strains manual ordering (four migrations exist; still manageable).
- Git Guide additions: the new ones are in `git-guide.md` (section 14). The Notion copy of the Git Guide still needs them.
- FastAPI as a standalone learning project — kept as 3.1 on the Dev Roadmap, not part of this project.

## Resolved since 3 November 2026
- **Which columns the load stores:** the 13 source fields only; the three derived columns are not stored (PR #28).
- **How inserted/updated/unchanged are counted:** by reading the stored rows, comparing, then writing in one transaction, which runs identically on the SQLite tests (PR #28).
- **NaN must reach Postgres as NULL:** implemented and protected by a pure test (SQLite cannot show a leak).
- **Cold-start worst case for the four biggest communes:** measured in two full loads; the failure is an HTTP 502 Proxy Error after about 60 s, and every commune succeeded within its attempts.
- **How often the count check fails:** zero times in 24 full walks, so no whole-commune retry is needed.
- **The "504 Gateway Time-out" wording in the test fakes:** corrected to 502 Proxy Error (PR #30).
- **Roadmap housekeeping:** checked 4 Nov 2026: steps 3.3 and 3.4 and all their tasks, and 3.4.4 (Scope V1), are Done. What remains is listed under "New since 4 November".
- **The empty `feat/dvf-load` branch:** deleted and recreated for PR #28.
