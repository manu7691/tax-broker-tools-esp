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
        # ``per_lot`` policy: sale #1's deferral is released when the 03-10 lot is
        # sold inside the same year, which is what makes the pledging arithmetic
        # visible in ``blocked_losses``. Under the default ``position_zero`` policy
        # nothing is released while 4 shares survive the year, so the same run
        # reports -280.00 — the pledge-once property under test is identical.
        engine = TaxEngine(release_policy="per_lot")

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
        # Sale #1 is deferred onto the 03-10 lot, but that lot is sold on 04-20,
        # inside the same year — so nothing of it is still pending at 31/12 and it
        # leaves no blocked balance. Only the 4 shares bought on 05-01 survive the
        # year, blocking 4/10 * -200 = -80. Those 4 shares are pledged once, never
        # once per sale (the buggy behavior blocked -80 on EACH sale).
        assert summary.blocked_losses == Decimal("-80.0000")
        assert summary.unlocked_historical_losses == Decimal("0")
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


def _ev(day: date, kind: EventType, shares: str, price: str) -> StockEvent:
    """Compact event builder for the wash-sale carry-forward tests (FX pinned to 1.0)."""
    return StockEvent(
        event_date=day,
        event_type=kind,
        shares=Decimal(shares),
        price_usd=Decimal(price),
        fx_rate=Decimal("1.00"),
    )


class TestWashSaleUnlockCarryForward:
    """Release semantics of the opt-in ``per_lot`` policy (literal DGT reading).

    Every engine here is built with ``release_policy="per_lot"`` on purpose: these
    cases pin the lot-by-lot unlock, where a deferred loss is freed as soon as the
    replacement lot is transmitted. The default ``position_zero`` policy is
    stricter — it also requires the whole position to reach 0,00 shares and a clean
    2-month quarantine — and is covered in ``test_compliance_rules.py``.
    """

    """Temporal allocation of a blocked loss once the 2-month block breaks.

    Art. 33.5.f LIRPF defers the loss; DGT V1547-16 and V1035-18 forbid
    rectifying the year of origin. The deferred loss becomes deductible in the
    year the replacement shares are finally disposed of, never retroactively.
    """

    # 100 shares bought at 100, sold at 60 (loss -4000), repurchased inside the
    # 2-month window, and the replacement finally sold the following year.
    BUY_2025 = date(2025, 1, 10)
    SELL_2025 = date(2025, 3, 10)
    REBUY_2025 = date(2025, 4, 1)

    def _base_events(self) -> list[StockEvent]:
        return [
            _ev(self.BUY_2025, EventType.BUY, "100", "100.00"),
            _ev(self.SELL_2025, EventType.SELL, "100", "60.00"),
            _ev(self.REBUY_2025, EventType.BUY, "100", "60.00"),
        ]

    def test_block_stands_while_replacement_is_still_held(self):
        """No disposal of the replacement → the loss stays blocked, nothing unlocked."""
        engine = TaxEngine(release_policy="per_lot")
        engine.process_all(self._base_events())

        s2025 = engine.get_yearly_summary(2025)
        assert s2025.blocked_losses == Decimal("-4000.0000")
        assert s2025.unlocked_historical_losses == Decimal("0")
        assert s2025.deductible_losses == Decimal("0.0000")

    def test_origin_year_is_immutable_when_block_breaks_later(self):
        """Selling the replacement in 2026 must not rewrite the 2025 summary."""
        engine = TaxEngine(release_policy="per_lot")
        engine.process_all(
            self._base_events() + [_ev(date(2026, 6, 1), EventType.SELL, "100", "70.00")]
        )

        s2025 = engine.get_yearly_summary(2025)
        assert s2025.total_losses == Decimal("-4000.0000")
        assert s2025.blocked_losses == Decimal("-4000.0000")
        assert s2025.unlocked_historical_losses == Decimal("0")
        assert s2025.deductible_losses == Decimal("0.0000")
        assert s2025.net_gain_loss == Decimal("0.0000")

    def test_unlocked_loss_lands_in_the_year_the_block_breaks(self):
        """2026 absorbs the released 2025 loss on top of its own result."""
        engine = TaxEngine(release_policy="per_lot")
        engine.process_all(
            self._base_events() + [_ev(date(2026, 6, 1), EventType.SELL, "100", "70.00")]
        )

        s2026 = engine.get_yearly_summary(2026)
        assert s2026.total_gains == Decimal("1000.0000")  # (70 - 60) * 100
        assert s2026.blocked_losses == Decimal("0")
        assert s2026.unlocked_historical_losses == Decimal("-4000.0000")
        assert s2026.unlocked_losses_by_origin == {2025: Decimal("-4000.0000")}
        assert s2026.deductible_losses == Decimal("-4000.0000")
        assert s2026.net_gain_loss == Decimal("-3000.0000")
        assert s2026.taxable_gain == Decimal("0")

    def test_partial_disposal_releases_the_block_pro_rata(self):
        """Half the replacement sold in 2026, half in 2027 → half the loss each year."""
        engine = TaxEngine(release_policy="per_lot")
        engine.process_all(
            self._base_events()
            + [
                _ev(date(2026, 6, 1), EventType.SELL, "40", "70.00"),
                _ev(date(2027, 6, 1), EventType.SELL, "60", "70.00"),
            ]
        )

        assert engine.get_yearly_summary(2025).blocked_losses == Decimal("-4000.0000")
        assert engine.get_yearly_summary(2025).unlocked_historical_losses == Decimal("0")
        assert engine.get_yearly_summary(2026).unlocked_historical_losses == Decimal("-1600.0000")
        assert engine.get_yearly_summary(2027).unlocked_historical_losses == Decimal("-2400.0000")

    def test_same_year_break_leaves_no_blocked_balance(self):
        """Block broken inside its own year → nothing pending at 31/12.

        ``blocked_losses`` reports the balance still deferred at year end, so a
        deferral that was created and released within the same year reports €0.00
        and the loss is simply deductible. Showing a gross figure here would claim
        a pending deferral that does not exist.
        """
        engine = TaxEngine(release_policy="per_lot")
        engine.process_all(
            self._base_events() + [_ev(date(2025, 11, 3), EventType.SELL, "100", "70.00")]
        )

        s2025 = engine.get_yearly_summary(2025)
        assert s2025.blocked_losses == Decimal("0")
        assert s2025.unlocked_historical_losses == Decimal("0")
        assert s2025.deductible_losses == Decimal("-4000.0000")
        # The lot that held the deferral has given it all back.
        assert all(lot.deferred_wash_sale_loss == Decimal("0") for lot in engine.lot_ledger)

    def test_released_amounts_never_exceed_the_blocked_amount(self):
        """Repeated round trips must not release more than was ever blocked."""
        engine = TaxEngine(release_policy="per_lot")
        engine.process_all(
            self._base_events()
            + [
                _ev(date(2026, 6, 1), EventType.SELL, "100", "70.00"),
                _ev(date(2026, 7, 1), EventType.BUY, "100", "70.00"),
                _ev(date(2028, 1, 5), EventType.SELL, "100", "80.00"),
            ]
        )

        blocked = sum((s.blocked_losses for s in engine.get_all_yearly_summaries()), Decimal("0"))
        unlocked = sum(
            (s.unlocked_historical_losses for s in engine.get_all_yearly_summaries()),
            Decimal("0"),
        )
        assert unlocked == blocked


