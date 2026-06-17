"""
Tests for the combined stocks + crypto savings base.

These lock the *canonical* "what you owe" guarantee: stock and crypto
gains/losses must net into ONE base del ahorro. Same-year cross-asset
compensation, the 4-year carryforward, and the 25% RCM cross-offset all run
on the **merged** total — exactly the aggregation `generate_combined_html` /
`tax-combined` perform (merge the per-source summaries, then run the ledger on
a TaxEngine carrying the merged total). Crypto keeps its own FIFO pools and
casillas elsewhere; only this savings-base layer is shared.
"""

from decimal import Decimal

from tax_engine.crypto_engine import CryptoTaxEngine
from tax_engine.models import SavingsIncomeYear, YearlyTaxSummary
from tax_engine.tax_engine import TaxEngine


def _summary(year, gains="0", losses="0"):
    return {
        year: YearlyTaxSummary(
            year=year, total_gains=Decimal(gains), total_losses=Decimal(losses)
        )
    }


def _merged_engine(stock_summaries, crypto_summaries):
    """Replicate generate_combined_html's aggregation path."""
    merged = CryptoTaxEngine.merge_yearly_summaries([stock_summaries, crypto_summaries])
    engine = TaxEngine()
    engine.yearly_summaries = merged
    return engine, merged


class TestCombinedSavingsBase:
    def test_crypto_loss_offsets_stock_gain_same_year(self):
        # 2024: +1000 stock gain and -400 crypto loss net to +600 taxable.
        engine, merged = _merged_engine(_summary(2024, gains="1000"), _summary(2024, losses="-400"))
        assert merged[2024].total_gains == Decimal("1000")
        assert merged[2024].total_losses == Decimal("-400")
        assert merged[2024].net_gain_loss == Decimal("600")
        assert merged[2024].taxable_gain == Decimal("600")

    def test_stock_loss_carried_into_later_crypto_gain(self):
        # A 2023 stock loss offsets a 2024 crypto gain via the 4-year carryforward.
        engine, _ = _merged_engine(_summary(2023, losses="-500"), _summary(2024, gains="800"))
        rows = {r.year: r for r in engine.compute_carryforward().rows}
        assert rows[2024].prior_losses_applied == Decimal("500")
        assert rows[2024].taxable_after == Decimal("300")

    def test_crypto_loss_carried_into_later_stock_gain(self):
        # Symmetric: a crypto loss offsets a later stock gain.
        engine, _ = _merged_engine(_summary(2025, gains="1000"), _summary(2023, losses="-300"))
        rows = {r.year: r for r in engine.compute_carryforward().rows}
        assert rows[2025].prior_losses_applied == Decimal("300")
        assert rows[2025].taxable_after == Decimal("700")

    def test_opening_losses_apply_to_combined_total(self):
        # Pre-window losses seed the pool and offset the merged gain.
        engine, merged = _merged_engine(_summary(2024, gains="200"), _summary(2024, gains="300"))
        assert merged[2024].net_gain_loss == Decimal("500")
        rows = {r.year: r for r in engine.compute_carryforward(opening_losses={2021: Decimal("400")}).rows}
        assert rows[2024].prior_losses_applied == Decimal("400")
        assert rows[2024].taxable_after == Decimal("100")

    def test_25pct_cross_offset_uses_combined_gp_total(self):
        # Stocks alone are a +200 gain; only when crypto's -600 loss merges in
        # does the combined G/L become a net loss that offsets RCM. The 25% cap
        # then limits the offset to 25% of the 1000 dividend income = 250.
        engine, merged = _merged_engine(_summary(2024, gains="200"), _summary(2024, losses="-600"))
        assert merged[2024].net_gain_loss == Decimal("-400")
        ledger = engine.compute_savings_ledger(
            {2024: SavingsIncomeYear(year=2024, dividends_eur=Decimal("1000"))}
        )
        row = {r.year: r for r in ledger.rows}[2024]
        assert row.cross_offset == Decimal("250")
        assert row.cross_direction == "gp->rcm"
        assert row.gp_taxable == Decimal("0")
        assert row.rcm_taxable == Decimal("750")  # 1000 - 250
        assert row.savings_base == Decimal("750")
