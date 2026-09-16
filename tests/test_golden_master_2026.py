"""
Golden-master regression suite: the 2026 total-liquidation scenario.

One dataset, audited against the four critical rules. Every figure below is
derived by hand in the comments so a reviewer can check the engine rather than
trust it.

Timeline (single security, EUR prices via fx=1.0 so the arithmetic is visible):

    2024-01-15  BUY   1000 @ €50.00, €100.00 acquisition commission
    2025-06-10  SELL   400 @ €30.00                      -> loss  -8,040.00
    2025-07-01  BUY    400 @ €30.00                      -> blocks that loss
    2025-12-05  ESPP   100 @ FMV €40.00 (paid €34.00)    -> €600 exempt discount
    2026-09-30  SELL  1100 @ €45.00, €55.00 sale fees    -> position hits 0,00
    2026-12-05  VEST    50 @ €60.00                      -> AFTER the quarantine
"""

from datetime import date
from decimal import Decimal

import pytest

from tax_engine.cli_main import detect_espp_early_sales, load_closed_years
from tax_engine.models import EventType, LotOrigin, StockEvent
from tax_engine.tax_engine import TaxEngine

FX = Decimal("1.0")


def _buy(day: date, shares: str, price: str, fees: str = "0") -> StockEvent:
    return StockEvent(
        event_date=day,
        event_type=EventType.BUY,
        shares=Decimal(shares),
        price_usd=Decimal(price),
        fx_rate=FX,
        fees_usd=Decimal(fees),
        notes="Market buy",
        symbol="ACME",
    )


def _sell(day: date, shares: str, price: str, fees: str = "0") -> StockEvent:
    return StockEvent(
        event_date=day,
        event_type=EventType.SELL,
        shares=Decimal(shares),
        price_usd=Decimal(price),
        fx_rate=FX,
        fees_usd=Decimal(fees),
        notes="Sell",
        symbol="ACME",
    )


def _espp(day: date, shares: str, fmv: str, paid: str) -> StockEvent:
    """An ESPP purchase: cost basis is the FMV, the discount is the taxable part."""
    return StockEvent(
        event_date=day,
        event_type=EventType.BUY,
        shares=Decimal(shares),
        price_usd=Decimal(fmv),
        fx_rate=FX,
        origin=LotOrigin.ESPP,
        espp_fmv_usd=Decimal(fmv),
        espp_price_usd=Decimal(paid),
        notes="ESPP Purchase",
        symbol="ACME",
    )


def _vest(day: date, shares: str, price: str) -> StockEvent:
    return StockEvent(
        event_date=day,
        event_type=EventType.VEST,
        shares=Decimal(shares),
        price_usd=Decimal(price),
        fx_rate=FX,
        notes="RSU Vest",
        symbol="ACME",
    )


#: Everything the taxpayer had filed by 31/12/2025.
HISTORY_THROUGH_2025 = [
    _buy(date(2024, 1, 15), "1000", "50.00", fees="100.00"),
    _sell(date(2025, 6, 10), "400", "30.00"),
    _buy(date(2025, 7, 1), "400", "30.00"),
    _espp(date(2025, 12, 5), "100", "40.00", "34.00"),
]

#: 2026: full liquidation on 30/09, then an RSU vest AFTER the quarantine closes.
YEAR_2026 = [
    _sell(date(2026, 9, 30), "1100", "45.00", fees="55.00"),
    _vest(date(2026, 12, 5), "50", "60.00"),
]


def _run(events: list[StockEvent], **kwargs: str) -> TaxEngine:
    engine = TaxEngine(**kwargs)  # type: ignore[arg-type]
    engine.process_all(list(events))
    return engine


def _event_on(engine: TaxEngine, day: date, kind: EventType):
    """The processed event of a given type on a given date (positions are brittle)."""
    return next(
        pe
        for pe in engine.processed_events
        if pe.event.event_date == day and pe.event.event_type is kind
    )


# =============================================================================
# RULE 1 — closed-year immutability and drift alerting
# =============================================================================


