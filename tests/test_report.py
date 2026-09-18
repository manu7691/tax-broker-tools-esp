"""
Tests for the HTML/PDF report renderer (``tax_engine.report``):

- Spanish localization of the transaction-ledger "Tipo" column notes.
- The report covers complete tax years only — the in-progress current year is
  excluded from the rendered tables while the FIFO engine still processes it.
"""

from datetime import date
from decimal import Decimal

from tax_engine.models import EventType, StockEvent
from tax_engine.report import ReportRenderer, _translate_notes
from tax_engine.tax_engine import TaxEngine


class TestNoteTranslation:
    def test_spanish_translates_broker_terms(self):
        note = "Sell Order (Restricted Stock) (Includes $5 fees)"
        out = _translate_notes(note, is_es=True)
        assert "Acciones Restringidas" in out
        assert "Orden de Venta" in out
        assert "Restricted Stock" not in out
        assert "Sell Order" not in out

    def test_spanish_translates_wash_sale_and_options(self):
        # The amount is localized too: a Spanish page must not mix "€10.00" in a
        # ledger note with "10,00 €" in the table beside it.
        assert _translate_notes("[Wash Sale Blocked Loss: €10.00]", is_es=True) == (
            "[Pérdida Bloqueada Regla 2 Meses: €10,00]"
        )
        assert "Opción sobre Acciones" in _translate_notes("Sell Order (Stock Option)", is_es=True)

    def test_english_is_unchanged(self):
        note = "Sell Order (Restricted Stock)"
        assert _translate_notes(note, is_es=False) == note


def _events_spanning_in_progress_year() -> tuple[list[StockEvent], int, int]:
    """A buy + a complete-year sale + an in-progress (current-year) sale.

    Returns the events plus (prior_year, current_year) so assertions stay correct
    whenever the suite runs.
    """
    current = date.today().year
    prior = current - 1
    buy = current - 2
    events = [
        StockEvent(
            event_date=date(buy, 1, 10),
            event_type=EventType.BUY,
            shares=Decimal("100"),
            price_usd=Decimal("10"),
            fx_rate=Decimal("1.0"),
        ),
        StockEvent(
            event_date=date(prior, 6, 1),
            event_type=EventType.SELL,
            shares=Decimal("40"),
            price_usd=Decimal("20"),
            fx_rate=Decimal("1.0"),
            notes="Sell Order (Restricted Stock)",
        ),
        StockEvent(
            event_date=date(current, 3, 1),
            event_type=EventType.SELL,
            shares=Decimal("30"),
            price_usd=Decimal("25"),
            fx_rate=Decimal("1.0"),
            notes="Sell Order (Stock Option)",
        ),
    ]
    return events, prior, current


class TestCompleteYearsOnly:
    def test_in_progress_year_excluded_from_html(self):
        events, prior, current = _events_spanning_in_progress_year()
        engine = TaxEngine()
        engine.process_all(events)
        html = ReportRenderer(engine).generate_html_content(lang="es")

        # The complete prior-year disposal is present; the in-progress sale is not.
        assert f"01/06/{prior}" in html
        assert f"01/03/{current}" not in html

    def test_engine_still_processes_in_progress_year(self):
        events, _prior, current = _events_spanning_in_progress_year()
        engine = TaxEngine()
        engine.process_all(events)

        # Calculation is unaffected — only the report view is bounded.
        assert any(s.year == current for s in engine.get_all_yearly_summaries())

    def test_spanish_ledger_localizes_notes(self):
        events, _prior, _current = _events_spanning_in_progress_year()
        engine = TaxEngine()
        engine.process_all(events)
        html = ReportRenderer(engine).generate_html_content(lang="es")

        assert "Acciones Restringidas" in html
        assert "Restricted Stock" not in html


