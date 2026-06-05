"""
Spanish Tax Engine for E-Trade RSUs, ESPP, and Stock Options.

Calculates capital gains tax using the Spanish FIFO cost basis method
for stocks acquired through RSU vesting, ESPP purchases, and stock options exercises.

Main entry point for the application.
"""

import argparse
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from tax_engine import (
    EventType,
    StockEvent,
    TaxEngine,
    load_rsu_events,
    prefetch_ecb_rates,
)
from tax_engine.options_parser import load_options_events


def load_prior_losses(path: Path) -> dict[int, Decimal]:
    """
    Load pending net losses from years *before* the imported data window.

    Expects a JSON object mapping origin-year (string) to loss magnitude
    (positive number), e.g. {"2019": 1500.00, "2020": 300}. Returns an empty
    dict if the file does not exist or cannot be parsed.
    """
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: could not read prior-losses file {path}: {e}")
        return {}

    result: dict[int, Decimal] = {}
    for year_str, amount in raw.items():
        try:
            result[int(year_str)] = abs(Decimal(str(amount)))
        except (ValueError, InvalidOperation):
            print(f"Warning: skipping invalid prior-loss entry {year_str!r}: {amount!r}")
    if result:
        print(f"Loaded prior-year pending losses for {len(result)} year(s) from {path}.")
    return result


def load_events_from_excel(input_dir: Path = Path("input")) -> list[StockEvent]:
    """
    Load stock events from the BenefitHistory.xlsx file.
    """
    excel_path = input_dir / "espp" / "BenefitHistory.xlsx"

    # Read the ESPP sheet
    # The user mentioned the sheet is named "ESPP"
    try:
        df = pd.read_excel(excel_path, sheet_name="ESPP")
    except ValueError:
        print("Warning: Sheet 'ESPP' not found, attempting to read the first sheet.")
        df = pd.read_excel(excel_path, sheet_name=0)

    events = []

    for i, (_, row) in enumerate(df.iterrows()):
        row_num = i + 2  # Excel row number (1-based + header)

        # Filter for Purchase events
        if row.get("Record Type") != "Purchase":
            continue

        try:
            # Parse date
            # Format in Excel is like "05-DEC-2022"
            raw_date = row["Purchase Date"]
            if pd.isna(raw_date):
                print(f"Warning: Empty purchase date in row {row_num}, skipping.")
                continue
            if hasattr(raw_date, "date"):
                event_date = raw_date.date()
            else:
                event_date = datetime.strptime(str(raw_date).strip(), "%d-%b-%Y").date()

            # Parse quantity
            qty_str = str(row["Purchased Qty."]).replace(",", "").strip()
            if pd.isna(row["Purchased Qty."]) or qty_str in ("", "--", "N/A"):
                print(f"Warning: Invalid quantity in row {row_num}, skipping.")
                continue
            shares = Decimal(qty_str)

            # Parse price (FMV at purchase date)
            # Format is like "$37.56"
            price_raw = row["Purchase Date FMV"]
            if pd.isna(price_raw):
                print(f"Warning: Empty price in row {row_num}, skipping.")
                continue
            price_str = str(price_raw).replace("$", "").replace(",", "").strip()
            if price_str in ("", "--", "N/A"):
                print(f"Warning: Invalid price '{price_raw}' in row {row_num}, skipping.")
                continue
            price_usd = Decimal(price_str)

        except (ValueError, InvalidOperation) as e:
            print(f"Error parsing ESPP row {row_num}: {e}")
            continue

        event = StockEvent(
            event_date=event_date,
            event_type=EventType.BUY,
            shares=shares,
            price_usd=price_usd,
            notes="ESPP Purchase",
        )
        events.append(event)

    # Sort events by date
    events.sort(key=lambda x: x.event_date)

    return events


