# Dashboard structure (task 4.2.1, agreed 22 Nov 2026)

What the dashboard is for, the questions it answers, and the pages that follow. This is the brief for the design system (4.2.2) and for Claude Design (4.2.3). Technology: Streamlit (see `decisions.md`, "Dashboard, step 4.2").

## Who it serves
- **A home buyer** (Louis): is this price fair, and how much can I negotiate?
- **An investor** (Louis again, and the portfolio audience): where is the market going, where is it cheap, and how far can I trust a figure?

## Questions and the views that answer them

| Question | Page |
|---|---|
| What do homes like this sell for in this commune (apartments and houses apart)? | 1. Sold prices |
| Is the market rising, flat or falling, and since when? | 1. Sold prices |
| Which communes are cheaper per m2 than their neighbours? | 1. Sold prices (commune comparison) |
| What are sellers asking now, and how many listings are there? | 2. Asking prices |
| Are sellers cutting their prices? | 2. Asking prices (empty until real price changes exist) |
| Is a listing price fair compared with recent sales? How much do sellers ask above what sells? | 3. Asking versus sold |
| Can I trust this figure, or is it based on a few sales? | the low-sample warning, on every page |

## The three pages

**1. Sold prices** (DVF; functions `price_trend` and `comparable_stats`)
- The user picks a commune and a property type (apartment or house).
- Yearly trend of the median price per m2, with the sale count per year.
- Latest 12 months: median, the quarter-range (25th to 75th percentile) and the number of sales.
- A comparison of the twelve communes for one property type.
- The low-sample warning appears on every figure and chart point it applies to.

**2. Asking prices** (ParuVendu; function `listing_stats`)
- For each commune and property type: live listings, median asking price per m2, quarter-range, listings with a price drop and number of drops.
- Also shown: how many live listings are left out of the statistics (other types, unmatched) and when the data was last refreshed.
- The "live is not certain to be for sale" warning is always visible, together with the low-sample warning.

**3. Asking versus sold** (both sets of functions)
- For each commune and property type: median asking price per m2 next to the median sold price per m2, and the gap in percent.
- Only where both exist; otherwise "no data", never a blank guess.
- Both warnings apply: a gap is flagged if either side is low-sample.
- First impression today (not a conclusion): Toulon apartments about 3,102 asked against 2,692 sold, about 15 percent above.
- Expected to be the most valuable page.

## The two warnings (always visible)
1. **Low sample:** fewer than 30 sales or listings with a surface; the figure is shown, never hidden, but marked.
2. **Live is not certain to be for sale:** only about 140 Toulon results are reachable and the list shifts between runs, so a listing can disappear from our view without being sold, and "live" only means "not marked gone".

## Limits known before design
- **Asking prices are almost all Toulon** (138 of 143 listings, plus 1 in Le Revest). Pages 2 and 3 are really about Toulon today; other communes show "no data". More communes need more scraping (a separate task).
- **Sold data ends on 31 Dec 2025**, and the "latest 12 months" window is anchored there, not on today. The page must say which dates it covers.
- **Statistics only, no list of individual listings.** Judging one listing belongs to the AI step (Phase 5) and the Notion workflow (Phase 6).
- **No price drops recorded yet**, so that part shows zero until real changes exist.
- **New-build is not covered** (no usable surface since 2019). A yearly count of new-build sales is a possible extra, not part of V1.
- **Only two property types** (apartment, house); buildings, parking and other types are counted but not priced.

## Out of scope for V1
Individual listing view, map, new-build prices, building-level analysis (4.1.4), scheduling, and any writing to the database. The dashboard only reads.
