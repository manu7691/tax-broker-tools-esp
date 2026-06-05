"""
Unit tests for the savings-income CLI helper (pure merge/IO functions).
"""

import json
from decimal import Decimal

from tax_engine.cli_savings_income import _json_num, _to_decimal, load_raw, save_raw, upsert


def test_json_num_integral_vs_fractional():
    assert _json_num(Decimal("320")) == 320
    assert isinstance(_json_num(Decimal("320")), int)
    assert _json_num(Decimal("1.45")) == 1.45


def test_upsert_adds_year():
    data = upsert({}, 2024, Decimal("320"), Decimal("15"), Decimal("48"))
    assert data["2024"] == {
        "dividends_eur": 320,
        "interest_eur": 15,
        "foreign_tax_eur": 48,
    }


def test_upsert_preserves_other_years():
    data = {"2023": {"dividends_eur": 100, "interest_eur": 0, "foreign_tax_eur": 0}}
    out = upsert(data, 2024, Decimal("5"), Decimal("0"), Decimal("0"))
    assert "2023" in out and out["2023"]["dividends_eur"] == 100
    assert out["2024"]["dividends_eur"] == 5
    # original dict not mutated
    assert "2024" not in data


def test_upsert_overwrites_same_year():
    data = {"2024": {"dividends_eur": 1, "interest_eur": 2, "foreign_tax_eur": 3}}
    out = upsert(data, 2024, Decimal("9"), Decimal("0"), Decimal("0"))
    assert out["2024"] == {"dividends_eur": 9, "interest_eur": 0, "foreign_tax_eur": 0}


def test_to_decimal_blank_uses_default():
    assert _to_decimal("", 7) == Decimal("7")
    assert _to_decimal(None, "1.45") == Decimal("1.45")
    assert _to_decimal("12,50", 0) == Decimal("12.50")  # comma decimal accepted


def test_roundtrip_and_loader_compat(tmp_path):
    path = tmp_path / "savings_income.json"
    data = upsert({}, 2024, Decimal("320.50"), Decimal("1.45"), Decimal("48"))
    save_raw(path, data)
    # reloads as plain dict
    assert load_raw(path)["2024"]["dividends_eur"] == 320.5
    # and the real engine loader accepts it
    from tax_engine.cli_main import load_savings_income

    parsed = load_savings_income(path)
    assert parsed[2024].rcm_net == Decimal("321.95")
    assert parsed[2024].foreign_tax_eur == Decimal("48")


def test_save_raw_sorts_years(tmp_path):
    path = tmp_path / "s.json"
    save_raw(path, {"2025": {}, "2023": {}, "2024": {}})
    assert list(json.loads(path.read_text())) == ["2023", "2024", "2025"]
