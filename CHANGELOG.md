# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Automated E\*TRADE dividend import.** A Playwright scraper
  (`tax-download-dividends`) downloads the dividends-only cash transactions to
  `input/dividends/Cash_Transactions.xlsx`, and a parser (`tax-import-dividends`)
  merges them into `input/savings_income.json` as dated USD payments (ECB-converted
  per date downstream). The parser classifies interest lines as `interest`, captures
  foreign tax withheld at source as `foreign_tax_usd` (including negative/parenthesised
  amounts), keeps reversals so they net per year, and de-duplicates idempotently by
  date + type + description + amount. Both steps run as part of the full **Download
  E\*TRADE Data** (menu option 2) and are also available standalone via menu option 3
  ("Auto-download").

- Multi-symbol, multi-platform FIFO (Phases 1–2 of
  `docs/planning/MULTI_SYMBOL_FIFO_PLAN.md`):
  - **Phase 1 — model & engine.** A `portfolio.run_portfolio` runner that gives
    each security its own FIFO queue keyed by **ISIN** (ticker as fallback),
    merges the same ISIN across brokers into one queue, and aggregates the
    per-security results into one savings base (carryforward and 25% cross offset
    on the portfolio total; wash-sale stays per security). Adds `symbol`/`isin` to
    `StockEvent`/`ShareLot`, a `Security` model + optional `input/securities.json`
    config (`include` / `isin_map` / `primary`), and an `all_securities` mode for
    the Revolut realized-gains export (emits every ticker, ISIN-tagged).
  - **Phase 2 — reporting.** A `--all-securities` flag (auto-enabled when
    `input/securities.json` is present) runs the portfolio and produces a report
    with a per-security **Portfolio Summary** table (gains / losses / net / open
    position / brokers), the combined savings base + 4-year carryforward + 25%
    cross-offset on the portfolio total, the per-broker subtotal, and a separate
    transaction-ledger + FIFO-detail section per security. Single-stock mode is
    unchanged.
  - **Phase 3 — ticker→ISIN resolution.** In `--all-securities` mode the
    ISIN-less Revolut *movements* export now emits **every** ticker (not just the
    configured one), each resolved to an ISIN via the `securities.json` `isin_map`,
    the ISINs learned from any realized-gains export in the same folder, and a
    persistent on-disk cache (`.isin_cache.json`, override with `ISIN_CACHE`).
    Unresolved tickers group by ticker with a printed caveat. Per-ticker split
    handling keeps each security's FIFO independent.
  - **Phase 4 — multi-currency FX.** `ECBRateFetcher` is generalized to any ECB
    reference currency (USD, GBP, CHF, JPY, …) via a `currency` argument, with the
    rate cache keyed per currency (old USD-only cache files still load). `StockEvent`
    gained a `currency` field that drives conversion to EUR; `prefetch_ecb_rates`
    fetches one batch per currency. The Revolut parser now accepts any ECB currency
    (previously only USD/EUR), and the ledger labels each row's native-currency
    price. Currencies the ECB does not publish are skipped with a warning.
  - **Phase 6 — charts.** The interactive dashboard gains a per-security
    **Portfolio breakdown** chart (invested cost basis + realized gain/loss per
    security, ISIN and open position in the tooltip), rendered in the Advanced tab
    only when more than one security is present. The existing employer-stock panels
    (ESPP, RSU, trend, peers) are unchanged. (Phase 5, Trade Republic, is deferred —
    it needs a real sample export.)
  - **Portfolio demo.** A multi-security sample dataset (DT + TSLA/NVDA/ADBE on
    Revolut + a GBP-priced Shell position) and an `--all-securities` flag on both
    demo entry points — `tax-demo --all-securities` (per-security PDF) and
    `generate_charts.py --demo --all-securities` (per-security chart) — so portfolio
    mode is demonstrable without real data. The sample also exercises the **full
    report**: multi-currency (GBP), wash-sale blocking, loss-carryforward
    *application* (a 2020 loss applied against 2021 gains), the dividend/interest
    (RCM) savings base with the **25% cross-category offset**, and the **ESPP
    3-year** early-sale analysis. The launchers' demo options prompt single vs.
    multi-symbol.
- Stock options exercise support (EXERCISE acquisition events; options
  confirmation PDF parsing) and 3-year ESPP holding-period analysis (Art. 42.3.f).
- Loss Carryforward Ledger (Art. 49 LIRPF): simulates the 4-year offset of net
  losses against later gains across the tracked years and flags losses that
  expire unused. Seed pre-window losses via `input/prior_losses.json` or
  `--prior-losses`.
- Dividend/interest (RCM) savings base with the 25% cross-category offset
  (Art. 48 & 49 LIRPF). Input via `input/savings_income.json` as USD payments
  (converted to EUR at the ECB rate per payment date) or as EUR-per-year totals;
  `--savings-income` flag; interactive `tax-savings-income` CLI; foreign tax
  withheld reported for reference.