class TestWashSaleClosedYearIntegrity:
    """A year already reported must not be rewritten by what happens afterwards.

    The block attaches at the moment the loss sale and the replacement holding
    coexist, so it is decided from facts that are already in the past. Later
    disposals release it into their own year; they never reopen the origin year.
    """

    def _events_through_2026(self) -> list[StockEvent]:
        return [
            _ev(date(2026, 1, 10), EventType.BUY, "100", "100.00"),
            _ev(date(2026, 11, 3), EventType.BUY, "50", "60.00"),  # replacement
            _ev(date(2026, 12, 1), EventType.SELL, "100", "60.00"),  # loss -4000
        ]

    def test_2026_figures_are_identical_with_and_without_the_2027_sale(self):
        """The regression: a 2027 disposal used to erase the 2026 block."""
        without = TaxEngine()
        without.process_all(self._events_through_2026())

        with_2027 = TaxEngine()
        with_2027.process_all(
            # Sold inside the 2-month window, but after the block attached.
            self._events_through_2026() + [_ev(date(2027, 1, 15), EventType.SELL, "50", "70.00")]
        )

        before = without.get_yearly_summary(2026)
        after = with_2027.get_yearly_summary(2026)
        assert before.blocked_losses == Decimal("-2000.0000")
        assert after.blocked_losses == before.blocked_losses
        assert after.total_losses == before.total_losses
        assert after.deductible_losses == before.deductible_losses == Decimal("-2000.0000")

    def test_the_2027_sale_receives_the_released_loss(self):
        engine = TaxEngine()
        engine.process_all(
            self._events_through_2026() + [_ev(date(2027, 1, 15), EventType.SELL, "50", "70.00")]
        )

        s2027 = engine.get_yearly_summary(2027)
        assert s2027.total_gains == Decimal("500.0000")
        assert s2027.unlocked_historical_losses == Decimal("-2000.0000")
        assert s2027.unlocked_losses_by_origin == {2026: Decimal("-2000.0000")}
        assert s2027.net_gain_loss == Decimal("-1500.0000")

    def test_forward_window_legitimately_crosses_the_year_boundary(self):
        """A December loss stays provisional until its +2 month window closes.

        Art. 33.5.f counts repurchases in the two months AFTER the sale, so a
        2026-12-07 sale is still exposed to a 2027-01-08 purchase. This is not a
        breach of the rule above — the 2026 figure is simply not final until
        2027-02-07, which is why the filing window opens later.
        """
        engine = TaxEngine()
        engine.process_all(
            [
                _ev(date(2026, 6, 1), EventType.BUY, "100", "100.00"),
                _ev(date(2026, 12, 7), EventType.SELL, "100", "60.00"),  # loss -4000
                _ev(date(2027, 1, 8), EventType.BUY, "40", "50.00"),  # inside the window
            ]
        )

        assert engine.get_yearly_summary(2026).blocked_losses == Decimal("-1600.0000")

    def test_full_liquidation_leaves_no_blocked_balance(self):
        """Strict requirement: liquidate 100% and nothing may stay blocked."""
        engine = TaxEngine()
        engine.process_all(
            self._events_through_2026()
            + [
                _ev(date(2027, 1, 15), EventType.SELL, "20", "70.00"),
                _ev(date(2027, 3, 2), EventType.BUY, "30", "55.00"),
                _ev(date(2027, 9, 9), EventType.SELL, "60", "50.00"),  # liquidates everything
            ]
        )

        assert engine.state.total_shares == Decimal("0")
        # The guarantee: the year the position is wound down reports no pending
        # deferral, because there are no replacement shares left to hold one.
        last_year = engine.get_all_yearly_summaries()[-1]
        assert last_year.year == 2027
        assert last_year.blocked_losses == Decimal("0.00")
        # And no lot anywhere is still sitting on a deferred loss.
        assert all(lot.deferred_wash_sale_loss == Decimal("0") for lot in engine.lot_ledger)