class TestRule1ClosedYearImmutability:
    def test_2025_is_identical_before_and_after_2026_data_arrives(self):
        """The filed 2025 figures must survive loading a later year unchanged."""
        filed = _run(list(HISTORY_THROUGH_2025)).get_yearly_summary(2025)
        # 400 sold at €30 against a €50.10 basis (commission included) = -8,040.00
        assert filed.total_losses == Decimal("-8040.0000")
        assert filed.blocked_losses == Decimal("-8040.0000")
        assert filed.net_gain_loss == Decimal("0.0000")

        reran = _run(HISTORY_THROUGH_2025 + YEAR_2026).get_yearly_summary(2025)

        assert reran.total_gains == filed.total_gains
        assert reran.total_losses == filed.total_losses
        assert reran.blocked_losses == filed.blocked_losses
        assert reran.net_gain_loss == filed.net_gain_loss

    def test_a_january_repurchase_that_moves_2025_is_reported_as_drift(self):
        """Art. 33.5.f can legitimately change a filed year — it must never be silent."""
        # A November 2025 loss with no replacement: fully deductible when filed.
        history = [
            _buy(date(2024, 1, 15), "1000", "50.00"),
            _sell(date(2025, 11, 20), "400", "30.00"),
        ]
        as_filed = _run(list(history)).get_yearly_summary(2025)
        assert as_filed.net_gain_loss == Decimal("-8000.0000")

        # The taxpayer then buys back in January 2026, inside the 2-month window.
        engine = _run(history + [_buy(date(2026, 1, 15), "400", "30.00")])

        drifts = engine.check_closed_years(
            {2025: {"net_gain_loss": Decimal("-8000.0000"), "blocked_losses": Decimal("0")}}
        )

        assert {d.field for d in drifts} == {"net_gain_loss", "blocked_losses"}
        by_field = {d.field: d for d in drifts}
        assert by_field["net_gain_loss"].computed == Decimal("0.0000")
        assert by_field["blocked_losses"].computed == Decimal("-8000.0000")

    def test_the_drift_check_does_not_mutate_the_year_it_inspects(self):
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)
        before = engine.get_yearly_summary(2025)
        snapshot = (before.total_gains, before.total_losses, before.blocked_losses)

        engine.check_closed_years({2025: {"net_gain_loss": Decimal("99999")}})

        after = engine.get_yearly_summary(2025)
        assert (after.total_gains, after.total_losses, after.blocked_losses) == snapshot

    def test_a_year_matching_what_was_filed_raises_no_alert(self):
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)

        assert (
            engine.check_closed_years(
                {
                    2025: {
                        "net_gain_loss": Decimal("0.0000"),
                        "blocked_losses": Decimal("-8040.0000"),
                    }
                }
            )
            == []
        )

    def test_closed_years_are_declared_on_disk_not_inferred(self, tmp_path):
        path = tmp_path / "closed_years.json"
        path.write_text('{"2025": {"net_gain_loss": "0.00", "blocked_losses": "-8040.00"}}')

        declared = load_closed_years(path)

        assert declared == {
            2025: {"net_gain_loss": Decimal("0.00"), "blocked_losses": Decimal("-8040.00")}
        }
        assert _run(HISTORY_THROUGH_2025 + YEAR_2026).check_closed_years(declared) == []


# =============================================================================
# RULE 2 — the carried loss unlocks on the clean 2026 exit
# =============================================================================


class TestRule2QuarantineRelease:
    def test_quarantine_runs_from_30_sep_to_30_nov_2026(self):
        from tax_engine.dates import add_months

        assert add_months(date(2026, 9, 30), 2) == date(2026, 11, 30)

    def test_the_position_actually_reaches_zero_on_30_sep_2026(self):
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)

        assert date(2026, 9, 30) in engine._zero_position_dates()

    def test_a_vest_after_the_quarantine_does_not_block_the_release(self):
        """5-Dec-2026 is outside the 30-Sep -> 30-Nov window, so the exit stands."""
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)

        assert engine.get_yearly_summary(2026).unlocked_historical_losses == Decimal("-8040.0000")
        assert engine.get_yearly_summary(2026).unlocked_losses_by_origin == {
            2025: Decimal("-8040.0000")
        }

    def test_the_whole_carried_backpack_becomes_deductible_in_2026(self):
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)
        summary = engine.get_yearly_summary(2026)

        # Sale result: lot A 600 sh -> -3,060.00 | lot B 400 sh -> +6,000.00
        #              ESPP lot 100 sh -> +500.00 | less €55.00 sale fees = +3,385.00
        assert summary.total_gains == Decimal("3385.0000")
        assert summary.blocked_losses == Decimal("0")
        assert summary.deductible_losses == Decimal("-8040.0000")
        assert summary.net_gain_loss == Decimal("-4655.0000")
        assert summary.taxable_gain == Decimal("0")

    def test_nothing_of_the_backpack_is_left_deferred(self):
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)

        assert all(lot.deferred_wash_sale_loss == Decimal("0") for lot in engine.lot_ledger), (
            "the 2025 deferral must be fully released once the clean exit completes"
        )

    def test_a_vest_inside_the_quarantine_would_keep_the_loss_blocked(self):
        """Control case (conservative policy): a vest on 15-Nov-2026 voids the exit."""
        engine = _run(
            HISTORY_THROUGH_2025
            + [_sell(date(2026, 9, 30), "1100", "45.00", fees="55.00")]
            + [_vest(date(2026, 11, 15), "50", "60.00")],
            release_policy="position_zero",
        )

        assert engine.get_yearly_summary(2026).unlocked_historical_losses == Decimal("0")
        assert engine.get_yearly_summary(2025).blocked_losses == Decimal("-8040.0000")


