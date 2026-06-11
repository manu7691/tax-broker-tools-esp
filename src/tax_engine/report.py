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

# Common English ledger notes -> Spanish, applied as ordered substring replacements.
_NOTE_REPLACEMENTS_ES = [
    ("RSU Vest", "Concesión RSU"),
    ("ESPP Purchase", "Compra ESPP"),
    ("Sell-to-Cover (Auto-detected)", "Venta para Impuestos (Automático)"),
    ("Pending Settlement", "Pendiente de Liquidación"),
    ("Manual Sell", "Venta Manual"),
    ("Sell Order", "Orden de Venta"),
    ("Revolut Buy", "Compra Revolut"),
    ("Revolut Sell", "Venta Revolut"),
    ("Includes", "Incluye"),
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


# --- Modelo 100 crosswalk rows (apartado names are stable; casillas shift) ---
#
# Box numbers ("casillas") change almost every campaign, so the guide anchors on
# the stable section names (*apartados*) and flags the casilla numbers as values
# to verify against the current year's form.
_MODELO100_ROWS_EN: list[tuple[str, str, str]] = [
    (
        "Each sale: transmission value, acquisition value, gain/loss",
        "Ganancias y pérdidas patrimoniales derivadas de la transmisión de acciones negociadas en mercados oficiales (base del ahorro)",
        "≈ 0328–0344",
    ),
    (
        "Net balance of gains/losses (savings base)",
        "Saldo neto de ganancias y pérdidas patrimoniales a integrar en la base imponible del ahorro",
        "≈ 0424 / 0425",
    ),
    (
        "Dividends & interest (RCM)",
        "Rendimientos del capital mobiliario a integrar en la base imponible del ahorro (dividends, interest)",
        "≈ 0027–0031",
    ),
    (
        "Foreign tax withheld (US)",
        "Deducción por doble imposición internacional",
        "≈ 0588 / 0589",
    ),
    (
        "Prior-year pending losses (this ledger's 'pending')",
        "Saldos netos negativos de ejercicios anteriores pendientes de compensar (one box per origin year)",
        "≈ 0439–0443",
    ),
    ("Resulting savings tax base", "Base imponible del ahorro", "≈ 0460"),
    (
        "ESPP early-sale discount (if any)",
        "Rendimientos del trabajo — via Declaración Complementaria for the purchase year",
        "trabajo boxes",
    ),
]
_MODELO100_ROWS_ES: list[tuple[str, str, str]] = [
    (
        "Cada venta: valor de transmisión, de adquisición y ganancia/pérdida",
        "Ganancias y pérdidas patrimoniales derivadas de la transmisión de acciones negociadas en mercados oficiales (base del ahorro)",
        "≈ 0328–0344",
    ),
    (
        "Saldo neto de ganancias y pérdidas (base del ahorro)",
        "Saldo neto de ganancias y pérdidas patrimoniales a integrar en la base imponible del ahorro",
        "≈ 0424 / 0425",
    ),
    (
        "Dividendos e intereses (RCM)",
        "Rendimientos del capital mobiliario a integrar en la base imponible del ahorro (dividendos, intereses)",
        "≈ 0027–0031",
    ),
    (
        "Retención en origen (EE. UU.)",
        "Deducción por doble imposición internacional",
        "≈ 0588 / 0589",
    ),
    (
        "Pérdidas pendientes de años anteriores ('pendientes' de este libro)",
        "Saldos netos negativos de ejercicios anteriores pendientes de compensar (una casilla por año de origen)",
        "≈ 0439–0443",
    ),
    ("Base imponible del ahorro resultante", "Base imponible del ahorro", "≈ 0460"),
    (
        "Descuento ESPP por venta anticipada (si lo hay)",
        "Rendimientos del trabajo — mediante Declaración Complementaria del año de compra",
        "casillas de trabajo",
    ),
]


class ReportRenderer:
    """Renders a processed :class:`TaxEngine` into reports and console output."""

    def __init__(self, engine: "TaxEngine") -> None:
        self.engine = engine

    def print_ledger(self) -> None:
        """Print the full transaction ledger in a readable format."""
        print("\n" + "=" * 120)
        print("TRANSACTION LEDGER (Spanish FIFO)")
        print("=" * 120)
        print(
            f"{'Date':<12} {'Type':<6} {'Shares':>10} {'Price USD':>12} "
            f"{'FX Rate':>10} {'Price EUR':>12} {'Total Qty':>10} "
            f"{'Avg Cost':>12} {'Gain/Loss':>12}"
        )
        print("-" * 120)

        for pe in self.engine.processed_events:
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
        print("\n" + "=" * 95)
        print("YEARLY TAX SUMMARY (Spain)")
        print("=" * 95)
        print(
            f"{'Year':<8} {'Total Gains':>15} {'Total Losses':>15} {'Blocked Loss':>15} "
            f"{'Taxable Base':>15} {'Est. Tax':>15}"
        )
        print("-" * 95)

        for summary in self.engine.get_all_yearly_summaries():
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
        for summary in self.engine.get_all_yearly_summaries():
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
                savings_income, opening_losses=opening_losses
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
            ledger = self.engine.compute_carryforward(opening_losses)
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

    def _portfolio_context(self, securities: "list[SecurityResult]") -> dict[str, Any]:
        """Per-security rollup view-model (gains / deductible losses / net / position)."""
        rows = []
        total_gains = total_losses = total_net = Decimal("0")
        for r in securities:
            summaries = r.engine.get_all_yearly_summaries()
            gains = sum((s.total_gains for s in summaries), Decimal("0"))
            losses = sum((s.deductible_losses for s in summaries), Decimal("0"))
            net = r.net_gain_loss
            total_gains += gains
            total_losses += losses
            total_net += net
            brokers = (
                ", ".join(sorted({pe.event.broker for pe in r.engine.processed_events})) or "—"
            )
            rows.append(
                {
                    "label": r.security.label,
                    "isin": r.security.isin,
                    "brokers": brokers,
                    "gains": gains,
                    "losses": losses,
                    "net": net,
                    "shares": r.engine.state.total_shares,
                }
            )
        return {
            "portfolio_rows": rows,
            "portfolio_totals": {"gains": total_gains, "losses": total_losses, "net": total_net},
        }

    def _broker_context(self) -> dict[str, Any]:
        """Per-broker realized G/L view-model, attributed to the selling broker."""
        gains: dict[str, Decimal] = {}
        losses: dict[str, Decimal] = {}
        for pe in self.engine.processed_events:
            if pe.event.event_type != EventType.SELL:
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

    def _transmisiones_context(self) -> dict[str, Any]:
        """Per-disposal capital-gains rows (one row per FIFO lot consumed by a sale)."""
        sells = [
            pe
            for pe in self.engine.processed_events
            if pe.event.event_type == EventType.SELL and pe.fifo_matches
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

    def _loss_context(
        self,
        savings_income: dict[int, SavingsIncomeYear] | None,
        opening_losses: dict[int, Decimal] | None,
    ) -> dict[str, Any]:
        """Either the two-bucket savings ledger or the single-bucket carryforward.

        The two-bucket savings base supersedes the single-bucket ledger when
        dividend/interest is supplied (the cross-offset changes what carries
        forward, so showing both would contradict).
        """
        if savings_income:
            sledger = self.engine.compute_savings_ledger(
                savings_income, opening_losses=opening_losses
            )
            over15: list[tuple[int, Decimal]] = []
            if sledger.total_foreign_tax > 0:
                over15 = [
                    (yr, (inc.foreign_tax_eur / inc.dividends_eur * 100).quantize(Decimal("0.1")))
                    for yr, inc in sorted(savings_income.items())
                    if inc.dividends_eur > 0
                    and inc.foreign_tax_eur > inc.dividends_eur * Decimal("0.15")
                ]
            return {
                "use_savings": True,
                "sledger": sledger,
                "over15_flagged": (
                    ", ".join(f"{yr} ({rate}%)" for yr, rate in over15) if over15 else None
                ),
            }

        cf = self.engine.compute_carryforward(opening_losses)
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

        # Distinct brokers across the processed transactions. Used to tag each
        # ledger row and, when more than one broker is present (e.g. E*TRADE +
        # Revolut), to add a per-broker realized G/L breakdown.
        brokers = sorted({pe.event.broker for pe in engine.processed_events})
        multi_broker = len(brokers) > 1

        ctx: dict[str, Any] = {
            "is_es": is_es,
            "today_str": (date.today().strftime("%d/%m/%Y") if is_es else date.today().isoformat()),
            "brokers": brokers,
            "brokers_joined": ", ".join(brokers),
            "multi_broker": multi_broker,
            "summaries": engine.get_all_yearly_summaries(),
            "securities": securities,
            "modelo100_rows": _MODELO100_ROWS_ES if is_es else _MODELO100_ROWS_EN,
            "espp_discounts": espp_discounts,
            "espp_early_sale_discounts": espp_early_sale_discounts,
            "espp_early_sale_rows": (
                sorted(espp_early_sale_discounts.items()) if espp_early_sale_discounts else []
            ),
        }

        if securities is not None:
            ctx.update(self._portfolio_context(securities))
            ctx["ledger_sections"] = [
                {
                    "head": (
                        f"{r.security.label} ({r.security.isin})"
                        if r.security.isin
                        else r.security.label
                    ),
                    "events": r.engine.processed_events,
                }
                for r in securities
            ]
        else:
            ctx["single_events"] = engine.processed_events

        if multi_broker:
            ctx.update(self._broker_context())

        ctx.update(self._loss_context(savings_income, opening_losses))
        ctx.update(self._transmisiones_context())

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
