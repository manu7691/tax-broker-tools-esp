"""
Unit tests for the Spanish FIFO Tax Engine.
"""

from datetime import date
from decimal import Decimal

import pytest

from tax_engine.models import EventType, StockEvent, YearlyTaxSummary
from tax_engine.tax_engine import TaxEngine


class TestTaxEngineAcquisition:
    """Tests for VEST and BUY processing (acquisitions)."""

    def test_first_vest_creates_lot(self):
        """First VEST should set up a share lot."""
        engine = TaxEngine()
        event = StockEvent(
            event_date=date(2021, 5, 17),
            event_type=EventType.VEST,
            shares=Decimal("100"),
            price_usd=Decimal("50.00"),
            fx_rate=Decimal("0.82"),
        )

        result = engine.process_event(event)

        # 100 shares @ $50 * 0.82 = €41 per share
        assert engine.state.total_shares == Decimal("100")
        assert engine.state.avg_cost_eur == Decimal("41.0000")
        assert engine.state.total_portfolio_cost_eur == Decimal("4100.0000")
        assert len(engine.state.lots) == 1
        assert engine.state.lots[0].shares == Decimal("100")
        assert engine.state.lots[0].price_eur == Decimal("41.0000")
        assert result.realized_gain_loss == Decimal("0")


class TestTaxEngineSell:
    """Tests for SELL processing."""

    def test_sell_calculates_gain_fifo(self):
        """SELL should realize gains based on FIFO matching."""
        engine = TaxEngine()

        # 1. Purchase 10 shares @ €10
        engine.process_event(
            StockEvent(
                event_date=date(2021, 1, 15),
                event_type=EventType.BUY,
                shares=Decimal("10"),
                price_usd=Decimal("10.00"),
                fx_rate=Decimal("1.00"),
            )
        )

        # 2. Purchase 10 shares @ €20
        engine.process_event(
            StockEvent(
                event_date=date(2021, 2, 15),
                event_type=EventType.BUY,
                shares=Decimal("10"),
                price_usd=Decimal("20.00"),
                fx_rate=Decimal("1.00"),
            )
        )

        # 3. Sell 12 shares @ €30
        # FIFO: 10 shares from lot 1 (cost €10) + 2 shares from lot 2 (cost €20)
        # Gain: (30-10)*10 + (30-20)*2 = 200 + 20 = €220
        result = engine.process_event(
            StockEvent(
                event_date=date(2021, 5, 15),
                event_type=EventType.SELL,
                shares=Decimal("12"),
                price_usd=Decimal("30.00"),
                fx_rate=Decimal("1.00"),
            )
        )

        assert engine.state.total_shares == Decimal("8")
        assert result.realized_gain_loss == Decimal("220.0000")
        assert len(result.fifo_matches) == 2
        assert result.fifo_matches[0].shares == Decimal("10")
        assert result.fifo_matches[1].shares == Decimal("2")

    def test_sell_more_than_held_raises_error(self):
        """Attempting to sell more shares than held should raise ValueError."""
        engine = TaxEngine()

        # Acquire 50 shares
        engine.process_event(
            StockEvent(
                event_date=date(2021, 5, 17),
                event_type=EventType.VEST,
                shares=Decimal("50"),
                price_usd=Decimal("50.00"),
                fx_rate=Decimal("0.82"),
            )
        )

        # Try to sell 100 shares (more than held)
        with pytest.raises(ValueError) as exc_info:
            engine.process_event(
                StockEvent(
                    event_date=date(2021, 7, 15),
                    event_type=EventType.SELL,
                    shares=Decimal("100"),
                    price_usd=Decimal("55.00"),
                    fx_rate=Decimal("0.84"),
                )
            )

        assert "Cannot sell" in str(exc_info.value)


class TestTaxEngineEventSorting:
    """Tests for event sorting logic."""

    def test_same_day_vest_before_sell(self):
        """On same day, VEST should be processed before SELL."""
        engine = TaxEngine()
        events = [
            StockEvent(
                event_date=date(2021, 8, 1),
                event_type=EventType.SELL,
                shares=Decimal("20"),
                price_usd=Decimal("45.00"),
                fx_rate=Decimal("0.84"),
            ),
            StockEvent(
                event_date=date(2021, 8, 1),
                event_type=EventType.VEST,
                shares=Decimal("50"),
                price_usd=Decimal("45.00"),
                fx_rate=Decimal("0.84"),
            ),
        ]

        sorted_events = engine._sort_events(events)

        assert sorted_events[0].event_type == EventType.VEST
        assert sorted_events[1].event_type == EventType.SELL


