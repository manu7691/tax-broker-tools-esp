# Dashboard Scope Split — "Whole portfolio" vs "Single stock" Tabs

> **Status:** Not started. This is a forward-looking design for a *structural*
> fix to the portfolio-mode dashboard. The **interim fix has already landed**:
> per-card **scope badges** (🌐 Whole portfolio / 🏷️ `<TICKER>` only) plus a
> hint next to the security selector. This document describes the larger
> reorganization we may do later if badges prove insufficient.

---

## 1. Problem

In **portfolio mode** (more than one security — e.g. employer stock plus stocks
traded on Revolut), the dashboard mixes two fundamentally different kinds of card
inside the same tabs:

- **Whole-portfolio cards** — always aggregate everything you own; they ignore
  the security selector.
- **Single-stock cards** — show only the security currently chosen in the
  `#securitySelect` dropdown; they change when you switch securities.

A non-financial user cannot tell which is which. They pick "DT", read a card that
now shows DT-only numbers, then their eye lands on a whole-portfolio card right
below it — with no signal that the scope changed. The lone explanatory sentence
under the dropdown does not connect to individual cards.

### Interim mitigation (shipped)
Every card header now carries a colored **scope badge**, gated behind
`body.portfolio-mode` (only shown when `holdingsTable.length > 1`):

- 🌐 **Whole portfolio** (blue) — fixed label.
- 🏷️ **`{ticker}` only** (amber) — re-labelled live in `onSecurityChange` →
  `setLanguage`, which substitutes `{ticker}` with the selected security.

This is low-risk and per-card, so the answer is always next to what the user is
reading. The structural split below is the heavier alternative.

---

## 2. Goal of the structural split

Make scope a **navigational** property instead of a per-card annotation: the user
is never looking at a mix. Two clearly named contexts:

1. **Whole portfolio** — the aggregate view. No security selector visible here.
2. **Single stock** — everything about one security; the selector lives here and
   governs the entire view.

Tax content (which is portfolio-level by Spanish law — the *base del ahorro* is an
aggregate) stays in the portfolio context, so users stop trying to read "my tax"
per-ticker.

---

## 3. Card inventory (current scope classification)

This is the authoritative mapping the badge work already encodes. A tab split
must preserve it. Cards live in `src/tax_engine/templates/dashboard_template.html`.

### Whole-portfolio cards (🌐, 8)
| Card | `data-i18n` key | Current tab |
|------|-----------------|-------------|
| Portfolio Roll-up Overview (holdings table + allocation doughnut) | `portfolio_summary_title` | My Stock |
| Dividends & interest income (savings income) | `div_title` | My Taxes |
| How is your portfolio split across securities? | `securities_chart_title` | Advanced |
| Which broker did this position come from? | `broker_chart_title` | Advanced |
| Realized Gains & Estimated Tax by Year | `yearly_tax_title` | Advanced |
| Currency Exposure | `currency_title` | Advanced |
| Tax-Loss Harvesting | `harvest_title` | Advanced |
| Portfolio Diversification Concentration Risk | `risk_card_title` | Advanced |

### Single-stock cards (🏷️, 15)
| Card | `data-i18n` key | Current tab |
|------|-----------------|-------------|
| Your situation today | `hero_title` | My Stock |
| Overall Scorecard | `scorecard_title` | My Stock |
| Sell Simulator + Scenario Planner (one card, two `<h2>`) | `sim_title` (+ `deval_title`) | Should I Sell? |
| Breakeven Price & Exchange Rate Sensitivity | `breakeven_card_title` | Should I Sell? |
| ESPP Tax Exemption Lock Tracker | `espp_tracker_title` | My Taxes |
| ESPP Tax Exemption Audit | `espp_audit_title` | My Taxes |
| Should I have sold my RSUs immediately? | `rsu_audit_title` | My Taxes |
| Stock Price Trend & Market Averages | `trend_chart_title` | Advanced |
| Where did my sales profits come from? (decomposition) | `decomp_chart_title` | Advanced |
| Stock Price & Transaction History | `price_chart_title` | Advanced |
| USD to EUR Exchange Rate Trend | `fx_chart_title` | Advanced |
| Market Intelligence & Competitor Hub | `intel_hub_title` | Advanced |
| Market Sentinel & Sell Alert Configurator | `alerts_card_title` | Advanced |
| Observability Peer Comparison | `peer_chart_title` | Advanced |
| Smart Holding & Selling Strategy | `strategy_title` | Advanced |

> Note: `div_title` (dividends/interest) is portfolio-level today because savings
> income is loaded as a yearly aggregate, even though `dividends_by_symbol` exists
> per-ticker. A future per-stock dividends card could move to the single-stock
> context — decide deliberately, don't split it by accident.

