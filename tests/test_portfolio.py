"""
Tests for the portfolio runner (portfolio.py).

Key behaviour under test:
- Events are grouped into one FIFO queue per ISIN (ticker as fallback).
- The same ISIN reported by different brokers merges into a single queue, so
  FIFO matches across brokers and never over-sells.
- The aggregate's net = the sum of the per-security nets; carryforward / savings
  ledger run on the portfolio total.
- The 2-month wash-sale rule stays per security (a replacement in another
  security never blocks a loss).
- Single-stock mode (no symbol/isin) is unchanged vs. a plain TaxEngine.
"""

from datetime import date
from decimal import Decimal

from tax_engine import TaxEngine, run_portfolio
from tax_engine.cli_main import build_portfolio_or_engine
from tax_engine.dashboard_helpers import build_securities_chart
from tax_engine.models import EventType, StockEvent
from tax_engine.portfolio import group_events_by_security
from tax_engine.securities import SecuritiesConfig

TSLA_ISIN = "US88160R1014"
NVDA_ISIN = "US67066G1040"


def _ev(
    d: str,
    etype: EventType,
    shares: str,
    price: str,
    *,
    isin: str | None = None,
    symbol: str = "",
    broker: str = "E*TRADE",
) -> StockEvent:
    """Build a StockEvent with fx_rate=1 so price_eur == price_usd (no ECB calls)."""
    return StockEvent(
        event_date=date.fromisoformat(d),
        event_type=etype,
        shares=Decimal(shares),
        price_usd=Decimal(price),
        fx_rate=Decimal("1"),
        isin=isin,
        symbol=symbol,
        broker=broker,
    )


class TestGrouping:
    def test_groups_by_isin(self) -> None:
        events = [
            _ev("2021-01-01", EventType.BUY, "10", "100", isin=TSLA_ISIN, symbol="TSLA"),
            _ev("2021-02-01", EventType.BUY, "5", "200", isin=NVDA_ISIN, symbol="NVDA"),
            _ev("2021-03-01", EventType.SELL, "10", "150", isin=TSLA_ISIN, symbol="TSLA"),
        ]
        grouped = group_events_by_security(events)
        assert set(grouped) == {TSLA_ISIN, NVDA_ISIN}
        assert len(grouped[TSLA_ISIN][1]) == 2
        assert grouped[TSLA_ISIN][0].ticker == "TSLA"

    def test_same_isin_across_brokers_merges(self) -> None:
        events = [
            _ev("2021-01-01", EventType.BUY, "10", "100", isin=TSLA_ISIN, broker="E*TRADE"),
            _ev("2021-03-01", EventType.SELL, "4", "150", isin=TSLA_ISIN, broker="Revolut"),
        ]
        grouped = group_events_by_security(events)
        assert set(grouped) == {TSLA_ISIN}  # one queue despite two brokers
        assert len(grouped[TSLA_ISIN][1]) == 2

    def test_ticker_only_groups_separately_from_isin(self) -> None:
        events = [
            _ev("2021-01-01", EventType.BUY, "10", "100", isin=TSLA_ISIN, symbol="TSLA"),
            _ev("2021-01-01", EventType.BUY, "1", "10", symbol="ZZZ"),
        ]
        grouped = group_events_by_security(events)
        assert set(grouped) == {TSLA_ISIN, "@ZZZ"}


class TestAggregation:
    def test_portfolio_net_is_sum_of_per_security_nets(self) -> None:
        events = [
            # TSLA: +500 gain
            _ev("2021-01-01", EventType.BUY, "10", "100", isin=TSLA_ISIN, symbol="TSLA"),
            _ev("2021-06-01", EventType.SELL, "10", "150", isin=TSLA_ISIN, symbol="TSLA"),
            # NVDA: -100 loss
            _ev("2021-02-01", EventType.BUY, "5", "200", isin=NVDA_ISIN, symbol="NVDA"),
            _ev("2021-07-01", EventType.SELL, "5", "180", isin=NVDA_ISIN, symbol="NVDA"),
        ]
        portfolio = run_portfolio(events)

        per_security = {r.security.ticker: r.net_gain_loss for r in portfolio.results}
        assert per_security == {"NVDA": Decimal("-100"), "TSLA": Decimal("500")}

        agg = portfolio.aggregate.yearly_summaries[2021]
        assert agg.total_gains == Decimal("500")
        assert agg.total_losses == Decimal("-100")
        assert agg.net_gain_loss == Decimal("400")
        assert sum(per_security.values()) == agg.net_gain_loss

    def test_cross_broker_fifo_does_not_oversell(self) -> None:
        # Buy on E*TRADE, sell the same ISIN on Revolut: one queue, no oversell.
        events = [
            _ev("2021-01-01", EventType.BUY, "10", "100", isin=TSLA_ISIN, broker="E*TRADE"),
            _ev("2021-06-01", EventType.SELL, "10", "150", isin=TSLA_ISIN, broker="Revolut"),
        ]
        portfolio = run_portfolio(events)  # must not raise "sell more than held"
        assert len(portfolio.results) == 1
        assert portfolio.aggregate.yearly_summaries[2021].net_gain_loss == Decimal("500")
        # The sell consumed a lot acquired at the *other* broker.
        sell = next(
            pe
            for pe in portfolio.results[0].engine.processed_events
            if pe.event.event_type == EventType.SELL
        )
        assert sell.fifo_matches[0].shares == Decimal("10")

    def test_aggregate_position_sums_surviving_lots(self) -> None:
        events = [
            _ev("2021-01-01", EventType.BUY, "10", "100", isin=TSLA_ISIN),
            _ev("2021-02-01", EventType.BUY, "5", "200", isin=NVDA_ISIN),
        ]
        portfolio = run_portfolio(events)
        assert portfolio.aggregate.state.total_shares == Decimal("15")
        assert {lot.isin for lot in portfolio.aggregate.state.lots} == {TSLA_ISIN, NVDA_ISIN}


