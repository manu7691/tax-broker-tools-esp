import json
from decimal import Decimal
from pathlib import Path

import pandas as pd

from tax_engine.dividends_parser import (
    classify_row,
    import_dividends,
    parse_usd_amount,
)


def _write_excel(input_dir: Path, rows: dict) -> None:
    """Write a Cash_Transactions.xlsx the way E*TRADE exports it."""
    excel_path = input_dir / "dividends" / "Cash_Transactions.xlsx"
    excel_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(excel_path, index=False)


def test_parse_usd_amount():
    assert parse_usd_amount("$10.50") == Decimal("10.50")
    assert parse_usd_amount("€1,250.00") == Decimal("1250.00")
    assert parse_usd_amount("0,04") == Decimal("0.04")
    assert parse_usd_amount("--") == Decimal("0")
    assert parse_usd_amount("") == Decimal("0")
    # Signed values: leading minus and accountant parentheses.
    assert parse_usd_amount("-0.04") == Decimal("-0.04")
    assert parse_usd_amount("($0.04)") == Decimal("-0.04")


def test_classify_row():
    assert classify_row("TREASURY LIQUIDITY FUND DIV PAYMENT") == "dividend"
    assert classify_row("Qualified Dividend") == "dividend"
    assert classify_row("CREDIT INTEREST") == "interest"
    assert classify_row("FOREIGN TAX WITHHELD ON DIVIDEND") == "withholding"
    assert classify_row("NRA WITHHOLDING") == "withholding"


def test_import_dividends_merge_and_deduplicate(tmp_path):
    json_path = tmp_path / "savings_income.json"

    # 1. Start with an existing savings_income.json
    existing = [
        {
            "date": "2023-02-10",
            "type": "dividend",
            "amount_usd": 0.04,
            "description": "TREASURY LIQUIDITY FUND DIV PAYMENT",
        },
        {
            "date": "2024-01-02",
            "type": "dividend",
            "amount_usd": 0.05,
            "description": "TREASURY LIQUIDITY FUND",
        },
    ]
    json_path.write_text(json.dumps(existing, indent=2))

    # Row 1 duplicates the 2024-01-02 entry; row 2 is new.
    _write_excel(
        tmp_path,
        {
            "Date": ["01/02/2024", "02/01/2024"],
            "Description": ["TREASURY LIQUIDITY FUND", "TREASURY LIQUIDITY FUND DIV PAYMENT"],
            "Value $": ["$0.05", "$0.04"],
        },
    )

    import_dividends(input_dir=tmp_path)

    updated = json.loads(json_path.read_text())

    # 2 existing + 1 new, duplicate skipped.
    assert len(updated) == 3
    dates = [p["date"] for p in updated]
    assert dates == ["2023-02-10", "2024-01-02", "2024-02-01"]

    new_entry = [p for p in updated if p["date"] == "2024-02-01"][0]
    assert new_entry["amount_usd"] == 0.04
    assert new_entry["description"] == "TREASURY LIQUIDITY FUND DIV PAYMENT"


def test_import_classifies_interest(tmp_path):
    json_path = tmp_path / "savings_income.json"
    _write_excel(
        tmp_path,
        {
            "Date": ["03/15/2024", "03/31/2024"],
            "Description": ["ACME CORP DIV PAYMENT", "CREDIT INTEREST"],
            "Value $": ["$80.00", "$1.25"],
        },
    )

    import_dividends(input_dir=tmp_path)
    entries = {p["type"]: p for p in json.loads(json_path.read_text())}

    assert entries["dividend"]["amount_usd"] == 80
    assert entries["interest"]["amount_usd"] == 1.25


def test_import_captures_foreign_tax_withholding(tmp_path):
    json_path = tmp_path / "savings_income.json"
    _write_excel(
        tmp_path,
        {
            "Date": ["03/15/2024", "03/15/2024"],
            "Description": ["ACME CORP DIV PAYMENT", "FOREIGN TAX WITHHELD"],
            # Withholding often arrives as a negative/parenthesised value.
            "Value $": ["$80.00", "($12.00)"],
        },
    )

    import_dividends(input_dir=tmp_path)
    payments = json.loads(json_path.read_text())

    div = [p for p in payments if p["amount_usd"] == 80][0]
    assert div["type"] == "dividend"
    assert "foreign_tax_usd" not in div

    wh = [p for p in payments if p.get("foreign_tax_usd")][0]
    assert wh["foreign_tax_usd"] == 12
    assert wh["amount_usd"] == 0


def test_same_day_same_amount_distinct_securities_not_deduped(tmp_path):
    json_path = tmp_path / "savings_income.json"
    _write_excel(
        tmp_path,
        {
            "Date": ["03/15/2024", "03/15/2024"],
            "Description": ["ACME CORP DIV PAYMENT", "GLOBEX CORP DIV PAYMENT"],
            "Value $": ["$10.00", "$10.00"],
        },
    )

    import_dividends(input_dir=tmp_path)
    payments = json.loads(json_path.read_text())

    # Different descriptions -> both kept despite same date and amount.
    assert len(payments) == 2
    assert {p["description"] for p in payments} == {
        "ACME CORP DIV PAYMENT",
        "GLOBEX CORP DIV PAYMENT",
    }


def test_idempotent_reimport(tmp_path):
    json_path = tmp_path / "savings_income.json"
    _write_excel(
        tmp_path,
        {
            "Date": ["03/15/2024"],
            "Description": ["ACME CORP DIV PAYMENT"],
            "Value $": ["$80.00"],
        },
    )

    import_dividends(input_dir=tmp_path)
    import_dividends(input_dir=tmp_path)  # second run must add nothing

    assert len(json.loads(json_path.read_text())) == 1
