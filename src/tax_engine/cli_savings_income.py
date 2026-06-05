"""
Interactive helper to add/update dividend & interest (RCM) income.

Writes/merges ``input/savings_income.json`` — the file the tax report reads to
compute the dividend/interest savings base and the 25% cross-category offset —
so you never have to hand-edit JSON.

Interactive::

    .venv/bin/python -m tax_engine.cli_savings_income

One-shot (non-interactive)::

    .venv/bin/python -m tax_engine.cli_savings_income \
        --year 2024 --dividends 320 --interest 15 --foreign-tax 48

Existing years are preserved; only the year you enter is added or updated.
"""

import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDS = ("dividends_eur", "interest_eur", "foreign_tax_eur")


def _json_num(d: Decimal) -> float | int:
    """Render a Decimal as a clean JSON number (int when integral)."""
    return int(d) if d == d.to_integral_value() else float(d)


def load_raw(path: Path) -> dict:
    """Load the savings-income JSON as a plain dict, or {} if absent/unreadable."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        print(f"Warning: {path} is unreadable; starting fresh.")
        return {}


def save_raw(path: Path, data: dict) -> None:
    """Write the savings-income dict to disk, sorted by year."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {y: data[y] for y in sorted(data)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, indent=2)
        fh.write("\n")


def upsert(
    data: dict,
    year: int,
    dividends: Decimal,
    interest: Decimal,
    foreign_tax: Decimal,
) -> dict:
    """Add or replace one year's entry, leaving every other year untouched."""
    data = dict(data)
    data[str(year)] = {
        "dividends_eur": _json_num(dividends),
        "interest_eur": _json_num(interest),
        "foreign_tax_eur": _json_num(foreign_tax),
    }
    return data


def _to_decimal(value: str | None, default) -> Decimal:
    """Parse a string to Decimal; blank/None falls back to ``default``."""
    if value is None or str(value).strip() == "":
        return Decimal(str(default))
    return Decimal(str(value).strip().replace(",", "."))


def _prompt_decimal(label: str, default) -> Decimal:
    """Prompt until a valid number is entered; blank keeps the default."""
    while True:
        raw = input(f"  {label} [{default}]: ").strip()
        try:
            return _to_decimal(raw, default)
        except InvalidOperation:
            print("    Please enter a number (e.g. 320.50), or leave blank.")


def _interactive(data: dict, path: Path) -> None:
    print("Enter dividend/interest income per year (EUR). Leave a field blank to keep it.\n")
    while True:
        try:
            year = int(input("Year (e.g. 2024): ").strip())
        except ValueError:
            print("  Please enter a 4-digit year.")
            continue

        existing = data.get(str(year), {})
        div = _prompt_decimal("Dividends received (EUR)", existing.get("dividends_eur", 0))
        intr = _prompt_decimal("Interest received (EUR)", existing.get("interest_eur", 0))
        ftax = _prompt_decimal("Foreign tax withheld (EUR)", existing.get("foreign_tax_eur", 0))

        data = upsert(data, year, div, intr, ftax)
        save_raw(path, data)
        print(f"  ✓ Saved {year} to {path}\n")

        if input("Add another year? [y/N]: ").strip().lower() not in ("y", "yes"):
            break

    print(f"\nFinal {path}:\n{json.dumps({y: data[y] for y in sorted(data)}, indent=2)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add or update dividend/interest (RCM) income in savings_income.json."
    )
    parser.add_argument("--file", type=Path, default=None, help="Target JSON file.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("input"),
        help="Directory holding savings_income.json (default: input).",
    )
    parser.add_argument("--year", type=int, help="Year to set (enables non-interactive mode).")
    parser.add_argument("--dividends", type=str, help="Dividends received in EUR.")
    parser.add_argument("--interest", type=str, help="Interest received in EUR.")
    parser.add_argument("--foreign-tax", type=str, help="Foreign tax withheld in EUR.")
    args = parser.parse_args()

    path: Path = args.file or (args.input_dir / "savings_income.json")
    data = load_raw(path)

    if args.year is not None:
        existing = data.get(str(args.year), {})
        try:
            div = _to_decimal(args.dividends, existing.get("dividends_eur", 0))
            intr = _to_decimal(args.interest, existing.get("interest_eur", 0))
            ftax = _to_decimal(args.foreign_tax, existing.get("foreign_tax_eur", 0))
        except InvalidOperation:
            parser.error("--dividends/--interest/--foreign-tax must be numbers.")
        data = upsert(data, args.year, div, intr, ftax)
        save_raw(path, data)
        print(f"✓ Saved {args.year} to {path}:\n{json.dumps(data[str(args.year)], indent=2)}")
    else:
        _interactive(data, path)


if __name__ == "__main__":
    main()
