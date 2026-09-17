"""
Spanish Tax Engine for E-Trade RSUs, ESPP, and Stock Options.

Calculates capital gains tax using the Spanish FIFO cost basis method
for stocks acquired through RSU vesting, ESPP purchases, and stock options exercises.

Main entry point for the application.
"""

import argparse
import contextlib
import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from tax_engine import (
    ECBRateFetcher,
    EsppEarlySaleReport,
    EventType,
    LotOrigin,
    ProcessedEvent,
    SavingsIncomeYear,
    StockEvent,
    TaxEngine,
    load_rsu_events,
    prefetch_ecb_rates,
)
from tax_engine.dates import add_months
from tax_engine.options_parser import load_options_events
from tax_engine.portfolio import AmbiguousSecurityError, SecurityResult, run_portfolio
from tax_engine.revolut_parser import load_revolut_events, merge_savings_income
from tax_engine.securities import SecuritiesConfig, load_securities_config


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


def _aggregate_usd_payments(payments: list[dict[str, Any]]) -> dict[int, SavingsIncomeYear]:
    """
    Convert a list of USD dividend/interest payments to per-year EUR totals.

    Each payment is converted at the ECB USD->EUR rate on its own payment date,
    exactly like stock transactions, then summed per year::

        [{"date": "2024-03-15", "type": "dividend", "amount_usd": 80,
          "foreign_tax_usd": 12}, ...]
    """
    from tax_engine.ecb_rates import ECBRateFetcher

    result: dict[int, SavingsIncomeYear] = {}
    for i, p in enumerate(payments):
        try:
            pay_date = date.fromisoformat(str(p["date"]).strip())
            rate = ECBRateFetcher.get_rate(pay_date)
            amount_eur = (Decimal(str(p.get("amount_usd", 0))) * rate).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )
            ftax_eur = (Decimal(str(p.get("foreign_tax_usd", 0))) * rate).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )
        except (ValueError, KeyError, InvalidOperation) as e:
            print(f"Warning: skipping invalid payment #{i + 1} ({p!r}): {e}")
            continue
        entry = result.setdefault(pay_date.year, SavingsIncomeYear(year=pay_date.year))
        if str(p.get("type", "dividend")).strip().lower().startswith("int"):
            entry.interest_eur += amount_eur
        else:
            entry.dividends_eur += amount_eur
        entry.foreign_tax_eur += ftax_eur
    return result


def load_savings_income(path: Path) -> dict[int, SavingsIncomeYear]:
    """
    Load dividend/interest (RCM) income, returning per-year EUR totals.

    Two input shapes are accepted:

    * **USD payments (exact)** — a JSON *list*, each converted at the ECB rate on
      its payment date (recommended; consistent with stock transactions)::

          [{"date": "2024-03-15", "type": "dividend", "amount_usd": 80,
            "foreign_tax_usd": 12}, ...]

    * **EUR per year (manual)** — a JSON *object* keyed by year, where you have
      already converted to EUR::

          {"2024": {"dividends_eur": 320, "interest_eur": 15, "foreign_tax_eur": 48}}

    Returns an empty dict if the file is absent or unparseable.
    """
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: could not read savings-income file {path}: {e}")
        return {}

    if isinstance(raw, list):
        result = _aggregate_usd_payments(raw)
        if result:
            print(
                f"Loaded {len(raw)} dividend/interest payment(s) "
                f"(USD, ECB-converted per date) across {len(result)} year(s) from {path}."
            )
        return result

    result = {}
    for year_str, vals in raw.items():
        try:
            year = int(year_str)
            entry = vals if isinstance(vals, dict) else {}
            result[year] = SavingsIncomeYear(
                year=year,
                dividends_eur=Decimal(str(entry.get("dividends_eur", 0))),
                interest_eur=Decimal(str(entry.get("interest_eur", 0))),
                foreign_tax_eur=Decimal(str(entry.get("foreign_tax_eur", 0))),
            )
        except (ValueError, InvalidOperation):
            print(f"Warning: skipping invalid savings-income entry {year_str!r}: {vals!r}")
    if result:
        print(f"Loaded dividend/interest income for {len(result)} year(s) (EUR) from {path}.")
    return result


