"""
Demo script to run the tax engine with sample data.

By default it runs the single-security demo (employer stock only). With
``--all-securities`` it runs the multi-security **portfolio** demo (DT + TSLA +
NVDA + a GBP-priced Shell position) so you can see per-security reporting and the
multi-currency path without supplying real data.

``--crypto`` runs the crypto FIFO demo (BTC + ETH + SOL across two exchanges) and
``--combined`` merges the stock portfolio and crypto demos into one savings-base
report. All modes use manual FX rates, so the demo never touches the network.
"""

import argparse
import datetime

from tax_engine import (
    CryptoTaxEngine,
    TaxEngine,
    create_sample_crypto_events,
    create_sample_espp_map,
    create_sample_events_with_ecb_rates,
    create_sample_multi_security_events,
    create_sample_savings_income,
    generate_combined_html,
    prefetch_ecb_rates,
    run_portfolio,
)


def _run_crypto_demo() -> None:
    """Crypto FIFO demo: per-coin sample trades, console summary + HTML reports."""
    print("Spanish Crypto Tax Engine — FIFO per coin")
    print("\n** DEMO MODE: Using sample crypto data (BTC + ETH + SOL) **\n")

    events_by_coin = create_sample_crypto_events()
    engine = CryptoTaxEngine()
    engine.process(events_by_coin)
    engine.print_console()

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    for lang in ("en", "es"):
        html_path = f"crypto_tax_report_demo_{lang.upper()}_{timestamp}.html"
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(engine.generate_html(lang=lang))
        print(f"Wrote {lang.upper()} HTML report to: {html_path}")
    print("\nDone.")


def _run_combined_demo() -> None:
    """Combined demo: stock portfolio + crypto merged into one savings base."""
    print("Spanish Tax Engine — Combined Stocks + Crypto Report")
    print("\n** DEMO MODE: Using sample stock portfolio + crypto data **\n")

    stock_portfolio = run_portfolio(create_sample_multi_security_events())
    stock_summaries = {s.year: s for s in stock_portfolio.aggregate.get_all_yearly_summaries()}

    crypto_engine = CryptoTaxEngine()
    crypto_engine.process(create_sample_crypto_events())
    crypto_summaries = crypto_engine.combined_summaries()
    savings_income = create_sample_savings_income()

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    for lang in ("en", "es"):
        html = generate_combined_html(
            stock_summaries=stock_summaries,
            crypto_summaries=crypto_summaries,
            savings_income=savings_income,
            lang=lang,
        )
        out_path = f"combined_tax_report_demo_{lang.upper()}_{timestamp}.html"
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"Wrote {lang.upper()} combined report to: {out_path}")
    print("\nDone.")


def main() -> None:
    """Run the tax engine with sample data (single-security or portfolio)."""
    parser = argparse.ArgumentParser(description="Spanish Tax Engine demo with sample data.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--all-securities",
        action="store_true",
        help="Portfolio demo: several securities (incl. a GBP one), each with its "
        "own ISIN-keyed FIFO queue, rolled up into one savings base.",
    )
    mode_group.add_argument(
        "--crypto",
        action="store_true",
        help="Crypto demo: per-coin FIFO over sample BTC/ETH/SOL trades, with "
        "bilingual HTML reports.",
    )
    mode_group.add_argument(
        "--combined",
        action="store_true",
        help="Combined demo: stock portfolio + crypto merged into one savings-base "
        "report (bilingual HTML).",
    )
    args = parser.parse_args()

    if args.crypto:
        _run_crypto_demo()
        return
    if args.combined:
        _run_combined_demo()
        return

    print("Spanish Tax Engine for E-Trade RSUs and ESPP")
    print("Using FIFO Cost Basis (First In, First Out) & Progressive Savings Rate Scale")
    mode = "PORTFOLIO (multi-security)" if args.all_securities else "single-security"
    print(f"\n** DEMO MODE: Using sample data — {mode} **\n")

    report_securities = None
    savings_income = None
    espp_discounts = None
    espp_early_sales = None
    if args.all_securities:
        # Multi-security sample uses manual FX rates for trades (no network).
        events = create_sample_multi_security_events()
        portfolio = run_portfolio(events)
        engine = portfolio.aggregate
        report_securities = portfolio.results
        # Add dividends/interest (RCM) and an ESPP map so the report's savings-base
        # + 25% cross-offset and ESPP 3-year sections render too.
        savings_income = create_sample_savings_income()
        from tax_engine.cli_main import detect_espp_early_sales

        espp_early_sales, _ = detect_espp_early_sales(
            engine.processed_events, create_sample_espp_map()
        )
        print(
            f"Portfolio: {len(portfolio.results)} securities — "
            f"{', '.join(r.security.label for r in portfolio.results)}.\n"
        )
    else:
        # Single-security sample: events without FX rates fetched from the ECB.
        events = create_sample_events_with_ecb_rates()
        prefetch_ecb_rates(events)
        engine = TaxEngine()
        engine.process_all(events)

    # Print results
    engine.print_ledger()
    engine.print_tax_summary(savings_income=savings_income)

    # Show current state
    if report_securities is not None:
        print("\nCurrent Positions (per security):")
        for r in report_securities:
            st = r.engine.state
            if st.total_shares > 0:
                print(
                    f"  {r.security.label}: {st.total_shares:,.4f} shares, "
                    f"avg cost €{st.avg_cost_eur:,.4f}, cost basis €{st.total_portfolio_cost_eur:,.2f}"
                )
    else:
        print(f"\nCurrent Position: {engine.state.total_shares} shares")
        print(f"Current Informational Avg Cost: €{engine.state.avg_cost_eur:,.4f}")
        print(f"Total Portfolio Cost: €{engine.state.total_portfolio_cost_eur:,.4f}")

    # Generate PDF reports (English and Spanish for Hacienda)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "portfolio_" if args.all_securities else ""
    pdf_path_en = f"tax_report_demo_{suffix}EN_{timestamp}.pdf"
    pdf_path_es = f"tax_report_demo_{suffix}ES_{timestamp}.pdf"
    print(f"Generating English PDF report at: {pdf_path_en}...")
    engine.generate_pdf_report(
        pdf_path_en,
        lang="en",
        savings_income=savings_income,
        espp_discounts=espp_discounts,
        espp_early_sale_discounts=espp_early_sales,
        securities=report_securities,
    )
    print(f"Generating Spanish PDF report (for Hacienda) at: {pdf_path_es}...")
    engine.generate_pdf_report(
        pdf_path_es,
        lang="es",
        savings_income=savings_income,
        espp_discounts=espp_discounts,
        espp_early_sale_discounts=espp_early_sales,
        securities=report_securities,
    )
    print("PDF generation complete.")


if __name__ == "__main__":
    main()
