# Copilot Instructions for Tax Engine

## Project Overview

This is a **Spanish Tax Engine** that calculates capital gains tax using the **FIFO (First-In, First-Out) Cost Basis method** required by Spanish tax law (Agencia Tributaria). It processes stock transactions from E-Trade including RSU vesting, ESPP purchases, stock options exercises, and manual sales.

## Running Commands

- **Always use `uv run`** to execute Python commands (not `pip`, `python`, or `python3`)
- Run tests: `uv run pytest tests/ -v`
- Run the app: `uv run python main.py`

## Architecture Notes

- **models.py**: Data classes (`StockEvent`, `ProcessedEvent`, `YearlyTaxSummary`, `TaxEngineState`, `ShareLot`, `FifoMatch`)
- **tax_engine.py**: Core calculation logic only (FIFO lots, progressive tax scale, wash sale rules, carryforward/savings ledgers). The `TaxEngine` reporting methods (`print_ledger`, `print_tax_summary`, `generate_html_content`, `generate_pdf_report`) are thin delegators to `report.py`.
- **report.py**: Report rendering (`ReportRenderer`). Console tables plus the bilingual HTML/PDF report, rendered from the Jinja template `templates/report.html.j2`. No tax calculation — it only reads computed results off the engine.
- **ecb_rates.py**: Fetches USD/EUR rates from ECB Statistical Data Warehouse
- **rsu_parser.py**: Parses RSU confirmation PDFs using regex
- **options_parser.py**: Parses Stock Options exercise confirmations
- **cli_main.py**: Orchestrates the CLI, reads `orders.xlsx` (sells) and `BenefitHistory.xlsx` (ESPP buys)
- **sample_data.py**: Creates sample events for testing/demo

## Key Tax Rules (Spanish Law - LIRPF)

- **FIFO Method (Art. 37.1.a LIRPF)**: Shares sold are always matched against the oldest acquired available shares of the same security.
- **Progressive Savings Tax Scale (Art. 66 LIRPF)**:
  - Up to €6,000: 19%
  - €6,000.01 - €50,000: 21%
  - €50,000.01 - €200,000: 23%
  - €200,000.01 - €300,000: 27%
  - Over €300,000: 28%
- **2-Month Wash Sale Rule (Art. 33.5.f LIRPF)**: Losses are blocked (deferred) if homogeneous shares remain in the portfolio that were acquired within 2 months before or after the loss-making sale.
- **ESPP Discounts**: Usually treated as salary income (Rendimiento del Trabajo). When correctly declared as such, the cost basis for capital gains is the FMV at purchase, preventing double taxation.