class TestWashSalePerSecurity:
    def test_replacement_in_other_security_does_not_block(self) -> None:
        events = [
            # Security A: loss sale with an A-replacement inside the 2-month window.
            _ev("2021-01-01", EventType.BUY, "10", "100", isin=TSLA_ISIN),
            _ev("2021-03-01", EventType.SELL, "10", "80", isin=TSLA_ISIN),
            _ev("2021-03-15", EventType.BUY, "10", "90", isin=TSLA_ISIN),
            # Security B: a purchase inside A's window — must NOT block A's loss.
            _ev("2021-02-20", EventType.BUY, "10", "50", isin=NVDA_ISIN),
        ]
        portfolio = run_portfolio(events)
        by_ticker = {r.security.key: r for r in portfolio.results}

        a_summary = by_ticker[TSLA_ISIN].engine.yearly_summaries[2021]
        b_summary = by_ticker[NVDA_ISIN].engine.yearly_summaries[2021]
        # A's €200 loss is blocked by A's own replacement; B contributes none.
        assert a_summary.blocked_losses == Decimal("-200")
        assert b_summary.blocked_losses == Decimal("0")
        # Aggregate blocked loss is exactly A's.
        assert portfolio.aggregate.yearly_summaries[2021].blocked_losses == Decimal("-200")


class TestIncludeFilter:
    def test_include_limits_processed_securities(self) -> None:
        events = [
            _ev("2021-01-01", EventType.BUY, "10", "100", isin=TSLA_ISIN, symbol="TSLA"),
            _ev("2021-06-01", EventType.SELL, "10", "150", isin=TSLA_ISIN, symbol="TSLA"),
            _ev("2021-02-01", EventType.BUY, "5", "200", isin=NVDA_ISIN, symbol="NVDA"),
        ]
        cfg = SecuritiesConfig(include=["TSLA"])
        portfolio = run_portfolio(events, config=cfg)
        assert [r.security.ticker for r in portfolio.results] == ["TSLA"]
        assert portfolio.aggregate.yearly_summaries[2021].net_gain_loss == Decimal("500")


class TestCliWiring:
    """build_portfolio_or_engine: the cli_main assembly used by `tax-engine`."""

    def test_single_mode_returns_one_engine_no_securities(self) -> None:
        etrade = [
            _ev("2021-01-01", EventType.BUY, "10", "100"),
            _ev("2021-06-01", EventType.SELL, "10", "150"),
        ]
        engine, securities, events = build_portfolio_or_engine(
            etrade,
            [],
            all_securities=False,
        )
        assert securities is None
        assert len(events) == 2
        assert engine.yearly_summaries[2021].net_gain_loss == Decimal("500")

    def test_portfolio_mode_tags_etrade_and_merges_cross_broker(self) -> None:
        # E*TRADE events have no identity yet; they must be tagged with the primary
        # ISIN and merge with the same-ISIN Revolut rows into one queue.
        etrade = [_ev("2021-01-01", EventType.BUY, "10", "100", broker="E*TRADE")]
        revolut = [
            _ev("2021-06-01", EventType.SELL, "6", "150", isin=TSLA_ISIN, broker="Revolut"),
            _ev("2021-02-01", EventType.BUY, "5", "200", isin=NVDA_ISIN, broker="Revolut"),
        ]
        engine, securities, _ = build_portfolio_or_engine(
            etrade,
            revolut,
            all_securities=True,
            primary_symbol="TSLA",
            primary_isin=TSLA_ISIN,
        )
        assert securities is not None
        # E*TRADE buy + Revolut sell merged under TSLA's ISIN; NVDA separate.
        labels = {r.security.key for r in securities}
        assert labels == {TSLA_ISIN, NVDA_ISIN}
        assert all(e.isin == TSLA_ISIN for e in etrade)  # tagged in place
        # Aggregate net = TSLA realized (sold 6 of the E*TRADE lot) only.
        assert engine.yearly_summaries[2021].total_gains == Decimal("300")  # (150-100)*6


