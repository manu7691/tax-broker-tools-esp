"""
Compliance tests for the four IRPF business rules the engine must satisfy.

Each test here pins one audited defect (H1..H7). They are written against the
*rules*, not against the current implementation:

* Rule 1 — global lot-level FIFO across every broker for homogeneous securities.
* Rule 2 — Art. 33.5.f 2-month wash-sale block.
* Rule 3 — deferred losses unlock only when the position reaches 0,00 shares and
  a 2-month quarantine elapses with no new acquisitions; closed years never mutate.
* Rule 4 — lot provenance is tracked so ESPP exemption breaches (Art. 42.3.f) are
  reported separately from the savings base.
"""

from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

from tax_engine.cli_main import detect_espp_early_sales, load_orders_from_excel
from tax_engine.models import EventType, LotOrigin, StockEvent
from tax_engine.portfolio import AmbiguousSecurityError, group_events_by_security, run_portfolio
from tax_engine.tax_engine import TaxEngine


def ev(
    day: date,
    kind: EventType,
    shares: str,
    price: str,
    *,
    notes: str = "",
    fx: str = "1.0",
    isin: str | None = None,
    symbol: str = "",
    origin: LotOrigin | None = None,
    fees: str = "0",
) -> StockEvent:
    """Build a StockEvent with an explicit FX rate (no ECB calls)."""
    kwargs = {}
    if origin is not None:
        kwargs["origin"] = origin
    return StockEvent(
        event_date=day,
        event_type=kind,
        shares=Decimal(shares),
        price_usd=Decimal(price),
        fx_rate=Decimal(fx),
        fees_usd=Decimal(fees),
        notes=notes,
        isin=isin,
        symbol=symbol,
        **kwargs,
    )


# =============================================================================
# Rule 1 — one global FIFO queue per homogeneous security (H1)
# =============================================================================


class TestRule1GlobalFifo:
    def test_same_ticker_merges_into_one_queue_when_only_one_broker_reports_isin(self):
        """A ticker whose ISIN is known from ANY event groups every event under it.

        E*TRADE exports carry no ISIN; Revolut's do. Without backfilling, the same
        homogeneous security lands in two FIFO queues and the cost basis is wrong.
        """
        events = [
            ev(date(2022, 1, 10), EventType.BUY, "100", "10", symbol="ACME"),
            ev(date(2022, 2, 10), EventType.BUY, "100", "50", isin="US0000000001", symbol="ACME"),
            ev(date(2023, 1, 10), EventType.SELL, "150", "30", isin="US0000000001", symbol="ACME"),
        ]

        grouped = group_events_by_security(events)

        assert list(grouped) == ["US0000000001"]
        assert len(grouped["US0000000001"][1]) == 3

    def test_split_queue_no_longer_breaks_the_fifo_cost_basis(self):
        """The 150-share sale must consume both lots, oldest first."""
        events = [
            ev(date(2022, 1, 10), EventType.BUY, "100", "10", symbol="ACME"),
            ev(date(2022, 2, 10), EventType.BUY, "100", "50", isin="US0000000001", symbol="ACME"),
            ev(date(2023, 1, 10), EventType.SELL, "150", "30", isin="US0000000001", symbol="ACME"),
        ]

        portfolio = run_portfolio(events)

        assert len(portfolio.results) == 1
        sale = portfolio.results[0].engine.processed_events[-1]
        # 100 @ €10 (gain €2000) + 50 @ €50 (loss €1000) = €1000 net gain
        assert [m.shares for m in sale.fifo_matches] == [Decimal("100"), Decimal("50")]
        assert sale.realized_gain_loss == Decimal("1000.0000")

    def test_one_ticker_resolving_to_two_isins_is_rejected(self):
        """Conflicting identities must fail loud, never merge silently."""
        events = [
            ev(date(2022, 1, 10), EventType.BUY, "10", "10", isin="US0000000001", symbol="ACME"),
            ev(date(2022, 2, 10), EventType.BUY, "10", "10", isin="US0000000002", symbol="ACME"),
        ]

        with pytest.raises(AmbiguousSecurityError, match="ACME"):
            group_events_by_security(events)

    def test_orders_loader_carries_the_symbol_so_sells_can_be_grouped(self, tmp_path: Path):
        """E*TRADE sells must keep their security identity (Symbol column)."""
        orders_dir = tmp_path / "input" / "orders"
        orders_dir.mkdir(parents=True)
        buf = BytesIO()
        pd.DataFrame(
            [
                {
                    "Execution Date": "01/15/2023",
                    "Symbol": "ACME",
                    "Sold Qty.": "10",
                    "Execution Price": "$30.00",
                    "Benefit Type": "Restricted Stock",
                }
            ]
        ).to_excel(buf, index=False)
        (orders_dir / "orders.xlsx").write_bytes(buf.getvalue())

        events = load_orders_from_excel(input_dir=tmp_path / "input")

        assert len(events) == 1
        assert events[0].symbol == "ACME"