def load_orders_from_excel(input_dir: Path = Path("input")) -> list[StockEvent]:
    """
    Load sell orders from the orders.xlsx file.
    """
    excel_path = input_dir / "orders" / "orders.xlsx"
    if not excel_path.exists():
        print(f"Warning: {excel_path} not found. No sell orders loaded.")
        return []

    df = pd.read_excel(excel_path)
    events = []

    for i, (_, row) in enumerate(df.iterrows()):
        # Parse date
        # Prefer Execution Date (actual trade date) over Order Date (when order was placed).
        # Older downloads may only have Order Date — fall back gracefully.
        raw_date = row.get("Execution Date") if "Execution Date" in row.index else None
        if raw_date is None or pd.isna(raw_date):
            raw_date = row["Order Date"]
        if hasattr(raw_date, "date"):
            event_date = raw_date.date()
        else:
            # Format is MM/DD/YYYY (e.g. 12/22/2019)
            # E-Trade might append time/timezone like "09/06/2024 02:45:31 PM ET", so we grab just the date part.
            date_str = str(raw_date).strip().split(" ")[0]
            try:
                event_date = datetime.strptime(date_str, "%m/%d/%Y").date()
            except ValueError:
                # Try alternate format just in case
                try:
                    event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    print(f"Error parsing date: {raw_date}")
                    continue

        # Skip Stock Options rows — same-day sales are already captured from
        # the options confirmation PDFs via load_options_stock_events()
        benefit_type = str(row.get("Benefit Type", "")).strip()
        if benefit_type == "Stock Options":
            continue

        # Parse quantity
        # row index + 1 for 0-based index, +1 for header row
        printable_index = i + 2
        sold_qty_str = str(row["Sold Qty."]).replace(",", "").strip()
        if sold_qty_str == "--":
            print(f"Skipping row #{printable_index}: canceled order")
            continue

        try:
            qty = Decimal(sold_qty_str)
        except InvalidOperation:
            print(f"Error parsing quantity in row #{printable_index}: {sold_qty_str}")
            continue
        if qty <= 0:
            print(f"Skipping row #{printable_index}: zero quantity")
            continue

        # Parse price
        price = Decimal(str(row["Execution Price"]).replace("$", "").replace(",", ""))

        # Sum up any fees from columns if they exist (excluding Wire/Disbursement Fees per user request)
        fees_usd = Decimal("0")
        for fee_col in ["Commission", "SEC Fees", "Brokerage Assist Fee", "Fees"]:
            if fee_col in df.columns:
                val = str(row[fee_col]).replace("$", "").replace(",", "").strip()
                if val and val.lower() not in ("nan", "none", "0", "0.00", ""):
                    try:
                        fees_usd += Decimal(val)
                    except Exception:
                        pass

        notes = f"Sell Order ({row.get('Benefit Type', 'Unknown')})"
        if fees_usd > 0:
            notes += f" (Includes ${fees_usd} fees)"

        events.append(
            StockEvent(
                event_date=event_date,
                event_type=EventType.SELL,
                shares=qty,
                price_usd=price,
                fees_usd=fees_usd,
                notes=notes,
            )
        )

    return events


def load_options_stock_events(input_dir: Path = Path("input")) -> list[StockEvent]:
    """
    Convert options exercise confirmations into StockEvent objects.

    Each exercise creates an EXERCISE event (acquisition at FMV).
    Same-day sales additionally create a SELL event at the sale price.
    """
    exercises = load_options_events(input_dir / "options")
    events: list[StockEvent] = []

    for ex in exercises:
        # EXERCISE event: acquire shares at FMV (Exercise Market Value)
        # This is the cost basis for Spanish capital gains purposes (FIFO).
        exercise_event = StockEvent(
            event_date=ex.exercise_date,
            event_type=EventType.EXERCISE,
            shares=ex.shares_exercised,
            price_usd=ex.fmv_usd,
            notes=f"Options Exercise (strike ${ex.grant_price_usd}, {ex.exercise_type})",
        )
        events.append(exercise_event)

        # Same-day sale: also add a SELL event at the sale price
        if ex.exercise_type == "Same-Day Sale" and ex.sale_price_usd and ex.shares_sold:
            sell_event = StockEvent(
                event_date=ex.exercise_date,
                event_type=EventType.SELL,
                shares=ex.shares_sold,
                price_usd=ex.sale_price_usd,
                notes=f"Options Same-Day Sale (order {ex.order_number})",
            )
            events.append(sell_event)

    return events


