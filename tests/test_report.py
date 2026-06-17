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
        assert _translate_notes("[Wash Sale Blocked Loss: €10.00]", is_es=True) == (
            "[Pérdida Bloqueada Regla 2 Meses: €10.00]"
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
            StockEvent(date(buy, 1, 10), EventType.BUY, Decimal("100"), Decimal("10"),
                       fx_rate=Decimal("1.0")),
            StockEvent(date(prior, 6, 1), EventType.SELL, Decimal("40"), Decimal("20"),
                       fx_rate=Decimal("1.0")),
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