class TestSecuritiesChartPayload:
    def test_build_securities_chart_shape_and_values(self) -> None:
        events = [
            _ev("2021-01-01", EventType.BUY, "10", "100", isin=TSLA_ISIN, symbol="TSLA"),
            _ev("2021-06-01", EventType.SELL, "10", "150", isin=TSLA_ISIN, symbol="TSLA"),
            _ev("2021-02-01", EventType.BUY, "5", "200", isin=NVDA_ISIN, symbol="NVDA"),
        ]
        portfolio = run_portfolio(events)
        chart = build_securities_chart(portfolio.results)
        # Sorted by label: NVDA, TSLA.
        assert chart["securities"] == ["NVDA", "TSLA"]
        assert chart["isin"] == [NVDA_ISIN, TSLA_ISIN]
        assert chart["invested"] == [1000.0, 1000.0]  # 5*200, 10*100
        assert chart["realized"] == [0.0, 500.0]  # NVDA unsold, TSLA +500
        assert chart["position"] == [5.0, 0.0]  # NVDA held, TSLA closed


class TestReporting:
    def _portfolio(self) -> object:
        events = [
            _ev("2021-01-01", EventType.BUY, "10", "100", isin=TSLA_ISIN, symbol="TSLA"),
            _ev("2021-06-01", EventType.SELL, "10", "150", isin=TSLA_ISIN, symbol="TSLA"),
            _ev("2021-02-01", EventType.BUY, "5", "200", isin=NVDA_ISIN, symbol="NVDA"),
            _ev("2021-07-01", EventType.SELL, "5", "180", isin=NVDA_ISIN, symbol="NVDA"),
        ]
        return run_portfolio(events)

    def test_portfolio_html_has_summary_and_per_security_sections(self) -> None:
        portfolio = self._portfolio()
        html = portfolio.aggregate.generate_html_content(lang="en", securities=portfolio.results)
        # Per-security rollup table is present, with each security and its ISIN.
        assert "Portfolio Summary by Security" in html
        assert TSLA_ISIN in html and NVDA_ISIN in html
        # Per-security ledger sections (one h2 per security), not the combined one.
        assert "TSLA (US88160R1014) — Transactions &amp; FIFO Detail" in html or (
            "TSLA (US88160R1014) — Transactions & FIFO Detail" in html
        )
        assert "Detailed Transaction Ledger" not in html  # combined section replaced

    def test_portfolio_html_spanish(self) -> None:
        portfolio = self._portfolio()
        html = portfolio.aggregate.generate_html_content(lang="es", securities=portfolio.results)
        assert "Resumen de Cartera por Valor" in html
        assert "Transacciones y Detalle FIFO" in html

    def test_single_stock_html_unchanged_sections(self) -> None:
        # Without securities=, the report keeps the combined ledger and no rollup.
        portfolio = self._portfolio()
        html = portfolio.aggregate.generate_html_content(lang="en")
        assert "Portfolio Summary by Security" not in html
        assert "Detailed Transaction Ledger" in html
        assert "Calculation Details for Sales" in html


class TestSingleStockRegression:
    def test_no_identity_matches_plain_engine(self) -> None:
        # Events with no symbol/isin = today's single-security flow. The portfolio
        # aggregate must reproduce a plain TaxEngine run over the same events.
        events = [
            _ev("2021-01-01", EventType.BUY, "10", "100"),
            _ev("2021-02-01", EventType.BUY, "10", "120"),
            _ev("2021-06-01", EventType.SELL, "15", "150"),
        ]
        plain = TaxEngine()
        plain.process_all(
            [
                StockEvent(
                    event_date=e.event_date,
                    event_type=e.event_type,
                    shares=e.shares,
                    price_usd=e.price_usd,
                    fx_rate=Decimal("1"),
                )
                for e in events
            ]
        )

        portfolio = run_portfolio(events)
        assert [r.security.key for r in portfolio.results] == ["@UNKNOWN"]

        agg = portfolio.aggregate.yearly_summaries[2021]
        ref = plain.yearly_summaries[2021]
        assert agg.total_gains == ref.total_gains
        assert agg.total_losses == ref.total_losses
        assert agg.net_gain_loss == ref.net_gain_loss
        assert portfolio.aggregate.state.total_shares == plain.state.total_shares