def calculate_espp_discounts(input_dir: Path = Path("input")) -> dict[int, Decimal]:
    """
    Calculate ESPP discount (taxable salary benefit) for each year.
    Returns a dictionary of year -> total discount in EUR.
    """
    excel_path = input_dir / "espp" / "BenefitHistory.xlsx"
    if not excel_path.exists():
        return {}

    try:
        df = pd.read_excel(excel_path, sheet_name="ESPP")
    except ValueError:
        try:
            df = pd.read_excel(excel_path, sheet_name=0)
        except Exception:
            return {}
    except Exception:
        return {}

    from tax_engine.ecb_rates import ECBRateFetcher

    discounts_by_year: dict[int, Decimal] = {}

    for i, row in df.iterrows():
        row_num = i + 2

        if row.get("Record Type") != "Purchase":
            continue

        try:
            # Parse Date
            raw_date = row["Purchase Date"]
            if pd.isna(raw_date):
                continue
            if hasattr(raw_date, "date"):
                event_date = raw_date.date()
            else:
                event_date = datetime.strptime(str(raw_date).strip(), "%d-%b-%Y").date()

            # Parse Quantity
            qty_str = str(row["Purchased Qty."]).replace(",", "").strip()
            shares = Decimal(qty_str)

            # Parse FMV
            fmv_raw = row["Purchase Date FMV"]
            fmv_str = str(fmv_raw).replace("$", "").replace(",", "").strip()
            fmv_usd = Decimal(fmv_str)

            # Parse Price Paid
            price_raw = row["Purchase Price"]
            price_str = str(price_raw).replace("$", "").replace(",", "").strip()
            price_usd = Decimal(price_str)

            # Calculate discount in USD
            discount_usd = (fmv_usd - price_usd) * shares

            # Convert to EUR using ECB rate
            fx_rate = ECBRateFetcher.get_rate(event_date)
            discount_eur = (discount_usd * fx_rate).quantize(Decimal("0.01"))

            year = event_date.year
            discounts_by_year[year] = discounts_by_year.get(year, Decimal("0")) + discount_eur

        except Exception:
            continue

    return discounts_by_year


def build_espp_purchase_map(input_dir: Path = Path("input")) -> dict:
    """
    Build a map of ESPP purchase dates to (fmv_usd, purchase_price_usd).
    Used for 3-year holding period analysis (Art. 42.3.f LIRPF).
    """
    excel_path = input_dir / "espp" / "BenefitHistory.xlsx"
    if not excel_path.exists():
        return {}

    try:
        df = pd.read_excel(excel_path, sheet_name="ESPP")
    except ValueError:
        try:
            df = pd.read_excel(excel_path, sheet_name=0)
        except Exception:
            return {}
    except Exception:
        return {}

    purchase_map: dict = {}

    for _, row in df.iterrows():
        if row.get("Record Type") != "Purchase":
            continue
        try:
            raw_date = row["Purchase Date"]
            if pd.isna(raw_date):
                continue
            if hasattr(raw_date, "date"):
                purchase_date = raw_date.date()
            else:
                purchase_date = datetime.strptime(str(raw_date).strip(), "%d-%b-%Y").date()

            fmv_str = str(row["Purchase Date FMV"]).replace("$", "").replace(",", "").strip()
            fmv_usd = Decimal(fmv_str)

            price_str = str(row["Purchase Price"]).replace("$", "").replace(",", "").strip()
            purchase_price_usd = Decimal(price_str)

            if purchase_date not in purchase_map:
                purchase_map[purchase_date] = (fmv_usd, purchase_price_usd)
        except Exception:
            continue

    return purchase_map