class TestCryptoInFlagshipReport:
    """Crypto folds into the combined savings base and gets its own GP line."""

    def _stock_engine_with_prior_gain(self):
        # Buy 100 @ 10 (two years ago), sell 40 @ 20 last year -> +400 stock gain.
        current = date.today().year
        prior, buy = current - 1, current - 2
        events = [
            StockEvent(
                date(buy, 1, 10),
                EventType.BUY,
                Decimal("100"),
                Decimal("10"),
                fx_rate=Decimal("1.0"),
            ),
            StockEvent(
                date(prior, 6, 1),
                EventType.SELL,
                Decimal("40"),
                Decimal("20"),
                fx_rate=Decimal("1.0"),
            ),
        ]
        engine = TaxEngine()
        engine.process_all(events)
        return engine, prior

    def test_crypto_line_and_combined_base(self):
        from tax_engine.models import YearlyTaxSummary

        engine, prior = self._stock_engine_with_prior_gain()
        crypto = {prior: YearlyTaxSummary(year=prior, total_losses=Decimal("-100"))}
        html = ReportRenderer(engine).generate_html_content(lang="en", crypto_summaries=crypto)

        # The distinct crypto capital-gains line is rendered...
        assert "otros elementos patrimoniales" in html
        # ...and the integrated base reflects the merged total (400 - 100 = 300),
        # not the stock-only 400.
        assert "€300.00" in html

    def test_stock_only_report_has_no_crypto_line(self):
        engine, _prior = self._stock_engine_with_prior_gain()
        html = ReportRenderer(engine).generate_html_content(lang="en")
        assert "otros elementos patrimoniales" not in html


def _two_broker_portfolio():
    """A portfolio whose gross realized result differs from its computable one.

    TSLA realises a loss that a repurchase two weeks later blocks (Art. 33.5.f),
    so the broker table's gross total and the portfolio table's computable total
    are genuinely different numbers — which is exactly what the report has to
    explain rather than leave as an apparent contradiction.
    """
    from tax_engine.portfolio import run_portfolio

    def _ev(d, kind, shares, price, isin, symbol, broker):
        return StockEvent(
            event_date=d,
            event_type=kind,
            shares=Decimal(shares),
            price_usd=Decimal(price),
            fx_rate=Decimal("1"),
            isin=isin,
            symbol=symbol,
            broker=broker,
        )

    events = [
        _ev(date(2023, 1, 10), EventType.BUY, "100", "100", "AAA", "AAA", "E*TRADE"),
        _ev(date(2023, 6, 10), EventType.SELL, "100", "40", "AAA", "AAA", "E*TRADE"),
        _ev(date(2023, 6, 24), EventType.BUY, "100", "40", "AAA", "AAA", "E*TRADE"),
        _ev(date(2023, 2, 1), EventType.BUY, "10", "10", "BBB", "BBB", "Revolut"),
        _ev(date(2023, 8, 1), EventType.SELL, "10", "30", "BBB", "BBB", "Revolut"),
    ]
    return run_portfolio(events)


class TestGrossVersusComputableLosses:
    """The two loss columns hold different quantities and must not share a name.

    The per-broker table reports the gross realized result; the portfolio table
    reports what is computable after Art. 33.5.f. Labelling both "Realized
    Losses" makes the report look like it contradicts itself, and invites the
    reader to carry the wrong figure to the Modelo 100.
    """

    def _html(self, lang: str) -> str:
        portfolio = _two_broker_portfolio()
        return ReportRenderer(portfolio.aggregate).generate_html_content(
            lang=lang, securities=portfolio.results
        )

    def _note(self, lang: str) -> str:
        """The reconciliation paragraph, isolated from the rest of the report."""
        html = self._html(lang)
        anchor = "Conciliación" if lang == "es" else "Reconciliation"
        assert anchor in html, f"no reconciliation note in the {lang} report"
        start = html.index(anchor)
        return html[start : html.index("</p>", start)]

    def test_spanish_portfolio_table_drops_the_realizadas_label(self):
        # Anchored on the neighbouring columns: "Pérdidas Deducibles" alone also
        # appears in the yearly table, which was never the one at issue.
        assert (
            "<th>Bróker(es)</th><th>Ganancias Realizadas</th><th>Pérdidas Deducibles</th>"
            in self._html("es")
        )

    def test_english_portfolio_table_drops_the_realized_label(self):
        assert "<th>Broker(s)</th><th>Realized Gains</th><th>Deductible Losses</th>" in self._html(
            "en"
        )

    def test_the_broker_column_says_it_is_gross(self):
        assert "<th>Pérdidas Realizadas (brutas)</th>" in self._html("es")
        assert "<th>Realized Losses (gross)</th>" in self._html("en")

    def test_the_bridge_adds_back_what_the_two_month_rule_defers(self):
        """A deferred loss RAISES the computable result; it is added, not subtracted.

        -5,800 gross + 6,000 that Art. 33.5.f defers = 200 computable. Wording
        the step as "less" would describe the opposite operation.
        """
        note = self._note("es")
        assert "más" in note
        assert "menos" not in note

    def test_the_note_bridges_gross_to_computable(self):
        note = self._note("es")
        # -6000 gross loss, all of it blocked by the repurchase; +200 gain on BBB.
        assert "-5.800,00\u00a0€" in note  # gross realized net
        assert "6.000,00\u00a0€" in note  # blocked by the 2-month rule
        assert "200,00\u00a0€" in note  # computable net = what the Modelo 100 tables use

    def test_the_note_is_bilingual(self):
        note = self._note("en")
        assert "€-5,800.00" in note
        assert "€200.00" in note

    def test_the_note_omits_a_renuncia_that_did_not_happen(self):
        assert "renuncia" not in self._note("es").lower()


