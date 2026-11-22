# Dashboard design system (task 4.2.2, agreed 22 Nov 2026)

A light design system for the Streamlit dashboard: a few tokens, four components and five rules, so every page looks consistent. It is the brief for Claude Design (4.2.3) and the source for the Streamlit theme file created at install time (4.2.4). The pages it applies to are in `dashboard-structure.md`.

## What a design system is (for later reference)
Three layers: **tokens** (raw decisions: colours, fonts, shapes), **components** (reusable pieces built from tokens) and **rules** (when to use what). Ours is deliberately light.

## What Streamlit allows (checked in the official docs, 22 Nov 2026)
- **Settable in `.streamlit/config.toml`:** accent, background and text colours, a palette of named colours, fonts (Google Fonts or own font files), corner radius, borders, heading sizes, and the **colour sequences of charts** (`chartCategoricalColors` and two others). Light and dark themes can be defined separately.
- **Not settable:** the page layout (sidebar, columns, containers), the look of the widgets themselves, and, as far as the pages read show, chart axes, grid lines and backgrounds (styled in the chart code). Anything finer needs injected CSS, which is fragile and kept to a minimum.
- **Not yet verified by running it:** the config reference page came back truncated, and Streamlit is not installed yet. Exact option names are confirmed when the theme is first run in 4.2.4.
- coss ui and shadcn are React libraries and cannot be used in Streamlit; only their restrained, neutral look is borrowed.

## Tokens (light theme only in V1)

| Role | Value | Notes |
|---|---|---|
| Sold prices (DVF) | **Navy `#1F3B77`** | Louis's choice. Contrast 10.8 on white. Used for data and sold-price labels, never for body text (it is close to the body text colour) |
| Asking prices (listings) | **Terracotta `#C8452F`** | Contrast 4.8 on white; use it on white, not on grey cards (4.5 there is borderline) |
| Warnings | Amber `#9A6700` on pale amber `#FFF4D6`, text `#3D2E00` | Only for warnings; text contrast 12:1 |
| Text / muted text | `#1A1D23` / `#5B6270` | Contrast 16.9 and 6.1 |
| Background / cards / borders | `#FFFFFF` / `#F4F6F8` / `#DDE1E6` | Quiet neutral base |
| Font | Inter (Google Fonts), tabular numbers | Free, widely used; Google sees the visitor's address, fine for a local app |
| Shape | Medium corners (about 8 px), thin borders, no shadows | Flat and modern |

Measured on 22 Nov 2026 (WCAG contrast; colour-blindness simulation for protanopia, deuteranopia and tritanopia): navy against terracotta differs clearly for all three (colour difference 68 to 97, where above 30 is clearly different), and their lightness differs by about 22 points, so they also stay apart in greyscale. Pure red failed the 4.5 text contrast (4.0), which is why a muted terracotta was chosen over red.

## Components
1. **Figure card:** a small label, a large number (for example "2,692 EUR/m2"), and beneath it the typical range (25th to 75th percentile) and the number of sales or listings.
2. **Low-sample chip:** a small amber tag "Low sample (27)" next to any figure based on fewer than 30 sales or listings.
3. **"Live is not still for sale" banner:** a pale amber band with an icon at the top of the asking-price page and the asking-versus-sold page. Always visible, cannot be closed.
4. **Chart style:** two data colours only; the typical range as a lightly tinted band of the line's colour; horizontal grid lines only; direct labels instead of a legend where possible; **low-sample points drawn hollow with a dashed line**; sold prices with round markers and asking prices with square markers (a second signal besides colour); in the twelve-commune comparison, the selected commune in navy and the others in light grey.

## Rules
1. Navy is always sold prices, terracotta is always asking prices, on every page.
2. Amber is only for warnings.
3. A warning always has an icon and words, never colour alone.
4. The gap between asking and sold ("+15 % above sold") is neutral text, with no red or green judgement: it is information, not a verdict.
5. Every figure shows the number of sales or listings behind it and the dates it covers.

## Open for later
- A dark theme is not part of V1.
- Exact Streamlit option names and the chart axis styling are settled when the theme is first run (4.2.4) and in the chart code (4.2.5).