# =============================================================================
# RULE 3 — acquisition fees prorated per share, disposal fees off the proceeds
# =============================================================================


class TestRule3FeeIntegrity:
    def test_acquisition_commission_is_prorated_over_the_fraction_consumed(self):
        """€100 on 1,000 shares = €0.10/share; 400 shares carry exactly €40.00."""
        engine = _run(list(HISTORY_THROUGH_2025))
        sale = _event_on(engine, date(2025, 6, 10), EventType.SELL)

        assert sale.fifo_matches[0].shares == Decimal("400")
        # Proceeds 12,000.00 - (400 x €50.00 + €40.00 commission) = -8,040.00
        assert sale.fifo_matches[0].realized_gain_loss == Decimal("-8040.0000")
        assert sale.realized_gain_loss == Decimal("-8040.0000")

    def test_the_rest_of_the_commission_stays_with_the_unsold_shares(self):
        """The remaining 600 shares still carry the other €60.00."""
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)
        liquidation = _event_on(engine, date(2026, 9, 30), EventType.SELL)

        first_lot = liquidation.fifo_matches[0]
        assert first_lot.shares == Decimal("600")
        # 600 x €45.00 - (600 x €50.00 + €60.00) = -3,060.00
        assert first_lot.realized_gain_loss == Decimal("-3060.0000")

    def test_acquisition_and_disposal_fees_are_reported_in_separate_buckets(self):
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)

        assert engine.get_yearly_summary(2024).acquisition_fees_eur == Decimal("100.0000")
        assert engine.get_yearly_summary(2024).disposal_fees_eur == Decimal("0")
        assert engine.get_yearly_summary(2026).disposal_fees_eur == Decimal("55.0000")
        assert engine.get_yearly_summary(2026).acquisition_fees_eur == Decimal("0")

    def test_disposal_fees_reduce_the_transmission_value(self):
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)
        liquidation = _event_on(engine, date(2026, 9, 30), EventType.SELL)

        gross = sum(m.realized_gain_loss for m in liquidation.fifo_matches)
        assert gross == Decimal("3440.0000")
        assert liquidation.realized_gain_loss == gross - Decimal("55.0000")

    def test_the_acquisition_commission_is_never_double_counted(self):
        """It raises the basis once; it must not also be expensed against the gain."""
        with_fee = _run(HISTORY_THROUGH_2025 + YEAR_2026)
        without_fee = _run(
            [
                _buy(date(2024, 1, 15), "1000", "50.00"),  # same lot, no commission
                *HISTORY_THROUGH_2025[1:],
                *YEAR_2026,
            ]
        )

        total_with = sum(
            s.total_gains + s.total_losses for s in with_fee.get_all_yearly_summaries()
        )
        total_without = sum(
            s.total_gains + s.total_losses for s in without_fee.get_all_yearly_summaries()
        )
        assert total_without - total_with == Decimal("100.0000")


# =============================================================================
# RULE 4 — the ESPP breach never touches the savings base
# =============================================================================


