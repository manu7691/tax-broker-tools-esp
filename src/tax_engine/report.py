"""
Spanish Tax Report rendering (HTML / PDF / console).

Separated from the calculation engine in ``tax_engine.py``: this module turns a
processed :class:`~tax_engine.tax_engine.TaxEngine` into the bilingual HTML/PDF
report and the console ledger/summary tables. It performs no FIFO, wash-sale, or
savings calculations itself — it only reads computed results off the engine.

The HTML report is rendered from the Jinja template ``templates/report.html.j2``;
this module's job for HTML is to assemble the render *context* (view-models for
the heavier aggregation tables) and expose the formatting filters. The console
tables (``print_ledger`` / ``print_tax_summary``) stay as direct ``print`` calls.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader

from .models import (
    EventType,
    SavingsIncomeYear,
)

if TYPE_CHECKING:
    from .portfolio import SecurityResult
    from .tax_engine import TaxEngine


# --- Jinja environment + formatting filters -------------------------------
#
# Autoescape is intentionally OFF: the report embeds its own trusted HTML markup
# and notes from the user's own brokerage files, matching the original
# string-concatenation renderer (which did no escaping). The bilingual filters
# take ``is_es`` explicitly because Jinja macros don't inherit the caller's
# template context.

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _shares_filter(val: Decimal) -> str:
    from .tax_engine import TaxEngine

    return TaxEngine.format_shares(val)


def _localized_date(d: date, is_es: bool) -> str:
    return d.strftime("%d/%m/%Y") if is_es else d.isoformat()


_EVENT_TYPE_ES = {
    EventType.VEST: "Concesión",
    EventType.SELL: "Venta",
    EventType.BUY: "Compra",
    EventType.EXERCISE: "Ejercicio",
}

# Common English ledger notes -> Spanish, applied as ordered substring
# replacements. Order matters: longer/more specific phrases come before the
# shorter ones they contain (e.g. the "Options …" phrases before "Stock Option").
_NOTE_REPLACEMENTS_ES = [
    ("Wash Sale Blocked Loss", "Pérdida Bloqueada Regla 2 Meses"),
    ("RSU Vest", "Concesión RSU"),
    ("ESPP Purchase", "Compra ESPP"),
    ("Sell-to-Cover (Auto-detected)", "Venta para Impuestos (Automático)"),
    ("Pending Settlement", "Pendiente de Liquidación"),
    ("Manual Sell", "Venta Manual"),
    ("Sell Order", "Orden de Venta"),
    ("Options Same-Day Sale", "Venta Mismo Día de Opciones"),
    ("Options Exercise", "Ejercicio de Opciones"),
    ("Same-Day Sale", "Venta Mismo Día"),
    ("Restricted Stock", "Acciones Restringidas"),
    ("Stock Option", "Opción sobre Acciones"),
    ("Revolut Buy", "Compra Revolut"),
    ("Revolut Sell", "Venta Revolut"),
    ("Includes", "Incluye"),
    ("strike", "precio ejercicio"),
    ("order", "orden"),
    ("Unknown", "Desconocido"),
    ("fees", "comisiones"),
]


def _translate_event_type(event_type: EventType, is_es: bool) -> str:
    if is_es:
        return _EVENT_TYPE_ES.get(event_type, event_type.value)
    return event_type.value


def _translate_notes(notes: str, is_es: bool) -> str:
    if not is_es:
        return notes
    for en, es in _NOTE_REPLACEMENTS_ES:
        notes = notes.replace(en, es)
    return notes


_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,
    keep_trailing_newline=False,
)
_ENV.filters["num2"] = lambda x: f"{x:,.2f}"
_ENV.filters["num4"] = lambda x: f"{x:,.4f}"
_ENV.filters["fx4"] = lambda x: f"{x:.4f}"
_ENV.filters["shares"] = _shares_filter
_ENV.filters["ld"] = _localized_date
_ENV.filters["tetype"] = _translate_event_type
_ENV.filters["tnotes"] = _translate_notes


class ReportRenderer:
    """Renders a processed :class:`TaxEngine` into reports and console output."""

    def __init__(self, engine: "TaxEngine") -> None:
        self.engine = engine

    @staticmethod
    def _max_complete_year() -> int:
        """Last fully-elapsed tax year. The in-progress current year is excluded
        from every report view (console and PDF) since it isn't declarable yet;
        the FIFO engine still processes those transactions."""
        return date.today().year - 1

    def print_ledger(self) -> None:
        """Print the full transaction ledger in a readable format."""
        max_year = self._max_complete_year()
        print("\n" + "=" * 120)
        print("TRANSACTION LEDGER (Spanish FIFO)")
        print(
            f"(complete tax years only — through {max_year}; {max_year + 1} in progress, excluded)"
        )
        print("=" * 120)
        print(
            f"{'Date':<12} {'Type':<6} {'Shares':>10} {'Price USD':>12} "
            f"{'FX Rate':>10} {'Price EUR':>12} {'Total Qty':>10} "
            f"{'Avg Cost':>12} {'Gain/Loss':>12}"
        )
        print("-" * 120)

        for pe in self.engine.processed_events:
            if pe.event.event_date.year > max_year:
                continue
            e = pe.event
            shares_sign = "+" if e.event_type != EventType.SELL else "-"
            shares_str = f"{shares_sign}{self.engine.format_shares(e.shares)}"
            gain_str = f"€{pe.realized_gain_loss:,.4f}" if pe.realized_gain_loss != 0 else ""

            wash_sale_info = ""
            if (
                pe.event.event_type == EventType.SELL
                and pe.realized_gain_loss < 0
                and "Wash Sale Blocked Loss" in pe.event.notes
            ):
                wash_sale_info = " (Wash Blocked)"

            print(
                f"{e.event_date.isoformat():<12} {e.event_type.value:<6} "
                f"{shares_str:>10} ${e.price_usd:>11,.2f} "
                f"{e.resolved_fx_rate:>10.4f} €{e.price_eur:>11,.4f} "
                f"{pe.total_shares_after:>10,.0f} €{pe.avg_cost_eur_after:>11,.4f} "
                f"{gain_str + wash_sale_info:>12}"
            )

            if pe.fifo_matches:
                for match in pe.fifo_matches:
                    print(
                        f"   └─ FIFO Match: {self.engine.format_shares(match.shares)} shares acq on {match.acquisition_date.isoformat()} "
                        f"cost €{match.acquisition_price_eur:,.4f} -> Gain/Loss: €{match.realized_gain_loss:,.4f}"
                    )

        print("=" * 120)

    def print_tax_summary(
        self,
        opening_losses: dict[int, Decimal] | None = None,
        savings_income: dict[int, SavingsIncomeYear] | None = None,
    ) -> None:
        """Print the yearly tax summary."""
        max_year = self._max_complete_year()
        summaries = [s for s in self.engine.get_all_yearly_summaries() if s.year <= max_year]
        print("\n" + "=" * 95)
        print("YEARLY TAX SUMMARY (Spain)")
        print(
            f"(complete tax years only — through {max_year}; {max_year + 1} in progress, excluded)"
        )
        print("=" * 95)
        print(
            f"{'Year':<8} {'Total Gains':>15} {'Total Losses':>15} {'Blocked Loss':>15} "
            f"{'Taxable Base':>15} {'Est. Tax':>15}"
        )
        print("-" * 95)

        for summary in summaries:
            print(
                f"{summary.year:<8} €{summary.total_gains:>14,.2f} "
                f"€{summary.total_losses:>14,.2f} €{summary.blocked_losses:>14,.2f} "
                f"€{summary.taxable_gain:>14,.2f} €{summary.tax_due:>14,.2f}"
            )

        print("=" * 95)

        print("\nSPANISH RENTA (Modelo 100 - Base Imponible del Ahorro)")
        print("-" * 95)
        print("Report these values under capital gains from stock transfers:")
        print()
        for summary in summaries:
            print(f"  Year {summary.year}:")
            print(f"    Total Realized Gains:      €{summary.total_gains:>12,.2f}")
            print(f"    Total Realized Losses:     €{summary.total_losses:>12,.2f}")
            print(f"    Blocked Losses (2-month):  €{summary.blocked_losses:>12,.2f}")
            print(f"    Total Fees Deducted:       €{summary.total_fees_eur:>12,.2f}")
            print(f"    Net Taxable Capital Gains: €{summary.taxable_gain:>12,.2f}")
            print(f"    Estimated Tax Due:         €{summary.tax_due:>12,.2f}")
            print()

        if savings_income:
            # Two-bucket savings base supersedes the single-bucket ledger to avoid
            # contradictory pending balances (cross-offset changes what carries forward).
            sledger = self.engine.compute_savings_ledger(
                savings_income, opening_losses=opening_losses, max_year=max_year
            )
            print("\nSAVINGS BASE — CAPITAL GAINS + DIVIDENDS/INTEREST (Art. 48 & 49 LIRPF)")
            print("-" * 95)
            print(
                f"{'Year':<8} {'Capital G/L':>15} {'Div+Interest':>15} "
                f"{'Cross Offset':>15} {'Savings Base':>15} {'Foreign Tax':>13}"
            )
            for r in sledger.rows:
                print(
                    f"{r.year:<8} €{r.gp_net:>14,.2f} €{r.rcm_net:>14,.2f} "
                    f"€{r.cross_offset:>14,.2f} €{r.savings_base:>14,.2f} €{r.foreign_tax_eur:>12,.2f}"
                )
            if sledger.gp_pending_end:
                print("\n  Pending capital losses carried forward:")
                for oy, rem, ub in sledger.gp_pending_end:
                    print(f"    From {oy}: €{rem:>12,.2f}  ->  use by {ub}")
            if sledger.rcm_pending_end:
                print("\n  Pending RCM (dividend/interest) losses carried forward:")
                for oy, rem, ub in sledger.rcm_pending_end:
                    print(f"    From {oy}: €{rem:>12,.2f}  ->  use by {ub}")
            if sledger.expired:
                print("\n  ⚠️  Losses that EXPIRED unused (4-year limit passed):")
                for bucket, oy, amount in sledger.expired:
                    print(f"    {bucket} from {oy}: €{amount:>12,.2f} lost")
            if sledger.total_foreign_tax > 0:
                print(
                    f"\n  Foreign tax withheld total: €{sledger.total_foreign_tax:,.2f} "
                    "(claim as deducción por doble imposición — advisor)."
                )
        else:
            # 4-Year Loss Carryforward Ledger (capital gains only)
            ledger = self.engine.compute_carryforward(opening_losses, max_year=max_year)
            print("\nLOSS CARRYFORWARD LEDGER (Art. 49 LIRPF)")
            print("-" * 95)
            if opening_losses:
                seeded = ", ".join(
                    f"{y}: €{abs(Decimal(str(a))):,.2f}" for y, a in sorted(opening_losses.items())
                )
                print(f"Seeded with prior-year pending losses -> {seeded}")
            print(
                f"{'Year':<8} {'Net Result':>15} {'Prior Loss Applied':>20} "
                f"{'Taxable After C/F':>20}"
            )
            for cf_row in ledger.rows:
                print(
                    f"{cf_row.year:<8} €{cf_row.net_result:>14,.2f} €{cf_row.prior_losses_applied:>19,.2f} "
                    f"€{cf_row.taxable_after:>19,.2f}"
                )
            if ledger.pending_end:
                print("\n  Pending losses carried forward (still usable):")
                for origin_year, remaining, use_by in ledger.pending_end:
                    print(f"    From {origin_year}: €{remaining:>12,.2f}  ->  use by {use_by}")
            if ledger.expired:
                print("\n  ⚠️  Losses that EXPIRED unused (4-year limit passed):")
                for origin_year, amount in ledger.expired:
                    print(f"    From {origin_year}: €{amount:>12,.2f} lost")
            if (
                not ledger.pending_end
                and not ledger.expired
                and all(cf_row.prior_losses_applied == 0 for cf_row in ledger.rows)
            ):
                print("  No losses to carry forward.")

        print("\n" + "=" * 95)
        print(
            "  NOTE: Transaction Fees (Commissions, SEC Fees) ARE DEDUCTED (Wire Transfers EXCLUDED)"
        )
        print("  from your capital gains automatically, per Spanish Tax Law (Gastos Inherentes).")
        print("  NOTE: 'Est. Tax' is an ISOLATED estimate on these stock gains only — your real")
        print("  liability depends on total savings income and prior-year loss carryforward.")
        print("=" * 95 + "\n")

    def _portfolio_context(
        self, securities: "list[SecurityResult]", max_year: int | None = None
    ) -> dict[str, Any]:
        """Per-security rollup view-model (gains / deductible losses / net / position).

        ``max_year`` bounds the report to complete tax years: realized gains/losses
        sum only years ``<= max_year`` and the share count is the position as of the
        end of that year, so the in-progress current year is excluded everywhere.
        """
        rows = []
        total_gains = total_losses = total_net = Decimal("0")
        for r in securities:
            events = [
                pe
                for pe in r.engine.processed_events
                if max_year is None or pe.event.event_date.year <= max_year
            ]
            summaries = [
                s
                for s in r.engine.get_all_yearly_summaries()
                if max_year is None or s.year <= max_year
            ]
            gains = sum((s.total_gains for s in summaries), Decimal("0"))
            losses = sum((s.deductible_losses for s in summaries), Decimal("0"))
            net = gains + losses
            # Open position as of the end of the last complete year (FIFO running
            # balance after the last in-window event); falls back to 0 if none.
            shares = events[-1].total_shares_after if events else Decimal("0")
            total_gains += gains
            total_losses += losses
            total_net += net
            brokers = ", ".join(sorted({pe.event.broker for pe in events})) or "—"
            rows.append(
                {
                    "label": r.security.label,
                    "isin": r.security.isin,
                    "brokers": brokers,
                    "gains": gains,
                    "losses": losses,
                    "net": net,
                    "shares": shares,
                }
            )
        return {
            "portfolio_rows": rows,
            "portfolio_totals": {"gains": total_gains, "losses": total_losses, "net": total_net},
        }

    def _broker_context(self, max_year: int | None = None) -> dict[str, Any]:
        """Per-broker realized G/L view-model, attributed to the selling broker."""
        gains: dict[str, Decimal] = {}
        losses: dict[str, Decimal] = {}
        for pe in self.engine.processed_events:
            if pe.event.event_type != EventType.SELL:
                continue
            if max_year is not None and pe.event.event_date.year > max_year:
                continue
            b = pe.event.broker
            if pe.realized_gain_loss > 0:
                gains[b] = gains.get(b, Decimal("0")) + pe.realized_gain_loss
            elif pe.realized_gain_loss < 0:
                losses[b] = losses.get(b, Decimal("0")) + pe.realized_gain_loss

        rows = []
        total_gain = total_loss = Decimal("0")
        for b in sorted(set(gains) | set(losses)):
            gain_amt = gains.get(b, Decimal("0"))
            loss_amt = losses.get(b, Decimal("0"))
            total_gain += gain_amt
            total_loss += loss_amt
            rows.append(
                {"broker": b, "gains": gain_amt, "losses": loss_amt, "net": gain_amt + loss_amt}
            )
        return {
            "broker_rows": rows,
            "broker_totals": {
                "gains": total_gain,
                "losses": total_loss,
                "net": total_gain + total_loss,
            },
        }

    def _transmisiones_context(self, max_year: int | None = None) -> dict[str, Any]:
        """Per-disposal capital-gains rows (one row per FIFO lot consumed by a sale)."""
        sells = [
            pe
            for pe in self.engine.processed_events
            if pe.event.event_type == EventType.SELL
            and pe.fifo_matches
            and (max_year is None or pe.event.event_date.year <= max_year)
        ]
        rows = []
        total_net = Decimal("0")
        for pe in sells:
            e = pe.event
            sale_fees_eur = (
                (e.fees_usd * e.resolved_fx_rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
                if e.fees_usd > 0
                else Decimal("0")
            )
            for m in pe.fifo_matches:
                acq_value = (m.acquisition_price_eur * m.shares).quantize(
                    Decimal("0.01"), ROUND_HALF_UP
                )
                transm_value = (e.price_eur * m.shares).quantize(Decimal("0.01"), ROUND_HALF_UP)
                fee_alloc = (
                    (sale_fees_eur * m.shares / e.shares).quantize(Decimal("0.01"), ROUND_HALF_UP)
                    if e.shares > 0
                    else Decimal("0")
                )
                net = (m.realized_gain_loss - fee_alloc).quantize(Decimal("0.01"), ROUND_HALF_UP)
                total_net += net
                rows.append(
                    {
                        "symbol": e.symbol,
                        "isin": e.isin,
                        "acq_date": m.acquisition_date,
                        "acq_value": acq_value,
                        "transm_date": e.event_date,
                        "transm_value": transm_value,
                        "fee": fee_alloc,
                        "net": net,
                    }
                )
        return {"transm_rows": rows, "transm_total": total_net}

    def _hacienda_summary_context(
        self,
        transm_rows: list[dict[str, Any]],
        loss_ctx: dict[str, Any],
        savings_income: dict[int, SavingsIncomeYear] | None,
        espp_early_sale_discounts: dict[int, Decimal] | None,
        max_year: int | None = None,
    ) -> dict[str, Any]:
        """Per-year "what to declare" view-model grouping the three IRPF buckets.

        One row per relevant year, each bundling: (1) rendimientos del trabajo
        (only ESPP early-sale discounts — ordinary vest/ESPP income is already on
        the payroll and pre-filled by Hacienda, so it is shown as a note, not a
        figure), (2) ganancias y pérdidas patrimoniales (valor de transmisión /
        adquisición, fees, blocked 2-month losses, and the *deductible* net that
        actually feeds the base), and (3) RCM (dividendos + intereses).

        The capital-gains figures come from the engine's yearly summary, **not**
        from the per-lot disposal rows: the disposal rows carry the *raw* realized
        result (which still includes blocked wash-sale losses), whereas the saldo
        that integrates into the savings base excludes them. Mixing the two is
        what made the displayed G/P look smaller than the resulting base. The
        valor de transmisión / adquisición totals still come from the disposal
        rows (those are gross, pre-fee values). The "base del ahorro integrada"
        reuses the loss-ledger result so cross-offset and 4-year carryforward stay
        consistent with the tables below.
        """
        # Integrated savings base per year (after cross-offset / carryforward),
        # taken from whichever loss ledger is active so figures never contradict.
        base_by_year: dict[int, Decimal] = {}
        if loss_ctx.get("use_savings"):
            for row in loss_ctx["sledger"].rows:
                base_by_year[row.year] = row.savings_base
        else:
            for row in loss_ctx["cf_ledger"].rows:
                base_by_year[row.year] = row.taxable_after

        # Deductible net / fees / blocked losses come from the engine summary so
        # the saldo reconciles with the base (raw disposal nets include blocked
        # losses that the base excludes).
        summary_by_year = {s.year: s for s in self.engine.get_all_yearly_summaries()}

        years: set[int] = {r["transm_date"].year for r in transm_rows}
        years |= set(base_by_year)
        years |= set(summary_by_year)
        if savings_income:
            years |= set(savings_income)
        if espp_early_sale_discounts:
            years |= set(espp_early_sale_discounts)
        if max_year is not None:
            years = {y for y in years if y <= max_year}

        rows = []
        for y in sorted(years):
            sells = [r for r in transm_rows if r["transm_date"].year == y]
            transm_value = sum((r["transm_value"] for r in sells), Decimal("0"))
            acq_value = sum((r["acq_value"] for r in sells), Decimal("0"))
            summary = summary_by_year.get(y)
            fees = summary.total_fees_eur if summary else Decimal("0")
            # blocked_losses is stored negative; show the add-back as positive.
            blocked = -summary.blocked_losses if summary else Decimal("0")
            saldo_neto = summary.net_gain_loss if summary else Decimal("0")
            si = savings_income.get(y) if savings_income else None
            rows.append(
                {
                    "year": y,
                    "espp_early": (
                        espp_early_sale_discounts.get(y) if espp_early_sale_discounts else None
                    ),
                    "has_disposals": bool(sells),
                    "transm_value": transm_value,
                    "acq_value": acq_value,
                    "fees": fees,
                    "blocked": blocked,
                    "saldo_neto": saldo_neto,
                    "dividends": si.dividends_eur if si else Decimal("0"),
                    "interest": si.interest_eur if si else Decimal("0"),
                    "rcm": si.rcm_net if si else Decimal("0"),
                    "foreign_tax": si.foreign_tax_eur if si else Decimal("0"),
                    "base_ahorro": base_by_year.get(y, max(saldo_neto, Decimal("0"))),
                }
            )
        return {"hacienda_years": rows}

    def _loss_context(
        self,
        savings_income: dict[int, SavingsIncomeYear] | None,
        opening_losses: dict[int, Decimal] | None,
        max_year: int | None = None,
    ) -> dict[str, Any]:
        """Either the two-bucket savings ledger or the single-bucket carryforward.

        The two-bucket savings base supersedes the single-bucket ledger when
        dividend/interest is supplied (the cross-offset changes what carries
        forward, so showing both would contradict). ``max_year`` bounds both
        simulations to complete tax years.
        """
        if savings_income:
            sledger = self.engine.compute_savings_ledger(
                savings_income, opening_losses=opening_losses, max_year=max_year
            )
            over15: list[tuple[int, Decimal]] = []
            if sledger.total_foreign_tax > 0:
                over15 = [
                    (yr, (inc.foreign_tax_eur / inc.dividends_eur * 100).quantize(Decimal("0.1")))
                    for yr, inc in sorted(savings_income.items())
                    if inc.dividends_eur > 0
                    and inc.foreign_tax_eur > inc.dividends_eur * Decimal("0.15")
                    and (max_year is None or yr <= max_year)
                ]
            return {
                "use_savings": True,
                "sledger": sledger,
                "over15_flagged": (
                    ", ".join(f"{yr} ({rate}%)" for yr, rate in over15) if over15 else None
                ),
            }

        cf = self.engine.compute_carryforward(opening_losses, max_year=max_year)
        seed_str = (
            ", ".join(
                f"{y}: €{abs(Decimal(str(a))):,.2f}" for y, a in sorted(opening_losses.items())
            )
            if opening_losses
            else None
        )
        return {
            "use_savings": False,
            "cf_ledger": cf,
            "cf_no_losses": (
                not cf.pending_end
                and not cf.expired
                and all(r.prior_losses_applied == 0 for r in cf.rows)
            ),
            "seed_str": seed_str,
        }

    def generate_html_content(
        self,
        lang: str = "en",
        espp_discounts: dict[int, Decimal] | None = None,
        espp_early_sale_discounts: dict[int, Decimal] | None = None,
        opening_losses: dict[int, Decimal] | None = None,
        savings_income: dict[int, SavingsIncomeYear] | None = None,
        securities: "list[SecurityResult] | None" = None,
    ) -> str:
        """Generate HTML content for the tax report (supports 'en' and 'es')."""
        is_es = lang.lower() == "es"
        engine = self.engine

        # The report only covers complete tax years: the in-progress current year
        # is excluded everywhere (it isn't declarable yet). The FIFO engine still
        # processes those transactions — only the report view is bounded — so a
        # current-year sale correctly consumes prior-year lots in the calculation.
        max_year = date.today().year - 1

        def _in_window(events: "list[Any]") -> "list[Any]":
            return [pe for pe in events if pe.event.event_date.year <= max_year]

        # Distinct brokers across the in-window transactions. Used to tag each
        # ledger row and, when more than one broker is present (e.g. E*TRADE +
        # Revolut), to add a per-broker realized G/L breakdown.
        brokers = sorted({pe.event.broker for pe in _in_window(engine.processed_events)})
        multi_broker = len(brokers) > 1

        ctx: dict[str, Any] = {
            "is_es": is_es,
            "today_str": (date.today().strftime("%d/%m/%Y") if is_es else date.today().isoformat()),
            "max_year": max_year,
            "brokers": brokers,
            "brokers_joined": ", ".join(brokers),
            "multi_broker": multi_broker,
            "summaries": [s for s in engine.get_all_yearly_summaries() if s.year <= max_year],
            "securities": securities,
            "espp_discounts": espp_discounts,
            "espp_early_sale_discounts": espp_early_sale_discounts,
            "espp_early_sale_rows": (
                sorted((y, a) for y, a in espp_early_sale_discounts.items() if y <= max_year)
                if espp_early_sale_discounts
                else []
            ),
        }

        if securities is not None:
            ctx.update(self._portfolio_context(securities, max_year=max_year))
            ctx["ledger_sections"] = [
                {
                    "head": (
                        f"{r.security.label} ({r.security.isin})"
                        if r.security.isin
                        else r.security.label
                    ),
                    "events": _in_window(r.engine.processed_events),
                }
                for r in securities
            ]
        else:
            ctx["single_events"] = _in_window(engine.processed_events)

        if multi_broker:
            ctx.update(self._broker_context(max_year=max_year))

        loss_ctx = self._loss_context(savings_income, opening_losses, max_year=max_year)
        ctx.update(loss_ctx)
        transm_ctx = self._transmisiones_context(max_year=max_year)
        ctx.update(transm_ctx)
        ctx.update(
            self._hacienda_summary_context(
                transm_ctx["transm_rows"],
                loss_ctx,
                savings_income,
                espp_early_sale_discounts,
                max_year=max_year,
            )
        )

        return _ENV.get_template("report.html.j2").render(**ctx)

    def generate_pdf_report(
        self,
        filepath: str,
        lang: str = "en",
        espp_discounts: dict[int, Decimal] | None = None,
        espp_early_sale_discounts: dict[int, Decimal] | None = None,
        opening_losses: dict[int, Decimal] | None = None,
        savings_income: dict[int, SavingsIncomeYear] | None = None,
        securities: "list[SecurityResult] | None" = None,
    ) -> None:
        """Generate a PDF tax report using Playwright (supports lang='en' or lang='es')."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("Error: Playwright is not installed. Cannot generate PDF.")
            print("Please install it with: pip install playwright && playwright install")
            return

        html_content = self.generate_html_content(
            lang=lang,
            espp_discounts=espp_discounts,
            espp_early_sale_discounts=espp_early_sale_discounts,
            opening_losses=opening_losses,
            savings_income=savings_income,
            securities=securities,
        )

        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch()
                except Exception as e:
                    print(f"Error launching browser: {e}")
                    print("Attempting to install browsers...")
                    import subprocess

                    subprocess.run(["playwright", "install", "chromium"])
                    browser = p.chromium.launch()

                page = browser.new_page()
                page.set_content(html_content)
                page.pdf(
                    path=filepath,
                    format="A4",
                    margin={"top": "2cm", "bottom": "2cm", "left": "2cm", "right": "2cm"},
                )
                browser.close()
        except Exception as e:
            print(f"Failed to generate PDF: {e}")
