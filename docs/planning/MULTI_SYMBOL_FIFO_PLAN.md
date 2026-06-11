# Multi-Symbol, Multi-Platform FIFO — Design Plan

> **Status:** Phases 1–4 and 6 implemented; phase 5 deferred (needs a sample).
> This captures the target architecture for turning the current single-security
> engine into a portfolio-wide one that ingests multiple securities from multiple
> platforms (E\*TRADE, Revolut, Trade Republic, …) and produces a single Spanish
> savings-base result. **Phases 1–4** (model + ISIN grouping + aggregation;
> per-security + portfolio reporting behind `--all-securities`; ticker→ISIN
> resolution; multi-currency FX) and **Phase 6** (charts: per-security portfolio
> breakdown) have landed. **Phase 5 (Trade Republic)** is deferred: TR issues only
> PDFs, and the usable CSV (community `pytr` `account_transactions.csv`:
> `;`-delimited, has ISIN/Shares/Value/Fees/Taxes, EUR, localized labels) needs a
> real sample to pin the value-sign convention and Type strings before it's safe.

---

## 1. Motivation

Today the engine is **single-security**: it assumes every input transaction is the
same homogeneous security (the employer stock, e.g. DT), keeps **one** FIFO queue,
and the optional Revolut import is *filtered down to that one ticker*. Everything
else is discarded.

But Spanish FIFO is **per security (per ISIN)**, and the taxable result is the
**aggregate** of every security's net gain/loss in the *base del ahorro*. So a user
who also traded TSLA, NVDA, ADBE, … on Revolut (or holds positions on Trade
Republic) has taxable gains/losses this tool simply does not compute.

**Goal:** process *all* securities across *all* connected platforms, each with its
own correct FIFO queue, and roll them up into one Modelo-100-ready savings base.

---

## 2. Goals / Non-goals

**Goals**
- One independent FIFO queue **per security (ISIN)**; never mix tickers.
- **Same ISIN across brokers merges** into one queue (cross-broker homogeneity).
- Pluggable **platform ingestion** (E\*TRADE, Revolut, Trade Republic, …).
- Aggregate per-security results into the savings base, with carryforward (4y) and
  the 25% cross-category offset against dividends/interest (RCM).
- Per-security **and** per-broker reporting; backward compatible with the current
  single-stock flow.
- Multi-currency (USD, EUR, GBP, …) via ECB reference rates.

**Non-goals (initial)**
- Corporate actions beyond forward stock splits (mergers, spin-offs, symbol
  changes handled only as far as ISIN continuity allows).
- Options/derivatives P&L beyond what the current options parser does.
- Non-Spanish tax residency; Modelo 720 generation (keep as a reminder only).

---

## 3. Core concepts

| Concept | Definition |
|---|---|
| **Security identity** | Canonical key = **ISIN**. Ticker is display + fallback when ISIN is unknown. |
| **Homogeneous grouping** | Events grouped by ISIN → one FIFO queue each. Same ISIN from different brokers merges. |
| **Broker / platform** | Provenance tag (E\*TRADE, Revolut, Trade Republic). Affects **reporting only**, never FIFO grouping. |
| **Portfolio** | The set of all securities; produces the aggregate savings base. |

Key invariant: **FIFO grouping key = ISIN; broker is metadata.**

---

## 4. Architecture

### 4.1 Data model changes
- `StockEvent`: add `symbol: str` and `isin: str | None` (already has `broker`).
- `ShareLot`: add `broker` and `isin` so surviving holdings can be attributed
  per-broker and per-security in the report/charts.
- New `Security(isin, ticker, name, country)` value object.
- Results become keyed by security: `dict[str /*isin*/, YearlyTaxSummary]`, etc.

### 4.2 Ingestion layer — platform parsers
Define a small protocol so each platform is independent:

```python
class PlatformParser(Protocol):
    name: str  # "E*TRADE", "Revolut", "Trade Republic"
    def parse(self, input_dir: Path) -> list[StockEvent]: ...
```

- **E\*TRADE** (existing espp/orders/rsu/options): wrap to set `symbol`/`isin` from
  the detected employer security; `broker="E*TRADE"`.
- **Revolut** (existing): **stop filtering to one ticker** — emit *all* tickers with
  their `symbol` + `isin` + `broker="Revolut"`. The gains export already carries
  ISIN; the movements export is ticker-only and needs an ISIN lookup (see §4.3).
- **Trade Republic** (NEW, TBD): format unknown — likely a CSV and/or PDF
  *Steuerreport*/transaction export, possibly German-labelled. **Needs sample data.**
  Add a parser + fixtures once we have one.
- Each parser normalizes to the common `StockEvent` schema (date, symbol, isin,
  type, qty, price, currency, fees, broker, notes).

### 4.3 Security resolution (ticker → ISIN)
- ISIN present → use directly (most exports, incl. Revolut gains).
- Ticker-only (Revolut movements) → resolve via, in order:
  1. `input/securities.json` user map (`{"TSLA": "US88160R1014", …}`),
  2. an offline cache, optionally a Yahoo/OpenFIGI lookup (network, cached),
  3. fall back to grouping by **ticker** with a visible caveat (a ticker that never
     changed ISIN is safe; this only risks cross-broker merge accuracy).