class TestRule4EsppSeparation:
    def test_the_december_2025_espp_lot_is_consumed_before_36_months(self):
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)
        liquidation = _event_on(engine, date(2026, 9, 30), EventType.SELL)

        espp_matches = [m for m in liquidation.fifo_matches if m.origin is LotOrigin.ESPP]
        assert len(espp_matches) == 1
        assert espp_matches[0].shares == Decimal("100")
        assert espp_matches[0].acquisition_date == date(2025, 12, 5)
        # Sold 2026-09-30, i.e. ~10 months in — far short of 36.
        assert date(2026, 9, 30) < date(2028, 12, 5)

    def test_the_breach_is_an_isolated_event_imputed_to_the_purchase_year(self):
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)

        report = detect_espp_early_sales(engine.processed_events)

        # (€40.00 FMV - €34.00 paid) x 100 shares = €600.00 of salary income, 2025.
        assert report.taxable_by_year == {2025: Decimal("600.00")}
        assert report.warnings == []
        assert len(report.details) == 1

    def test_the_600_euro_discount_is_absent_from_every_savings_base_figure(self):
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)

        for summary in engine.get_all_yearly_summaries():
            for field in (
                summary.total_gains,
                summary.total_losses,
                summary.blocked_losses,
                summary.unlocked_historical_losses,
                summary.net_gain_loss,
            ):
                assert abs(field) != Decimal("600.0000")

        # 2025 stays exactly as filed: the breach is a Rendimientos del Trabajo
        # matter, settled through a complementaria, not a savings-base adjustment.
        assert engine.get_yearly_summary(2025).net_gain_loss == Decimal("0.0000")
        # 2026's capital result is the sale's own arithmetic, discount excluded.
        assert engine.get_yearly_summary(2026).total_gains == Decimal("3385.0000")

    def test_the_espp_lot_contributes_its_capital_gain_only(self):
        """The ESPP shares still produce a normal capital gain: basis = FMV."""
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)
        liquidation = _event_on(engine, date(2026, 9, 30), EventType.SELL)

        espp_match = next(m for m in liquidation.fifo_matches if m.origin is LotOrigin.ESPP)
        # 100 x (€45.00 - €40.00) = €500.00 — the €600 discount is NOT netted here.
        assert espp_match.realized_gain_loss == Decimal("500.0000")

    def test_the_carryforward_ledger_ignores_the_espp_discount(self):
        engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)

        ledger = engine.compute_carryforward()
        rows = {r.year: r for r in ledger.rows}

        assert rows[2026].net_result == Decimal("-4655.0000")
        assert rows[2026].taxable_after == Decimal("0")
        assert [(y, amount) for y, amount, _ in ledger.pending_end] == [
            (2026, Decimal("4655.0000"))
        ]


# =============================================================================
# Golden master — the whole scenario in one frozen table
# =============================================================================


GOLDEN_MASTER = {
    2024: {"gains": "0", "losses": "0", "blocked": "0", "unlocked": "0", "net": "0"},
    2025: {
        "gains": "0",
        "losses": "-8040.0000",
        "blocked": "-8040.0000",
        "unlocked": "0",
        "net": "0.0000",
    },
    2026: {
        "gains": "3385.0000",
        "losses": "0",
        "blocked": "0",
        "unlocked": "-8040.0000",
        "net": "-4655.0000",
    },
}


@pytest.mark.parametrize("year", sorted(GOLDEN_MASTER))
def test_golden_master(year: int):
    engine = _run(HISTORY_THROUGH_2025 + YEAR_2026)
    summary = engine.get_yearly_summary(year)
    expected = GOLDEN_MASTER[year]

    assert summary.total_gains == Decimal(expected["gains"])
    assert summary.total_losses == Decimal(expected["losses"])
    assert summary.blocked_losses == Decimal(expected["blocked"])
    assert summary.unlocked_historical_losses == Decimal(expected["unlocked"])
    assert summary.net_gain_loss == Decimal(expected["net"])


# =============================================================================
# RULE 1 (freeze) — the ledgers must carry what was FILED, not what is recomputed
# =============================================================================


