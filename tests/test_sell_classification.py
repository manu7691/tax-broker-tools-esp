"""
Tests for auto_detect_sell_to_cover() in cli_main.py.

A SELL order is classified three ways:
  * "Sell-to-Cover (Auto-detected)" — sold qty matches a VEST's
    shares_sold_to_cover within _COVER_SALE_MATCH_DAYS calendar days (confirmed
    against the RSU PDF). The window has to absorb T+2 settlement plus a weekend.
  * "Pending Settlement" — unmatched but not yet settled, so the RSU
    confirmation PDF may not exist yet. Neutral: neither manual nor sell-to-cover.
  * "Manual Sell" — unmatched AND settled, so the data is complete.

Settlement is decided by the real E-Trade per-row status when present, falling
back to execution-date recency for legacy downloads that lack a Status column.
"""

from datetime import date
from decimal import Decimal

from tax_engine.cli_main import auto_detect_sell_to_cover
from tax_engine.models import EventType, StockEvent

TODAY = date(2026, 6, 8)


def _sell(
    *,
    event_date: date,
    shares: str = "10",
    status: str = "",
    benefit: str = "Restricted Stock",
) -> StockEvent:
    return StockEvent(
        event_date=event_date,
        event_type=EventType.SELL,
        shares=Decimal(shares),
        price_usd=Decimal("50.00"),
        notes=f"Sell Order ({benefit})",
        order_status=status,
    )


def _vest(*, event_date: date, sold_to_cover: str) -> StockEvent:
    return StockEvent(
        event_date=event_date,
        event_type=EventType.VEST,
        shares=Decimal("100"),
        price_usd=Decimal("50.00"),
        shares_sold_to_cover=Decimal(sold_to_cover),
        notes="RSU Vest",
    )


class TestConfirmedSellToCover:
    def test_matches_vest_within_3_days(self) -> None:
        events = [
            _vest(event_date=date(2026, 5, 15), sold_to_cover="25"),
            _sell(event_date=date(2026, 5, 18), shares="25", status="Settled"),
        ]
        auto_detect_sell_to_cover(events, today=TODAY)
        assert "Sell-to-Cover (Auto-detected)" in events[1].notes

    def test_match_wins_even_if_unsettled(self) -> None:
        """A confirmed match outranks settlement status."""
        events = [
            _vest(event_date=date(2026, 6, 5), sold_to_cover="10"),
            _sell(event_date=date(2026, 6, 8), shares="10", status="Executed"),
        ]
        auto_detect_sell_to_cover(events, today=TODAY)
        assert "Sell-to-Cover (Auto-detected)" in events[1].notes

    def test_no_match_when_quantity_differs(self) -> None:
        events = [
            _vest(event_date=date(2026, 5, 15), sold_to_cover="25"),
            _sell(event_date=date(2026, 5, 18), shares="26", status="Settled"),
        ]
        auto_detect_sell_to_cover(events, today=TODAY)
        assert "Manual Sell" in events[1].notes


class TestPendingSettlement:
    def test_executed_unmatched_is_pending(self) -> None:
        """The reported bug: today's sell-to-cover, executed but not settled,
        whose RSU confirmation PDF does not exist yet — must NOT be Manual Sell."""
        events = [_sell(event_date=date(2026, 6, 8), shares="40", status="Executed")]
        auto_detect_sell_to_cover(events, today=TODAY)
        assert events[0].notes == "Pending Settlement (Restricted Stock)"

    def test_open_unmatched_is_pending(self) -> None:
        events = [_sell(event_date=date(2026, 6, 8), status="Open")]
        auto_detect_sell_to_cover(events, today=TODAY)
        assert "Pending Settlement" in events[0].notes

    def test_recent_legacy_no_status_is_pending(self) -> None:
        """No Status column (legacy) but executed within the fallback window."""
        events = [_sell(event_date=date(2026, 6, 6), status="")]
        auto_detect_sell_to_cover(events, today=TODAY)
        assert "Pending Settlement" in events[0].notes


class TestManualSell:
    def test_settled_unmatched_is_manual(self) -> None:
        events = [_sell(event_date=date(2026, 2, 4), shares="100", status="Settled")]
        auto_detect_sell_to_cover(events, today=TODAY)
        assert "Manual Sell" in events[0].notes

    def test_old_legacy_no_status_is_manual(self) -> None:
        """No Status column and well past the settlement window."""
        events = [_sell(event_date=date(2025, 12, 1), status="")]
        auto_detect_sell_to_cover(events, today=TODAY)
        assert "Manual Sell" in events[0].notes

    def test_settled_status_is_case_insensitive(self) -> None:
        events = [_sell(event_date=date(2026, 6, 8), status="SETTLED")]
        auto_detect_sell_to_cover(events, today=TODAY)
        assert "Manual Sell" in events[0].notes


class TestSettlementLagWindow:
    """A vest on a Friday or Saturday settles its cover sale the next week.

    The match window has to absorb a weekend plus T+2 settlement. Real data:
    a 15-Feb-2025 vest (a Saturday) whose 29 withheld shares were sold on the
    19th, and a 5-Jun-2025 vest (a Thursday) sold on the 9th — both four calendar
    days out, both exact quantity matches, both previously labelled "Manual Sell".
    Mislabelling a withholding sale as a voluntary one misrepresents the taxpayer's
    own record of what they did.
    """

    def test_matches_a_vest_four_calendar_days_earlier(self) -> None:
        events = [
            _vest(event_date=date(2025, 2, 15), sold_to_cover="29"),
            _sell(event_date=date(2025, 2, 19), shares="29", status="Settled"),
        ]

        auto_detect_sell_to_cover(events, today=TODAY)

        assert "Sell-to-Cover (Auto-detected)" in events[1].notes

    def test_matches_across_a_full_weekend(self) -> None:
        events = [
            _vest(event_date=date(2025, 6, 5), sold_to_cover="65"),
            _sell(event_date=date(2025, 6, 9), shares="65", status="Settled"),
        ]

        auto_detect_sell_to_cover(events, today=TODAY)

        assert "Sell-to-Cover (Auto-detected)" in events[1].notes

    def test_a_quantity_that_does_not_match_is_still_a_manual_sell(self) -> None:
        """Widening the window must not turn any nearby sale into a cover sale."""
        events = [
            _vest(event_date=date(2025, 6, 5), sold_to_cover="65"),
            _sell(event_date=date(2025, 6, 9), shares="440", status="Settled"),
        ]

        auto_detect_sell_to_cover(events, today=TODAY)

        assert "Manual Sell" in events[1].notes

    def test_a_sale_well_beyond_the_window_stays_manual(self) -> None:
        events = [
            _vest(event_date=date(2025, 6, 5), sold_to_cover="65"),
            _sell(event_date=date(2025, 6, 20), shares="65", status="Settled"),
        ]

        auto_detect_sell_to_cover(events, today=TODAY)

        assert "Manual Sell" in events[1].notes
