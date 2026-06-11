"""
Test the tax engine using the sample data example.

This test verifies that the engine produces correct results with the sample data,
preserving the original example that was in main.py.
"""

from datetime import date
from decimal import Decimal

import pytest

from tax_engine import (
    TaxEngine,
    create_sample_events_with_ecb_rates,
    prefetch_ecb_rates,
)


def test_sample_data_with_ecb_rates():
    """
    Test the tax engine with sample data using ECB rates.

    This test preserves the original example from main.py and verifies:
    - Transaction ledger calculations (gains/losses)
    - Yearly tax summary
    - Final position state
    """
    # Create events and fetch ECB rates
    events = create_sample_events_with_ecb_rates()
    prefetch_ecb_rates(events)

    # Process events
    engine = TaxEngine()
    engine.process_all(events)

    # Verify final position state
    assert engine.state.total_shares == 73
    assert engine.state.avg_cost_eur == pytest.approx(Decimal("35.6198"), abs=Decimal("0.0001"))
    assert engine.state.total_portfolio_cost_eur == pytest.approx(
        Decimal("2600.2454"), abs=Decimal("0.0001")
    )

    # Verify we have processed events for all stock events
    assert len(engine.processed_events) == 16

    # Verify specific transaction details
    # First transaction: 2020-11-27 BUY
    pe0 = engine.processed_events[0]
    assert pe0.event.event_date == date(2020, 11, 27)
    assert pe0.event.event_type.value == "BUY"
    assert pe0.event.shares == 50
    assert pe0.event.price_usd == pytest.approx(Decimal("38.42"), abs=Decimal("0.01"))
    assert pe0.total_shares_after == 50
    assert pe0.avg_cost_eur_after == pytest.approx(Decimal("32.2267"), abs=Decimal("0.0001"))

    # 2021-02-03 SELL - first profitable sale
    pe1 = engine.processed_events[1]
    assert pe1.event.event_date == date(2021, 2, 3)
    assert pe1.event.event_type.value == "SELL"
    assert pe1.event.shares == 50
    assert pe1.total_shares_after == 0
    assert pe1.realized_gain_loss == pytest.approx(Decimal("421.3150"), abs=Decimal("0.0001"))

    # 2021-05-17 SELL - first loss
    pe3 = engine.processed_events[3]
    assert pe3.event.event_date == date(2021, 5, 17)
    assert pe3.event.event_type.value == "SELL"
    assert pe3.event.shares == 25
    assert pe3.realized_gain_loss == pytest.approx(Decimal("-38.2925"), abs=Decimal("0.0001"))

    # 2022-06-01 SELL - large loss (now at index 15)
    pe15 = engine.processed_events[15]
    assert pe15.event.event_date == date(2022, 6, 1)
    assert pe15.event.event_type.value == "SELL"
    assert pe15.event.shares == 205
    assert pe15.realized_gain_loss == pytest.approx(Decimal("-2859.4179"), abs=Decimal("0.0001"))

    # Verify yearly tax summary
    summaries = engine.get_all_yearly_summaries()
    tax_summary = {s.year: s for s in summaries}

    # 2020: No taxable events
    assert 2020 in tax_summary
    summary_2020 = tax_summary[2020]
    assert summary_2020.total_gains == pytest.approx(Decimal("0.00"), abs=Decimal("0.01"))
    assert summary_2020.total_losses == pytest.approx(Decimal("0.00"), abs=Decimal("0.01"))
    assert summary_2020.net_gain_loss == pytest.approx(Decimal("0.00"), abs=Decimal("0.01"))
    assert summary_2020.taxable_gain == pytest.approx(Decimal("0.00"), abs=Decimal("0.01"))
    assert summary_2020.tax_due == pytest.approx(Decimal("0.00"), abs=Decimal("0.01"))

    # 2021: Net gain
    assert 2021 in tax_summary
    summary_2021 = tax_summary[2021]
    assert summary_2021.total_gains == pytest.approx(Decimal("572.59"), abs=Decimal("0.01"))
    assert summary_2021.total_losses == pytest.approx(Decimal("-39.05"), abs=Decimal("0.01"))
    assert summary_2021.blocked_losses == pytest.approx(Decimal("0.00"), abs=Decimal("0.01"))
    assert summary_2021.net_gain_loss == pytest.approx(Decimal("533.54"), abs=Decimal("0.01"))
    assert summary_2021.taxable_gain == pytest.approx(Decimal("533.54"), abs=Decimal("0.01"))
    assert summary_2021.tax_due == pytest.approx(Decimal("101.37"), abs=Decimal("0.01"))

    # 2022: Net loss
    assert 2022 in tax_summary
    summary_2022 = tax_summary[2022]
    assert summary_2022.total_gains == pytest.approx(Decimal("0.00"), abs=Decimal("0.01"))
    assert summary_2022.total_losses == pytest.approx(Decimal("-2941.27"), abs=Decimal("0.01"))
    assert summary_2022.blocked_losses == pytest.approx(Decimal("-1018.23"), abs=Decimal("0.01"))
    assert summary_2022.net_gain_loss == pytest.approx(Decimal("-1923.04"), abs=Decimal("0.01"))
    assert summary_2022.taxable_gain == pytest.approx(Decimal("0.00"), abs=Decimal("0.01"))
    assert summary_2022.tax_due == pytest.approx(Decimal("0.00"), abs=Decimal("0.01"))