# =============================================================================
# Rule 3 — unlock on zero position + quarantine (H2), closed years (H3)
# =============================================================================


class TestRule3Unlocking:
    """The conservative ``position_zero`` policy: unlock only on a fully clean exit.

    Stricter than the statute (which frees progressively — see
    ``TestDefinitiveTransmissionPolicy``); kept as an opt-in for taxpayers who
    prefer never to integrate a deferred loss before liquidating the position.
    """

    def test_loss_stays_blocked_while_the_position_is_still_open(self):
        """Selling the replacement lot does not unlock while shares remain held."""
        engine = TaxEngine(release_policy="position_zero")
        engine.process_all(
            [
                ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2022, 5, 10), EventType.SELL, "100", "50"),  # loss -5000
                ev(date(2022, 5, 20), EventType.BUY, "100", "50"),  # blocks it
                ev(date(2023, 4, 10), EventType.BUY, "500", "50"),  # position stays open
                ev(date(2023, 6, 10), EventType.SELL, "100", "50"),  # sells replacement lot
            ]
        )

        assert engine.get_yearly_summary(2022).blocked_losses == Decimal("-5000.0000")
        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("0")

    def test_repurchase_inside_the_quarantine_voids_the_zero_crossing(self):
        """Hitting 0,00 shares is not enough: 2 months must pass with no buys."""
        engine = TaxEngine(release_policy="position_zero")
        engine.process_all(
            [
                ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2022, 5, 10), EventType.SELL, "100", "50"),  # loss -5000
                ev(date(2022, 5, 20), EventType.BUY, "100", "50"),  # blocks it
                ev(date(2023, 6, 10), EventType.SELL, "100", "50"),  # position -> 0
                ev(date(2023, 6, 20), EventType.BUY, "100", "50"),  # buy inside quarantine
            ]
        )

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("0")

    def test_loss_unlocks_when_position_hits_zero_and_quarantine_elapses(self):
        """Clean zero crossing: the loss becomes deductible in the quarantine-end year."""
        engine = TaxEngine(release_policy="position_zero")
        engine.process_all(
            [
                ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2022, 5, 10), EventType.SELL, "100", "50"),  # loss -5000
                ev(date(2022, 5, 20), EventType.BUY, "100", "50"),  # blocks it
                ev(date(2023, 6, 10), EventType.SELL, "100", "50"),  # position -> 0, clean
            ]
        )

        assert engine.get_yearly_summary(2022).blocked_losses == Decimal("-5000.0000")
        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-5000.0000")

    def test_quarantine_ending_in_the_next_year_defers_the_unlock_to_that_year(self):
        """A December zero crossing unlocks in the following year (2 months later)."""
        engine = TaxEngine(release_policy="position_zero")
        engine.process_all(
            [
                ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2022, 5, 10), EventType.SELL, "100", "50"),  # loss -5000
                ev(date(2022, 5, 20), EventType.BUY, "100", "50"),  # blocks it
                ev(date(2023, 12, 10), EventType.SELL, "100", "50"),  # position -> 0
            ]
        )

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("0")
        assert engine.get_yearly_summary(2024).unlocked_historical_losses == Decimal("-5000.0000")

    def test_per_lot_policy_remains_available_for_the_literal_dgt_reading(self):
        """The looser 'as the remaining securities are transmitted' reading stays opt-in."""
        engine = TaxEngine(release_policy="per_lot")
        engine.process_all(
            [
                ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2022, 5, 10), EventType.SELL, "100", "50"),
                ev(date(2022, 5, 20), EventType.BUY, "100", "50"),
                ev(date(2023, 4, 10), EventType.BUY, "500", "50"),
                ev(date(2023, 6, 10), EventType.SELL, "100", "50"),
            ]
        )

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-5000.0000")


class TestRule3ClosedYears:
    def test_recomputing_a_filed_year_differently_is_reported_as_drift(self):
        """A later repurchase changing a filed year must be surfaced, never silent."""
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2022, 12, 15), EventType.SELL, "100", "50"),  # loss -5000
                ev(date(2023, 1, 20), EventType.BUY, "100", "50"),  # blocks it retroactively
            ]
        )

        drifts = engine.check_closed_years({2022: {"net_gain_loss": Decimal("-5000.0000")}})

        assert len(drifts) == 1
        assert drifts[0].year == 2022
        assert drifts[0].declared_net == Decimal("-5000.0000")
        assert drifts[0].computed_net == Decimal("0.0000")

    def test_a_filed_year_that_still_matches_reports_no_drift(self):
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2022, 12, 15), EventType.SELL, "100", "50"),
            ]
        )

        assert engine.check_closed_years({2022: {"net_gain_loss": Decimal("-5000.0000")}}) == []

    def test_checking_closed_years_never_mutates_the_summaries(self):
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2022, 12, 15), EventType.SELL, "100", "50"),
            ]
        )
        before = engine.get_yearly_summary(2022).net_gain_loss

        engine.check_closed_years({2022: {"net_gain_loss": Decimal("999")}})

        assert engine.get_yearly_summary(2022).net_gain_loss == before