class TestSpanishTaxCompliance:
    """Tests for Spanish FIFO and tax compliance logic."""

    def test_spanish_progressive_tax_calculation(self):
        """Verify Spanish progressive savings tax bands calculation."""
        summary = YearlyTaxSummary(year=2021)

        # Band 1: up to 6000 @ 19%
        summary.total_gains = Decimal("6000.00")
        assert summary.tax_due == Decimal("1140.00")

        # Band 2: next 44000 @ 21% (base = 50000)
        summary.total_gains = Decimal("50000.00")
        assert summary.tax_due == Decimal("10380.00")

        # Band 3: next 150000 @ 23% (base = 200000)
        summary.total_gains = Decimal("200000.00")
        assert summary.tax_due == Decimal("44880.00")

    def test_spanish_wash_sale_rule_detection(self):
        """Test that a sale at a loss is flagged under 2-month rule when purchase occurs within window."""
        engine = TaxEngine()

        events = [
            StockEvent(
                event_date=date(2021, 3, 1),
                event_type=EventType.BUY,
                shares=Decimal("10"),
                price_usd=Decimal("50.00"),
                fx_rate=Decimal("1.00"),
            ),
            StockEvent(
                event_date=date(2021, 4, 15),
                event_type=EventType.SELL,
                shares=Decimal("5"),
                price_usd=Decimal("30.00"),
                fx_rate=Decimal("1.00"),
            ),
            StockEvent(
                event_date=date(2021, 5, 1),
                event_type=EventType.BUY,
                shares=Decimal("5"),
                price_usd=Decimal("30.00"),
                fx_rate=Decimal("1.00"),
            ),
        ]

        engine.process_all(events)

        summary = engine.get_yearly_summary(2021)
        assert summary is not None
        assert summary.total_losses == Decimal("-100.0000")
        assert summary.blocked_losses == Decimal("-100.0000")
        assert summary.taxable_gain == Decimal("0.00")
        assert "Wash Sale Blocked" in engine.processed_events[1].event.notes


class TestTaxEngineEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_fractional_shares(self):
        """Test handling of fractional shares."""
        engine = TaxEngine()

        event = StockEvent(
            event_date=date(2021, 5, 17),
            event_type=EventType.VEST,
            shares=Decimal("63.5432"),
            price_usd=Decimal("46.68"),
            fx_rate=Decimal("0.8214"),
        )

        engine.process_event(event)
        assert engine.state.total_shares == Decimal("63.5432")

    def test_empty_events_list(self):
        """Test processing empty events list."""
        engine = TaxEngine()
        results = engine.process_all([])

        assert results == []
        assert engine.state.total_shares == Decimal("0")

    def test_format_shares(self):
        """Test formatting of whole and fractional shares."""
        assert TaxEngine.format_shares(Decimal("100")) == "100"
        assert TaxEngine.format_shares(Decimal("0.0227")) == "0.0227"
        assert TaxEngine.format_shares(Decimal("1000.5")) == "1,000.5"
        assert TaxEngine.format_shares(Decimal("0")) == "0"
        assert TaxEngine.format_shares(Decimal("123.456789")) == "123.456789"
        assert TaxEngine.format_shares(Decimal("123.4567891")) == "123.456789"