def detect_espp_early_sales(
    processed_events: list,
    espp_map: dict,
) -> tuple:
    """
    Detect ESPP shares sold before the 3-year holding period (Art. 42.3.f LIRPF).

    When ESPP shares are sold before 3 years, the purchase discount loses its
    tax exemption and becomes taxable salary income (rendimiento del trabajo)
    in the year of the sale.

    Returns:
        - dict of sell_year -> total taxable discount in EUR
        - list of detail dicts for reporting
    """
    from tax_engine.ecb_rates import ECBRateFetcher

    def _add_years(d: date, years: int) -> date:
        try:
            return d.replace(year=d.year + years)
        except ValueError:  # Feb 29
            return d.replace(year=d.year + years, day=28)

    taxable_by_year: dict[int, Decimal] = {}
    details: list[dict] = []

    for pe in processed_events:
        if pe.event.event_type != EventType.SELL:
            continue

        sell_date = pe.event.event_date

        for match in pe.fifo_matches:
            if "ESPP" not in match.notes:
                continue

            acq_date = match.acquisition_date
            three_year_mark = _add_years(acq_date, 3)

            espp_info = espp_map.get(acq_date)
            if not espp_info:
                continue

            fmv_usd, purchase_price_usd = espp_info
            discount_per_share_usd = fmv_usd - purchase_price_usd

            if sell_date < three_year_mark:
                # Sold before 3 years — discount is taxable salary income
                fx_rate = ECBRateFetcher.get_rate(acq_date)
                discount_eur = (discount_per_share_usd * match.shares * fx_rate).quantize(
                    Decimal("0.01")
                )

                purchase_year = acq_date.year
                taxable_by_year[purchase_year] = taxable_by_year.get(purchase_year, Decimal("0")) + discount_eur

                holding_days = (sell_date - acq_date).days
                details.append({
                    "acquisition_date": acq_date,
                    "sell_date": sell_date,
                    "shares": match.shares,
                    "holding_days": holding_days,
                    "discount_per_share_usd": discount_per_share_usd,
                    "discount_eur": discount_eur,
                })

    return taxable_by_year, details


def auto_detect_sell_to_cover(events: list[StockEvent]) -> None:
    """
    Detects which SELL events are actually 'Sell-to-Cover' for RSU taxes.
    Matches SELL events within 3 days of a VEST event where the sold quantity
    exactly matches the shares_sold_to_cover from the RSU confirmation.
    """
    vests = [e for e in events if e.event_type == EventType.VEST]
    sells = [e for e in events if e.event_type == EventType.SELL and "Sell Order" in e.notes]

    for sell in sells:
        is_sell_to_cover = False
        
        # Check against RSU vests
        for vest in vests:
            # Must be within 3 days (often same day, but can vary by a day or two)
            days_diff = abs((sell.event_date - vest.event_date).days)
            if days_diff <= 3:
                # If quantity matches exactly the withheld shares from the PDF
                if sell.shares == vest.shares_sold_to_cover and vest.shares_sold_to_cover > 0:
                    is_sell_to_cover = True
                    break
        
        if is_sell_to_cover:
            sell.notes = sell.notes.replace("Sell Order", "Sell-to-Cover (Auto-detected)")
        else:
            sell.notes = sell.notes.replace("Sell Order", "Manual Sell")