class TestClosedYearFreeze:
    """A closed year's declared result is what feeds the carry-forward pool.

    Reporting the drift is not enough on its own: until the taxpayer files a
    rectificativa, the loss they actually declared is still the loss they are
    entitled to carry forward. Recomputing it away would silently destroy it.
    """

    HISTORY = [
        _buy(date(2024, 1, 15), "1000", "50.00"),
        _sell(date(2025, 11, 20), "400", "30.00"),  # -8,000.00, deductible as filed
    ]
    REPURCHASE = _buy(date(2026, 1, 15), "400", "30.00")  # blocks it retroactively
    DECLARED = {2025: {"net_gain_loss": Decimal("-8000.0000")}}

    def test_the_filed_loss_stays_in_the_carryforward_pool(self):
        engine = _run(self.HISTORY + [self.REPURCHASE])

        ledger = engine.compute_carryforward(closed_years=self.DECLARED)

        assert [(y, amount) for y, amount, _ in ledger.pending_end] == [
            (2025, Decimal("8000.0000"))
        ]

    def test_the_closed_year_row_reports_what_was_declared(self):
        engine = _run(self.HISTORY + [self.REPURCHASE])

        rows = {r.year: r for r in engine.compute_carryforward(closed_years=self.DECLARED).rows}

        assert rows[2025].net_result == Decimal("-8000.0000")
        assert rows[2025].new_loss_carried == Decimal("8000.0000")

    def test_the_savings_ledger_also_honours_the_declared_year(self):
        engine = _run(self.HISTORY + [self.REPURCHASE])

        ledger = engine.compute_savings_ledger({}, closed_years=self.DECLARED)
        rows = {r.year: r for r in ledger.rows}

        assert rows[2025].gp_net == Decimal("-8000.0000")
        assert [(y, amount) for y, amount, _ in ledger.gp_pending_end] == [
            (2025, Decimal("8000.0000"))
        ]

    def test_open_years_are_untouched_by_the_freeze(self):
        engine = _run(self.HISTORY + [self.REPURCHASE])

        ledger = engine.compute_carryforward(closed_years=self.DECLARED)
        rows = {r.year: r for r in ledger.rows}

        # 2026 has no realised result of its own; only 2025 is pinned.
        assert rows[2026].net_result == Decimal("0")

    def test_without_a_declaration_the_recomputed_value_is_used(self):
        """No closed_years -> today's behaviour, so nothing changes for open years."""
        engine = _run(self.HISTORY + [self.REPURCHASE])

        assert engine.compute_carryforward().pending_end == []

    def test_freezing_does_not_mutate_the_stored_summaries(self):
        engine = _run(self.HISTORY + [self.REPURCHASE])

        engine.compute_carryforward(closed_years=self.DECLARED)

        # The engine still *reports* the recomputed truth; only the ledger view
        # is pinned to what was filed.
        assert engine.get_yearly_summary(2025).net_gain_loss == Decimal("0.0000")

    def test_the_freeze_can_be_set_once_on_the_engine(self):
        """Which years are closed is taxpayer state, not a per-call argument.

        Setting it on the engine makes every downstream view (console summary,
        carry-forward ledger, savings ledger, PDF) honour it without each one
        having to pass it along.
        """
        engine = _run(self.HISTORY + [self.REPURCHASE])
        engine.closed_years = self.DECLARED

        assert [(y, amount) for y, amount, _ in engine.compute_carryforward().pending_end] == [
            (2025, Decimal("8000.0000"))
        ]
        rows = {r.year: r for r in engine.compute_savings_ledger({}).rows}
        assert rows[2025].gp_net == Decimal("-8000.0000")

    def test_an_explicit_argument_overrides_the_engine_default(self):
        engine = _run(self.HISTORY + [self.REPURCHASE])
        engine.closed_years = self.DECLARED

        assert engine.compute_carryforward(closed_years={}).pending_end == []

    def test_a_fresh_engine_freezes_nothing(self):
        assert _run(self.HISTORY + [self.REPURCHASE]).closed_years == {}


class TestClosedYearPrecision:
    """A filing is expressed in cents; the engine carries four decimals.

    Comparing them raw flags a drift on every correctly-declared year, which would
    train the user to ignore the alert — the worst outcome for a compliance check.
    """

    #: 333 shares at €38.3333 against a €50.0000 basis = -3,885.0111 (a real
    #: sub-cent remainder, exactly like the live data: 335.8952 declared as 335.90).
    HISTORY = [
        _buy(date(2024, 1, 15), "1000", "50.0000"),
        _sell(date(2025, 6, 10), "333", "38.3333"),
    ]

    def test_the_engine_really_does_carry_a_sub_cent_remainder(self):
        assert _run(list(self.HISTORY)).get_yearly_summary(2025).net_gain_loss == Decimal(
            "-3885.0111"
        )

    def test_a_year_declared_to_the_cent_raises_no_drift(self):
        engine = _run(list(self.HISTORY))

        assert engine.check_closed_years({2025: {"net_gain_loss": Decimal("-3885.01")}}) == []

    def test_a_real_one_cent_difference_is_still_reported(self):
        engine = _run(list(self.HISTORY))

        drifts = engine.check_closed_years({2025: {"net_gain_loss": Decimal("-3885.00")}})

        assert len(drifts) == 1
        assert drifts[0].computed == Decimal("-3885.01")
        assert drifts[0].delta == Decimal("-0.01")

    def test_rounding_is_half_up_at_the_cent(self):
        engine = _run(
            [
                _buy(date(2024, 1, 15), "1000", "50.0000"),
                _sell(date(2025, 6, 10), "500", "38.3331"),  # -5,833.4500
            ]
        )
        assert engine.get_yearly_summary(2025).net_gain_loss == Decimal("-5833.4500")

        assert engine.check_closed_years({2025: {"net_gain_loss": Decimal("-5833.45")}}) == []

    def test_the_alert_speaks_in_cents_like_the_tax_return(self):
        engine = _run(list(self.HISTORY))

        drift = engine.check_closed_years({2025: {"net_gain_loss": Decimal("0")}})[0]

        assert drift.computed == Decimal("-3885.01")