# =============================================================================
# Rule 4 — typed lot provenance and ESPP exemption breaches (H4, H5, H6)
# =============================================================================


class TestRule4EsppTraceability:
    def test_lot_origin_survives_the_fifo_match(self):
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2022, 1, 10), EventType.BUY, "10", "85", origin=LotOrigin.ESPP),
                ev(date(2022, 2, 10), EventType.VEST, "10", "90", origin=LotOrigin.RSU),
                ev(date(2022, 6, 10), EventType.SELL, "20", "120"),
            ]
        )

        origins = [m.origin for m in engine.processed_events[-1].fifo_matches]
        assert origins == [LotOrigin.ESPP, LotOrigin.RSU]

    def test_a_market_lot_mentioning_espp_in_its_notes_is_not_an_espp_lot(self):
        """Provenance comes from the typed origin, never from free-text notes."""
        engine = TaxEngine()
        engine.process_all(
            [
                ev(
                    date(2022, 1, 10),
                    EventType.BUY,
                    "100",
                    "85",
                    notes="Compra de mercado con fondos de una venta ESPP",
                    origin=LotOrigin.MARKET,
                ),
                ev(date(2022, 6, 10), EventType.SELL, "100", "120"),
            ]
        )

        report = detect_espp_early_sales(engine.processed_events)

        assert report.taxable_by_year == {}
        assert report.details == []

    def test_espp_lot_sold_before_36_months_breaks_the_exemption(self):
        engine = TaxEngine()
        engine.process_all(
            [
                StockEvent(
                    event_date=date(2020, 1, 15),
                    event_type=EventType.BUY,
                    shares=Decimal("100"),
                    price_usd=Decimal("85"),
                    fx_rate=Decimal("1.0"),
                    origin=LotOrigin.ESPP,
                    espp_fmv_usd=Decimal("100"),
                    espp_price_usd=Decimal("85"),
                ),
                ev(date(2022, 12, 1), EventType.SELL, "100", "120"),
            ]
        )

        report = detect_espp_early_sales(engine.processed_events)

        # Discount €15/share x 100 shares, imputed to the PURCHASE year
        # (autoliquidación complementaria), never to the savings base.
        assert report.taxable_by_year == {2020: Decimal("1500.00")}

    def test_exactly_36_months_is_enough_to_keep_the_exemption(self):
        engine = TaxEngine()
        engine.process_all(
            [
                StockEvent(
                    event_date=date(2020, 1, 15),
                    event_type=EventType.BUY,
                    shares=Decimal("100"),
                    price_usd=Decimal("85"),
                    fx_rate=Decimal("1.0"),
                    origin=LotOrigin.ESPP,
                    espp_fmv_usd=Decimal("100"),
                    espp_price_usd=Decimal("85"),
                ),
                ev(date(2023, 1, 15), EventType.SELL, "100", "120"),
            ]
        )

        assert detect_espp_early_sales(engine.processed_events).taxable_by_year == {}

    def test_leap_day_lot_needs_a_full_36_months(self):
        """29-Feb-2020 + 36 months is 28-Feb-2023, so a sale that day is still early."""
        engine = TaxEngine()
        engine.process_all(
            [
                StockEvent(
                    event_date=date(2020, 2, 29),
                    event_type=EventType.BUY,
                    shares=Decimal("100"),
                    price_usd=Decimal("85"),
                    fx_rate=Decimal("1.0"),
                    origin=LotOrigin.ESPP,
                    espp_fmv_usd=Decimal("100"),
                    espp_price_usd=Decimal("85"),
                ),
                ev(date(2023, 2, 27), EventType.SELL, "100", "120"),
            ]
        )

        assert detect_espp_early_sales(engine.processed_events).taxable_by_year == {
            2020: Decimal("1500.00")
        }

    def test_an_espp_lot_without_discount_data_raises_a_warning_instead_of_vanishing(self):
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2022, 1, 10), EventType.BUY, "100", "85", origin=LotOrigin.ESPP),
                ev(date(2022, 6, 10), EventType.SELL, "100", "120"),
            ]
        )

        report = detect_espp_early_sales(engine.processed_events)

        assert report.taxable_by_year == {}
        assert len(report.warnings) == 1
        assert "2022-01-10" in report.warnings[0]

    def test_two_espp_purchases_on_the_same_day_are_both_accounted_for(self):
        """Per-lot discount data, so same-day purchases no longer collapse."""

        def espp_lot(shares: str, fmv: str, paid: str) -> StockEvent:
            return StockEvent(
                event_date=date(2022, 1, 10),
                event_type=EventType.BUY,
                shares=Decimal(shares),
                price_usd=Decimal(paid),
                fx_rate=Decimal("1.0"),
                origin=LotOrigin.ESPP,
                espp_fmv_usd=Decimal(fmv),
                espp_price_usd=Decimal(paid),
            )

        engine = TaxEngine()
        engine.process_all(
            [
                espp_lot("100", "100", "85"),  # discount €15/share
                espp_lot("50", "200", "170"),  # discount €30/share
                ev(date(2022, 6, 10), EventType.SELL, "150", "250"),
            ]
        )

        report = detect_espp_early_sales(engine.processed_events)

        assert report.taxable_by_year == {2022: Decimal("3000.00")}

    def test_espp_breach_never_reaches_the_savings_base(self):
        engine = TaxEngine()
        engine.process_all(
            [
                StockEvent(
                    event_date=date(2022, 1, 10),
                    event_type=EventType.BUY,
                    shares=Decimal("100"),
                    price_usd=Decimal("85"),
                    fx_rate=Decimal("1.0"),
                    origin=LotOrigin.ESPP,
                    espp_fmv_usd=Decimal("100"),
                    espp_price_usd=Decimal("85"),
                ),
                ev(date(2022, 6, 10), EventType.SELL, "100", "120"),
            ]
        )

        summary = engine.get_yearly_summary(2022)
        # Capital gain is (120 - 85) x 100 = 3500; the €1500 salary discount is
        # reported separately and must not appear in the savings base.
        assert summary.total_gains == Decimal("3500.0000")