class TestSpanishNumberFormat:
    """The Spanish report must read 1.234,56 €, not €1,234.56.

    This report is transcribed into the Modelo 100 by hand, often by an adviser.
    A decimal point read as a thousands separator is a three-orders-of-magnitude
    error on a figure that goes straight into a tax return. The renderer already
    localizes dates and notes; the amounts were left in en-US.
    """

    NBSP = " "

    def _html(self, lang: str) -> str:
        portfolio = _two_broker_portfolio()
        return ReportRenderer(portfolio.aggregate).generate_html_content(
            lang=lang, securities=portfolio.results
        )

    def test_spanish_swaps_the_separators(self):
        html = self._html("es")
        assert "6.000,00" in html
        assert "6,000.00" not in html

    def test_spanish_puts_the_symbol_after_the_amount(self):
        assert f"200,00{self.NBSP}€" in self._html("es")

    def test_spanish_keeps_the_minus_in_front_of_the_digits(self):
        assert f"-5.800,00{self.NBSP}€" in self._html("es")

    def test_english_keeps_its_own_convention(self):
        html = self._html("en")
        assert "€6,000.00" in html
        assert "6.000,00" not in html

    def test_the_fx_rate_column_is_localized_too(self):
        """The ledger prints the ECB rate beside the amounts it converted.

        Left in en-US it is the one value on the row with a decimal point,
        which is precisely the mixed-separator reading this change removes.
        """
        engine = TaxEngine()
        engine.process_all(
            [
                StockEvent(
                    event_date=date(date.today().year - 2, 3, 1),
                    event_type=EventType.BUY,
                    shares=Decimal("10"),
                    price_usd=Decimal("100"),
                    fx_rate=Decimal("0.8618"),
                )
            ]
        )
        html = ReportRenderer(engine).generate_html_content(lang="es")

        assert "0,8618" in html
        assert "0.8618" not in html

    def test_four_decimal_amounts_are_localized_too(self):
        # Share prices and average costs render at 4 decimals.
        assert "100,0000" in self._html("es")


def _deferral_released_portfolio():
    """A deferral that is blocked in 2023 and definitively released in 2024.

    Without a closed-year declaration nothing is renounced, so the release
    genuinely reduces the computable result — the one scenario in which the
    bridge's "released" term is non-zero.
    """
    from tax_engine.portfolio import run_portfolio

    def _ev(d, kind, shares, price, isin, symbol, broker):
        return StockEvent(
            event_date=d,
            event_type=kind,
            shares=Decimal(shares),
            price_usd=Decimal(price),
            fx_rate=Decimal("1"),
            isin=isin,
            symbol=symbol,
            broker=broker,
        )

    return run_portfolio(
        [
            _ev(date(2022, 1, 10), EventType.BUY, "100", "100", "AAA", "AAA", "E*TRADE"),
            _ev(date(2023, 5, 10), EventType.SELL, "100", "50", "AAA", "AAA", "E*TRADE"),
            _ev(date(2023, 5, 20), EventType.BUY, "100", "50", "AAA", "AAA", "E*TRADE"),
            _ev(date(2024, 9, 10), EventType.SELL, "100", "50", "AAA", "AAA", "E*TRADE"),
            _ev(date(2023, 2, 1), EventType.BUY, "10", "10", "BBB", "BBB", "Revolut"),
            _ev(date(2023, 8, 1), EventType.SELL, "10", "30", "BBB", "BBB", "Revolut"),
        ]
    )


