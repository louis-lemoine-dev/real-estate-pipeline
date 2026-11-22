# Open questions & parking lot

_Last updated: 22 November 2026. Items resolved since the 3 November version are listed at the end, with the outcome._

## Open

### New since 4 November 2026
- **The first-request retry budget is thin.** Hyères needed 4 of its 5 attempts in the second full load. A failed commune is recorded and can be re-run with the command the report prints, so a failure costs time, not data; consider raising the budget before scheduling (Phase 7).
- **`clean_transactions([])` raises `KeyError: 'price'`** (an empty list gives a DataFrame with no `price` column). The runner skips an empty fetch, so it is harmless today; decide whether to fix it at the source.
- **`transactions` has no address or neighbourhood, only `commune_code`.** "Toulon Centre" comparables (Phase 5) cannot be narrowed below the commune of Toulon (47,213 rows) with today's data. Check whether the DVF+ API offers a finer field before deciding; otherwise document the approximation.
- **The price-drop figures of the listing statistics are not checked on real data.** No price change has been logged yet (the price-changes table is empty), so only the tests cover them. Check them against the database once real changes exist, after a few more real scrapes.
- **Listings of other types get no price statistics** (Immeuble, Parking / Garage and any other label are counted as "unmatched"). If the dashboard needs buildings or other types, that belongs with the building-level analysis (4.1.4, V2).
- **Only about 140 of the Toulon listings are reachable.** The search serves 5 pages (the page text mentions much larger totals, such as "803"), and the list shifts between runs (3 of 139 absent 80 seconds later, 4 new). To reach more listings we would need other searches (by type, price band or sort order): decide with the dashboard design.
- **"Gone" will include listings that only left the visible window.** Idea for Phase 7: before marking a listing gone, check its own page (does the URL still answer?), or require more misses.
- **The scraper has no retry and treats the 404 after the last page as a failure.** One timeout (10 s) aborted a probe once; a run with too many pages dies on the 404. Phase 7 candidates: retry the transient errors, and treat a 404 after at least one good page as the end of the results.
- **New-build has no usable price per m2 since 2019.** Sales continue (300 to 1,000 a year in the twelve communes), but almost all lack a built surface, so new-build is left out of the market statistics (PR #38). A yearly count of new-build sales needs no surface and could be a dashboard extra: left out of V1 in `dashboard-structure.md`, revisit after the first dashboard works.
- **Asking-price pages will only have data for Toulon** (and one house in Le Revest). Decide, before or after the dashboard is built, whether to scrape more communes (other search URLs). The dashboard shows "no data" for the others meanwhile.
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
- **Roadmap rows for the first scrape and the listing statistics:** created in Notion on 20 Nov 2026 as 4.0 (Listings data), 4.0.1 and 4.0.2, with Status and Scope filled at Louis's request; the 4.0.1 write-up is on its page.
- **The 49 rows whose primary commune is outside the twelve:** kept in the table but never returned, because the market queries accept only the twelve known communes (PR #38, 20 Nov 2026).
- **Notion fields for step 3.5:** Status, Done and Scope are all set (checked 20 Nov 2026, PR #37).
- **Which columns the load stores:** the 13 source fields only; the three derived columns are not stored (PR #28).
- **How inserted/updated/unchanged are counted:** by reading the stored rows, comparing, then writing in one transaction, which runs identically on the SQLite tests (PR #28).
- **NaN must reach Postgres as NULL:** implemented and protected by a pure test (SQLite cannot show a leak).
- **Cold-start worst case for the four biggest communes:** measured in two full loads; the failure is an HTTP 502 Proxy Error after about 60 s, and every commune succeeded within its attempts.
- **How often the count check fails:** zero times in 24 full walks, so no whole-commune retry is needed.
- **The "504 Gateway Time-out" wording in the test fakes:** corrected to 502 Proxy Error (PR #30).
- **Roadmap housekeeping:** checked 4 Nov 2026: steps 3.3 and 3.4 and all their tasks, and 3.4.4 (Scope V1), are Done. What remains is listed under "New since 4 November".
- **The empty `feat/dvf-load` branch:** deleted and recreated for PR #28.