def load_security_config(input_dir: Path) -> tuple[str | None, str | None]:
    """
    Load the tracked security's ticker and ISIN from ``input/ticker.json``.

    Used to filter the optional Revolut export down to the same security the
    E-Trade data tracks. Accepts either an object (``{"ticker": "DT",
    "isin": "US..."}``) or a bare ticker string. Returns ``(symbol, isin)``,
    either of which may be ``None``.
    """
    json_path = input_dir / "ticker.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None, None
        if isinstance(data, dict):
            symbol = (data.get("ticker") or "").strip().upper() or None
            isin = (data.get("isin") or "").strip().upper() or None
            return symbol, isin
        if isinstance(data, str):
            return data.strip().upper() or None, None
    txt_path = input_dir / "ticker.txt"
    if txt_path.exists():
        try:
            return txt_path.read_text(encoding="utf-8").strip().upper() or None, None
        except OSError:
            pass
    return None, None


def load_closed_years(path: Path) -> dict[int, dict[str, Decimal]]:
    """Read the tax years already filed, as declared by the taxpayer.

    Rule 3 forbids rewriting a closed year, so the engine needs to be told which
    years are closed and what was reported — it can never infer that from the
    transaction data. The file maps a year to the figures as filed::

        {"2022": {"net_gain_loss": "-5000.00"}, "2021": "120.00"}

    A bare number is shorthand for ``{"net_gain_loss": ...}``. Keys beginning with
    ``_`` are treated as comments and skipped. A missing file
    simply declares nothing; a malformed one is reported rather than ignored,
    because silently skipping it would defeat the whole check.
    """
    if not path.exists():
        return {}

    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    declared: dict[int, dict[str, Decimal]] = {}
    for year, value in raw.items():
        # The file is hand-maintained and records WHY each year reads as it does —
        # a year that omitted its losses looks nothing like one that deducted them,
        # and only the taxpayer knows which happened. Keys starting with "_" are
        # notes; anything else must be a real tax year, so a typo still fails loudly.
        if year.startswith("_"):
            continue
        fields = value if isinstance(value, dict) else {"net_gain_loss": value}
        declared[int(year)] = {name: Decimal(str(amount)) for name, amount in fields.items()}
    return declared