class TestTheNoteReportsARenuncia:
    """A forfeited release is a figure the reader cannot derive from the tables.

    It is the difference between what the engine computes and what the taxpayer
    has decided to declare, so an AEAT-facing report has to state it rather than
    leave it inside an unexplained gap.
    """

    def _forfeited_portfolio(self):
        portfolio = _deferral_released_portfolio()
        # As filed, 2023 reported no blocked loss: the -5000 was deducted then.
        portfolio.aggregate.closed_years = {2023: {"blocked_losses": Decimal("0.00")}}
        portfolio.aggregate.apply_closed_year_forfeits(
            mirror_engines=[r.engine for r in portfolio.results]
        )
        return portfolio

    def _note(self) -> str:
        portfolio = self._forfeited_portfolio()
        html = ReportRenderer(portfolio.aggregate).generate_html_content(
            lang="es", securities=portfolio.results
        )
        start = html.index("Conciliación")
        return html[start : html.index("</p>", start)]

    def test_the_renounced_amount_is_stated(self):
        assert "renuncia" in self._note().lower()
        assert "5.000,00\u00a0€" in self._note()


class TestTheBridgeArithmetic:
    """gross + deferred - released == computable, as a number, not as prose.

    The wording and the endpoints were right while the operation between them
    was inverted, because nothing asserted that the terms actually add up.
    """

    def _bridge(self, portfolio):
        ctx = ReportRenderer(portfolio.aggregate)._broker_context()
        return ctx["broker_bridge"]

    def test_the_terms_add_up_with_a_deferred_loss(self):
        b = self._bridge(_two_broker_portfolio())

        assert b["blocked"] > 0, "scenario must actually defer something"
        assert b["gross_net"] + b["blocked"] - b["released"] == b["computable_net"]

    def test_the_terms_add_up_when_a_deferral_is_released(self):
        b = self._bridge(_deferral_released_portfolio())

        assert b["released"] > 0, "scenario must actually release something"
        assert b["gross_net"] + b["blocked"] - b["released"] == b["computable_net"]

    def test_the_terms_add_up_when_a_release_is_renounced(self):
        portfolio = TestTheNoteReportsARenuncia()._forfeited_portfolio()
        b = self._bridge(portfolio)

        assert b["forfeited"] > 0, "scenario must actually renounce something"
        assert b["gross_net"] + b["blocked"] - b["released"] == b["computable_net"]


class TestThePrintedBridgeBalances:
    """The three numbers on the page must add up as printed, not just internally.

    Every term is a 4-decimal Decimal rounded to cents for display. Rounding
    each one independently can leave the printed line a cent out — on a note
    whose whole purpose is to show that two different totals reconcile, that
    single cent is what makes a reader stop trusting it.
    """

    def _bridge(self):
        from tax_engine.portfolio import run_portfolio

        def _ev(d, kind, shares, price, isin, symbol, broker):
            return StockEvent(
                event_date=d,
                event_type=kind,
                shares=Decimal(shares),
                price_usd=Decimal(price),
                fx_rate=Decimal("1"),
                isin=isin,
                symbol=symbol,
                broker=broker,
            )

        # 6 shares at 16.6675 put the gross total and the computable total on
        # opposite sides of a half-cent.
        portfolio = run_portfolio(
            [
                _ev(date(2022, 1, 10), EventType.BUY, "6", "100", "AAA", "AAA", "E*TRADE"),
                _ev(date(2023, 5, 10), EventType.SELL, "6", "16.6675", "AAA", "AAA", "E*TRADE"),
                _ev(date(2023, 5, 20), EventType.BUY, "6", "16.6675", "AAA", "AAA", "E*TRADE"),
                _ev(date(2023, 2, 1), EventType.BUY, "7", "14.2857", "BBB", "BBB", "Revolut"),
                _ev(date(2023, 8, 1), EventType.SELL, "7", "28.5715", "BBB", "BBB", "Revolut"),
            ]
        )
        return ReportRenderer(portfolio.aggregate)._broker_context()["broker_bridge"]

    def test_the_terms_balance_after_rounding_to_cents(self):
        b = self._bridge()
        cents = Decimal("0.01")

        assert b["gross_net"].quantize(cents) + b["blocked"].quantize(cents) - b[
            "released"
        ].quantize(cents) == b["computable_net"].quantize(cents)

    def test_the_deferred_term_stays_within_a_cent_of_the_real_figure(self):
        """Absorbing the residual must not let the term drift from the truth."""
        b = self._bridge()

        assert abs(b["blocked"] - Decimal("500.00")) <= Decimal("0.02")


