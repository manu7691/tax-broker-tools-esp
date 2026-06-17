# Crypto Support — Roadmap & Future Ideas

> **Status:** The crypto MVP has landed — per-coin Spanish FIFO (Pionex + Binance),
> EUR valuation at the ECB rate per trade date, the `tax-crypto` / `tax-combined`
> CLIs, offline demo data, and crypto folded into the flagship combined PDF.
> This document captures the **deferred** work and ideas we may pick up later, so
> the boundaries of the current implementation are explicit rather than implied.

---

## Known limitations of the current implementation

These are intentional MVP boundaries. Several are surfaced at runtime (counted
warnings) or in the report's Modelo 100 notes; they are collected here too.

| Limitation | Today | Where it shows |
|---|---|---|
| **Crypto-to-crypto swaps (*permutas*)** | Not computed (taxable in Spain). Counted and reported as "declare manually". | Console + crypto HTML report |
| **Fees in an unsupported coin** (e.g. BNB) | Not valued (slightly overstates the gain). Counted and reported. | Console + crypto HTML report |
| **Income-type events** (staking, airdrops, hard forks, lending interest) | Not handled — these are income (RCM / *rendimientos*), not capital gains, and are absent from spot-trade exports. | Guides + Modelo 100 notes |
| **Stablecoin conversions** | Treated as USD cash; not split out as individual disposals. | Guides + Modelo 100 notes |
| **Crypto-only flagship PDF** | The polished "¿Qué declarar?" PDF is rendered from the stock engine, so a crypto-only run gets the `tax-crypto` HTML instead of the PDF. | `tax-combined` runtime note |

---

## Idea 1 — Dashboard crypto tab (was review point #5)

The interactive dashboard (`generate_charts.py`) has no crypto view; crypto only
appears in its own standalone HTML report (`tax-crypto`) and the combined PDF.

**Why deferred:** architectural mismatch. The dashboard is built around the stock
**portfolio path** (`securities` / `portfolio.run_portfolio`), whereas crypto has
its own per-coin `CryptoTaxEngine` and its own tabbed HTML report. Bolting a crypto
tab onto the stock dashboard duplicates rendering, while unifying the engines is the
larger refactor below.

**Options to weigh first:**
1. **Fold-in** — add a crypto tab to `charts_dashboard.html`, reusing the dashboard
   shell but feeding it `CryptoTaxEngine` data (per-coin P&L, monthly P&L, open
   positions — charts the crypto HTML report already computes).
2. **Keep separate** — keep emitting the standalone crypto report and just
   cross-link the two. Lower effort, no engine coupling.

Decide fold-in vs keep-separate **before** building. Treat as its own PR.

## Idea 2 — More exchanges (was review point #7)

Only **Pionex** and **Binance** spot-trade exports are parsed today
(`crypto_parser.py`). Natural next targets: **Coinbase, Kraken, Bitvavo, Kucoin**.

**Shape of the work:** each exchange needs a `parse_<exchange>(csv)` that returns
the shared `CryptoTrade` records — the FIFO engine, EUR valuation, and reporting are
exchange-agnostic, so this is parser-only. Watch for: the timezone of the export's
timestamps (see `--binance-utc-offset`), how the pair/amount columns are encoded,
and fee columns. Ship a `docs/crypto-<exchange>.example.csv` per parser.

## Idea 3 — Crypto-only flagship PDF

Make `tax-combined` (or `tax-crypto`) able to render the polished bilingual PDF for
a **crypto-only** run, not just the HTML. Today the PDF renderer (`ReportRenderer`)
is built around a stock `TaxEngine`; this needs either a crypto-aware renderer path
or a thin adapter that presents crypto summaries/disposals to the existing template.

## Idea 4 — Handle *permutas* and income events natively

The bigger correctness items currently surfaced-but-not-computed:
- **Crypto-to-crypto swaps** — model each leg as a disposal at EUR market value.
- **Income events** (staking/airdrops/forks) — a separate income stream (RCM /
  *rendimientos*) with its own acquisition-cost basis going forward.

Both likely need a richer input than the current spot-trade exports provide.

## Idea 5 — Unify crypto onto the securities/portfolio path (the "P3" refactor)

Retire the parallel `CryptoTaxEngine` orchestration and treat a coin as a `Security`
flowing through `group_events_by_security` / `run_portfolio`, **while preserving the
asset-class distinction** Hacienda requires (different casillas; crypto is *not*
subject to the 2-month wash-sale rule). The parser/event-builder stays crypto-specific;
only the FIFO + grouping + reporting layer unifies. This would give dashboard
inclusion (Idea 1) and combined reporting largely for free, but it is the highest-risk
change — keep it isolated to its own branch.