class TestWashSaleRefinement:
    """Tests for the refined wash sale rule (only remaining shares trigger blocking)."""

    def test_no_wash_sale_when_lot_fully_consumed(self):
        """Selling ALL shares from a lot should NOT trigger wash sale if no repurchase."""
        engine = TaxEngine()

        events = [
            StockEvent(
                event_date=date(2021, 3, 1),
                event_type=EventType.BUY,
                shares=Decimal("10"),
                price_usd=Decimal("50.00"),
                fx_rate=Decimal("1.00"),
            ),
            StockEvent(
                event_date=date(2021, 4, 15),
                event_type=EventType.SELL,
                shares=Decimal("10"),
                price_usd=Decimal("30.00"),
                fx_rate=Decimal("1.00"),
            ),
        ]

        engine.process_all(events)

        summary = engine.get_yearly_summary(2021)
        assert summary is not None
        # Loss of (30-50)*10 = -200
        assert summary.total_losses == Decimal("-200.0000")
        # No remaining shares from the original lot → no wash sale
        assert summary.blocked_losses == Decimal("0")
        assert "Wash Sale Blocked" not in engine.processed_events[1].event.notes

    def test_wash_sale_triggered_by_post_sale_repurchase(self):
        """Repurchase AFTER a loss-making sale should trigger wash sale."""
        engine = TaxEngine()

        events = [
            StockEvent(
                event_date=date(2021, 3, 1),
                event_type=EventType.BUY,
                shares=Decimal("10"),
                price_usd=Decimal("50.00"),
                fx_rate=Decimal("1.00"),
            ),
            # Sell all 10 shares at a loss
            StockEvent(
                event_date=date(2021, 4, 15),
                event_type=EventType.SELL,
                shares=Decimal("10"),
                price_usd=Decimal("30.00"),
                fx_rate=Decimal("1.00"),
            ),
            # Repurchase within 2 months of sale → triggers wash sale
            StockEvent(
                event_date=date(2021, 5, 1),
                event_type=EventType.BUY,
                shares=Decimal("3"),
                price_usd=Decimal("32.00"),
                fx_rate=Decimal("1.00"),
            ),
        ]

        engine.process_all(events)

        summary = engine.get_yearly_summary(2021)
        assert summary is not None
        # Loss: (30-50)*10 = -200
        assert summary.total_losses == Decimal("-200.0000")
        # 3 replacement shares still held → blocks 3/10 of the loss = -60
        assert summary.blocked_losses == Decimal("-60.0000")
        assert summary.deductible_losses == Decimal("-140.0000")
        assert "Wash Sale Blocked" in engine.processed_events[1].event.notes

    def test_replacement_shares_block_only_one_sale(self):
        """A single replacement lot must not block more than one loss sale.

        Two separate loss sales both fall within 2 months of the same surviving
        repurchase. The replacement shares can neutralize at most their own count
        of sold shares in total — not once per sale.
        """
        engine = TaxEngine()

        events = [
            # Two independent acquisitions, each fully sold at a loss.
            StockEvent(
                event_date=date(2021, 3, 1),
                event_type=EventType.BUY,
                shares=Decimal("10"),
                price_usd=Decimal("50.00"),
                fx_rate=Decimal("1.00"),
            ),
            StockEvent(
                event_date=date(2021, 3, 10),
                event_type=EventType.BUY,
                shares=Decimal("10"),
                price_usd=Decimal("50.00"),
                fx_rate=Decimal("1.00"),
            ),
            # Sell #1 at a loss (FIFO consumes the 03-01 lot).
            StockEvent(
                event_date=date(2021, 4, 1),
                event_type=EventType.SELL,
                shares=Decimal("10"),
                price_usd=Decimal("30.00"),
                fx_rate=Decimal("1.00"),
            ),
            # Sell #2 at a loss (FIFO consumes the 03-10 lot).
            StockEvent(
                event_date=date(2021, 4, 20),
                event_type=EventType.SELL,
                shares=Decimal("10"),
                price_usd=Decimal("30.00"),
                fx_rate=Decimal("1.00"),
            ),
            # A single repurchase of 4 shares, within 2 months of BOTH sales.
            StockEvent(
                event_date=date(2021, 5, 1),
                event_type=EventType.BUY,
                shares=Decimal("4"),
                price_usd=Decimal("32.00"),
                fx_rate=Decimal("1.00"),
            ),
        ]

        engine.process_all(events)

        summary = engine.get_yearly_summary(2021)
        assert summary is not None
        # Two sales, each loss (30-50)*10 = -200 → total -400.
        assert summary.total_losses == Decimal("-400.0000")
        # Only 4 replacement shares exist, so only 4 shares of loss may be blocked
        # in total: 4/10 * -200 = -80 (claimed entirely by the first sale).
        # The buggy behavior blocked -80 on EACH sale (-160 total).
        assert summary.blocked_losses == Decimal("-80.0000")
        assert summary.deductible_losses == Decimal("-320.0000")


class TestFeeConversion:
    """Fees in the native currency convert to EUR by the same rate as the price."""

    def test_fees_deducted_using_multiplication(self):
        # Buy 10 @ $100, sell 10 @ $150 with $20 fees, fx 0.90 EUR per USD.
        # Gross gain = (150-100)*10*0.90 = €450. Fees = 20*0.90 = €18 (NOT 20/0.90).
        engine = TaxEngine()
        engine.process_all(
            [
                StockEvent(
                    event_date=date(2021, 1, 1),
                    event_type=EventType.BUY,
                    shares=Decimal("10"),
                    price_usd=Decimal("100"),
                    fx_rate=Decimal("0.90"),
                ),
                StockEvent(
                    event_date=date(2021, 6, 1),
                    event_type=EventType.SELL,
                    shares=Decimal("10"),
                    price_usd=Decimal("150"),
                    fx_rate=Decimal("0.90"),
                    fees_usd=Decimal("20"),
                ),
            ]
        )
        sell = next(pe for pe in engine.processed_events if pe.event.event_type == EventType.SELL)
        assert sell.realized_gain_loss == Decimal("432.0000")  # 450 - 18
        assert engine.get_yearly_summary(2021).total_fees_eur == Decimal("18.0000")
