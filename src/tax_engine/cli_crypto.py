"""
CLI for the crypto capital-gains report (Spanish FIFO).

Reads exchange exports from an input directory, computes per-coin FIFO realised
gains/losses in EUR (ECB daily rates), prints a console summary, and writes a
per-disposal CSV plus bilingual HTML reports.

Expected layout (any subset)::

    <input-dir>/pionex/trading.csv
    <input-dir>/binance/<...>Spot-Trade-History<...>.csv

Run with::

    uv run tax-crypto --input-dir input/crypto
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from tax_engine.crypto_engine import CryptoTaxEngine
from tax_engine.crypto_parser import load_crypto_trades, trades_to_events_by_coin


def main() -> None:
    parser = argparse.ArgumentParser(description="Spanish crypto capital-gains report (FIFO).")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("input/crypto"),
        help="Directory holding pionex/ and binance/ exports (default: input/crypto).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Directory where the CSV and HTML reports are written (default: current dir).",
    )
    parser.add_argument(
        "--wash-sale",
        action="store_true",
        help="Apply the 2-month homogeneous-asset rule per coin. Off by default: "
        "per DGT criteria crypto is not «valores homogéneos», so the rule does "
        "NOT apply — enable only as an explicit advisor-directed override.",
    )
    args = parser.parse_args()
    input_dir: Path = args.input_dir
    output_dir: Path = args.output_dir

    print("Spanish Crypto Tax Engine — FIFO per coin, ECB EUR valuation")
    print(f"Reading exports from: {input_dir}\n")

    trades = load_crypto_trades(input_dir)
    if not trades:
        print(
            f"\nError: no trades found under {input_dir}.\n"
            "Expected e.g. pionex/trading.csv or a binance/*Spot-Trade-History*.csv file."
        )
        return
    print(f"\nTotal trades loaded: {len(trades)}")

    unhandled_swaps: list = []
    events_by_coin = trades_to_events_by_coin(trades, unhandled_swaps=unhandled_swaps)
    if not events_by_coin and not unhandled_swaps:
        print("\nError: no taxable (non-stablecoin) positions found in the data.")
        return

    engine = CryptoTaxEngine(detect_wash_sale=args.wash_sale)
    engine.unhandled_swaps = unhandled_swaps
    engine.process(events_by_coin)
    engine.print_console()

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_path = output_dir / f"crypto_disposals_{timestamp}.csv"
    n = engine.write_disposals_csv(csv_path)
    print(f"Wrote {n} disposal row(s) to: {csv_path}")

    for lang in ("en", "es"):
        html_path = output_dir / f"crypto_tax_report_{lang.upper()}_{timestamp}.html"
        html_path.write_text(engine.generate_html(lang=lang), encoding="utf-8")
        print(f"Wrote {lang.upper()} HTML report to: {html_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
