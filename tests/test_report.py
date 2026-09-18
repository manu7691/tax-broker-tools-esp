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