def main() -> None:
    """Run the tax engine with actual data from Excel files."""
    parser = argparse.ArgumentParser(
        description="Spanish Tax Engine for E-Trade RSUs, ESPP, and Stock Options."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("input"),
        help="Directory holding espp/, orders/, rsu/, options/ data (default: input).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Directory where PDF reports are written (default: current directory).",
    )
    parser.add_argument(
        "--prior-losses",
        type=Path,
        default=None,
        help="JSON file of pending losses from before the data window "
        "({\"2019\": 1500, ...}). Defaults to <input-dir>/prior_losses.json if present.",
    )
    args = parser.parse_args()
    input_dir: Path = args.input_dir
    output_dir: Path = args.output_dir
    prior_losses_path: Path = args.prior_losses or (input_dir / "prior_losses.json")
    opening_losses = load_prior_losses(prior_losses_path)

    print("Spanish Tax Engine for E-Trade RSUs and ESPP")
    print("Using FIFO Cost Basis (First In, First Out) & Progressive Savings Rate Scale")
    print()

    # Load events from Excel file
    excel_path = input_dir / "espp" / "BenefitHistory.xlsx"
    if not excel_path.exists():
        print(f"Error: {excel_path} not found")
        print("\nTo run the sample example, use: uv run tax-demo")
        return

    espp_events = load_events_from_excel(input_dir)
    sell_events = load_orders_from_excel(input_dir)
    rsu_events = load_rsu_events(input_dir / "rsu")
    options_events = load_options_stock_events(input_dir)

    # Combine all events (engine.process_all handles chronological sorting internally)
    events = espp_events + sell_events + rsu_events + options_events

    # Auto-detect sell-to-cover vs manual sells
    auto_detect_sell_to_cover(events)

    # Pre-fetch all ECB rates in one API call (more efficient)
    prefetch_ecb_rates(events)

    # Create engine and process events
    engine = TaxEngine()
    engine.process_all(events)

    # Print results
    engine.print_ledger()
    engine.print_tax_summary(opening_losses=opening_losses)

    # ESPP Analysis: 3-year holding period detection
    espp_discounts = calculate_espp_discounts(input_dir)
    espp_map = build_espp_purchase_map(input_dir)
    espp_early_sales, espp_early_details = detect_espp_early_sales(
        engine.processed_events, espp_map
    )

    if espp_early_sales:
        print("⚠️  ESPP EARLY SALE ALERT (Art. 42.3.f LIRPF)")
        print("-" * 95)
        print("The following ESPP discounts are TAXABLE as salary income")
        print("because shares were sold BEFORE the 3-year holding period:")
        print("Required Action: You must file a 'Declaración Complementaria' (Complementary Return)")
        print("for the purchase year to include this income as taxable salary, as the original")
        print("exemption is now void.")
        print()
        for year, amount in sorted(espp_early_sales.items()):
            print(f"  Purchase Year {year} (Complementary Return required):")
            print(f"    Total Taxable ESPP Discount (Rendimiento del Trabajo): €{amount:>12,.2f}")
            print(f"    (Because shares purchased in {year} were sold in the following transactions before 3 years)")
            print()
            
            # Print details for this year
            for detail in espp_early_details:
                if detail["acquisition_date"].year != year:
                    continue
                    
                acq_str = detail["acquisition_date"].strftime("%Y-%m-%d")
                sell_str = detail["sell_date"].strftime("%Y-%m-%d")
                days = detail["holding_days"]
                y, m = divmod(days, 365)
                m = m // 30
                shares = detail["shares"]
                disc_eur = detail["discount_eur"]

                print(
                    f"    ├─ Sold {shares:,.0f} shares on {sell_str} "
                    f"(Held only {y}y {m}m) -> €{disc_eur:,.2f} taxable"
                )
            print()
        print("  ⚠️  REMINDER: Employee must have signed the ESPP enrollment/agreement")
        print("     document from the company for each offering period.")
        print()
    elif espp_discounts:
        print("\nSPANISH RENTA (Modelo 100 - Rendimientos del Trabajo)")
        print("-" * 95)
        print("  ✅ No ESPP shares were sold before the 3-year holding period.")
        print("     All ESPP discounts may be exempt under Art. 42.3.f LIRPF")
        print("     (subject to €12,000/year limit and company sign-off).")
        print()
        print("  ⚠️  REMINDER: Employee must have signed the ESPP enrollment/agreement")
        print("     document from the company for each offering period.")
        print()

    # Show current state
    print(f"\nCurrent Position: {engine.state.total_shares} shares")
    print(f"Current Informational Avg Cost: €{engine.state.avg_cost_eur:,.4f}")
    print(f"Total Portfolio Cost: €{engine.state.total_portfolio_cost_eur:,.4f}")

    # Generate PDF reports (English and Spanish for Hacienda)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path_en = str(output_dir / f"tax_report_EN_{timestamp}.pdf")
    pdf_path_es = str(output_dir / f"tax_report_ES_{timestamp}.pdf")
    print(f"Generating English PDF report at: {pdf_path_en}...")
    engine.generate_pdf_report(pdf_path_en, lang="en", espp_discounts=espp_discounts,
                               espp_early_sale_discounts=espp_early_sales,
                               opening_losses=opening_losses)
    print(f"Generating Spanish PDF report (for Hacienda) at: {pdf_path_es}...")
    engine.generate_pdf_report(pdf_path_es, lang="es", espp_discounts=espp_discounts,
                               espp_early_sale_discounts=espp_early_sales,
                               opening_losses=opening_losses)
    print("PDF generation complete.")


if __name__ == "__main__":
    main()