# =============================================================================
# Art. 35 LIRPF — acquisition costs belong in the cost basis (H7)
# =============================================================================


class TestAcquisitionCosts:
    def test_purchase_commission_raises_the_cost_basis(self):
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2022, 1, 10), EventType.BUY, "100", "10", fees="50"),
                ev(date(2023, 1, 10), EventType.SELL, "100", "20"),
            ]
        )

        # Proceeds 2000 - basis (1000 + 50 commission) = 950
        assert engine.get_yearly_summary(2023).total_gains == Decimal("950.0000")

    def test_acquisition_fees_are_reported_in_their_own_bucket(self):
        engine = TaxEngine()
        engine.process_all([ev(date(2022, 1, 10), EventType.BUY, "100", "10", fees="50")])

        summary = engine.get_yearly_summary(2022)
        assert summary.acquisition_fees_eur == Decimal("50.0000")
        assert summary.disposal_fees_eur == Decimal("0")

    def test_partial_sale_only_absorbs_its_share_of_the_commission(self):
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2022, 1, 10), EventType.BUY, "100", "10", fees="50"),
                ev(date(2023, 1, 10), EventType.SELL, "25", "20"),
            ]
        )

        # Proceeds 500 - basis (250 + 12.50 prorated commission) = 237.50
        assert engine.get_yearly_summary(2023).total_gains == Decimal("237.5000")

    def test_disposal_fees_still_reduce_the_gain(self):
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2022, 1, 10), EventType.BUY, "100", "10"),
                ev(date(2023, 1, 10), EventType.SELL, "100", "20", fees="40"),
            ]
        )

        summary = engine.get_yearly_summary(2023)
        assert summary.total_gains == Decimal("960.0000")
        assert summary.disposal_fees_eur == Decimal("40.0000")


# =============================================================================
# Rule 4 — the dashboard must classify lots the same way the tax rules do
# =============================================================================


class TestRule4Dashboard:
    def _engine_with(self, *events: StockEvent) -> TaxEngine:
        engine = TaxEngine()
        engine.process_all(list(events))
        return engine

    def test_tracker_ignores_a_market_lot_whose_notes_mention_espp(self):
        from tax_engine.dashboard_helpers import build_unsold_lots_and_espp_tracker

        engine = self._engine_with(
            ev(
                date(2022, 1, 10),
                EventType.BUY,
                "100",
                "85",
                notes="Compra de mercado tras vender ESPP",
                origin=LotOrigin.MARKET,
            )
        )

        _, espp_lots = build_unsold_lots_and_espp_tracker(engine, date(2023, 1, 1))

        assert espp_lots == []

    def test_tracker_counts_36_months_date_to_date_for_a_leap_day_lot(self):
        from tax_engine.dashboard_helpers import build_unsold_lots_and_espp_tracker

        engine = self._engine_with(
            StockEvent(
                event_date=date(2020, 2, 29),
                event_type=EventType.BUY,
                shares=Decimal("100"),
                price_usd=Decimal("85"),
                fx_rate=Decimal("1.0"),
                origin=LotOrigin.ESPP,
                espp_fmv_usd=Decimal("100"),
                espp_price_usd=Decimal("85"),
            )
        )

        _, espp_lots = build_unsold_lots_and_espp_tracker(engine, date(2020, 3, 1))

        assert len(espp_lots) == 1
        assert espp_lots[0]["unlock_date"] == "2023-02-28"
        # 100 shares x €15 discount still exposed if sold today.
        assert espp_lots[0]["discount_at_risk"] == pytest.approx(1500.0)