def _deferral_engine():
    """A deferral blocked in 2022 and definitively freed in the CURRENT year.

    Pending at 31 December of the last complete year, gone today — which is
    exactly the difference the two columns exist to show.
    """
    cur = date.today().year

    def _ev(d, kind, shares, price):
        return StockEvent(
            event_date=d,
            event_type=kind,
            shares=Decimal(shares),
            price_usd=Decimal(price),
            fx_rate=Decimal("1"),
        )

    engine = TaxEngine(release_policy="definitive")
    engine.process_all(
        [
            _ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
            _ev(date(2022, 5, 10), EventType.SELL, "100", "50"),  # loss -5000
            _ev(date(2022, 5, 20), EventType.BUY, "100", "50"),  # blocks it
            _ev(date(cur, 3, 10), EventType.SELL, "100", "50"),  # clean exit, frees it
        ]
    )
    return engine


class TestOutstandingDeferrals:
    """What Art. 33.5.f still has locked up, and what would free it.

    The yearly table says how much was blocked in each year; nothing said how
    much is still deferred now, on which lot it sits, or what has to be sold to
    integrate it. Without that a técnico cannot verify a future deduction, and
    the taxpayer cannot plan one.
    """

    def _ctx(self):
        cutoff = date.today().year - 1
        return ReportRenderer(_deferral_engine())._deferrals_context(max_year=cutoff)

    def test_the_deferral_is_listed_with_the_year_it_came_from(self):
        rows = self._ctx()["deferral_rows"]

        assert len(rows) == 1
        assert rows[0]["origin_year"] == 2022

    def test_it_was_still_pending_at_the_last_year_end(self):
        assert self._ctx()["deferral_rows"][0]["pending_cutoff"] == Decimal("-5000.0000")

    def test_and_is_gone_today(self):
        assert self._ctx()["deferral_rows"][0]["pending_today"] == Decimal("0")

    def test_the_row_names_the_lot_holding_it(self):
        row = self._ctx()["deferral_rows"][0]

        assert row["lot_date"] == date(2022, 5, 20)
        assert row["lot_shares_today"] == Decimal("0")  # sold, which is what freed it

    def test_the_totals_match_the_rows(self):
        ctx = self._ctx()

        assert ctx["deferral_totals"]["pending_cutoff"] == Decimal("-5000.0000")
        assert ctx["deferral_totals"]["pending_today"] == Decimal("0")

    def test_the_pending_balance_reconciles_with_the_yearly_table(self):
        """Balance at 31/12 == blocked in all years so far, less what was released.

        Ties the new table to the "Pérdidas Bloqueadas" column it explains; if
        the two ever disagree, one of them is lying.
        """
        engine = _deferral_engine()
        cutoff = date.today().year - 1
        summaries = [s for s in engine.get_all_yearly_summaries() if s.year <= cutoff]
        expected = sum((s.blocked_losses for s in summaries), Decimal("0")) - sum(
            (s.unlocked_historical_losses for s in summaries), Decimal("0")
        )

        ctx = ReportRenderer(engine)._deferrals_context(max_year=cutoff)
        assert ctx["deferral_totals"]["pending_cutoff"] == expected

    def test_an_engine_with_no_deferrals_produces_no_rows(self):
        engine = TaxEngine()
        engine.process_all(
            [
                StockEvent(
                    event_date=date(2022, 1, 10),
                    event_type=EventType.BUY,
                    shares=Decimal("10"),
                    price_usd=Decimal("10"),
                    fx_rate=Decimal("1"),
                )
            ]
        )

        assert ReportRenderer(engine)._deferrals_context(max_year=2025)["deferral_rows"] == []


class TestTheDeferralsSectionRenders:
    """The section has to say the one thing the reviewer of this report got wrong.

    Art. 33.5.f deferrals do not expire at four years — that is Art. 49, for the
    negative balance of the savings base. Confusing the two leads to selling
    shares to "rescue" losses that were never at risk.
    """

    def _html(self, lang="es"):
        return ReportRenderer(_deferral_engine()).generate_html_content(lang=lang)

    def test_the_section_is_present(self):
        assert "Pérdidas Diferidas" in self._html()

    def test_it_states_that_these_do_not_expire(self):
        html = self._html()
        assert "no caducan" in html
        assert "Art. 49" in html  # the four-year rule it is contrasted with

    def test_both_dates_are_labelled(self):
        html = self._html()
        assert f"31/12/{date.today().year - 1}" in html
        assert date.today().strftime("%d/%m/%Y") in html

    def test_english_has_its_own_wording(self):
        html = self._html(lang="en")
        assert "Outstanding Deferred Losses" in html
        assert "do not expire" in html

    def test_a_report_with_no_deferrals_omits_the_section_entirely(self):
        engine = TaxEngine()
        engine.process_all(
            [
                StockEvent(
                    event_date=date(2022, 1, 10),
                    event_type=EventType.BUY,
                    shares=Decimal("10"),
                    price_usd=Decimal("10"),
                    fx_rate=Decimal("1"),
                )
            ]
        )

        assert "Pérdidas Diferidas" not in ReportRenderer(engine).generate_html_content(lang="es")