#: Holding period that keeps the ESPP purchase discount exempt (Art. 42.3.f LIRPF).
ESPP_HOLDING_MONTHS = 36


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

            # Price actually paid, kept alongside the FMV so the Art. 42.3.f
            # discount travels with the lot instead of being looked up by date.
            paid_usd: Decimal | None = None
            paid_raw = row.get("Purchase Price")
            if paid_raw is not None and not pd.isna(paid_raw):
                paid_str = str(paid_raw).replace("$", "").replace(",", "").strip()
                if paid_str not in ("", "--", "N/A"):
                    paid_usd = Decimal(paid_str)
            if paid_usd is None:
                print(
                    f"Warning: ESPP row {row_num} has no 'Purchase Price'; the "
                    f"Art. 42.3.f discount cannot be valued for this lot."
                )

        except (ValueError, InvalidOperation) as e:
            print(f"Error parsing ESPP row {row_num}: {e}")
            continue

        event = StockEvent(
            event_date=event_date,
            event_type=EventType.BUY,
            shares=shares,
            price_usd=price_usd,
            notes="ESPP Purchase",
            origin=LotOrigin.ESPP,
            espp_fmv_usd=price_usd,
            espp_price_usd=paid_usd,
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
        price_str = str(row["Execution Price"]).replace("$", "").replace(",", "").strip()
        try:
            price = Decimal(price_str)
        except InvalidOperation:
            print(f"Error parsing price in row #{printable_index}: {price_str}")
            continue
        if price <= 0:
            print(f"Skipping row #{printable_index}: zero or negative price ({price_str})")
            continue

        # Sum up any fees from columns if they exist (excluding Wire/Disbursement Fees per user request)
        fees_usd = Decimal("0")
        for fee_col in ["Commission", "SEC Fees", "Brokerage Assist Fee", "Fees"]:
            if fee_col in df.columns:
                val = str(row[fee_col]).replace("$", "").replace(",", "").strip()
                if val and val.lower() not in ("nan", "none", "0", "0.00", ""):
                    with contextlib.suppress(Exception):
                        fees_usd += Decimal(val)

        notes = f"Sell Order ({row.get('Benefit Type', 'Unknown')})"
        if fees_usd > 0:
            notes += f" (Includes ${fees_usd} fees)"

        # Per-row settlement status (newer downloads only). Empty for older
        # orders.xlsx files that predate the Status column.
        # Security identity. E*TRADE exports carry no ISIN, but the ticker is
        # enough for the portfolio runner to merge this sale with the same
        # security reported by another broker (Rule 1).
        symbol = ""
        if "Symbol" in df.columns:
            raw_symbol = row.get("Symbol")
            if raw_symbol is not None and not pd.isna(raw_symbol):
                symbol = str(raw_symbol).strip().upper()

        order_status = ""
        if "Status" in df.columns:
            raw_status = row.get("Status")
            if raw_status is not None and not pd.isna(raw_status):
                order_status = str(raw_status).strip()

        events.append(
            StockEvent(
                event_date=event_date,
                event_type=EventType.SELL,
                shares=qty,
                price_usd=price,
                fees_usd=fees_usd,
                notes=notes,
                order_status=order_status,
                symbol=symbol,
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

    for _i, row in df.iterrows():
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


def build_espp_purchase_map(input_dir: Path = Path("input")) -> dict[date, tuple[Decimal, Decimal]]:
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

    purchase_map: dict[date, tuple[Decimal, Decimal]] = {}

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
    processed_events: list[ProcessedEvent],
) -> EsppEarlySaleReport:
    """
    Detect ESPP shares sold before the 36-month holding period (Art. 42.3.f LIRPF).

    Provenance comes from the lot's typed ``origin``, never from free-text notes,
    and the discount comes from the FMV/price stored on the lot itself — so two
    ESPP purchases on the same day are valued separately and a market purchase
    that merely mentions "ESPP" in its notes is never mistaken for one.

    The 36 months are counted date to date (``add_months``), so a lot bought on
    29-Feb-2020 is only clear from 28-Feb-2023.

    Breaching the exemption makes the discount *rendimiento del trabajo* of the
    PURCHASE year — that is what makes it an autoliquidación complementaria — and
    it never touches the savings base.

    An ESPP disposal whose discount cannot be valued produces a warning rather
    than being skipped: a missing input must not look like "no breach".
    """
    taxable_by_year: dict[int, Decimal] = {}
    details: list[dict[str, Any]] = []
    warnings: list[str] = []

    for pe in processed_events:
        if pe.event.event_type != EventType.SELL:
            continue

        sell_date = pe.event.event_date

        for match in pe.fifo_matches:
            if match.origin is not LotOrigin.ESPP:
                continue

            acq_date = match.acquisition_date
            if sell_date >= add_months(acq_date, ESPP_HOLDING_MONTHS):
                continue  # exemption secured

            if match.espp_fmv_usd is None or match.espp_price_usd is None:
                warnings.append(
                    f"ESPP lot acquired {acq_date.isoformat()} was sold on "
                    f"{sell_date.isoformat()} before {ESPP_HOLDING_MONTHS} months, but its "
                    f"purchase discount (FMV / price paid) is missing from the input, so the "
                    f"Art. 42.3.f adjustment could not be quantified."
                )
                continue

            discount_per_share_usd = match.espp_fmv_usd - match.espp_price_usd
            # Convert with the lot's own acquisition rate; the taxable salary
            # accrued at purchase, not at sale.
            fx_rate = match.acquisition_fx_rate
            if fx_rate is None:
                fx_rate = ECBRateFetcher.get_rate(acq_date)
            discount_eur = (discount_per_share_usd * match.shares * fx_rate).quantize(
                Decimal("0.01")
            )

            purchase_year = acq_date.year
            taxable_by_year[purchase_year] = (
                taxable_by_year.get(purchase_year, Decimal("0")) + discount_eur
            )

            details.append(
                {
                    "acquisition_date": acq_date,
                    "sell_date": sell_date,
                    "shares": match.shares,
                    "holding_days": (sell_date - acq_date).days,
                    "discount_per_share_usd": discount_per_share_usd,
                    "discount_eur": discount_eur,
                }
            )

    return EsppEarlySaleReport(taxable_by_year=taxable_by_year, details=details, warnings=warnings)


# How far after a vest its withholding sale may appear. Must absorb T+2
# settlement plus a weekend, and vests dated on a non-trading day push the sale
# later still; seven days covers the observed cases without loosening the exact
# quantity match that actually identifies the sale.
_COVER_SALE_MATCH_DAYS = 7

# E-Trade order statuses that mean the trade has fully settled and its RSU
# confirmation PDF is therefore available. Anything else ("Executed", "Open", ...)
# is still in flight, so a matching VEST event may not exist yet.
_SETTLED_STATUSES = {"settled", "complete", "completed"}

# Fallback window (calendar days) used only when the order has no Status value
# — older orders.xlsx files downloaded before the Status column existed. US
# equities settle T+1, but RSU confirmation PDFs can lag a few business days.
_PENDING_FALLBACK_DAYS = 7


def _is_settled(sell: StockEvent, today: date) -> bool:
    """Whether a sell's RSU confirmation should already be available.

    Prefers the real per-row E-Trade status (deterministic, captured at download
    time). Falls back to execution-date recency only when the status is unknown.
    """
    status = sell.order_status.strip().lower()
    if status:
        return status in _SETTLED_STATUSES
    # No status (legacy download): assume settled once the window has elapsed.
    return (today - sell.event_date).days > _PENDING_FALLBACK_DAYS


def auto_detect_sell_to_cover(events: list[StockEvent], today: date | None = None) -> None:
    """
    Detects which SELL events are actually 'Sell-to-Cover' for RSU taxes.

    Classification is three-way:
      * Sell-to-Cover (Auto-detected): the sold quantity matches a VEST's
        shares_sold_to_cover within a week — confirmed against the RSU PDF. The window
        absorbs T+2 settlement plus a weekend; the exact quantity match is what
        actually identifies the sale (see _COVER_SALE_MATCH_DAYS).
      * Pending Settlement: unmatched, but the order has not settled yet, so its
        RSU confirmation PDF may simply not exist yet. We do NOT assert manual
        vs sell-to-cover; re-running after settlement resolves it.
      * Manual Sell: unmatched AND settled, so the data is complete and the sale
        was genuinely user-initiated.

    ``today`` is injectable for testing; defaults to the current date.
    """
    if today is None:
        today = date.today()

    vests = [e for e in events if e.event_type == EventType.VEST]
    sells = [e for e in events if e.event_type == EventType.SELL and "Sell Order" in e.notes]

    for sell in sells:
        is_sell_to_cover = False

        # Check against RSU vests
        for vest in vests:
            days_diff = abs((sell.event_date - vest.event_date).days)
            # The cover sale settles a few days after the vest: T+2 plus a weekend
            # reaches four calendar days, and a vest dated on a Saturday pushes it
            # further still. The quantity has to equal the withheld shares from the
            # RSU confirmation exactly, which is what keeps the wider window safe.
            if (
                days_diff <= _COVER_SALE_MATCH_DAYS
                and sell.shares == vest.shares_sold_to_cover
                and vest.shares_sold_to_cover > 0
            ):
                is_sell_to_cover = True
                break

        if is_sell_to_cover:
            sell.notes = sell.notes.replace("Sell Order", "Sell-to-Cover (Auto-detected)")
        elif not _is_settled(sell, today):
            # Executed but not yet settled — the RSU confirmation PDF that would
            # confirm a sell-to-cover may not exist yet. Stay neutral.
            sell.notes = sell.notes.replace("Sell Order", "Pending Settlement")
        else:
            sell.notes = sell.notes.replace("Sell Order", "Manual Sell")


def build_portfolio_or_engine(
    etrade_events: list[StockEvent],
    revolut_events: list[StockEvent],
    *,
    all_securities: bool,
    securities_config: SecuritiesConfig | None = None,
    primary_symbol: str | None = None,
    primary_isin: str | None = None,
    release_policy: str = "definitive",
) -> tuple[TaxEngine, list[SecurityResult] | None, list[StockEvent]]:
    """Assemble events and run either the single-security engine or the portfolio.

    In ``all_securities`` mode the E*TRADE (employer) events are tagged with the
    primary identity so they merge with the same ISIN/ticker from other brokers,
    every security gets its own ISIN-keyed FIFO queue, and the returned engine is
    the portfolio *aggregate* (driving the combined savings base + reporting) with
    the per-security results alongside. Otherwise a single engine processes the
    merged event stream, exactly as before.

    Returns ``(engine, report_securities_or_None, all_events)``.
    """
    if all_securities and (primary_symbol or primary_isin):
        for e in etrade_events:
            e.symbol = primary_symbol or e.symbol
            if primary_isin:
                e.isin = primary_isin or e.isin

    events = etrade_events + revolut_events
    auto_detect_sell_to_cover(events)
    prefetch_ecb_rates(events)

    if all_securities:
        portfolio = run_portfolio(events, config=securities_config, release_policy=release_policy)
        return portfolio.aggregate, portfolio.results, events

    # Single-security mode pools every event into ONE FIFO queue. That is only
    # sound while the events really are one homogeneous security: pooling two
    # tickers would match sales of one against purchases of the other. Portfolio
    # mode is the supported way to handle several securities.
    tickers = sorted({(e.symbol or "").strip().upper() for e in events} - {""})
    if len(tickers) > 1:
        raise AmbiguousSecurityError(
            f"Single-security mode received {len(tickers)} tickers ({', '.join(tickers)}), "
            f"which would pool unrelated securities into one FIFO queue. "
            f"Re-run with --all-securities (or add input/securities.json)."
        )

    engine = TaxEngine(release_policy=release_policy)
    engine.process_all(events)
    return engine, None, events


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
        "--wash-sale-release",
        choices=list(TaxEngine.RELEASE_POLICIES),
        default="definitive",
        help="When an Art. 33.5.f deferred loss becomes deductible. 'definitive' "
        "(default) is the statutory rule: freed as the remaining securities are "
        "transmitted, but only for transmissions with no homogeneous repurchase in "
        "the following 2 months. 'position_zero' additionally demands a full "
        "liquidation (more conservative); 'per_lot' frees on any disposal, with no "
        "definitiveness test (more aggressive).",
    )
    parser.add_argument(
        "--forfeit-declared-releases",
        action="store_true",
        help="Give up future integration of deferrals that a closed year already "
        "deducted when it was filed, instead of regularising that year. No direct "
        "statutory backing: Art. 122.2 LGT settles such an error by amending the "
        "affected year. Use only as a deliberate decision taken with an advisor; "
        "without this flag the conflict is reported but no figure is changed.",
    )
    parser.add_argument(
        "--closed-years",
        type=Path,
        default=None,
        help="JSON file of already-filed years and their declared figures "
        '({"2022": {"net_gain_loss": "-5000.00"}}). Defaults to '
        "<input-dir>/closed_years.json if present. Divergences are reported, never "
        "applied silently.",
    )
    parser.add_argument(
        "--prior-losses",
        type=Path,
        default=None,
        help="JSON file of pending losses from before the data window "
        '({"2019": 1500, ...}). Defaults to <input-dir>/prior_losses.json if present.',
    )
    parser.add_argument(
        "--savings-income",
        type=Path,
        default=None,
        help="JSON file of dividend/interest income per year in EUR "
        '({"2024": {"dividends_eur": 320, ...}}). Defaults to '
        "<input-dir>/savings_income.json if present.",
    )
    parser.add_argument(
        "--revolut-isin",
        default=None,
        help="ISIN of the tracked security, used to filter the optional Revolut "
        "CSV (input/revolut/*.csv) to homogeneous shares. Overrides input/ticker.json.",
    )
    parser.add_argument(
        "--revolut-symbol",
        default=None,
        help="Ticker to filter the optional Revolut CSV when no ISIN is available "
        "(ISIN is preferred). Overrides input/ticker.json.",
    )
    parser.add_argument(
        "--all-securities",
        action="store_true",
        help="Portfolio mode: process every security across all platforms, each "
        "with its own FIFO queue (grouped by ISIN), and roll them up into one "
        "savings base. Auto-enabled when input/securities.json is present. Without "
        "it, only the primary security (input/ticker.json) is processed, as before.",
    )
    args = parser.parse_args()
    input_dir: Path = args.input_dir
    output_dir: Path = args.output_dir
    prior_losses_path: Path = args.prior_losses or (input_dir / "prior_losses.json")
    opening_losses = load_prior_losses(prior_losses_path)
    savings_income_path: Path = args.savings_income or (input_dir / "savings_income.json")
    savings_income = load_savings_income(savings_income_path)

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

    # Portfolio mode: process every security (each with its own ISIN-keyed FIFO
    # queue) and roll them up. Auto-enabled when input/securities.json exists, or
    # via --all-securities. Without it, behaviour = today (primary security only).
    config_symbol, config_isin = load_security_config(input_dir)
    securities_config = load_securities_config(input_dir)
    all_securities = args.all_securities or (input_dir / "securities.json").exists()

    # Optional Revolut export. In single-security mode it is filtered to the
    # tracked security (matched on ISIN) and folded into the same FIFO pool; in
    # portfolio mode it emits every ticker from the gains export (each ISIN-tagged).
    revolut_isin = (args.revolut_isin or config_isin) or None
    revolut_symbol = (args.revolut_symbol or config_symbol) or None
    revolut_events, revolut_income = load_revolut_events(
        input_dir,
        isin=revolut_isin,
        symbol=revolut_symbol,
        all_securities=all_securities,
        isin_map=securities_config.isin_map,
    )
    if revolut_income:
        savings_income = merge_savings_income(savings_income, revolut_income)

    # Assemble events and run the engine (single-security) or the portfolio
    # (all securities, each with its own ISIN-keyed FIFO queue rolled up into one
    # savings base). The returned engine drives the prints + report below.
    etrade_events = espp_events + sell_events + rsu_events + options_events
    engine, report_securities, events = build_portfolio_or_engine(
        etrade_events,
        revolut_events,
        all_securities=all_securities,
        securities_config=securities_config,
        primary_symbol=config_symbol,
        primary_isin=config_isin,
        release_policy=args.wash_sale_release,
    )
    if report_securities is not None:
        print(
            f"Portfolio mode: {len(report_securities)} security(ies) processed — "
            f"{', '.join(r.security.label for r in report_securities)}."
        )

    # Rule 3: years already filed must never be rewritten in silence. A repurchase
    # made after filing can legitimately change a past year under Art. 33.5.f, so
    # the divergence is surfaced and left for the taxpayer to act on.
    closed_years_path: Path = args.closed_years or (input_dir / "closed_years.json")
    closed_years = load_closed_years(closed_years_path)
    # Pin the filed years for every downstream view: the carry-forward pool must
    # keep the loss the taxpayer actually declared until they amend it, even when
    # a later repurchase changes what the engine now computes.
    engine.closed_years = closed_years
    # A year filed with the loss already deducted would otherwise release it again.
    # Detected always; only acted upon when the user explicitly asks, because the
    # remedy the law provides is to regularise that year (Art. 122.2 LGT), not to
    # net the error against a later one.
    already_deducted = engine.releases_already_deducted()
    if already_deducted and args.forfeit_declared_releases:
        forfeited = engine.apply_closed_year_forfeits()
        print()
        print("ℹ️  RELEASES FORFEITED (--forfeit-declared-releases)")
        print("-" * 95)
        for origin, amount in sorted(forfeited.items()):
            print(f"    origin {origin}: €{amount:,.2f} of deferred loss will NOT be integrated.")
        print("    Note: this leaves the affected return uncorrected. The remedy the law")
        print("    provides is a complementaria for that year — see --help.")
        print()
    elif already_deducted:
        print()
        print("⚠️  RISK OF DEDUCTING THE SAME LOSS TWICE")
        print("-" * 95)
        print("These years were filed deducting a loss that the current criterion blocks.")
        print("Their deferral is scheduled to be integrated again in a later year:")
        for origin, amount in sorted(already_deducted.items()):
            print(f"    {origin}: €{amount:,.2f} already deducted when filed")
        print()
        print("Art. 122.2 LGT settles this by regularising the affected year (complementaria),")
        print("after which the later integration is legitimate. If you decide with your advisor")
        print("NOT to regularise, re-run with --forfeit-declared-releases to give up the")
        print("corresponding future deduction instead.")
        print()
    drifts = engine.check_closed_years(closed_years)
    if drifts:
        print()
        print("⚠️  CLOSED TAX YEAR DIVERGENCE")
        print("-" * 95)
        print("These years were already filed, but recomputing them with the current data")
        print("gives a different result. Nothing was changed automatically — review whether")
        print("a declaración complementaria or a rectificativa is needed:")
        for drift in drifts:
            print(
                f"    {drift.year}  {drift.field}: declared €{drift.declared:,.2f} "
                f"-> now €{drift.computed:,.2f}  (delta €{drift.delta:,.2f})"
            )
        print()

    # Print results
    engine.print_ledger()
    engine.print_tax_summary(opening_losses=opening_losses, savings_income=savings_income)

    # ESPP Analysis: 3-year holding period detection
    espp_discounts = calculate_espp_discounts(input_dir)
    espp_report = detect_espp_early_sales(engine.processed_events)
    espp_early_sales = espp_report.taxable_by_year
    espp_early_details = espp_report.details
    for warning in espp_report.warnings:
        print(f"⚠️  {warning}")

    if espp_early_sales:
        print("⚠️  ESPP EARLY SALE ALERT (Art. 42.3.f LIRPF)")
        print("-" * 95)
        print("The following ESPP discounts are TAXABLE as salary income")
        print("because shares were sold BEFORE the 3-year holding period:")
        print(
            "Required Action: You must file a 'Declaración Complementaria' (Complementary Return)"
        )
        print("for the purchase year to include this income as taxable salary, as the original")
        print("exemption is now void.")
        print()
        for year, amount in sorted(espp_early_sales.items()):
            print(f"  Purchase Year {year} (Complementary Return required):")
            print(f"    Total Taxable ESPP Discount (Rendimiento del Trabajo): €{amount:>12,.2f}")
            print(
                f"    (Because shares purchased in {year} were sold in the following transactions before 3 years)"
            )
            print()

            # Print details for this year
            for detail in espp_early_details:
                if detail["acquisition_date"].year != year:
                    continue

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

    # Show current state. In portfolio mode the aggregate mixes securities, so a
    # single "avg cost / shares" line is meaningless — print one row per security.
    if report_securities is not None:
        print("\nCurrent Positions (per security):")
        for r in report_securities:
            st = r.engine.state
            if st.total_shares > 0:
                print(
                    f"  {r.security.label}: {st.total_shares:,.4f} shares, "
                    f"avg cost €{st.avg_cost_eur:,.4f}, cost basis €{st.total_portfolio_cost_eur:,.2f}"
                )
        total_cost = sum(
            (r.engine.state.total_portfolio_cost_eur for r in report_securities), Decimal("0")
        )
        print(f"Total cost basis across securities: €{total_cost:,.2f}")
    else:
        print(f"\nCurrent Position: {engine.state.total_shares} shares")
        print(f"Current Informational Avg Cost: €{engine.state.avg_cost_eur:,.4f}")
        print(f"Total Portfolio Cost: €{engine.state.total_portfolio_cost_eur:,.4f}")

    # Generate PDF reports (English and Spanish for Hacienda)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path_en = str(output_dir / f"tax_report_EN_{timestamp}.pdf")
    pdf_path_es = str(output_dir / f"tax_report_ES_{timestamp}.pdf")
    print(f"Generating English PDF report at: {pdf_path_en}...")
    engine.generate_pdf_report(
        pdf_path_en,
        lang="en",
        espp_discounts=espp_discounts,
        espp_early_sale_discounts=espp_early_sales,
        opening_losses=opening_losses,
        savings_income=savings_income,
        securities=report_securities,
    )
    print(f"Generating Spanish PDF report (for Hacienda) at: {pdf_path_es}...")
    engine.generate_pdf_report(
        pdf_path_es,
        lang="es",
        espp_discounts=espp_discounts,
        espp_early_sale_discounts=espp_early_sales,
        opening_losses=opening_losses,
        savings_income=savings_income,
        securities=report_securities,
    )
    print("PDF generation complete.")


if __name__ == "__main__":
    main()
