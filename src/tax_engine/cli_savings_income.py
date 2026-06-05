"""
Interactive helper to record dividend & interest (RCM) payments.

Writes/merges ``input/savings_income.json`` as a list of **USD payments, each with
its date** — the tax report converts every payment to EUR at the ECB rate on that
date (exactly like stock transactions) and computes the dividend/interest savings
base and the 25% cross-category offset. You never hand-edit JSON.

Interactive::

    .venv/bin/python -m tax_engine.cli_savings_income

One-shot (non-interactive)::

    .venv/bin/python -m tax_engine.cli_savings_income \
        --date 2024-03-15 --type dividend --amount-usd 80 --foreign-tax-usd 12

Each run appends; existing payments are preserved.
"""

import argparse
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

TYPES = ("dividend", "interest")


def _json_num(d: Decimal) -> float | int:
    """Render a Decimal as a clean JSON number (int when integral)."""
    return int(d) if d == d.to_integral_value() else float(d)


def load_payments(path: Path) -> list | None:
    """
    Load the payments list, or [] if absent.

    Returns None (and warns) if the file is an EUR-per-year object rather than a
    payments list, so we never clobber a hand-made EUR file.
    """
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        print(f"Warning: {path} is unreadable; starting fresh.")
        return []
    if isinstance(data, dict):
        print(
            f"{path} is in the EUR-per-year format. This tool writes USD payments.\n"
            "Move/rename that file first, or hand-edit it instead."
        )
        return None
    return data if isinstance(data, list) else []


def save_payments(path: Path, payments: list) -> None:
    """Write the payments list to disk, sorted by date."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(payments, key=lambda p: str(p.get("date", "")))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, indent=2)
        fh.write("\n")


def append_payment(
    payments: list,
    date_str: str,
    ptype: str,
    amount_usd: Decimal,
    foreign_tax_usd: Decimal,
) -> list:
    """Return a new list with one validated payment appended."""
    pay_date = date.fromisoformat(date_str.strip())  # raises ValueError if invalid
    norm_type = "interest" if ptype.strip().lower().startswith("int") else "dividend"
    entry = {
        "date": pay_date.isoformat(),
        "type": norm_type,
        "amount_usd": _json_num(amount_usd),
    }
    if foreign_tax_usd != 0:
        entry["foreign_tax_usd"] = _json_num(foreign_tax_usd)
    return [*payments, entry]


def _eur_preview(date_str: str, amount_usd: Decimal) -> str:
    """Best-effort EUR preview at the ECB rate for the given date."""
    try:
        from tax_engine.ecb_rates import ECBRateFetcher

        rate = ECBRateFetcher.get_rate(date.fromisoformat(date_str))
        return f"≈ €{(amount_usd * rate):,.2f} at ECB rate {rate}"
    except Exception:
        return "(EUR conversion happens when the report runs)"


def _prompt_decimal(label: str, default: str = "0") -> Decimal:
    while True:
        raw = input(f"  {label} [{default}]: ").strip()
        try:
            return Decimal((raw or default).replace(",", "."))
        except InvalidOperation:
            print("    Please enter a number (e.g. 80.50).")


def _interactive(payments: list, path: Path) -> None:
    print("Record each dividend/interest payment in USD with its date.\n")
    while True:
        date_str = input("Payment date (YYYY-MM-DD): ").strip()
        try:
            date.fromisoformat(date_str)
        except ValueError:
            print("  Please use YYYY-MM-DD.")
            continue
        ptype = input("  Type [dividend/interest] (dividend): ").strip() or "dividend"
        amount = _prompt_decimal("Amount (USD)")
        ftax = _prompt_decimal("Foreign tax withheld (USD)")

        payments = append_payment(payments, date_str, ptype, amount, ftax)
        save_payments(path, payments)
        print(f"  ✓ Saved. {_eur_preview(date_str, amount)}\n")

        if input("Add another payment? [y/N]: ").strip().lower() not in ("y", "yes"):
            break

    print(f"\n{len(payments)} payment(s) in {path}.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record dividend/interest (RCM) payments (USD + date) in savings_income.json."
    )
    parser.add_argument("--file", type=Path, default=None, help="Target JSON file.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("input"),
        help="Directory holding savings_income.json (default: input).",
    )
    parser.add_argument("--date", type=str, help="Payment date YYYY-MM-DD (non-interactive mode).")
    parser.add_argument("--type", type=str, default="dividend", help="dividend or interest.")
    parser.add_argument("--amount-usd", type=str, help="Payment amount in USD.")
    parser.add_argument("--foreign-tax-usd", type=str, default="0", help="US tax withheld in USD.")
    args = parser.parse_args()

    path: Path = args.file or (args.input_dir / "savings_income.json")
    payments = load_payments(path)
    if payments is None:
        return  # legacy EUR file present; load_payments already explained.

    if args.date is not None:
        try:
            amount = Decimal((args.amount_usd or "0").replace(",", "."))
            ftax = Decimal((args.foreign_tax_usd or "0").replace(",", "."))
            payments = append_payment(payments, args.date, args.type, amount, ftax)
        except (ValueError, InvalidOperation) as e:
            parser.error(f"invalid --date/--amount-usd/--foreign-tax-usd: {e}")
        save_payments(path, payments)
        print(f"✓ Appended payment to {path}. {_eur_preview(args.date, amount)}")
    else:
        _interactive(payments, path)


if __name__ == "__main__":
    main()
