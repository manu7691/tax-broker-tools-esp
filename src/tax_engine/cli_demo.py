"""
Demo script to run the tax engine with sample data.

This script demonstrates the tax engine functionality using the sample data
that was previously in main.py. Use this to see the example calculations.
"""


from tax_engine import (
    TaxEngine,
    create_sample_events_with_ecb_rates,
    prefetch_ecb_rates,
)


def main() -> None:
    """Run the tax engine with sample data using ECB rates."""
    print("Spanish Tax Engine for E-Trade RSUs and ESPP")
    print("Using FIFO Cost Basis (First In, First Out) & Progressive Savings Rate Scale")
    print("\n** DEMO MODE: Using sample data **\n")

    # Create events without FX rates - they'll be fetched from ECB
    events = create_sample_events_with_ecb_rates()

    # Pre-fetch all ECB rates in one API call (more efficient)
    prefetch_ecb_rates(events)

    # Create engine and process events
    engine = TaxEngine()
    engine.process_all(events)

    # Print results
    engine.print_ledger()
    engine.print_tax_summary()

    # Show current state
    print(f"\nCurrent Position: {engine.state.total_shares} shares")
    print(f"Current Informational Avg Cost: €{engine.state.avg_cost_eur:,.4f}")
    print(f"Total Portfolio Cost: €{engine.state.total_portfolio_cost_eur:,.4f}")

    # Generate PDF reports (English and Spanish for Hacienda)
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path_en = f"tax_report_demo_EN_{timestamp}.pdf"
    pdf_path_es = f"tax_report_demo_ES_{timestamp}.pdf"
    print(f"Generating English PDF report at: {pdf_path_en}...")
    engine.generate_pdf_report(pdf_path_en, lang="en")
    print(f"Generating Spanish PDF report (for Hacienda) at: {pdf_path_es}...")
    engine.generate_pdf_report(pdf_path_es, lang="es")
    print("PDF generation complete.")


if __name__ == "__main__":
    main()
