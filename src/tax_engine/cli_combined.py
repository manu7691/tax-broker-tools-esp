"""
Combined stocks + crypto savings-base report (Spanish IRPF).

Loads both the E-Trade stock engine and the crypto engine, merges their
per-year gains/losses into one Art. 49 LIRPF savings-base simulation, and
generates a bilingual HTML report with the combined breakdown.

Either source is optional — if stock data is absent the report shows crypto
only, and vice versa — but running both is the main use-case.

Typical usage::

    uv run tax-combined \\
      --input-dir   input \\
      --crypto-dir  input/crypto \\
      --output-dir  .

The savings-income.json and prior_losses.json from the stock input directory
are also loaded if present (same format as tax-engine).
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from tax_engine.cli_main import (
    auto_detect_sell_to_cover,
    load_events_from_excel,
    load_options_stock_events,
    load_orders_from_excel,
    load_prior_losses,
    load_savings_income,
)
from tax_engine.crypto_engine import CryptoTaxEngine, generate_combined_html
from tax_engine.crypto_parser import CryptoTrade, load_crypto_trades, trades_to_events_by_coin
from tax_engine.ecb_rates import prefetch_ecb_rates
from tax_engine.models import YearlyTaxSummary
from tax_engine.rsu_parser import load_rsu_events
from tax_engine.tax_engine import TaxEngine


def _load_stock_engine(input_dir: Path) -> TaxEngine | None:
    """Return a processed TaxEngine for E-Trade data, or None if no data found."""
    espp_path = input_dir / "espp" / "BenefitHistory.xlsx"
    orders_path = input_dir / "orders" / "orders.xlsx"
    rsu_dir = input_dir / "rsu"
    options_dir = input_dir / "options"

    has_data = any(
        [
            espp_path.exists(),
            orders_path.exists(),
            (rsu_dir.exists() and any(rsu_dir.iterdir())),
            (options_dir.exists() and any(options_dir.glob("*.pdf"))),
        ]
    )
    if not has_data:
        return None

    print("  Loading E-Trade stock data…")
    espp_events = load_events_from_excel(input_dir) if espp_path.exists() else []
    sell_events = load_orders_from_excel(input_dir)
    rsu_events = load_rsu_events(rsu_dir) if rsu_dir.exists() else []
    options_events = load_options_stock_events(input_dir)

    all_events = espp_events + sell_events + rsu_events + options_events
    if not all_events:
        return None

    auto_detect_sell_to_cover(all_events)
    prefetch_ecb_rates(all_events)

    engine = TaxEngine()
    engine.process_all(all_events)
    print(f"  Stock engine: {len(all_events)} events processed.")
    return engine


def _load_crypto_engine(
    crypto_dir: Path, binance_utc_offset_hours: int = 2
) -> CryptoTaxEngine | None:
    """Return a processed CryptoTaxEngine, or None if no data found."""
    trades = load_crypto_trades(crypto_dir, binance_utc_offset_hours=binance_utc_offset_hours)
    if not trades:
        return None
    unhandled_swaps: list[CryptoTrade] = []
    ignored_fees: list[CryptoTrade] = []
    events_by_coin = trades_to_events_by_coin(
        trades, unhandled_swaps=unhandled_swaps, ignored_fees=ignored_fees
    )
    if not events_by_coin and not unhandled_swaps:
        return None
    engine = CryptoTaxEngine()
    engine.unhandled_swaps = unhandled_swaps
    engine.ignored_fees = ignored_fees
    engine.process(events_by_coin)
    print(f"  Crypto engine: {len(trades)} trades across {len(engine.coins)} coins.")
    return engine


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Combined stocks + crypto Spanish savings-base report."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("input"),
        help="E-Trade stock data directory (default: input).",
    )
    parser.add_argument(
        "--crypto-dir",
        type=Path,
        default=None,
        help="Crypto exchange exports directory (default: <input-dir>/crypto).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Output directory for HTML reports (default: current dir).",
    )
    parser.add_argument(
        "--lang",
        choices=["es", "en", "both"],
        default="both",
        help="Report language(s): es, en, or both (default: both).",
    )
    parser.add_argument(
        "--binance-utc-offset",
        type=int,
        default=2,
        metavar="HOURS",
        help="Timezone offset (in hours) of the Binance export's Time column, "
        "shifted back to UTC so dates land on the correct day/tax year "
        "(default: 2 = CEST). Use 0 for UTC, 1 for CET.",
    )
    args = parser.parse_args()

    input_dir: Path = args.input_dir
    crypto_dir: Path = args.crypto_dir or (input_dir / "crypto")
    output_dir: Path = args.output_dir

    print("Spanish Tax Engine — Combined Stocks + Crypto Report")
    print(f"  Stock data:  {input_dir}")
    print(f"  Crypto data: {crypto_dir}\n")

    stock_engine = _load_stock_engine(input_dir)
    crypto_engine = _load_crypto_engine(
        crypto_dir, binance_utc_offset_hours=args.binance_utc_offset
    )

    if stock_engine is None and crypto_engine is None:
        print(
            "\nError: no data found in either directory.\n"
            f"  Stock:  expected {input_dir}/espp/BenefitHistory.xlsx or {input_dir}/orders/orders.xlsx\n"
            f"  Crypto: expected {crypto_dir}/pionex/trading.csv or {crypto_dir}/binance/*.csv"
        )
        return

    stock_summaries: dict[int, YearlyTaxSummary] = (
        {s.year: s for s in stock_engine.get_all_yearly_summaries()} if stock_engine else {}
    )
    crypto_summaries: dict[int, YearlyTaxSummary] = (
        crypto_engine.combined_summaries() if crypto_engine else {}
    )

    # Load savings income and prior losses from the stock input dir
    savings_income = load_savings_income(input_dir / "savings_income.json")
    prior_losses = load_prior_losses(input_dir / "prior_losses.json")

    # Print quick combined console summary
    merged = CryptoTaxEngine.merge_yearly_summaries([stock_summaries, crypto_summaries])
    print("\n" + "=" * 80)
    print("COMBINED SAVINGS BASE (Stocks + Crypto — Modelo 100)")
    print("=" * 80)
    print(f"{'Year':<8}{'Stocks':>16}{'Crypto':>16}{'Combined':>16}{'Est. Tax':>16}")
    print("-" * 80)
    for year in sorted(merged):
        ss = stock_summaries.get(year)
        cs = crypto_summaries.get(year)
        ms = merged[year]
        s_net = float(ss.net_gain_loss) if ss else 0.0
        c_net = float(cs.net_gain_loss) if cs else 0.0
        print(
            f"{year:<8}€{s_net:>14,.2f}€{c_net:>14,.2f}"
            f"€{float(ms.net_gain_loss):>14,.2f}€{float(ms.tax_due):>14,.2f}"
        )
    print("=" * 80)
    if savings_income:
        print("  (savings_income.json loaded — use full savings-ledger in the HTML report)")
    if crypto_engine and crypto_engine.unhandled_swaps:
        print(
            f"\n⚠️  {len(crypto_engine.unhandled_swaps)} crypto-to-crypto swap(s) NOT handled "
            "(taxable permutas — declare these manually)."
        )
    print()

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    langs = ["es", "en"] if args.lang == "both" else [args.lang]
    for lang in langs:
        html = generate_combined_html(
            stock_summaries=stock_summaries,
            crypto_summaries=crypto_summaries,
            savings_income=savings_income if savings_income else None,
            opening_losses=prior_losses if prior_losses else None,
            lang=lang,
        )
        out_path = output_dir / f"combined_tax_report_{lang.upper()}_{timestamp}.html"
        out_path.write_text(html, encoding="utf-8")
        print(f"Wrote {lang.upper()} combined report to: {out_path}")

    # When stock data is present, also emit the flagship PDF (the polished
    # "¿Qué declarar en Hacienda?" report), with crypto folded into the combined
    # savings base and shown as a distinct capital-gains line. Crypto-only runs
    # keep using the dedicated tax-crypto HTML report.
    if stock_engine is not None:
        for lang in langs:
            pdf_path = output_dir / f"combined_tax_report_{lang.upper()}_{timestamp}.pdf"
            stock_engine.generate_pdf_report(
                str(pdf_path),
                lang=lang,
                savings_income=savings_income if savings_income else None,
                opening_losses=prior_losses if prior_losses else None,
                crypto_summaries=crypto_summaries if crypto_summaries else None,
            )
            print(f"Wrote {lang.upper()} combined PDF to: {pdf_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