def _espp_exposure_engine():
    """An ESPP lot still inside the 36 months, behind an RSU cushion that a
    current-year sell-to-cover has just eaten through.

    Timeline (fx = 1 throughout):
      2024-01-10  VEST 50 @ 40   -> the cushion standing in front
      2024-06-15  ESPP 20 @ 34, FMV 40 -> €6/share of discount at risk
      <this year> SELL 55        -> consumes the 50 RSU and reaches 5 ESPP shares

    So 20 shares were exposed at the last year end and 15 are exposed today,
    with nothing left in front of them.
    """
    from tax_engine.models import LotOrigin

    engine = TaxEngine()
    engine.process_all(
        [
            StockEvent(
                event_date=date(2024, 1, 10),
                event_type=EventType.VEST,
                shares=Decimal("50"),
                price_usd=Decimal("40"),
                fx_rate=Decimal("1"),
                origin=LotOrigin.RSU,
            ),
            StockEvent(
                event_date=date(2024, 6, 15),
                event_type=EventType.BUY,
                shares=Decimal("20"),
                price_usd=Decimal("34"),
                fx_rate=Decimal("1"),
                origin=LotOrigin.ESPP,
                espp_fmv_usd=Decimal("40"),
                espp_price_usd=Decimal("34"),
            ),
            StockEvent(
                event_date=date(date.today().year, 3, 1),
                event_type=EventType.SELL,
                shares=Decimal("55"),
                price_usd=Decimal("45"),
                fx_rate=Decimal("1"),
            ),
        ]
    )
    return engine


class TestLiveEsppExposure:
    """ESPP lots still inside the 36 months, and what stands between them and FIFO.

    The existing table reports exemption breaches that already happened. Nothing
    reported the exposure still open, so a reader could not see that a future
    vest's sell-to-cover turns a clean file into a complementaria.
    """

    def _ctx(self):
        cutoff = date.today().year - 1
        return ReportRenderer(_espp_exposure_engine())._espp_exposure_context(max_year=cutoff)

    def test_the_lot_still_inside_the_window_is_listed(self):
        rows = self._ctx()["espp_exposure_rows"]

        assert len(rows) == 1
        assert rows[0]["acq_date"] == date(2024, 6, 15)

    def test_it_reports_the_shares_exposed_at_each_date(self):
        row = self._ctx()["espp_exposure_rows"][0]

        assert row["shares_cutoff"] == Decimal("20")
        assert row["shares_today"] == Decimal("15")

    def test_the_discount_at_risk_follows_the_shares_still_held(self):
        row = self._ctx()["espp_exposure_rows"][0]

        assert row["discount_cutoff"] == Decimal("120.00")  # 20 x €6
        assert row["discount_today"] == Decimal("90.00")  # 15 x €6

    def test_it_says_when_the_exemption_is_secured(self):
        assert self._ctx()["espp_exposure_rows"][0]["exempt_from"] == date(2027, 6, 15)

    def test_the_cushion_counts_only_shares_ahead_in_the_queue(self):
        """The RSU lot in front was consumed by the sale, so nothing shields it now."""
        row = self._ctx()["espp_exposure_rows"][0]

        assert row["cushion_today"] == Decimal("0")

    def test_a_lot_past_36_months_is_not_listed(self):
        from tax_engine.models import LotOrigin

        engine = TaxEngine()
        engine.process_all(
            [
                StockEvent(
                    event_date=date(2019, 1, 10),
                    event_type=EventType.BUY,
                    shares=Decimal("10"),
                    price_usd=Decimal("34"),
                    fx_rate=Decimal("1"),
                    origin=LotOrigin.ESPP,
                    espp_fmv_usd=Decimal("40"),
                    espp_price_usd=Decimal("34"),
                )
            ]
        )

        ctx = ReportRenderer(engine)._espp_exposure_context(max_year=date.today().year - 1)
        assert ctx["espp_exposure_rows"] == []