# =============================================================================
# Rule 1 — single-security mode must not silently pool different securities
# =============================================================================


class TestSingleSecurityGuard:
    def test_mixing_two_tickers_without_portfolio_mode_is_refused(self):
        from tax_engine.cli_main import build_portfolio_or_engine

        events = [
            ev(date(2022, 1, 10), EventType.BUY, "10", "10", symbol="ACME"),
            ev(date(2022, 2, 10), EventType.BUY, "10", "10", symbol="OTHER"),
        ]

        with pytest.raises(AmbiguousSecurityError, match="ACME"):
            build_portfolio_or_engine(events, [], all_securities=False)


# =============================================================================
# Rule 3 — the release policy must reach every per-security engine
# =============================================================================


class TestReleasePolicyPropagation:
    def test_run_portfolio_forwards_the_release_policy(self):
        events = [
            ev(date(2021, 1, 10), EventType.BUY, "100", "100", symbol="ACME"),
            ev(date(2022, 5, 10), EventType.SELL, "100", "50", symbol="ACME"),
            ev(date(2022, 5, 20), EventType.BUY, "100", "50", symbol="ACME"),
            ev(date(2023, 4, 10), EventType.BUY, "500", "50", symbol="ACME"),
            ev(date(2023, 6, 10), EventType.SELL, "100", "50", symbol="ACME"),
        ]

        strict = run_portfolio(list(events), release_policy="position_zero")
        loose = run_portfolio(list(events), release_policy="per_lot")

        assert strict.aggregate.yearly_summaries[2023].unlocked_historical_losses == Decimal("0")
        assert loose.aggregate.yearly_summaries[2023].unlocked_historical_losses == Decimal(
            "-5000.0000"
        )


# =============================================================================
# Rule 3 — closed years are declared on disk, never inferred
# =============================================================================


class TestClosedYearsFile:
    def test_loader_reads_declared_years(self, tmp_path: Path):
        from tax_engine.cli_main import load_closed_years

        (tmp_path / "closed_years.json").write_text(
            '{"2022": {"net_gain_loss": "-5000.00"}, "2021": {"net_gain_loss": "120.00"}}'
        )

        declared = load_closed_years(tmp_path / "closed_years.json")

        assert declared == {
            2021: {"net_gain_loss": Decimal("120.00")},
            2022: {"net_gain_loss": Decimal("-5000.00")},
        }

    def test_a_bare_number_is_read_as_the_net_result(self, tmp_path: Path):
        from tax_engine.cli_main import load_closed_years

        (tmp_path / "closed_years.json").write_text('{"2022": "-5000.00"}')

        assert load_closed_years(tmp_path / "closed_years.json") == {
            2022: {"net_gain_loss": Decimal("-5000.00")}
        }

    def test_a_missing_file_declares_nothing(self, tmp_path: Path):
        from tax_engine.cli_main import load_closed_years

        assert load_closed_years(tmp_path / "nope.json") == {}


class TestEsppDiscountsFromLots:
    """The exempt discount is derived from the same lots the tax rules read.

    The charts used to rebuild it from a hardcoded table, which could disagree
    with what the engine actually held.
    """

    def test_discount_is_summed_per_purchase_year_from_the_lots(self):
        from tax_engine.dashboard_helpers import espp_discounts_from_lots

        engine = TaxEngine()
        engine.process_all(
            [
                StockEvent(
                    event_date=date(2021, 5, 28),
                    event_type=EventType.BUY,
                    shares=Decimal("50"),
                    price_usd=Decimal("60"),
                    fx_rate=Decimal("0.80"),
                    origin=LotOrigin.ESPP,
                    espp_fmv_usd=Decimal("60"),
                    espp_price_usd=Decimal("51"),
                ),
                ev(date(2021, 6, 1), EventType.BUY, "10", "60"),  # market lot, ignored
            ]
        )

        # (60 - 51) x 50 x 0.80 = €360.00
        assert espp_discounts_from_lots(engine) == {2021: Decimal("360.00")}

    def test_a_lot_without_discount_data_contributes_nothing(self):
        from tax_engine.dashboard_helpers import espp_discounts_from_lots

        engine = TaxEngine()
        engine.process_all(
            [ev(date(2021, 5, 28), EventType.BUY, "50", "60", origin=LotOrigin.ESPP)]
        )

        assert espp_discounts_from_lots(engine) == {}


