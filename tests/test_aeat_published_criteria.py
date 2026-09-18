"""
The engine pinned against criteria as PUBLISHED, quoted verbatim.

Every other suite tests the engine against a developer's understanding of the
rule. This one tests it against the words the administration actually printed,
quoted in each docstring with its source, so the criterion is traceable to
something a tax adviser can open rather than to a paraphrase that drifted.

Sources (retrieved 2026-09-18):

* AEAT, Manual práctico Renta 2025, «Pérdidas patrimoniales que no se computan
  fiscalmente como tales»
  https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/irpf-2025/c11-ganancias-perdidas-patrimoniales/ganancias-perdidas-patrimoniales-que-no-bi/perdidas-patrimoniales-que-no-se-tales.html
* AEAT, «Integración diferida: pérdidas patrimoniales derivadas de
  transmisiones con recompra del elemento»
  https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-ayuda-presentacion/irpf-2020/8-cumplimentacion-irpf/8_2-ganancias-perdidas-patrimoniales/8_2_1-conceptos-generales/8_2_1_1-concepto-ganancias-perdidas-patrimoniales/integracion-diferida-perdidas-patrimoniales-derivadas-transmisiones.html
* DGT, consulta vinculante V3282-18 (28/12/2018), cited by the engine alongside
  V0046-20 and V1119-21.

These tests check that the SOFTWARE follows the published criterion. They say
nothing about whether that criterion is the right reading for any given
taxpayer — that is an adviser's judgement, not a test's.
"""

from datetime import date
from decimal import Decimal

from tax_engine.models import EventType, StockEvent
from tax_engine.tax_engine import TaxEngine


def ev(day: date, kind: EventType, shares: str, price: str) -> StockEvent:
    """A single-security event priced in EUR (fx = 1), so the arithmetic is visible."""
    return StockEvent(
        event_date=day,
        event_type=kind,
        shares=Decimal(shares),
        price_usd=Decimal(price),
        fx_rate=Decimal("1"),
    )


class TestTheWindowRunsBothWays:
    """AEAT: «en los dos meses **anteriores o posteriores**».

    Both halves of that sentence are load-bearing, and only the "posteriores"
    half had tests. A purchase made shortly BEFORE a loss sale blocks it just as
    a repurchase after does — the taxpayer never really left the position.
    """

    def test_a_purchase_two_weeks_before_the_sale_blocks_the_loss(self):
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2022, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2023, 5, 26), EventType.BUY, "100", "50"),  # 15 days before
                ev(date(2023, 6, 10), EventType.SELL, "100", "50"),
            ]
        )

        assert engine.get_yearly_summary(2023).blocked_losses < 0

    def test_a_purchase_just_outside_the_prior_window_does_not(self):
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2022, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2023, 4, 9), EventType.BUY, "100", "50"),  # 2 months + 1 day
                ev(date(2023, 6, 10), EventType.SELL, "100", "50"),
            ]
        )

        assert engine.get_yearly_summary(2023).blocked_losses == Decimal("0")

    def test_a_repurchase_two_weeks_after_the_sale_blocks_the_loss(self):
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2022, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2023, 6, 10), EventType.SELL, "100", "50"),
                ev(date(2023, 6, 24), EventType.BUY, "100", "50"),
            ]
        )

        assert engine.get_yearly_summary(2023).blocked_losses < 0


class TestIntegrationIsProgressiveNotAllOrNothing:
    """AEAT: «se integrarán **a medida que** se transmitan los valores o
    participaciones que permanezcan en el patrimonio del contribuyente».

    "A medida que" is the whole point: the deferral is released in step with the
    replacement shares leaving, and does not wait for the position to reach zero.
    The engine offers a stricter reading as ``position_zero``; the default must
    follow the published one.
    """

    def test_selling_half_the_replacement_frees_half_the_deferral(self):
        engine = TaxEngine(release_policy="definitive")
        engine.process_all(
            [
                ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2022, 5, 10), EventType.SELL, "100", "50"),  # loss -5000
                ev(date(2022, 5, 20), EventType.BUY, "100", "50"),  # blocks it
                ev(date(2023, 9, 10), EventType.SELL, "50", "50"),  # half, definitively
            ]
        )

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-2500.0000")
        assert engine.state.total_shares == Decimal("50"), "position deliberately still open"