class TestTheEsppExposureSectionRenders:
    def _html(self, lang="es"):
        return ReportRenderer(_espp_exposure_engine()).generate_html_content(lang=lang)

    def test_the_section_is_present(self):
        assert "Exposición ESPP" in self._html()

    def test_it_shows_the_cushion_and_says_it_is_a_projection(self):
        html = self._html()
        assert "Colchón FIFO" in html
        assert "proyección" in html

    def test_it_names_the_consequence_of_breaching(self):
        html = self._html()
        assert "complementaria" in html
        assert "rendimiento del trabajo" in html.lower()

    def test_english_has_its_own_wording(self):
        html = self._html(lang="en")
        assert "Live ESPP Exposure" in html
        assert "FIFO cushion" in html

    def test_a_report_with_no_live_espp_lots_omits_the_section(self):
        engine = TaxEngine()
        engine.process_all(
            [
                StockEvent(
                    event_date=date(2022, 1, 10),
                    event_type=EventType.BUY,
                    shares=Decimal("10"),
                    price_usd=Decimal("10"),
                    fx_rate=Decimal("1"),
                )
            ]
        )

        assert "Exposición ESPP" not in ReportRenderer(engine).generate_html_content(lang="es")


class TestTheDeferralNoteExplainsAFullySoldLot:
    """A lot with no shares left can still hold a pending deferral.

    Under the 'definitive' policy a transmission only frees the loss if no
    homogeneous securities are repurchased in the following two months. Real
    data has three such rows, and a footnote claiming the loss was already
    integrated would contradict the very table it sits under.
    """

    def _html(self, lang="es"):
        cur = date.today().year

        def _ev(d, kind, shares, price):
            return StockEvent(
                event_date=d,
                event_type=kind,
                shares=Decimal(shares),
                price_usd=Decimal(price),
                fx_rate=Decimal("1"),
            )

        engine = TaxEngine(release_policy="definitive")
        engine.process_all(
            [
                _ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
                _ev(date(2022, 5, 10), EventType.SELL, "100", "50"),  # loss
                _ev(date(2022, 5, 20), EventType.BUY, "100", "50"),  # blocks it
                _ev(date(cur, 3, 10), EventType.SELL, "100", "50"),  # sells the lot...
                _ev(date(cur, 3, 20), EventType.BUY, "100", "50"),  # ...but buys back
            ]
        )
        return ReportRenderer(engine).generate_html_content(lang=lang)

    def test_the_note_does_not_claim_a_sold_lot_is_settled(self):
        html = self._html()

        assert "ya se integró" not in html
        assert "se integró en esa venta" not in html

    def test_it_explains_that_a_repurchase_keeps_the_loss_waiting(self):
        html = self._html()

        assert "recompra" in html
        assert "2 meses siguientes" in html

    def test_english_says_the_same(self):
        html = self._html(lang="en")

        assert "repurchase" in html.lower()
        assert "following two months" in html


def _fee_rounding_engine():
    """One sale consuming three equal lots, with a fee that does not divide by three.

    Each row's share of the €10 fee rounds to €3.33, so the rows deduct €9.99
    while the engine deducts the whole €10 once. The disposal table therefore
    totals €290.01 where every aggregate table says €290.00 — the same mechanism
    that puts a cent between the real report's two totals.
    """

    def _ev(d, kind, shares, price, fees="0"):
        return StockEvent(
            event_date=d,
            event_type=kind,
            shares=Decimal(shares),
            price_usd=Decimal(price),
            fx_rate=Decimal("1"),
            fees_usd=Decimal(fees),
        )

    engine = TaxEngine()
    engine.process_all(
        [
            _ev(date(2023, 1, 10), EventType.BUY, "10", "10"),
            _ev(date(2023, 2, 10), EventType.BUY, "10", "10"),
            _ev(date(2023, 3, 10), EventType.BUY, "10", "10"),
            _ev(date(2023, 6, 10), EventType.SELL, "30", "20", fees="10"),
        ]
    )
    return engine