class TestAggregateLotLedger:
    def test_the_portfolio_aggregate_keeps_every_lot_ever_acquired(self):
        """Lot-level analysis (ESPP, holdings) must work on the rollup too.

        ``state.lots`` is the live portfolio and is emptied on a full liquidation,
        so anything reading provenance needs the durable ledger as well.
        """
        events = [
            ev(date(2021, 1, 10), EventType.BUY, "10", "10", symbol="AAA"),
            ev(date(2021, 2, 10), EventType.BUY, "10", "10", symbol="BBB"),
            ev(date(2022, 1, 10), EventType.SELL, "10", "20", symbol="AAA"),
        ]

        aggregate = run_portfolio(events).aggregate

        assert len(aggregate.lot_ledger) == 2
        # AAA was fully liquidated, so it is gone from the live portfolio...
        assert len(aggregate.state.lots) == 1
        # ...but still on record in the ledger.
        assert sorted(lot.acquisition_date for lot in aggregate.lot_ledger) == [
            date(2021, 1, 10),
            date(2021, 2, 10),
        ]


# =============================================================================
# Art. 33.5 LIRPF — release requires a DEFINITIVE transmission
# =============================================================================


class TestDefinitiveTransmissionPolicy:
    """The statutory release rule, as the DGT reads it.

    Art. 33.5 (final paragraph): the deferred losses "se integrarán a medida que
    se transmitan los valores o participaciones que permanezcan en el patrimonio
    del contribuyente" — progressively, not only on a full liquidation.

    But the doctrine adds a condition: each of those later transmissions must
    itself be DEFINITIVE, i.e. no homogeneous securities are repurchased within
    the two months following it. Otherwise the same anti-avoidance logic applies
    again and nothing is freed.
    """

    def _events(self, *extra: StockEvent) -> list[StockEvent]:
        return [
            ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
            ev(date(2022, 5, 10), EventType.SELL, "100", "50"),  # loss -5000
            ev(date(2022, 5, 20), EventType.BUY, "100", "50"),  # blocks it
            *extra,
        ]

    def test_a_definitive_sale_releases_without_liquidating_the_position(self):
        """Half the replacement sold cleanly: that half's loss is freed."""
        engine = TaxEngine(release_policy="definitive")
        engine.process_all(
            self._events(
                ev(date(2023, 3, 1), EventType.BUY, "400", "50"),  # unrelated holding
                ev(date(2023, 9, 10), EventType.SELL, "450", "50"),  # clean: no buy after
            )
        )

        assert engine.state.total_shares == Decimal("50")  # position still open
        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-5000.0000")

    def test_a_repurchase_within_two_months_makes_the_sale_non_definitive(self):
        engine = TaxEngine(release_policy="definitive")
        engine.process_all(
            self._events(
                ev(date(2023, 6, 10), EventType.SELL, "100", "50"),  # sells replacement
                ev(date(2023, 7, 5), EventType.BUY, "100", "50"),  # 25 days later
            )
        )

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("0")
        assert engine.get_yearly_summary(2022).blocked_losses == Decimal("-5000.0000")

    def test_a_repurchase_just_outside_the_window_leaves_the_sale_definitive(self):
        engine = TaxEngine(release_policy="definitive")
        engine.process_all(
            self._events(
                ev(date(2023, 6, 10), EventType.SELL, "100", "50"),
                ev(date(2023, 8, 11), EventType.BUY, "100", "50"),  # 2 months + 1 day
            )
        )

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-5000.0000")

    def test_a_vest_blocks_only_the_shares_it_actually_replaces(self):
        """Vestings are acquisitions like any other — and they block pro rata.

        Selling the 100 replacement shares with only 20 vesting inside the window
        means 80 were definitively transmitted: their 80% of the deferral is
        integrated, and only the 20 shares' worth rolls on to the vested lot.
        """
        engine = TaxEngine(release_policy="definitive")
        engine.process_all(
            self._events(
                ev(date(2023, 6, 10), EventType.SELL, "100", "50"),
                ev(date(2023, 7, 15), EventType.VEST, "20", "50"),
            )
        )

        # 80/100 of -5000 integrated; the remaining -1000 rides on the vested shares.
        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-4000.0000")
        assert engine.get_yearly_summary(2022).blocked_losses == Decimal("-5000.0000")
        assert sum(
            (lot.deferred_wash_sale_loss for lot in engine.lot_ledger), Decimal("0")
        ) == Decimal("-1000.0000")

    def test_a_rolled_deferral_is_freed_by_the_later_clean_exit(self):
        """A deferral survives a non-definitive round trip and is freed later.

        Sale 1 (50 shares) is partly replaced by a 10-share vest: 40 shares' worth
        (-2000) is integrated at once and 10 shares' worth (-500) rolls onto the
        vested lot. Sale 2 then clears the remaining 50 replacement shares (-2500)
        AND the 10 vested ones (-500), with nothing bought afterwards. By the end
        of 2023 the whole -5000 has been integrated and nothing stays deferred.
        """
        engine = TaxEngine(release_policy="definitive")
        engine.process_all(
            self._events(
                ev(date(2023, 3, 10), EventType.SELL, "50", "50"),  # half the replacement
                ev(date(2023, 3, 20), EventType.VEST, "10", "50"),  # replaces 10 of them
                ev(date(2023, 9, 10), EventType.SELL, "60", "50"),  # rest, clean exit
            )
        )

        assert engine.get_yearly_summary(2022).blocked_losses == Decimal("-5000.0000")
        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-5000.0000")
        assert engine.state.total_shares == Decimal("0")
        assert all(lot.deferred_wash_sale_loss == Decimal("0") for lot in engine.lot_ledger), (
            "a clean exit must leave nothing deferred"
        )

    def test_the_release_is_dated_to_the_transmission_not_the_window_end(self):
        """'A medida que se transmitan' — the transmission is the taxable event."""
        engine = TaxEngine(release_policy="definitive")
        engine.process_all(self._events(ev(date(2023, 12, 10), EventType.SELL, "100", "50")))

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-5000.0000")
        assert engine.get_yearly_summary(2024) is None


