"""
Unit tests for the crypto parsers and per-coin FIFO orchestrator.

FX is supplied manually (fx_rate on the events) so the tests never touch the
network — they assert parsing, the cross-exchange merge, per-coin FIFO matching,
and the short-sale guard, all in EUR.
"""

from datetime import datetime
from decimal import Decimal

from tax_engine.crypto_engine import CryptoTaxEngine
from tax_engine.crypto_parser import (
    CryptoTrade,
    _split_amount,
    _split_pair,
    trades_to_events_by_coin,
)
from tax_engine.models import EventType


class TestParsingHelpers:
    def test_split_amount(self):
        assert _split_amount("692.7SEI") == (Decimal("692.7"), "SEI")
        assert _split_amount("204.3465USDC") == (Decimal("204.3465"), "USDC")

    def test_split_pair_longest_quote_first(self):
        assert _split_pair("SEIUSDC") == ("SEI", "USDC")
        assert _split_pair("ONDOUSDT") == ("ONDO", "USDT")
        # FDUSD must win over USD
        assert _split_pair("ETHFDUSD") == ("ETH", "FDUSD")


def _trade(dt, base, side, qty, amount, quote="USDT", source="Pionex"):
    return CryptoTrade(
        dt=datetime.fromisoformat(dt),
        base=base,
        quote=quote,
        side=side,
        qty=Decimal(qty),
        quote_amount=Decimal(amount),
        fee_qty=Decimal("0"),
        fee_coin="",
        source=source,
    )


class TestEventBuilding:
    def test_stablecoin_base_is_skipped(self):
        # A USDC->USDT convert has a stablecoin base: not a tracked position.
        trades = [_trade("2025-01-01T00:00:00", "USDC", "SELL", "100", "100", quote="USDT")]
        assert trades_to_events_by_coin(trades) == {}

    def test_crypto_to_crypto_is_skipped(self):
        trades = [_trade("2025-01-01T00:00:00", "ETH", "BUY", "1", "20", quote="BTC")]
        assert trades_to_events_by_coin(trades) == {}

    def test_crypto_to_crypto_is_collected_when_requested(self):
        # A permuta (ETH/BTC) is taxable in Spain — it must be surfaced, not lost.
        trades = [
            _trade("2025-01-01T00:00:00", "BTC", "BUY", "1", "100"),  # normal, handled
            _trade("2025-02-01T00:00:00", "ETH", "BUY", "1", "20", quote="BTC"),  # permuta
        ]
        unhandled: list = []
        by_coin = trades_to_events_by_coin(trades, unhandled_swaps=unhandled)
        assert set(by_coin) == {"BTC"}  # only the stablecoin-quoted trade is handled
        assert len(unhandled) == 1
        assert (unhandled[0].base, unhandled[0].quote) == ("ETH", "BTC")

    def test_unhandled_swaps_shown_in_console(self, capsys):
        from tax_engine.crypto_engine import CryptoTaxEngine

        trades = [_trade("2025-02-01T00:00:00", "ETH", "BUY", "1", "20", quote="BTC")]
        unhandled: list = []
        trades_to_events_by_coin(trades, unhandled_swaps=unhandled)
        engine = CryptoTaxEngine()
        engine.unhandled_swaps = unhandled
        engine.print_console()
        out = capsys.readouterr().out
        assert "NOT handled" in out and "ETH" in out

    def test_events_grouped_per_coin_in_order(self):
        trades = [
            _trade("2025-01-02T00:00:00", "BTC", "SELL", "1", "120"),
            _trade("2025-01-01T00:00:00", "BTC", "BUY", "1", "100"),
            _trade("2025-01-01T00:00:00", "SOL", "BUY", "10", "50"),
        ]
        by_coin = trades_to_events_by_coin(trades)
        assert set(by_coin) == {"BTC", "SOL"}
        # Sorted by timestamp: BTC buy (Jan 1) before BTC sell (Jan 2).
        assert [e.event_type for e in by_coin["BTC"]] == [EventType.BUY, EventType.SELL]


def _manual_engine(events_by_coin):
    """Run the orchestrator with fx pinned to 1.0 (EUR == quote)."""
    for evs in events_by_coin.values():
        for e in evs:
            e.fx_rate = Decimal("1")
            e._fx_rate_resolved = Decimal("1")
    engine = CryptoTaxEngine()
    # Bypass the network prefetch since every event already has a manual fx_rate.
    for coin, events in events_by_coin.items():
        ordered = engine._guard_short_sales(coin, events)
        from tax_engine.tax_engine import TaxEngine

        te = TaxEngine()
        for e in ordered:
            te.process_event(e)
        engine.engines[coin] = te
    return engine


class TestFifoGains:
    def test_simple_gain_in_eur(self):
        by_coin = trades_to_events_by_coin(
            [
                _trade("2025-03-01T00:00:00", "BTC", "BUY", "1", "100"),
                _trade("2025-06-01T00:00:00", "BTC", "SELL", "1", "150"),
            ]
        )
        engine = _manual_engine(by_coin)
        summaries = engine.combined_summaries()
        assert summaries[2025].total_gains == Decimal("50")
        assert summaries[2025].net_gain_loss == Decimal("50")
        # One open... no: fully sold, so no open position.
        assert engine.open_positions() == []

    def test_cross_exchange_merge_uses_cheapest_first(self):
        # Buy cheap on Binance, expensive on Pionex; FIFO sells the cheap lot.
        by_coin = trades_to_events_by_coin(
            [
                _trade("2025-01-01T00:00:00", "TAO", "BUY", "1", "100", source="Binance"),
                _trade("2025-01-02T00:00:00", "TAO", "BUY", "1", "300", source="Pionex"),
                _trade("2025-02-01T00:00:00", "TAO", "SELL", "1", "200"),
            ]
        )
        engine = _manual_engine(by_coin)
        summaries = engine.combined_summaries()
        # Sold 1 @200 against the 100 lot => +100 gain; one 300 lot remains.
        assert summaries[2025].net_gain_loss == Decimal("100")
        pos = engine.open_positions()
        assert len(pos) == 1 and pos[0].quantity == Decimal("1")

    def test_short_sale_guard_adds_synthetic_lot(self):
        # Sell more than ever bought -> a synthetic neutral lot covers it.
        by_coin = trades_to_events_by_coin(
            [
                _trade("2025-01-01T00:00:00", "SEI", "BUY", "1", "10"),
                _trade("2025-02-01T00:00:00", "SEI", "SELL", "3", "30"),
            ]
        )
        engine = _manual_engine(by_coin)
        assert engine.synthetic_notes  # a gap was reported
        # Net is not negative inventory; result stays finite.
        summaries = engine.combined_summaries()
        assert 2025 in summaries