class TestTheDisposalTableExplainsItsOwnTotal:
    """Per-row rounding puts a cent between this table and every aggregate one.

    The row total must keep matching the column above it — a table whose own
    figures do not add up is worse than one that differs from another table. So
    the total stays as printed and the report states the other figure and why
    they differ, instead of leaving a reader to find the cent themselves.
    """

    def _ctx(self):
        return ReportRenderer(_fee_rounding_engine())._transmisiones_context(max_year=2025)

    def test_the_total_is_the_sum_of_the_printed_rows(self):
        ctx = self._ctx()

        assert ctx["transm_total"] == sum((r["net"] for r in ctx["transm_rows"]), Decimal("0"))
        assert ctx["transm_total"] == Decimal("290.01")

    def test_the_aggregate_figure_is_reported_alongside(self):
        assert self._ctx()["transm_total_aggregate"] == Decimal("290.00")

    def test_the_report_explains_the_difference(self):
        html = ReportRenderer(_fee_rounding_engine()).generate_html_content(lang="es")

        assert "redondeo" in html
        assert "290,01" in html
        assert "290,00" in html

    def test_english_explains_it_too(self):
        html = ReportRenderer(_fee_rounding_engine()).generate_html_content(lang="en")

        assert "rounding" in html
        assert "290.01" in html and "290.00" in html

    def test_nothing_is_said_when_the_two_agree(self):
        """No note when there is nothing to explain."""

        engine = TaxEngine()
        engine.process_all(
            [
                StockEvent(
                    event_date=date(2023, 1, 10),
                    event_type=EventType.BUY,
                    shares=Decimal("10"),
                    price_usd=Decimal("10"),
                    fx_rate=Decimal("1"),
                ),
                StockEvent(
                    event_date=date(2023, 6, 10),
                    event_type=EventType.SELL,
                    shares=Decimal("10"),
                    price_usd=Decimal("20"),
                    fx_rate=Decimal("1"),
                ),
            ]
        )
        ctx = ReportRenderer(engine)._transmisiones_context(max_year=2025)

        assert ctx["transm_total"] == ctx["transm_total_aggregate"]
        assert "redondeo" not in ReportRenderer(engine).generate_html_content(lang="es")


class TestTheDeferralsSectionAccountsForARenuncia:
    """With a renuncia applied, this table stops tying to the yearly columns.

    The renounced part genuinely released — the block broke — so it is not
    pending here; and the yearly table no longer credits it, because it was
    given up. Both figures are right and they differ by exactly the renounced
    amount, which is the same unexplained-gap problem this whole section was
    added to remove. So the section states it.
    """

    def _html(self):
        """A portfolio with BOTH a renounced release and a deferral still alive.

        AAA's deferral is blocked in 2023, released in 2024, and renounced.
        BBB's is blocked in 2023 and still held, so the section actually renders
        and there is a table for the renounced amount to fail to tie to.
        """
        from tax_engine.portfolio import run_portfolio

        def _ev(d, kind, shares, price, isin):
            return StockEvent(
                event_date=d,
                event_type=kind,
                shares=Decimal(shares),
                price_usd=Decimal(price),
                fx_rate=Decimal("1"),
                isin=isin,
                symbol=isin,
                broker="E*TRADE",
            )

        portfolio = run_portfolio(
            [
                _ev(date(2022, 1, 10), EventType.BUY, "100", "100", "AAA"),
                _ev(date(2023, 5, 10), EventType.SELL, "100", "50", "AAA"),
                _ev(date(2023, 5, 20), EventType.BUY, "100", "50", "AAA"),
                _ev(date(2024, 9, 10), EventType.SELL, "100", "50", "AAA"),
                # BBB blocks a loss in 2023 and simply keeps the replacement.
                _ev(date(2022, 2, 1), EventType.BUY, "10", "100", "BBB"),
                _ev(date(2023, 3, 1), EventType.SELL, "10", "50", "BBB"),
                _ev(date(2023, 3, 10), EventType.BUY, "10", "50", "BBB"),
            ]
        )
        portfolio.aggregate.closed_years = {2023: {"blocked_losses": Decimal("-500.00")}}
        portfolio.aggregate.apply_closed_year_forfeits(
            mirror_engines=[r.engine for r in portfolio.results]
        )
        return ReportRenderer(portfolio.aggregate).generate_html_content(
            lang="es", securities=portfolio.results
        )

    def test_the_renounced_amount_is_stated_in_this_section_too(self):
        html = self._html()
        start = html.index("Pérdidas Diferidas del Art")
        section = html[start : html.index("<h2", start + 10)]

        assert "renuncia" in section.lower()
        assert "5.000,00 €" in section

    def test_nothing_is_said_when_there_was_no_renuncia(self):
        html = ReportRenderer(_deferral_engine()).generate_html_content(lang="es")
        start = html.index("Pérdidas Diferidas del Art")
        section = html[start : html.index("<h2", start + 10)]

        assert "renuncia" not in section.lower()