class TestClosedYearForfeits:
    """A closed year that already deducted its blocked losses cannot release them again.

    If the return as filed took the loss as deductible, the deferral the engine now
    computes for that year was, in practice, already used. Integrating its later
    release would deduct the same loss twice — so releases attributed to that origin
    are forfeited, up to the amount the filed return already absorbed.
    """

    HISTORY = [
        ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
        ev(date(2022, 5, 10), EventType.SELL, "100", "50"),  # loss -5000
        ev(date(2022, 5, 20), EventType.BUY, "100", "50"),  # blocks it
        ev(date(2023, 9, 10), EventType.SELL, "100", "50"),  # definitive -> releases
    ]

    def test_without_a_declaration_the_release_stands(self):
        engine = TaxEngine()
        engine.process_all(list(self.HISTORY))

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-5000.0000")

    def test_a_year_filed_with_the_loss_deducted_forfeits_its_release(self):
        engine = TaxEngine()
        engine.process_all(list(self.HISTORY))
        # As filed, 2022 reported no blocked loss: the -5000 was taken that year.
        engine.closed_years = {2022: {"blocked_losses": Decimal("0.00")}}

        engine.apply_closed_year_forfeits()

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("0")
        assert engine.get_yearly_summary(2023).unlocked_losses_by_origin == {}

    def test_a_year_filed_as_blocked_keeps_its_release(self):
        engine = TaxEngine()
        engine.process_all(list(self.HISTORY))
        engine.closed_years = {2022: {"blocked_losses": Decimal("-5000.00")}}

        engine.apply_closed_year_forfeits()

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-5000.0000")

    def test_a_partial_declaration_forfeits_only_the_part_already_deducted(self):
        engine = TaxEngine()
        engine.process_all(list(self.HISTORY))
        # Filed as 3000 blocked, so 2000 of the deferral was deducted in 2022.
        engine.closed_years = {2022: {"blocked_losses": Decimal("-3000.00")}}

        engine.apply_closed_year_forfeits()

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-3000.0000")

    def test_applying_it_twice_changes_nothing(self):
        engine = TaxEngine()
        engine.process_all(list(self.HISTORY))
        engine.closed_years = {2022: {"blocked_losses": Decimal("0.00")}}

        engine.apply_closed_year_forfeits()
        engine.apply_closed_year_forfeits()

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("0")


class TestForfeitsAreOptIn:
    """Forfeiting a release is a reconciliation choice, not a rule of law.

    Art. 122.2 LGT settles an error in a non-prescribed year by regularising THAT
    year, not by netting it against a later one. So the engine detects the
    situation and lets the caller decide; it never forfeits on its own.
    """

    HISTORY = [
        ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
        ev(date(2022, 5, 10), EventType.SELL, "100", "50"),
        ev(date(2022, 5, 20), EventType.BUY, "100", "50"),
        ev(date(2023, 9, 10), EventType.SELL, "100", "50"),
    ]

    def _engine(self) -> TaxEngine:
        engine = TaxEngine()
        engine.process_all(list(self.HISTORY))
        engine.closed_years = {2022: {"blocked_losses": Decimal("0.00")}}
        return engine

    def test_setting_closed_years_does_not_forfeit_anything_by_itself(self):
        engine = self._engine()

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-5000.0000")

    def test_the_conflict_is_reported_without_changing_any_figure(self):
        engine = self._engine()

        pending = engine.releases_already_deducted()

        assert pending == {2022: Decimal("5000.00")}
        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-5000.0000")

    def test_nothing_is_reported_when_the_year_was_filed_as_blocked(self):
        engine = TaxEngine()
        engine.process_all(list(self.HISTORY))
        engine.closed_years = {2022: {"blocked_losses": Decimal("-5000.00")}}

        assert engine.releases_already_deducted() == {}