---

## 4. Proposed navigation

Replace the current 4 content tabs (`mystock`, `sell`, `taxes`, `advanced`) with a
**two-level** structure: a top-level scope switch, then sub-tabs within each scope.

```
┌ Whole portfolio ─────────────┐   ┌ Single stock  [ DT ▼ ] ──────┐
│  • Overview (roll-up, alloc) │   │  • Today (hero, scorecard)    │
│  • Taxes (yearly tax, div,   │   │  • Should I sell? (sim, b/e)  │
│    harvesting)               │   │  • This stock's taxes (ESPP,  │
│  • Breakdown (by security,   │   │    RSU audits, lock tracker)  │
│    by broker, currency, risk)│   │  • Charts (trend, price, fx,  │
└──────────────────────────────┘   │    decomp, peers)             │
                                    │  • Market intel & alerts      │
   selector HIDDEN here             └───────────────────────────────┘
                                       selector LIVES here, drives all
```

Key rules:
- The `#securitySelect` dropdown is **only visible in the Single-stock scope**.
  Moving it out of the global header removes the "does this affect everything?"
  ambiguity entirely.
- In **single-security mode** (`holdingsTable.length <= 1`) the scope switch is
  hidden and the dashboard collapses to today's single flat view — no regression
  for the common employee-with-one-stock case.
- Scope badges become redundant *within* a scope and can be dropped there, but
  keeping the amber 🏷️ `<TICKER>` label as a heading on the Single-stock scope is
  still useful (it states which security you're on).

---

## 5. Implementation sketch

All changes are confined to `dashboard_template.html` (markup + inline JS + the
`locales` dict) and possibly a few helper labels. No Python/engine change is
required — the data payloads (`multiSecurityData`, `holdingsTable`, aggregate
chart JSON) are already computed in `generate_charts.py` and injected; this is
purely a presentation reorg.

1. **Markup:** wrap the existing cards into two top-level containers
   (`#scope-portfolio`, `#scope-stock`) and move each card per the inventory in
   §3. Within each, group into sub-tabs. Reuse the existing `.tab-content` /
   `.tab-btn` machinery (`switchTab`) — generalize it to two tab *bars* rather
   than one.
2. **Selector relocation:** move `#security-switcher-container` out of the global
   `.header-controls` into the Single-stock scope's header. `onSecurityChange`
   logic is unchanged; only its DOM location moves.
3. **Scope switch JS:** a `setScope('portfolio'|'stock')` toggling the two
   containers and remembering the last sub-tab per scope.
4. **Single-security guard:** in `initPortfolioViews`, when `length <= 1`, hide
   the scope switch + selector and render a single merged column (today's
   behavior). The `portfolio-mode` body class already gates this — reuse it.
5. **i18n:** add `scope_tab_portfolio` / `scope_tab_stock` and sub-tab labels in
   both `en` and `es` blocks. Follow the existing `data-i18n` pattern; remember
   `setLanguage` rewrites `innerHTML`, so any dynamic element (badge/label) must
   be a **sibling** of the translated node, not a child (this is why the current
   badges sit before each `<h2>`, not inside it).

---

## 6. Risks / open questions

- **`switchTab` reuse:** today it assumes one global tab set. Two independent tab
  bars need either namespaced IDs or a small refactor. Verify the privacy toggle,
  language switch, and chart lazy-rendering still fire on the right events.
- **Chart sizing:** Chart.js canvases inside an initially-hidden tab can render at
  0×0. The current code already copes (charts build on `init`); confirm the
  added hidden scope container doesn't reintroduce the zero-size bug — call
  `chart.resize()` on scope/tab show if needed.
- **Deep-linking / default scope:** decide the landing scope. Recommend
  **Whole portfolio** first (the roll-up answers "how am I doing overall?"), with
  Single-stock one click away.
- **Per-stock dividends:** see §3 note — resolve whether dividends move into the
  stock scope or stay aggregate before splitting.
- **Docs:** `docs/DASHBOARD_GUIDE_EN.md` / `_ES.md` and
  `docs/REPORT_VS_DASHBOARD_*.md` reference the current tab names and would need
  updating in lockstep.

---

## 7. Decision criteria (badges vs split)

Stay with **badges** if user testing shows people correctly read scope per card.
Move to the **split** if confusion persists specifically because portfolio and
single-stock cards are *adjacent* — the split is the only thing that fully removes
adjacency. The split is a larger, higher-regression-risk change (tab machinery,
i18n, docs, chart sizing), so it should be justified by an observed problem the
badges did not solve.
