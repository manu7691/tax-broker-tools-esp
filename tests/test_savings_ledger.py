"""
Unit tests for the two-bucket savings-base ledger (Art. 48 & 49 LIRPF):
capital gains/losses (G/L) vs. returns on movable capital (RCM = dividends + interest),
including the 25% cross-category offset.
"""

from decimal import Decimal

from tax_engine.models import SavingsIncomeYear, YearlyTaxSummary
from tax_engine.tax_engine import TaxEngine


def _engine_with(years: dict[int, tuple[str, str]]) -> TaxEngine:
    """Build an engine with given (total_gains, total_losses) per year (losses negative)."""
    engine = TaxEngine()
    engine.yearly_summaries = {}
    for year, (gains, losses) in years.items():
        engine.yearly_summaries[year] = YearlyTaxSummary(
            year=year,
            total_gains=Decimal(gains),
            total_losses=Decimal(losses),
        )
    return engine


def _income(year: int, dividends="0", interest="0", foreign="0") -> SavingsIncomeYear:
    return SavingsIncomeYear(
        year=year,
        dividends_eur=Decimal(dividends),
        interest_eur=Decimal(interest),
        foreign_tax_eur=Decimal(foreign),
    )


class TestSavingsLedger:
    def test_cross_offset_loss_against_dividends_capped_at_25pct(self):
        # 2024: stock loss -2000, dividends +3000 -> offset capped at 25% of 3000 = 750.
        engine = _engine_with({2024: ("0", "-2000")})
        income = {2024: _income(2024, dividends="3000", foreign="450")}
        ledger = engine.compute_savings_ledger(income)
        row = ledger.rows[0]

        assert row.cross_offset == Decimal("750.00")
        assert row.cross_direction == "gp->rcm"
        assert row.rcm_taxable == Decimal("2250.00")  # 3000 - 750
        assert row.gp_taxable == Decimal("0")
        assert row.savings_base == Decimal("2250.00")
        # Remaining loss 2000 - 750 = 1250 carries forward, usable through 2028.
        assert ledger.gp_pending_end == [(2024, Decimal("1250.00"), 2028)]
        assert ledger.total_foreign_tax == Decimal("450")

    def test_year_dependent_cap_2017_is_20pct(self):
        engine = _engine_with({2017: ("0", "-1000")})
        income = {2017: _income(2017, dividends="1000")}
        ledger = engine.compute_savings_ledger(income)
        # 2017 cap is 20% -> offset 200.
        assert ledger.rows[0].cross_offset == Decimal("200.00")
        assert ledger.rows[0].rcm_taxable == Decimal("800.00")

    def test_prior_loss_applied_before_cross_offset(self):
        # 2022 loss carries; 2024 has a stock gain that consumes it, plus dividends.
        engine = _engine_with({2022: ("0", "-500"), 2024: ("800", "0")})
        income = {2024: _income(2024, dividends="200")}
        ledger = engine.compute_savings_ledger(income)
        rows = {r.year: r for r in ledger.rows}

        # 2024 gain 800 consumes the 500 prior loss -> gp_taxable 300; dividends fully taxed.
        assert rows[2024].gp_prior_applied == Decimal("500")
        assert rows[2024].gp_taxable == Decimal("300")
        assert rows[2024].rcm_taxable == Decimal("200")
        assert rows[2024].cross_offset == Decimal("0")
        assert ledger.gp_pending_end == []

    def test_no_dividends_behaves_like_plain_gains(self):
        engine = _engine_with({2024: ("1000", "0")})
        ledger = engine.compute_savings_ledger({2024: _income(2024)})
        assert ledger.rows[0].gp_taxable == Decimal("1000")
        assert ledger.rows[0].rcm_taxable == Decimal("0")
        assert ledger.rows[0].savings_base == Decimal("1000")

    def test_dividends_only_year_has_no_summary(self):
        # A year present only in savings income (no stock activity) still appears.
        engine = _engine_with({2023: ("500", "0")})
        income = {2024: _income(2024, dividends="100", interest="20")}
        ledger = engine.compute_savings_ledger(income)
        rows = {r.year: r for r in ledger.rows}
        assert 2024 in rows
        assert rows[2024].rcm_taxable == Decimal("120")
        assert rows[2024].gp_net == Decimal("0")

    def test_opening_rcm_loss_offsets_later_dividends(self):
        engine = _engine_with({})
        income = {2024: _income(2024, dividends="500")}
        ledger = engine.compute_savings_ledger(
            income, opening_rcm_losses={2021: Decimal("200")}
        )
        # 2021 RCM loss usable through 2025 -> offsets 200 of 2024 dividends.
        assert ledger.rows[0].rcm_prior_applied == Decimal("200")
        assert ledger.rows[0].rcm_taxable == Decimal("300")
