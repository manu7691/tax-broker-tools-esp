# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
- Modelo 100 filing guide in the report, mapping each figure to its *apartado*
  (with casilla numbers flagged to verify per year).
- ECB exchange-rate cache persisted to disk for reproducible, offline-capable
  runs (override path with `ECB_RATE_CACHE`).
- CLI options for the analysis: `--input-dir`, `--output-dir`, `--prior-losses`,
  `--savings-income`.
- Wash-sale "single account" scope caveat in the report.

### Changed

- Customized for Spanish tax rules; consolidated compliance notes and added
  visual guides.
- Merged the duplicate "Modelo 100" report table into the Yearly Tax Summary;
  relabeled the tax column as an *isolated* estimate with a disclaimer.
- Use the execution date (not the order date) for sell events.
- Transaction commissions and SEC fees are deducted; wire-transfer fees are
  excluded by default.

### Fixed

- Prevent wash-sale over-blocking across overlapping loss sales.
- Documentation: corrected the orders input path (`input/orders/orders.xlsx`)
  and added the required `input/espp/BenefitHistory.xlsx` to the input diagrams.

## [0.1.0] - 2026-02-05

### Added

- Initial release
- Spanish tax calculation using FIFO Cost Basis method (and 2-month wash sale rule)
- E-Trade integration for RSU and ESPP data download
- ECB exchange rate fetching for USD/EUR conversion
- PDF report generation
- RSU confirmation PDF parsing
- Interactive CLI for non-developers