- "Add Dividend/Interest Income" option in the macOS/Linux and Windows launchers.
- Interactive Charts & Tax Dashboard (`generate_charts.py`, launcher option 6):
  a self-contained `charts_dashboard.html` organised as a plain-language, four-tab
  narrative (My Stock / Should I Sell? / My Taxes / Advanced) for non-finance users.
  Includes a "situation today" snapshot, a sell simulator with after-tax net cash and
  an ESPP-lock warning, the ESPP secured/at-risk/lost split with a 3-year countdown,
  fresh-start vs. full-portfolio break-even, a dividends/interest card feeding the
  tax-bracket optimiser, an EN/ES toggle (localised dates and month names), and a
  privacy-blur mode. The ticker is auto-detected from the data and the live price is
  fetched from Yahoo Finance. Bilingual guide in `docs/DASHBOARD_GUIDE_{EN,ES}.md`.
- Modelo 100 filing guide in the report, mapping each figure to its *apartado*
  (with casilla numbers flagged to verify per year).
- ECB exchange-rate cache persisted to disk for reproducible, offline-capable
  runs (override path with `ECB_RATE_CACHE`).
- CLI options for the analysis: `--input-dir`, `--output-dir`, `--prior-losses`,
  `--savings-income`.
- Wash-sale "single account" scope caveat in the report.

### Added

- Dashboard: three portfolio-level cards — **Realized Gains & Estimated Tax by
  Year** (per-year bar), **Currency Exposure** (current EUR value by trading
  currency, shown when >1 currency), and **Tax-Loss Harvesting** (open positions
  at an unrealized loss you could sell to offset this year's gains, with the
  2-month wash-sale caveat). Bilingual.
- Report: a **Capital Gains Detail — Disposals (Modelo 100)** table — one
  copy-ready row per FIFO lot consumed by each sale (security, ISIN, acquisition
  date/value, transmission date/value, pro-rata fees, net gain/loss) for the
  *ganancias y pérdidas patrimoniales* entry.
- Report: a **15% treaty-cap note** on the *deducción por doble imposición*, plus
  a per-year flag when the effective withholding exceeds the 15% treaty rate
  (only 15% is creditable in Spain; the rest must be reclaimed at source).
- Revolut RCM: symbol-less "Other income" rows are now classified as **interest**
  (vs dividends for symbol-bearing rows) — different Modelo 100 casillas.

### Changed

- Internal: split report rendering out of `tax_engine.py` into a new
  `report.py` (`ReportRenderer`); `tax_engine.py` is now calculation-only and its
  reporting methods delegate to it. No change to output or the public API.
- Internal: the PDF/HTML report is now rendered from a Jinja template
  (`templates/report.html.j2`) instead of inline string concatenation. Output is
  byte-for-byte equivalent (verified across all report branches). Adds a `jinja2`
  dependency.
- Tooling: CI now runs across Python **3.10–3.13** (was 3.12–3.13) to match the
  advertised `requires-python = ">=3.10"`, type-checks against the 3.10 floor, and
  reports test coverage (`pytest-cov`, non-blocking).
- Dashboard break-even: clearer description of why it can look high (Spanish FIFO
  leaves the newest lots; EUR cost shown as a USD price at today's FX; the full-
  portfolio line also recovers past realized losses), and a **per-broker filter**
  (All / E*TRADE / Revolut) for the remaining shares — flagged as informational,
  since Spanish FIFO is one pool per ISIN.
- Dashboard: honor `securities.json` `primary` as the default-selected security,
  and add a per-security **Dividends (EUR)** column to the portfolio holdings table
  (from the Revolut "Other income" rows; informational — the taxable RCM base stays
  portfolio-level).
- Launcher *Calculate Tax* now asks whether to process **all securities across
  brokers** (portfolio mode) and passes `--all-securities`; corrected the stale
  "other tickers are ignored" hint (both `run_tax_engine.command` and `.bat`).
- Portfolio-mode console output prints **per-security** current positions instead
  of a single mixed-securities average (which was meaningless across securities).
- Documented portfolio mode: README (EN+ES) Revolut/Input-Files sections updated
  (incl. an `input/securities.json` row and the widened currency support), plus a
  new bilingual `docs/MULTI_SECURITY_GUIDE_{EN,ES}.md`.
- Customized for Spanish tax rules; consolidated compliance notes and added
  visual guides.
- Merged the duplicate "Modelo 100" report table into the Yearly Tax Summary;
  relabeled the tax column as an *isolated* estimate with a disclaimer.
- Use the execution date (not the order date) for sell events.
- Transaction commissions and SEC fees are deducted; wire-transfer fees are
  excluded by default.

### Fixed

- Transaction fees were converted to EUR by **dividing** by the FX rate instead of
  multiplying (the price uses multiplication). Fees are now deducted at the same
  rate as the price. Small magnitude (fees are a few dollars) but a correctness fix.
- Prevent wash-sale over-blocking across overlapping loss sales.
- Documentation: corrected the orders input path (`input/orders/orders.xlsx`)
  and added the required `input/espp/BenefitHistory.xlsx` to the input diagrams.
- Fixed formatting of stock share counts to correctly display fractional shares (up to 6 decimal places) in CLI output and HTML reports instead of rounding/truncating them to 0.
- Fixed historical market data fetching to query and use the actual real-time/live price first, falling back to the last close quote if unavailable.

## [0.1.0] - 2026-02-05

### Added

- Initial release
- Spanish tax calculation using FIFO Cost Basis method (and 2-month wash sale rule)
- E-Trade integration for RSU and ESPP data download
- ECB exchange rate fetching for USD/EUR conversion
- PDF report generation
- RSU confirmation PDF parsing
- Interactive CLI for non-developers
