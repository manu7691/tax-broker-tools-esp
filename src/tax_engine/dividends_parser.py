import json
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

# Description keywords used to classify each cash-transaction row.
INTEREST_KEYWORDS = ("INTEREST",)
WITHHOLDING_KEYWORDS = ("WITHHOLD", "NRA", "FOREIGN TAX", "TAX WITHHELD", "TAX PAID")


def _json_num(d: Decimal) -> float | int:
    """Render a Decimal as a clean JSON number (int when integral)."""
    return int(d) if d == d.to_integral_value() else float(d)


def parse_usd_amount(val_str: str) -> Decimal:
    """Robustly parse a signed numeric string with currency symbols and custom separators.

    Handles ``$``/``€`` symbols, thousands/decimal separators, a leading ``-`` and
    accountant-style parentheses for negatives (``(0.04)`` -> ``-0.04``).
    """
    val_str = val_str.replace("$", "").replace("€", "").strip()
    if not val_str or val_str in ("--", "N/A"):
        return Decimal("0")

    negative = False
    if val_str.startswith("(") and val_str.endswith(")"):
        negative = True
        val_str = val_str[1:-1].strip()
    if val_str.startswith("-"):
        negative = True
        val_str = val_str[1:].strip()

    if "," in val_str and "." in val_str:
        val_str = val_str.replace(",", "")
    elif "," in val_str and "." not in val_str:
        val_str = val_str.replace(",", ".")
    try:
        amount = Decimal(val_str)
    except InvalidOperation:
        return Decimal("0")
    return -amount if negative else amount


def classify_row(description: str) -> str:
    """Classify a cash-transaction row as 'dividend', 'interest' or 'withholding'.

    Withholding rows (foreign tax withheld at source) are detected first so a line
    like "FOREIGN TAX WITHHELD ON DIVIDEND" is not mistaken for a dividend payment.
    """
    desc = description.upper()
    if any(kw in desc for kw in WITHHOLDING_KEYWORDS):
        return "withholding"
    if any(kw in desc for kw in INTEREST_KEYWORDS):
        return "interest"
    return "dividend"


def _entry_key(entry: dict[str, Any]) -> tuple[str, str, str, float, float]:
    """Dedup key: date, type, description and rounded amount + foreign tax.

    Including the description (security/payment identity) avoids treating two
    distinct payments of the same amount on the same date as duplicates.
    """
    try:
        amt = round(float(entry.get("amount_usd", 0)), 2)
    except (ValueError, TypeError):
        amt = 0.0
    try:
        ftax = round(float(entry.get("foreign_tax_usd", 0)), 2)
    except (ValueError, TypeError):
        ftax = 0.0
    return (
        str(entry.get("date", "")),
        str(entry.get("type", "dividend")),
        str(entry.get("description", "")).strip().upper(),
        amt,
        ftax,
    )


def _parse_date(raw_date: Any) -> date | None:
    """Parse an Excel cell into a date, accepting datetimes and common string formats."""
    if isinstance(raw_date, datetime):
        return raw_date.date()
    if isinstance(raw_date, date):
        return raw_date
    date_str = str(raw_date).strip().split(" ")[0]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def import_dividends(input_dir: Path = Path("input")) -> None:
    excel_path = input_dir / "dividends" / "Cash_Transactions.xlsx"
    json_path = input_dir / "savings_income.json"

    if not excel_path.exists():
        print(f"Dividends Excel file {excel_path} not found. Skipping import.")
        return

    print(f"Reading E*TRADE dividends from {excel_path}...")
    try:
        df = pd.read_excel(excel_path)
    except Exception as e:
        print(f"Error reading {excel_path}: {e}")
        return

    # Standard columns expected: Date, Description, Value $
    required_cols = {"Date", "Description", "Value $"}
    if not required_cols.issubset(df.columns):
        # Let's try case-insensitive mapping
        col_map = {col.lower(): col for col in df.columns}
        if (
            "date" in col_map
            and "description" in col_map
            and any(v in col_map for v in ["value $", "value", "amount"])
        ):
            df = df.rename(
                columns={
                    col_map["date"]: "Date",
                    col_map["description"]: "Description",
                    col_map.get("value $", col_map.get("value", col_map.get("amount"))): "Value $",
                }
            )
        else:
            print(
                f"Error: Excel sheet missing required columns {required_cols}. Columns found: {list(df.columns)}"
            )
            return

    # Load existing payments
    existing_payments: list[dict[str, Any]] = []
    if json_path.exists():
        try:
            with open(json_path, encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, list):
                    existing_payments = data
                else:
                    print(
                        f"Warning: {json_path} has an unexpected structure (not a list). Starting clean."
                    )
        except Exception as e:
            print(f"Warning: Failed to load existing {json_path}: {e}")

    existing_keys = {_entry_key(p) for p in existing_payments}

    new_count = 0
    duplicate_count = 0

    for i, (_, row) in enumerate(df.iterrows()):
        row_num = i + 2  # 1-based + header row
        raw_date = row.get("Date")
        raw_desc = str(row.get("Description", "")).strip()
        raw_value = str(row.get("Value $", "")).strip()

        if pd.isna(raw_date) or not raw_value:
            continue

        parsed_date = _parse_date(raw_date)
        if not parsed_date:
            print(f"Warning: Row {row_num} has an invalid date: {raw_date}")
            continue

        amount = parse_usd_amount(raw_value)
        kind = classify_row(raw_desc)
        date_iso = parsed_date.isoformat()

        if kind == "withholding":
            # Foreign tax withheld at source: recorded as its own entry carrying only
            # foreign_tax_usd (amount_usd = 0) so it feeds the per-year foreign-tax
            # total and the double-taxation analysis without inflating dividends.
            ftax = abs(amount)
            if ftax == 0:
                continue
            entry: dict[str, Any] = {
                "date": date_iso,
                "type": "dividend",
                "amount_usd": 0,
                "foreign_tax_usd": _json_num(ftax),
            }
        else:
            # Dividend/interest payment. Keep negatives (reversals) so they net out
            # per year downstream; only skip true zero-value rows.
            if amount == 0:
                continue
            entry = {
                "date": date_iso,
                "type": kind,
                "amount_usd": _json_num(amount),
            }

        if raw_desc:
            entry["description"] = raw_desc

        entry_key = _entry_key(entry)
        if entry_key in existing_keys:
            duplicate_count += 1
            continue

        existing_payments.append(entry)
        existing_keys.add(entry_key)
        new_count += 1

    if new_count > 0:
        # Sort payments by date
        existing_payments.sort(key=lambda p: str(p.get("date", "")))

        # Save back to JSON
        try:
            os.makedirs(json_path.parent, exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump(existing_payments, fh, indent=2)
                fh.write("\n")
            print(
                f"✓ Imported {new_count} new dividend/interest entries. Skipped {duplicate_count} duplicates."
            )
            print(f"Saved total of {len(existing_payments)} entries to {json_path}")
        except Exception as e:
            print(f"Error saving updated payments to {json_path}: {e}")
    else:
        print(f"No new dividends to import. (Skipped {duplicate_count} duplicates)")


if __name__ == "__main__":
    import_dividends()
