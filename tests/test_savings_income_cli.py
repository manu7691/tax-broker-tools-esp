"""
Unit tests for the savings-income CLI helper (USD payments) and the loader's
USD->EUR per-payment conversion.
"""

import json
from decimal import Decimal
from unittest.mock import patch

from tax_engine.cli_savings_income import (
    _json_num,
    append_payment,
    load_payments,
    save_payments,
)


def test_json_num_integral_vs_fractional():
    assert _json_num(Decimal("80")) == 80
    assert isinstance(_json_num(Decimal("80")), int)
    assert _json_num(Decimal("12.34")) == 12.34


def test_append_payment_normalises_type_and_fields():
    out = append_payment([], "2024-03-15", "Dividend", Decimal("80"), Decimal("12"))
    assert out == [
        {"date": "2024-03-15", "type": "dividend", "amount_usd": 80, "foreign_tax_usd": 12}
    ]


def test_append_payment_omits_zero_foreign_tax():
    out = append_payment([], "2024-03-15", "interest", Decimal("1.45"), Decimal("0"))
    assert out[0] == {"date": "2024-03-15", "type": "interest", "amount_usd": 1.45}


def test_append_payment_preserves_existing():
    first = append_payment([], "2024-03-15", "dividend", Decimal("80"), Decimal("0"))
    second = append_payment(first, "2024-06-15", "dividend", Decimal("90"), Decimal("0"))
    assert len(second) == 2
    assert len(first) == 1  # original not mutated


def test_append_payment_rejects_bad_date():
    import pytest

    with pytest.raises(ValueError):
        append_payment([], "15-03-2024", "dividend", Decimal("80"), Decimal("0"))


def test_save_sorts_by_date_and_load_roundtrips(tmp_path):
    path = tmp_path / "savings_income.json"
    payments = append_payment([], "2024-06-15", "dividend", Decimal("90"), Decimal("0"))
    payments = append_payment(payments, "2024-03-15", "dividend", Decimal("80"), Decimal("0"))
    save_payments(path, payments)
    on_disk = json.loads(path.read_text())
    assert [p["date"] for p in on_disk] == ["2024-03-15", "2024-06-15"]
    assert load_payments(path) == on_disk


def test_load_payments_refuses_eur_dict(tmp_path, capsys):
    path = tmp_path / "savings_income.json"
    path.write_text(json.dumps({"2024": {"dividends_eur": 320}}))
    assert load_payments(path) is None
    assert "EUR-per-year format" in capsys.readouterr().out


def test_loader_converts_usd_payments_per_date(tmp_path):
    """The engine loader converts each USD payment at its date's ECB rate."""
    from tax_engine.cli_main import load_savings_income

    path = tmp_path / "savings_income.json"
    path.write_text(
        json.dumps(
            [
                {"date": "2024-03-15", "type": "dividend", "amount_usd": 100, "foreign_tax_usd": 15},
                {"date": "2024-06-15", "type": "interest", "amount_usd": 50},
            ]
        )
    )
    # Mock ECB: 0.90 EUR/USD in March, 0.92 in June.
    rates = {3: Decimal("0.90"), 6: Decimal("0.92")}
    with patch(
        "tax_engine.ecb_rates.ECBRateFetcher.get_rate",
        side_effect=lambda d: rates[d.month],
    ):
        si = load_savings_income(path)

    year = si[2024]
    assert year.dividends_eur == Decimal("90.00")  # 100 * 0.90
    assert year.interest_eur == Decimal("46.00")  # 50 * 0.92
    assert year.foreign_tax_eur == Decimal("13.50")  # 15 * 0.90
    assert year.rcm_net == Decimal("136.00")