### 4.4 Engine layer
- Group all events by ISIN (fallback ticker).
- For each group, run the **existing** `TaxEngine` unchanged → per-security
  processed events, yearly summaries, wash-sale (per security), split handling.
- **Aggregation** (`portfolio.py`): sum per-security `YearlyTaxSummary` into a
  combined summary; feed that into the existing carryforward + savings ledger so the
  4-year carryforward and 25% cross-offset operate on the **portfolio** net (correct
  — these are not per-security in Spanish law). Wash-sale stays **per security**.
- **Multi-currency:** generalize `ECBRateFetcher` to take a currency code (ECB
  publishes USD, GBP, CHF, …). Non-EUR/USD currently skipped; this removes that
  limit.

### 4.5 Reporting layer
- **PDF:** a portfolio summary table (per security: gains / losses / net), then the
  existing per-security ledger + FIFO detail sections (one per security), the
  per-broker subtotal (across securities), and the combined savings base.
- **Charts dashboard:** a security selector (or portfolio aggregate view) + the
  per-broker breakdown (see the separate broker-chart task, already in progress).

---

## 5. Configuration & migration

- `input/ticker.json` keeps working = the "primary"/employer security (single-stock
  mode is just a portfolio of one).
- New optional `input/securities.json`:
  ```json
  {
    "include": ["DT", "TSLA", "NVDA"],        // empty/absent = all detected
    "isin_map": { "TSLA": "US88160R1014" },   // ticker → ISIN for ticker-only feeds
    "primary": "DT"
  }
  ```
- Backward compatible: with no `securities.json`, behaviour = today (primary
  security only) unless a new `--all-securities` flag / menu toggle is set.

---

## 6. Phased rollout

1. ✅ **Model + grouping** *(done)*: added `symbol`/`isin` to `StockEvent`/`ShareLot`;
   built `portfolio.py` group-by-ISIN runner + aggregation and `securities.py`
   (`Security` + `securities.json`); Revolut emits all tickers via `all_securities`.
   (Gains export only — has ISIN. No new platforms yet.)
2. ✅ **Reporting** *(done)*: per-security Portfolio Summary table + per-security
   ledger/FIFO sections + per-broker subtotal + combined savings base, behind
   `--all-securities` (auto-on with `securities.json`). Single-stock mode unchanged.
3. ✅ **Ticker→ISIN resolution** *(done)*: the movements export emits all tickers,
   each resolved via `securities.json` `isin_map` → ISINs learned from the gains
   export in the same folder → persistent `.isin_cache.json` (`build_isin_resolver`),
   falling back to ticker grouping with a visible caveat. Network lookup is a
   pluggable hook (`network=`), off by default.
4. ✅ **Multi-currency FX** *(done)*: `ECBRateFetcher.get_rate(date, currency)` for
   any ECB reference currency (per-currency cache, backward-compatible with the
   old USD-only file); `StockEvent.currency` drives EUR conversion; Revolut accepts
   any ECB currency; non-ECB currencies are skipped with a warning.
5. **Trade Republic** parser (after we get a sample export) + any other platform.
   *Deferred — needs a real export sample (likely the `pytr` `;`-delimited CSV).*
6. ✅ **Charts** *(done)*: per-security Portfolio breakdown chart (invested + realized
   per security) in the Advanced tab, shown only when >1 security is present.

Each phase is independently shippable and keeps single-stock mode green.

---

## 7. Open questions / risks

- **Ticker → ISIN** source of truth for movements-only exports (Revolut). Offline
  map vs online lookup vs ticker-grouping fallback.
- **ISIN continuity** across corporate actions / ticker renames (e.g. SQ → XYZ in
  the sample data is a *name* change at the same ISIN — confirm the export keeps the
  ISIN stable so the FIFO queue stays intact).
- **Trade Republic export shape** — unknown; need real samples (CSV vs PDF,
  language, columns, currency, whether ISIN is present).
- **Incomplete history per security** → FIFO could go negative; keep the existing
  per-queue guard + a clear, per-security error.
- **Dividends/withholding per security & per country** → RCM aggregation and the
  *deducción por doble imposición* may differ by source country.
- **Modelo 720** reminder when total foreign holdings exceed €50k across platforms.

---

## 8. Testing strategy

- Group-by-ISIN correctness; same-ISIN-across-brokers merge.
- Aggregation: portfolio net = sum of per-security nets; carryforward + 25%
  cross-offset on the aggregate; wash-sale stays per security.
- Revolut multi-ticker emission (no longer filtered); ticker→ISIN resolution paths.
- Multi-currency FX (GBP/EUR) conversion.
- Trade Republic parser fixtures (once available).
- Regression: single-stock mode output unchanged when only the primary security is
  present.

---

## 9. Proposed module layout

```
src/tax_engine/
  platforms/
    __init__.py        # registry of PlatformParser implementations
    etrade.py          # wraps existing espp/orders/rsu/options loaders
    revolut.py         # current revolut_parser, emitting ALL tickers
    traderepublic.py   # NEW (TBD)
  portfolio.py         # group-by-ISIN, per-security engines, aggregation
  securities.py        # Security model + ticker→ISIN resolution
input/
  securities.json      # optional multi-security config
```