class TestOnlyADefinitiveTransmissionFrees:
    """AEAT: «Una transmisión se considerará definitiva cuando, en los dos meses
    anteriores o posteriores a ella, no se adquieran nuevamente valores
    homogéneos.» (also DGT V3282-18)

    This is the criterion the whole outstanding-deferral balance rests on, so it
    is pinned against the published wording rather than only against the DGT
    citation in the engine's docstring.
    """

    def _events(self, *extra: StockEvent) -> list[StockEvent]:
        return [
            ev(date(2021, 1, 10), EventType.BUY, "100", "100"),
            ev(date(2022, 5, 10), EventType.SELL, "100", "50"),
            ev(date(2022, 5, 20), EventType.BUY, "100", "50"),
            *extra,
        ]

    def test_a_sale_followed_by_a_repurchase_frees_nothing(self):
        engine = TaxEngine(release_policy="definitive")
        engine.process_all(
            self._events(
                ev(date(2023, 6, 10), EventType.SELL, "100", "50"),
                ev(date(2023, 7, 5), EventType.BUY, "100", "50"),  # within 2 months
            )
        )

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("0")

    def test_a_sale_with_nothing_bought_after_it_frees_the_loss(self):
        engine = TaxEngine(release_policy="definitive")
        engine.process_all(self._events(ev(date(2023, 6, 10), EventType.SELL, "100", "50")))

        assert engine.get_yearly_summary(2023).unlocked_historical_losses == Decimal("-5000.0000")


class TestFifoIdentifiesWhatWasTransmitted:
    """AEAT: «Tratándose de valores homogéneos, se aplicará la regla FIFO […]
    conforme al artículo 37.2 de la Ley del IRPF […] se considerará que se
    transmiten aquellos adquiridos en primer lugar por el contribuyente.»
    """

    def test_the_oldest_lot_is_consumed_first(self):
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2022, 1, 10), EventType.BUY, "10", "10"),  # oldest, cheap
                ev(date(2022, 6, 10), EventType.BUY, "10", "100"),  # newer, dear
                ev(date(2023, 3, 10), EventType.SELL, "10", "50"),
            ]
        )

        # FIFO consumes the €10 lot: a €400 gain. LIFO would have shown a loss.
        assert engine.get_yearly_summary(2023).total_gains == Decimal("400.0000")


class TestTheDeferralDoesNotExpire:
    """Neither AEAT page states any deadline for a deferred loss.

    The report asserts, in a document sent to the administration, that these
    deferrals **do not expire** — unlike the four-year limit that Art. 49 LIRPF
    puts on carrying a negative savings balance forward. The published material
    contains no expiry, and this pins the engine to behave that way.

    Note what this test can and cannot do: it proves the SOFTWARE never drops a
    deferral through the passage of time. It cannot prove the absence of a rule;
    that is an argument from silence and belongs with an adviser.
    """

    def test_a_deferral_survives_far_beyond_four_years(self):
        engine = TaxEngine(release_policy="definitive")
        engine.process_all(
            [
                ev(date(2015, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2016, 5, 10), EventType.SELL, "100", "50"),  # loss -5000
                ev(date(2016, 5, 20), EventType.BUY, "100", "50"),  # blocks it
                # Nine years pass with the replacement shares simply held.
                ev(date(2025, 9, 10), EventType.SELL, "100", "50"),  # definitive at last
            ]
        )

        assert engine.get_yearly_summary(2016).blocked_losses == Decimal("-5000.0000")
        assert engine.get_yearly_summary(2025).unlocked_historical_losses == Decimal("-5000.0000")


class TestScopeIsListedSecurities:
    """AEAT gives TWO windows: «dos meses» for securities admitted to trading on
    an official secondary market, and **«un año»** for those that are not.

    The engine implements only the two-month window. Every security it is used
    for here is listed, so that is correct — but it is a scope limit, not a
    universal rule, and a silent one is the kind that bites later. This test
    states it out loud: feed it unlisted securities and the blocking window
    would be wrong by ten months.
    """

    def test_a_repurchase_at_six_months_does_not_block(self):
        engine = TaxEngine()
        engine.process_all(
            [
                ev(date(2022, 1, 10), EventType.BUY, "100", "100"),
                ev(date(2023, 6, 10), EventType.SELL, "100", "50"),
                ev(date(2023, 12, 10), EventType.BUY, "100", "50"),  # 6 months later
            ]
        )

        # Correct for a listed security. For an UNLISTED one the one-year window
        # would block this loss, and the engine has no way to express that.
        assert engine.get_yearly_summary(2023).blocked_losses == Decimal("0")