def test_sample_data_ledger_output(capsys):
    """
    Test that the ledger output can be printed without errors.
    This ensures the output formatting works correctly.
    """
    events = create_sample_events_with_ecb_rates()
    prefetch_ecb_rates(events)

    engine = TaxEngine()
    engine.process_all(events)

    # Print outputs (captured by capsys)
    engine.print_ledger()
    engine.print_tax_summary()

    # Verify output contains expected content
    captured = capsys.readouterr()
    assert "TRANSACTION LEDGER" in captured.out
    assert "YEARLY TAX SUMMARY" in captured.out
    assert "2020" in captured.out
    assert "2021" in captured.out
    assert "2022" in captured.out


class TestMultiSecuritySample:
    """The portfolio demo dataset spans several securities and currencies."""

    def test_spans_multiple_securities_and_currencies(self):
        from tax_engine import create_sample_multi_security_events

        events = create_sample_multi_security_events()
        # Every event is tagged with an identity (symbol + ISIN).
        assert all(e.symbol and e.isin for e in events)
        symbols = {e.symbol for e in events}
        assert {"DT", "TSLA", "NVDA", "SHEL"} <= symbols
        # Exercises the multi-currency path: at least one non-USD (GBP) security.
        assert "GBP" in {e.currency for e in events}

    def test_portfolio_runs_and_groups_per_security(self):
        from tax_engine import create_sample_multi_security_events, run_portfolio

        portfolio = run_portfolio(create_sample_multi_security_events())
        labels = [r.security.label for r in portfolio.results]
        assert labels == ["ADBE", "DT", "NVDA", "SHEL", "TSLA"]  # sorted by label
        # NVDA is bought-and-held → open position, no realized result.
        nvda = next(r for r in portfolio.results if r.security.label == "NVDA")
        assert nvda.engine.state.total_shares > 0
        assert nvda.net_gain_loss == Decimal("0")
        # SHEL (GBP): sold 30 of 40 → a realized gain plus a small open GBP position
        # (so the demo also shows currency exposure).
        shel = next(r for r in portfolio.results if r.security.label == "SHEL")
        assert shel.engine.state.total_shares == Decimal("10")
        assert shel.net_gain_loss > 0
        # ADBE is sold at a loss in 2020 → an early-year loss for the carryforward.
        adbe = next(r for r in portfolio.results if r.security.label == "ADBE")
        assert adbe.net_gain_loss < 0

    def test_savings_income_and_carryforward_application(self):
        from tax_engine import (
            create_sample_multi_security_events,
            create_sample_savings_income,
            run_portfolio,
        )

        agg = run_portfolio(create_sample_multi_security_events()).aggregate
        ledger = agg.compute_savings_ledger(create_sample_savings_income())
        rows = {r.year: r for r in ledger.rows}
        # 2020's loss is APPLIED against 2021's gain (carryforward application).
        assert rows[2021].gp_prior_applied > 0
        # 2022 is a net capital loss with dividend income → 25% cross-offset fires.
        assert rows[2022].gp_net < 0
        assert rows[2022].cross_offset > 0

    def test_espp_map_has_discount_and_flags_early_sales(self):
        from tax_engine import (
            create_sample_espp_map,
            create_sample_multi_security_events,
            run_portfolio,
        )
        from tax_engine.cli_main import detect_espp_early_sales

        espp_map = create_sample_espp_map()
        # Each ESPP purchase has FMV above the (discounted) purchase price.
        assert all(fmv > price for fmv, price in espp_map.values())

        agg = run_portfolio(create_sample_multi_security_events()).aggregate
        early, details = detect_espp_early_sales(agg.processed_events, espp_map)
        assert early  # at least one ESPP lot was sold before the 3-year mark
