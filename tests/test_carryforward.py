"""
Unit tests for the 4-year loss-carryforward ledger (Art. 49 LIRPF).
"""

from decimal import Decimal

from tax_engine.models import YearlyTaxSummary
from tax_engine.tax_engine import TaxEngine


def _engine_with(years: dict[int, tuple[str, str]]) -> TaxEngine:
    """
    Build an engine whose yearly summaries have the given gains/losses.

    ``years`` maps year -> (total_gains, total_losses) as strings; losses are
    expected to be negative (or zero).
    """
    engine = TaxEngine()
    engine.yearly_summaries = {}
    for year, (gains, losses) in years.items():
        engine.yearly_summaries[year] = YearlyTaxSummary(
            year=year,
            total_gains=Decimal(gains),
            total_losses=Decimal(losses),
        )
    return engine


class TestCarryforwardLedger:
    def test_loss_consumed_by_later_gain(self):
        engine = _engine_with(
            {
                2022: ("0", "-900"),  # net loss 900
                2024: ("200", "0"),  # gain 200 -> consumes 200 of the loss
                2025: ("1000", "0"),  # gain 1000 -> consumes remaining 700
            }
        )
        ledger = engine.compute_carryforward()
        rows = {r.year: r for r in ledger.rows}

        assert rows[2024].prior_losses_applied == Decimal("200")
        assert rows[2024].taxable_after == Decimal("0")
        assert rows[2025].prior_losses_applied == Decimal("700")
        assert rows[2025].taxable_after == Decimal("300")
        assert ledger.expired == []
        assert ledger.pending_end == []

    def test_opening_losses_seed_pool(self):
        engine = _engine_with({2022: ("450", "0")})
        ledger = engine.compute_carryforward(opening_losses={2019: Decimal("100")})
        rows = {r.year: r for r in ledger.rows}

        # 2019 loss is usable through 2023, so it offsets the 2022 gain.
        assert rows[2022].prior_losses_applied == Decimal("100")
        assert rows[2022].taxable_after == Decimal("350")

    def test_loss_expires_after_four_years(self):
        engine = _engine_with(
            {
                2020: ("0", "-500"),  # usable 2021..2024 only
                2025: ("600", "0"),  # too late -> loss expired
            }
        )
        ledger = engine.compute_carryforward()
        rows = {r.year: r for r in ledger.rows}

        assert (2020, Decimal("500")) in ledger.expired
        assert rows[2025].prior_losses_applied == Decimal("0")
        assert rows[2025].taxable_after == Decimal("600")

    def test_unused_loss_reported_as_pending(self):
        engine = _engine_with({2024: ("0", "-300")})
        ledger = engine.compute_carryforward()

        # Loss generated in 2024, never offset -> pending, usable through 2028.
        assert ledger.pending_end == [(2024, Decimal("300"), 2028)]
        assert ledger.expired == []

    def test_no_losses_is_empty(self):
        engine = _engine_with({2023: ("500", "0"), 2024: ("700", "0")})
        ledger = engine.compute_carryforward()

        assert all(r.prior_losses_applied == Decimal("0") for r in ledger.rows)
        assert ledger.expired == []
        assert ledger.pending_end == []