class TestClosedYearsFileAnnotations:
    """``closed_years.json`` is hand-edited, so it has to tolerate notes.

    It records why each year is declared the way it is — which matters here,
    because a year that OMITTED its losses looks nothing like one that deducted
    them, and only a human can say which happened.
    """

    def test_underscore_keys_are_treated_as_comments(self, tmp_path: Path):
        from tax_engine.cli_main import load_closed_years

        (tmp_path / "closed_years.json").write_text(
            '{"_nota": "2025 refleja el estado previsto tras la complementaria",'
            ' "2025": {"net_gain_loss": "6276.42"}}'
        )

        assert load_closed_years(tmp_path / "closed_years.json") == {
            2025: {"net_gain_loss": Decimal("6276.42")}
        }

    def test_a_genuinely_malformed_year_still_fails_loudly(self, tmp_path: Path):
        from tax_engine.cli_main import load_closed_years

        (tmp_path / "closed_years.json").write_text('{"dosmilveinticinco": "100"}')

        with pytest.raises(ValueError):
            load_closed_years(tmp_path / "closed_years.json")


class TestForfeitsReachThePerSecurityView:
    """A forfeited release must disappear from the per-security tables too.

    In portfolio mode the rollup and the per-security engines hold *separate*
    summary objects. The forfeit budget is portfolio-level (it compares the
    declared return against the whole base del ahorro), so it can only be
    computed on the rollup — but if it is applied there alone, the per-security
    view keeps counting a deduction the taxpayer has renounced, and the report's
    portfolio table stops tying to its own Modelo 100 tables.
    """

    # AAA realises a loss in 2022, blocks it with a repurchase, and frees it in
    # 2023. BBB is an unrelated security, present so portfolio mode is in play.
    HISTORY = [
        ev(date(2021, 1, 10), EventType.BUY, "100", "100", isin="AAA", symbol="AAA"),
        ev(date(2022, 5, 10), EventType.SELL, "100", "50", isin="AAA", symbol="AAA"),
        ev(date(2022, 5, 20), EventType.BUY, "100", "50", isin="AAA", symbol="AAA"),
        ev(date(2023, 9, 10), EventType.SELL, "100", "50", isin="AAA", symbol="AAA"),
        ev(date(2022, 3, 1), EventType.BUY, "10", "10", isin="BBB", symbol="BBB"),
        ev(date(2023, 3, 1), EventType.SELL, "10", "20", isin="BBB", symbol="BBB"),
    ]

    def _portfolio(self):
        portfolio = run_portfolio(list(self.HISTORY))
        # As filed, 2022 reported no blocked loss: the -5000 was taken that year.
        portfolio.aggregate.closed_years = {2022: {"blocked_losses": Decimal("0.00")}}
        return portfolio

    def test_the_per_security_engine_drops_the_forfeited_release(self):
        portfolio = self._portfolio()

        portfolio.aggregate.apply_closed_year_forfeits(
            mirror_engines=[r.engine for r in portfolio.results]
        )

        aaa = next(r.engine for r in portfolio.results if r.security.ticker == "AAA")
        assert aaa.get_yearly_summary(2023).unlocked_historical_losses == Decimal("0")

    def test_the_per_security_deductible_losses_still_sum_to_the_rollup(self):
        portfolio = self._portfolio()

        portfolio.aggregate.apply_closed_year_forfeits(
            mirror_engines=[r.engine for r in portfolio.results]
        )

        per_security = sum(
            (
                s.deductible_losses
                for r in portfolio.results
                for s in r.engine.get_all_yearly_summaries()
            ),
            Decimal("0"),
        )
        rollup = sum(
            (s.deductible_losses for s in portfolio.aggregate.get_all_yearly_summaries()),
            Decimal("0"),
        )
        assert per_security == rollup

    def test_the_rollup_still_forfeits_the_same_amount(self):
        portfolio = self._portfolio()

        forfeited = portfolio.aggregate.apply_closed_year_forfeits(
            mirror_engines=[r.engine for r in portfolio.results]
        )

        assert forfeited == {2022: Decimal("5000.00")}
        assert portfolio.aggregate.get_yearly_summary(2023).unlocked_historical_losses == (
            Decimal("0")
        )

    def test_mirroring_twice_changes_nothing(self):
        portfolio = self._portfolio()
        mirrors = [r.engine for r in portfolio.results]

        portfolio.aggregate.apply_closed_year_forfeits(mirror_engines=mirrors)
        portfolio.aggregate.apply_closed_year_forfeits(mirror_engines=mirrors)

        aaa = next(r.engine for r in portfolio.results if r.security.ticker == "AAA")
        assert aaa.get_yearly_summary(2023).unlocked_historical_losses == Decimal("0")

    def test_the_amount_forfeited_is_recorded_on_the_engine(self):
        portfolio = self._portfolio()

        portfolio.aggregate.apply_closed_year_forfeits(
            mirror_engines=[r.engine for r in portfolio.results]
        )

        assert portfolio.aggregate.forfeited_releases == {2022: Decimal("5000.00")}
