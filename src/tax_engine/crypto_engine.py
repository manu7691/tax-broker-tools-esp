"""
Crypto capital-gains orchestrator for the Spanish Tax Engine.

Runs one FIFO :class:`TaxEngine` per coin (homogeneous assets are matched per
coin, across all exchanges), then aggregates the per-coin results into combined
yearly summaries that plug into the same savings-base machinery (Art. 48 & 49
LIRPF) used for stocks.

Design choices:

* **Stablecoins are USD cash** — only BTC/SOL/ONDO/… generate gains/losses;
  the EUR value of each leg comes from the ECB USD/EUR rate of the trade date.
* **True chronological order** — trades are fed to the engine in timestamp order
  so intraday buy/sell sequences and cross-exchange merges are matched correctly.
* **No 2-month wash-sale rule by default** — its applicability to crypto is
  unsettled at AEAT; enable explicitly if your advisor wants it.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .ecb_rates import prefetch_ecb_rates
from .models import (
    CarryforwardLedger,
    EventType,
    ProcessedEvent,
    SavingsIncomeYear,
    SavingsLedger,
    StockEvent,
    YearlyTaxSummary,
)
from .tax_engine import TaxEngine

# Coin colours for charts (matches Chart.js palette)
_COIN_COLOURS = [
    "#3B82F6",
    "#8B5CF6",
    "#F59E0B",
    "#10B981",
    "#EF4444",
    "#06B6D4",
    "#F97316",
    "#6366F1",
    "#EC4899",
    "#84CC16",
]


@dataclass
class OpenPosition:
    """A coin still held at the end of the processed period (unrealised)."""

    coin: str
    quantity: Decimal
    cost_basis_eur: Decimal

    @property
    def avg_cost_eur(self) -> Decimal:
        if self.quantity <= 0:
            return Decimal("0")
        return (self.cost_basis_eur / self.quantity).quantize(Decimal("0.0001"), ROUND_HALF_UP)


@dataclass
class DisposalRow:
    """One realised disposal (SELL), for the per-trade export."""

    date: str
    coin: str
    quantity: Decimal
    proceeds_eur: Decimal
    cost_basis_eur: Decimal
    fees_eur: Decimal
    gain_eur: Decimal
    notes: str


class CryptoTaxEngine:
    """Per-coin FIFO orchestration over a set of crypto trades."""

    def __init__(self, detect_wash_sale: bool = False) -> None:
        self.detect_wash_sale = detect_wash_sale
        self.engines: dict[str, TaxEngine] = {}
        self.synthetic_notes: list[str] = []

    def process(self, events_by_coin: dict[str, list[StockEvent]]) -> None:
        """Run a FIFO engine per coin, in true chronological order."""
        all_events = [e for evs in events_by_coin.values() for e in evs]
        prefetch_ecb_rates(all_events)

        for coin, events in events_by_coin.items():
            ordered = self._guard_short_sales(coin, events)
            engine = TaxEngine()
            engine.reset()
            for event in ordered:
                engine.process_event(event)
            if self.detect_wash_sale:
                engine.detect_blocked_losses_spain()
            self.engines[coin] = engine

    def _guard_short_sales(self, coin: str, events: list[StockEvent]) -> list[StockEvent]:
        """Prepend a synthetic opening lot if sells exceed tracked acquisitions."""
        bought = sum((e.shares for e in events if e.event_type == EventType.BUY), Decimal("0"))
        sold = sum((e.shares for e in events if e.event_type == EventType.SELL), Decimal("0"))
        shortfall = sold - bought
        if shortfall <= Decimal("0"):
            return events

        first_sell = next((e for e in events if e.event_type == EventType.SELL), None)
        if first_sell is None:
            return events

        note = (
            f"{coin}: sells exceed tracked buys by {shortfall:f}; added a synthetic "
            f"opening lot at €{first_sell.price_eur:,.4f} (missing acquisition history)."
        )
        self.synthetic_notes.append(note)
        print(f"  Warning: {note}")
        synthetic = StockEvent(
            event_date=events[0].event_date,
            event_type=EventType.BUY,
            shares=shortfall,
            price_usd=first_sell.price_usd,
            fx_rate=first_sell.resolved_fx_rate,
            notes=f"SYNTHETIC opening lot ({coin}) — missing acquisition history",
        )
        return [synthetic, *events]

    # ----- aggregation ---------------------------------------------------

    @property
    def coins(self) -> list[str]:
        return sorted(self.engines)

    def combined_summaries(self) -> dict[int, YearlyTaxSummary]:
        """Sum each coin's per-year gains/losses/fees into combined summaries."""
        combined: dict[int, YearlyTaxSummary] = {}
        for engine in self.engines.values():
            for s in engine.get_all_yearly_summaries():
                agg = combined.setdefault(s.year, YearlyTaxSummary(year=s.year))
                agg.total_gains += s.total_gains
                agg.total_losses += s.total_losses
                agg.blocked_losses += s.blocked_losses
                agg.total_fees_eur += s.total_fees_eur
        return combined

    def aggregate_engine(self) -> TaxEngine:
        """A TaxEngine carrying the combined summaries (for savings/carryforward)."""
        engine = TaxEngine()
        engine.yearly_summaries = self.combined_summaries()
        return engine

    def per_coin_year_results(self) -> dict[str, dict[int, YearlyTaxSummary]]:
        return {
            coin: {s.year: s for s in engine.get_all_yearly_summaries()}
            for coin, engine in self.engines.items()
        }

    def open_positions(self) -> list[OpenPosition]:
        positions: list[OpenPosition] = []
        for coin, engine in self.engines.items():
            qty = engine.state.total_shares
            if qty > 0:
                positions.append(
                    OpenPosition(
                        coin=coin,
                        quantity=qty,
                        cost_basis_eur=engine.state.total_portfolio_cost_eur,
                    )
                )
        return sorted(positions, key=lambda p: p.coin)

    def disposals(self) -> list[DisposalRow]:
        rows: list[DisposalRow] = []
        for coin, engine in self.engines.items():
            for pe in engine.processed_events:
                if pe.event.event_type != EventType.SELL:
                    continue
                rows.append(self._disposal_row(coin, pe))
        rows.sort(key=lambda r: (r.date, r.coin))
        return rows

    def monthly_pnl(self) -> dict[str, float]:
        """Monthly realized P&L totals keyed by 'YYYY-MM', in chronological order."""
        totals: dict[str, float] = {}
        for r in self.disposals():
            month = r.date[:7]
            totals[month] = totals.get(month, 0.0) + float(r.gain_eur)
        return dict(sorted(totals.items()))

    @staticmethod
    def merge_yearly_summaries(
        summaries_list: list[dict[int, YearlyTaxSummary]],
    ) -> dict[int, YearlyTaxSummary]:
        """Merge several engines' yearly-summary dicts into one combined dict."""
        combined: dict[int, YearlyTaxSummary] = {}
        for summaries in summaries_list:
            for year, s in summaries.items():
                agg = combined.setdefault(year, YearlyTaxSummary(year=year))
                agg.total_gains += s.total_gains
                agg.total_losses += s.total_losses
                agg.blocked_losses += s.blocked_losses
                agg.total_fees_eur += s.total_fees_eur
        return combined

    @staticmethod
    def _disposal_row(coin: str, pe: ProcessedEvent) -> DisposalRow:
        e = pe.event
        proceeds = e.total_value_eur
        cost_basis = sum(
            (m.acquisition_price_eur * m.shares for m in pe.fifo_matches), Decimal("0")
        ).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        fees_eur = Decimal("0")
        if e.fees_usd > 0:
            fees_eur = (e.fees_usd / e.resolved_fx_rate).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        return DisposalRow(
            date=e.event_date.isoformat(),
            coin=coin,
            quantity=e.shares,
            proceeds_eur=proceeds,
            cost_basis_eur=cost_basis,
            fees_eur=fees_eur,
            gain_eur=pe.realized_gain_loss,
            notes=e.notes,
        )

    # ----- console output ------------------------------------------------

    def print_console(self) -> None:
        summaries = self.combined_summaries()
        print("\n" + "=" * 92)
        print("CRYPTO CAPITAL GAINS — REALISED RESULT PER COIN (Spanish FIFO, EUR)")
        print("=" * 92)
        per_coin = self.per_coin_year_results()
        years = sorted({y for d in per_coin.values() for y in d})
        print(f"{'Coin':<8}{'Year':<8}{'Gains':>14}{'Losses':>14}{'Net':>14}{'Fees':>12}")
        print("-" * 92)
        for coin in self.coins:
            for year in years:
                s = per_coin[coin].get(year)
                if not s or (s.total_gains == 0 and s.total_losses == 0):
                    continue
                print(
                    f"{coin:<8}{year:<8}€{s.total_gains:>12,.2f}€{s.total_losses:>12,.2f}"
                    f"€{s.net_gain_loss:>12,.2f}€{s.total_fees_eur:>10,.2f}"
                )
        print("-" * 92)
        print("\n" + "=" * 92)
        print("COMBINED YEARLY SUMMARY (Modelo 100 — Base Imponible del Ahorro)")
        print("=" * 92)
        print(
            f"{'Year':<8}{'Total Gains':>15}{'Total Losses':>15}"
            f"{'Net Result':>15}{'Taxable Base':>15}{'Est. Tax*':>15}"
        )
        print("-" * 92)
        for year in sorted(summaries):
            s = summaries[year]
            print(
                f"{year:<8}€{s.total_gains:>13,.2f}€{s.total_losses:>13,.2f}"
                f"€{s.net_gain_loss:>13,.2f}€{s.taxable_gain:>13,.2f}€{s.tax_due:>13,.2f}"
            )
        print("-" * 92)
        print("* Isolated estimate — combine with stocks, dividends and prior-year losses.")
        positions = self.open_positions()
        if positions:
            print("\n" + "=" * 92)
            print("OPEN POSITIONS (unrealised — informational)")
            print("=" * 92)
            print(f"{'Coin':<8}{'Quantity':>20}{'Cost Basis (EUR)':>20}{'Avg Cost (EUR)':>20}")
            print("-" * 92)
            for p in positions:
                print(
                    f"{p.coin:<8}{p.quantity:>20,.6f}€{p.cost_basis_eur:>18,.2f}€{p.avg_cost_eur:>18,.4f}"
                )
            print("-" * 92)
        if self.synthetic_notes:
            print("\n⚠️  Data gaps (synthetic opening lots added):")
            for note in self.synthetic_notes:
                print(f"   - {note}")
        print()

    # ----- CSV export ----------------------------------------------------

    def write_disposals_csv(self, path: Path) -> int:
        rows = self.disposals()
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "date",
                    "coin",
                    "quantity",
                    "proceeds_eur",
                    "cost_basis_eur",
                    "fees_eur",
                    "gain_eur",
                    "notes",
                ]
            )
            for r in rows:
                writer.writerow(
                    [
                        r.date,
                        r.coin,
                        f"{r.quantity:f}",
                        f"{r.proceeds_eur:.2f}",
                        f"{r.cost_basis_eur:.2f}",
                        f"{r.fees_eur:.2f}",
                        f"{r.gain_eur:.2f}",
                        r.notes,
                    ]
                )
        return len(rows)

    # ----- HTML dashboard ------------------------------------------------

    def generate_html(
        self, lang: str = "es", opening_losses: dict[int, Decimal] | None = None
    ) -> str:
        """Generate an interactive tabbed HTML dashboard (lang 'es' or 'en')."""
        is_es = lang.lower() == "es"
        parts: list[str] = []
        parts.append(self._html_head(is_es))
        parts.append(self._html_body(is_es, opening_losses))
        return "".join(parts)

    # --- HTML head -------------------------------------------------------

    @staticmethod
    def _html_head(is_es: bool) -> str:
        title = "Crypto Tax Dashboard — Spain"
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{{
  --bg:#0f172a;--bg2:#1e293b;--bg3:#263351;
  --txt:#f8fafc;--txt2:#94a3b8;
  --green:#10b981;--red:#ef4444;--blue:#3b82f6;
  --yellow:#f59e0b;--purple:#8b5cf6;
  --border:#334155;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:var(--bg);color:var(--txt);padding:24px;}}
h1{{font-size:2rem;font-weight:800;
  background:linear-gradient(135deg,#60a5fa,#a78bfa,#f43f5e);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  margin-bottom:4px;}}
.subtitle{{color:var(--txt2);margin-bottom:24px;font-size:.95rem;}}
h2{{color:#e2e8f0;font-size:1.1rem;margin:24px 0 12px;}}
h3{{color:var(--txt2);font-size:.9rem;margin-bottom:8px;}}
.tabs{{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:24px;
  border-bottom:1px solid var(--border);padding-bottom:0;}}
.tab-btn{{background:transparent;border:none;color:var(--txt2);
  padding:10px 18px;cursor:pointer;font-size:.9rem;border-radius:6px 6px 0 0;
  border-bottom:3px solid transparent;transition:.2s;}}
.tab-btn:hover{{color:var(--txt);background:var(--bg2);}}
.tab-btn.active{{color:var(--blue);border-bottom:3px solid var(--blue);background:var(--bg2);}}
.tab-content{{display:none;}}.tab-content.active{{display:block;}}
table{{border-collapse:collapse;width:100%;font-size:12px;margin-bottom:16px;}}
th,td{{border:1px solid var(--border);padding:7px 10px;text-align:left;}}
th{{background:var(--bg2);color:var(--txt2);font-weight:600;}}
tr:hover td{{background:#1a2744;}}
.gain{{color:var(--green);}}.loss{{color:var(--red);}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;}}
.badge-gain{{background:#052e16;color:var(--green);}}
.badge-loss{{background:#450a0a;color:var(--red);}}
.chart-grid{{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px;}}
@media(max-width:800px){{.chart-grid{{grid-template-columns:1fr;}}}}
.chart-card{{background:var(--bg2);border:1px solid var(--border);
  border-radius:12px;padding:20px;}}
.positions-grid{{display:grid;grid-template-columns:320px 1fr;gap:24px;align-items:start;}}
@media(max-width:800px){{.positions-grid{{grid-template-columns:1fr;}}}}
.kpi-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
  gap:16px;margin-bottom:24px;}}
.kpi{{background:var(--bg2);border:1px solid var(--border);border-radius:12px;
  padding:16px 20px;}}
.kpi-label{{color:var(--txt2);font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;}}
.kpi-value{{font-size:1.5rem;font-weight:700;margin-top:4px;}}
.filter-row{{display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap;}}
select{{background:var(--bg2);border:1px solid var(--border);color:var(--txt);
  padding:6px 12px;border-radius:6px;font-size:13px;cursor:pointer;}}
.disclaimer{{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
  padding:14px 18px;font-size:11px;color:var(--txt2);margin-top:16px;line-height:1.6;}}
.warn{{color:#fb923c;}}
a{{color:var(--blue);}}
</style>
</head>
"""

    # --- HTML body -------------------------------------------------------

    def _html_body(self, is_es: bool, opening_losses: dict[int, Decimal] | None) -> str:
        summaries = self.combined_summaries()
        per_coin = self.per_coin_year_results()
        monthly = self.monthly_pnl()
        positions = self.open_positions()
        disposals = self.disposals()
        agg = self.aggregate_engine()
        ledger = agg.compute_carryforward(opening_losses)

        # KPI values
        total_gains = sum(s.total_gains for s in summaries.values())
        total_losses = sum(s.total_losses for s in summaries.values())
        net_result = total_gains + total_losses
        n_disposals = len(disposals)
        open_cost = sum(p.cost_basis_eur for p in positions)

        tab_labels = ["Summary", "Charts", "Positions", "Modelo 100", f"Disposals ({n_disposals})"]

        h: list[str] = ["<body>\n"]
        h.append("<h1>Crypto Tax Dashboard</h1>\n")
        h.append(
            f"<p class='subtitle'>FIFO per coin · ECB EUR · Art. 49 LIRPF · generated "
            f"{__import__('datetime').date.today().strftime('%Y-%m-%d')}</p>\n"
        )

        # KPIs
        h.append("<div class='kpi-row'>\n")
        kpis = [
            ("Total Gains", f"€{total_gains:,.0f}", "gain"),
            ("Total Losses", f"€{abs(total_losses):,.0f}", "loss"),
            ("Net Result", f"€{net_result:,.0f}", "gain" if net_result >= 0 else "loss"),
            ("Disposals", str(n_disposals), ""),
            ("Open Positions", f"€{open_cost:,.0f}" if positions else "—", ""),
        ]
        for label, val, cls in kpis:
            h.append(
                f"<div class='kpi'><div class='kpi-label'>{label}</div>"
                f"<div class='kpi-value {cls}'>{val}</div></div>\n"
            )
        h.append("</div>\n")

        # Tab buttons
        h.append("<div class='tabs'>\n")
        for i, lbl in enumerate(tab_labels):
            active = " active" if i == 0 else ""
            h.append(f"<button class='tab-btn{active}' onclick=\"showTab({i})\">{lbl}</button>\n")
        h.append("</div>\n")

        # --- Tab 0: Summary ---
        h.append("<div id='tab-0' class='tab-content active'>\n")
        h.extend(self._tab_summary(is_es, summaries, per_coin, ledger, opening_losses))
        h.append("</div>\n")

        # --- Tab 1: Charts ---
        h.append("<div id='tab-1' class='tab-content'>\n")
        h.extend(self._tab_charts(is_es, summaries, per_coin, monthly, positions))
        h.append("</div>\n")

        # --- Tab 2: Positions ---
        h.append("<div id='tab-2' class='tab-content'>\n")
        h.extend(self._tab_positions(is_es, positions))
        h.append("</div>\n")

        # --- Tab 3: Modelo 100 ---
        h.append("<div id='tab-3' class='tab-content'>\n")
        h.extend(self._tab_modelo100(is_es))
        h.append("</div>\n")

        # --- Tab 4: Disposals ---
        h.append("<div id='tab-4' class='tab-content'>\n")
        h.extend(self._tab_disposals(is_es, disposals))
        h.append("</div>\n")

        # Scripts
        h.append(self._scripts(is_es, summaries, per_coin, monthly, positions, disposals))
        h.append("</body></html>\n")
        return "".join(h)

    # --- Tab helpers -----------------------------------------------------

    def _tab_summary(
        self,
        is_es: bool,
        summaries: dict[int, YearlyTaxSummary],
        per_coin: dict[str, dict[int, YearlyTaxSummary]],
        ledger: CarryforwardLedger,
        opening_losses: dict[int, Decimal] | None,
    ) -> list[str]:
        h: list[str] = []
        # Yearly summary
        h.append("<h2>Yearly Summary (Modelo 100 — Savings Base)</h2>\n")
        cols = ["Year", "Gains", "Losses", "Net Result", "Taxable Base", "Est. Tax*"]
        h.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>\n")
        for year in sorted(summaries):
            s = summaries[year]
            nc = "gain" if s.net_gain_loss >= 0 else "loss"
            h.append(
                f"<tr><td>{year}</td><td>€{s.total_gains:,.2f}</td>"
                f"<td>€{abs(s.total_losses):,.2f}</td>"
                f"<td class='{nc}'>€{s.net_gain_loss:,.2f}</td>"
                f"<td><strong>€{s.taxable_gain:,.2f}</strong></td>"
                f"<td>€{s.tax_due:,.2f}</td></tr>\n"
            )
        h.append("</table>\n")
        h.append(
            "<p style='font-size:11px;color:#64748b;'>* Isolated estimate. Real liability depends on total savings base and prior-year losses.</p>\n"
        )

        # Per-coin breakdown
        h.append("<h2>Per-Coin Breakdown</h2>\n")
        years = sorted({y for d in per_coin.values() for y in d})
        cols2 = ["Coin", "Year", "Gains", "Losses", "Net", "Fees"]
        h.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cols2) + "</tr>\n")
        for coin in self.coins:
            for year in years:
                cs = per_coin[coin].get(year)
                if not cs or (cs.total_gains == 0 and cs.total_losses == 0):
                    continue
                nc = "gain" if cs.net_gain_loss >= 0 else "loss"
                h.append(
                    f"<tr><td><strong>{coin}</strong></td><td>{year}</td>"
                    f"<td>€{cs.total_gains:,.2f}</td>"
                    f"<td>€{abs(cs.total_losses):,.2f}</td>"
                    f"<td class='{nc}'>€{cs.net_gain_loss:,.2f}</td>"
                    f"<td>€{cs.total_fees_eur:,.2f}</td></tr>\n"
                )
        h.append("</table>\n")

        # Loss carryforward
        h.append("<h2>Loss Carryforward (Art. 49 LIRPF)</h2>\n")
        h.append(
            "<p style='font-size:12px;color:#64748b;'>Net losses offset savings-base gains of the following <strong>4 years</strong>; unused losses expire.</p>\n"
        )
        cf_cols = ["Year", "Net Result", "Prior Loss Applied", "Taxable After"]
        h.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cf_cols) + "</tr>\n")
        for r in ledger.rows:
            nc = "gain" if r.net_result >= 0 else "loss"
            h.append(
                f"<tr><td>{r.year}</td><td class='{nc}'>€{r.net_result:,.2f}</td>"
                f"<td>€{r.prior_losses_applied:,.2f}</td>"
                f"<td><strong>€{r.taxable_after:,.2f}</strong></td></tr>\n"
            )
        h.append("</table>\n")
        if ledger.pending_end:
            h.append(
                "<p><strong>Pending losses carried forward:</strong></p><ul style='margin:8px 0 16px 20px;'>\n"
            )
            for oy, rem, use_by in ledger.pending_end:
                h.append(f"<li class='loss'>From {oy}: €{rem:,.2f} — use by {use_by}</li>\n")
            h.append("</ul>\n")
        if ledger.expired:
            h.append("<p class='warn'><strong>⚠ EXPIRED losses unused:</strong></p>\n")
        if self.synthetic_notes:
            h.append(
                "<p class='warn'>⚠ Synthetic lots added (incomplete history):</p>\n<ul style='margin:4px 0 12px 20px'>\n"
            )
            for note in self.synthetic_notes:
                h.append(f"<li style='font-size:11px;color:#94a3b8;'>{note}</li>\n")
            h.append("</ul>\n")
        return h

    def _tab_charts(
        self,
        is_es: bool,
        summaries: dict[int, YearlyTaxSummary],
        per_coin: dict[str, dict[int, YearlyTaxSummary]],
        monthly: dict[str, float],
        positions: list[OpenPosition],
    ) -> list[str]:
        h: list[str] = []
        h.append("<div class='chart-grid'>\n")

        # Chart 1: per-coin P&L bar
        h.append("<div class='chart-card'><h3>P&L per Coin (€)</h3>")
        h.append("<canvas id='coinChart' height='280'></canvas></div>\n")

        # Chart 2: monthly P&L
        h.append("<div class='chart-card'><h3>Monthly & Cumulative P&L (€)</h3>")
        h.append("<canvas id='monthlyChart' height='280'></canvas></div>\n")

        h.append("</div>\n")

        # Chart 3: open positions donut (only if there are positions)
        if positions:
            h.append(
                "<div class='chart-card' style='max-width:400px'><h3>Open Positions — Cost Basis (€)</h3>"
            )
            h.append("<canvas id='positionsChart' height='300'></canvas></div>\n")
        return h

    def _tab_positions(self, is_es: bool, positions: list[OpenPosition]) -> list[str]:
        h: list[str] = []
        if not positions:
            h.append("<p style='color:#64748b;'>No open positions at end of period.</p>\n")
            return h
        h.append("<h2>Open Positions (unrealised)</h2>\n")
        pos_note = "Cost basis in EUR — latent gains/losses are not included until disposal."
        h.append(f"<p style='font-size:12px;color:#64748b;'>{pos_note}</p>\n")
        cols = ["Coin", "Quantity", "Cost Basis (€)", "Avg Cost (€/unit)"]
        h.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>\n")
        for p in positions:
            h.append(
                f"<tr><td><strong>{p.coin}</strong></td>"
                f"<td>{p.quantity:,.6f}</td>"
                f"<td>€{p.cost_basis_eur:,.2f}</td>"
                f"<td>€{p.avg_cost_eur:,.4f}</td></tr>\n"
            )
        total_cost = sum(p.cost_basis_eur for p in positions)
        h.append(
            f"<tr style='font-weight:600;background:#1e293b;'>"
            f"<td colspan='2'>Total</td>"
            f"<td>€{total_cost:,.2f}</td><td>—</td></tr>\n"
        )
        h.append("</table>\n")
        h.append(
            "<div class='disclaimer'>⚠ Reminder: latent gains are not taxable until disposal. Cost basis here is the FIFO cost in EUR at the ECB rate of each purchase date.</div>\n"
        )
        return h

    @staticmethod
    def _tab_modelo100(is_es: bool) -> list[str]:
        h: list[str] = []
        h.append("<h2>Filing Guide — Modelo 100 (IRPF)</h2>\n")
        h.append(
            "<p style='font-size:12px;color:#94a3b8;'>Crypto-assets are classified as "
            "<strong>«otros bienes y derechos de carácter patrimonial»</strong> (DGT V1149-18, V0999-18). "
            "Every disposal is a capital gain/loss feeding the <strong>savings tax base</strong> "
            "(Art. 33 LIRPF). FIFO per coin is mandatory (DGT V1374-21, V1816-20).</p>\n"
        )

        rows = [
            (
                "Each disposal: transmission value, acquisition value, gain/loss",
                "Ganancias y pérdidas patrimoniales — Transmisiones de otros elementos patrimoniales",
                "≈ 1624–1631 (verify)",
            ),
            (
                "Net result for the year",
                "Saldo neto de ganancias y pérdidas patrimoniales (base del ahorro)",
                "≈ 0424 / 0425",
            ),
            (
                "Prior-year pending losses",
                "Saldos netos negativos de ejercicios anteriores pendientes de compensar",
                "≈ 0439–0443 (one per origin year)",
            ),
            ("Resulting savings tax base", "Base imponible del ahorro", "≈ 0460"),
            (
                "Foreign exchange balance > €50,000",
                "Modelo 721 — Declaration of crypto held abroad",
                "Separate filing (annual, April)",
            ),
            (
                "Reporting by Spanish exchange (if applicable)",
                "Modelo 172 / 173 (filed by the exchange, not the investor)",
                "Informative — not filed by you",
            ),
        ]
        cols = ["Figure from this report", "Modelo 100 Section / Regulation", "Casilla (verify)"]

        h.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>\n")
        for fig, apt, cas in rows:
            h.append(f"<tr><td>{fig}</td><td>{apt}</td><td>{cas}</td></tr>\n")
        h.append("</table>\n")

        notes = [
            "⚠ Box numbers change every year. Verify them against the form for your filing year.",
            "⚠ Crypto is NOT subject to the 2-month wash-sale rule (Art. 33.5 LIRPF) — the DGT does not consider them «valores homogéneos» for anti-loss-washing purposes.",
            "⚠ Stablecoins (USDT/USDC) are treated as USD cash in this report. Strictly each conversion may be a swap — consult your advisor.",
            "🔗 DGT V1149-18, V1816-20, V1374-21 · Ley 11/2021 (Ley de Medidas de Prevención del Fraude Fiscal)",
        ]
        h.append("<div class='disclaimer'>" + "<br>".join(notes) + "</div>\n")
        return h

    @staticmethod
    def _tab_disposals(is_es: bool, disposals: list[DisposalRow]) -> list[str]:
        h: list[str] = []
        h.append("<h2>Disposal Register</h2>\n")
        coins = sorted({r.coin for r in disposals})
        h.append("<div class='filter-row'>\n")
        lbl = "Filter by coin:"
        h.append(f"<label style='color:#94a3b8;font-size:13px;'>{lbl}</label>\n")
        h.append("<select id='coinFilter'>\n")
        all_opt = "All"
        h.append(f"<option value=''>{all_opt}</option>\n")
        for c in coins:
            h.append(f"<option value='{c}'>{c}</option>\n")
        h.append("</select>\n")
        n = len(disposals)
        h.append(
            f"<span style='color:#64748b;font-size:12px;' id='disposalCount'>Showing {n} disposals</span>\n"
        )
        h.append("</div>\n")

        cols = [
            "Date",
            "Coin",
            "Quantity",
            "Proceeds (€)",
            "Cost Basis (€)",
            "Fees (€)",
            "Gain / Loss (€)",
        ]
        h.append("<table id='disposalTable'>\n")
        h.append("<thead><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr></thead>\n")
        h.append("<tbody>\n")
        for r in disposals:
            nc = "badge-gain" if r.gain_eur >= 0 else "badge-loss"
            sign = "+" if r.gain_eur >= 0 else ""
            h.append(
                f"<tr data-coin='{r.coin}'>"
                f"<td>{r.date}</td>"
                f"<td><strong>{r.coin}</strong></td>"
                f"<td>{float(r.quantity):,.6f}</td>"
                f"<td>€{r.proceeds_eur:,.4f}</td>"
                f"<td>€{r.cost_basis_eur:,.4f}</td>"
                f"<td>€{r.fees_eur:,.4f}</td>"
                f"<td><span class='badge {nc}'>{sign}€{r.gain_eur:,.4f}</span></td>"
                f"</tr>\n"
            )
        h.append("</tbody></table>\n")
        return h

    # --- JavaScript ------------------------------------------------------

    def _scripts(
        self,
        is_es: bool,
        summaries: dict[int, YearlyTaxSummary],
        per_coin: dict[str, dict[int, YearlyTaxSummary]],
        monthly: dict[str, float],
        positions: list[OpenPosition],
        disposals: list[DisposalRow],
    ) -> str:
        # Per-coin chart data
        coins = self.coins
        years = sorted({y for d in per_coin.values() for y in d})
        gains_by_coin = [
            float(sum(per_coin[c].get(y, YearlyTaxSummary(year=y)).total_gains for y in years))
            for c in coins
        ]
        losses_by_coin = [
            float(sum(per_coin[c].get(y, YearlyTaxSummary(year=y)).total_losses for y in years))
            for c in coins
        ]

        # Monthly chart data
        month_labels = list(monthly.keys())
        month_vals = list(monthly.values())
        cumulative: list[float] = []
        acc = 0.0
        for v in month_vals:
            acc += v
            cumulative.append(round(acc, 4))

        # Positions donut data
        pos_labels = [p.coin for p in positions]
        pos_vals = [float(p.cost_basis_eur) for p in positions]
        pos_colours = [_COIN_COLOURS[i % len(_COIN_COLOURS)] for i in range(len(positions))]

        gain_label = "Gains"
        loss_label = "Losses"
        monthly_label = "Monthly P&L"
        cumul_label = "Cumulative"

        return f"""<script>
// Tab switching
function showTab(n){{
  document.querySelectorAll('.tab-content').forEach((el,i)=>{{
    el.classList.toggle('active',i===n);
  }});
  document.querySelectorAll('.tab-btn').forEach((el,i)=>{{
    el.classList.toggle('active',i===n);
  }});
  if(n===1){{renderCharts();}}
}}
let chartsRendered=false;
function renderCharts(){{
  if(chartsRendered)return;
  chartsRendered=true;

  // Per-coin bar
  const coinCtx=document.getElementById('coinChart');
  if(coinCtx){{
    new Chart(coinCtx,{{
      type:'bar',
      data:{{
        labels:{json.dumps(coins)},
        datasets:[
          {{label:{json.dumps(gain_label)},data:{json.dumps(gains_by_coin)},
           backgroundColor:'rgba(16,185,129,.75)',borderRadius:4}},
          {{label:{json.dumps(loss_label)},data:{json.dumps(losses_by_coin)},
           backgroundColor:'rgba(239,68,68,.75)',borderRadius:4}}
        ]
      }},
      options:{{
        responsive:true,indexAxis:'y',
        plugins:{{legend:{{labels:{{color:'#94a3b8'}}}}}},
        scales:{{
          x:{{grid:{{color:'#1e293b'}},ticks:{{color:'#94a3b8'}},
             title:{{display:true,text:'EUR',color:'#64748b'}}}},
          y:{{grid:{{color:'#1e293b'}},ticks:{{color:'#e2e8f0',font:{{weight:'600'}}}}}}
        }}
      }}
    }});
  }}

  // Monthly P&L mixed chart
  const monthCtx=document.getElementById('monthlyChart');
  if(monthCtx){{
    new Chart(monthCtx,{{
      type:'bar',
      data:{{
        labels:{json.dumps(month_labels)},
        datasets:[
          {{type:'bar',label:{json.dumps(monthly_label)},data:{json.dumps(month_vals)},
           backgroundColor:ctx=>ctx.raw>=0?'rgba(16,185,129,.65)':'rgba(239,68,68,.65)',
           borderRadius:3,yAxisID:'y'}},
          {{type:'line',label:{json.dumps(cumul_label)},data:{json.dumps(cumulative)},
           borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,.1)',
           fill:true,tension:.3,pointRadius:3,yAxisID:'y2'}}
        ]
      }},
      options:{{
        responsive:true,
        plugins:{{legend:{{labels:{{color:'#94a3b8'}}}}}},
        scales:{{
          y:{{position:'left',grid:{{color:'#1e293b'}},ticks:{{color:'#94a3b8'}}}},
          y2:{{position:'right',grid:{{drawOnChartArea:false}},ticks:{{color:'#3b82f6'}}}}
        }}
      }}
    }});
  }}

  // Positions donut
  const posCtx=document.getElementById('positionsChart');
  if(posCtx && {json.dumps(pos_labels)}.length){{
    new Chart(posCtx,{{
      type:'doughnut',
      data:{{
        labels:{json.dumps(pos_labels)},
        datasets:[{{data:{json.dumps(pos_vals)},
          backgroundColor:{json.dumps(pos_colours)},
          borderColor:'#0f172a',borderWidth:2}}]
      }},
      options:{{
        responsive:true,
        plugins:{{
          legend:{{position:'bottom',labels:{{color:'#94a3b8',padding:12}}}},
          tooltip:{{callbacks:{{label:ctx=>`${{ctx.label}}: €${{ctx.raw.toLocaleString('ca-ES',{{minimumFractionDigits:2}})}}`}}}}
        }}
      }}
    }});
  }}
}}

// Disposal filter
const coinFilter=document.getElementById('coinFilter');
if(coinFilter){{
  coinFilter.addEventListener('change',function(){{
    const val=this.value;
    const rows=document.querySelectorAll('#disposalTable tbody tr');
    let visible=0;
    rows.forEach(tr=>{{
      const show=!val||tr.dataset.coin===val;
      tr.style.display=show?'':'none';
      if(show)visible++;
    }});
    const count=document.getElementById('disposalCount');
    if(count){{
      count.textContent=`Showing ${{visible}} disposals`;
    }}
  }});
}}
</script>
"""


# ---------------------------------------------------------------------------
# Combined report helpers
# ---------------------------------------------------------------------------


def generate_combined_html(
    stock_summaries: dict[int, YearlyTaxSummary],
    crypto_summaries: dict[int, YearlyTaxSummary],
    savings_income: dict[int, SavingsIncomeYear] | None = None,
    opening_losses: dict[int, Decimal] | None = None,
    lang: str = "es",
) -> str:
    """Generate a combined stocks + crypto savings-base HTML report."""
    merged = CryptoTaxEngine.merge_yearly_summaries([stock_summaries, crypto_summaries])
    all_years = sorted(set(merged) | set(stock_summaries) | set(crypto_summaries))

    agg_engine = TaxEngine()
    agg_engine.yearly_summaries = merged

    ledger_obj: SavingsLedger | CarryforwardLedger
    if savings_income:
        ledger_obj = agg_engine.compute_savings_ledger(savings_income, opening_losses)
    else:
        ledger_obj = agg_engine.compute_carryforward(opening_losses)

    h: list[str] = []
    title = "Combined Report — Stocks + Crypto"
    h.append(f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset='utf-8'><title>{title}</title>
<style>
:root{{--bg:#0f172a;--bg2:#1e293b;--txt:#f8fafc;--txt2:#94a3b8;
  --green:#10b981;--red:#ef4444;--blue:#3b82f6;--border:#334155;}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:var(--bg);color:var(--txt);padding:24px;max-width:960px;margin:0 auto;}}
h1{{font-size:1.8rem;font-weight:800;
  background:linear-gradient(135deg,#60a5fa,#a78bfa,#f43f5e);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:4px;}}
h2{{color:#e2e8f0;font-size:1rem;margin:24px 0 10px;}}
table{{border-collapse:collapse;width:100%;font-size:12px;margin-bottom:16px;}}
th,td{{border:1px solid var(--border);padding:7px 10px;text-align:left;}}
th{{background:var(--bg2);color:var(--txt2);font-weight:600;}}
.gain{{color:var(--green);}}.loss{{color:var(--red);}}
.sub{{color:var(--txt2);font-size:.75rem;}}
.disclaimer{{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
  padding:14px 18px;font-size:11px;color:var(--txt2);margin-top:16px;line-height:1.6;}}
</style></head><body>
""")
    h.append(f"<h1>{title}</h1>\n")
    today = __import__("datetime").date.today()
    h.append(
        f"<p style='color:#64748b;font-size:.85rem;'>Generated {today.strftime('%Y-%m-%d')}</p>\n"
    )

    # Source breakdown table
    h.append("<h2>Breakdown by Source</h2>\n")
    head_cols = ["Year", "Source", "Gains", "Losses", "Net"]
    h.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in head_cols) + "</tr>\n")
    stock_lbl = "Stocks (E-Trade)"
    crypto_lbl = "Crypto"
    combined_lbl = "COMBINED"
    for year in all_years:
        ss = stock_summaries.get(year)
        cs = crypto_summaries.get(year)
        ms = merged.get(year)
        for s, lbl in [(ss, stock_lbl), (cs, crypto_lbl)]:
            if s and (s.total_gains != 0 or s.total_losses != 0):
                nc = "gain" if s.net_gain_loss >= 0 else "loss"
                h.append(
                    f"<tr><td>{year}</td><td class='sub'>{lbl}</td>"
                    f"<td>€{s.total_gains:,.2f}</td><td>€{abs(s.total_losses):,.2f}</td>"
                    f"<td class='{nc}'>€{s.net_gain_loss:,.2f}</td></tr>\n"
                )
        if ms:
            nc = "gain" if ms.net_gain_loss >= 0 else "loss"
            h.append(
                f"<tr style='font-weight:700;'><td>{year}</td><td><strong>{combined_lbl}</strong></td>"
                f"<td>€{ms.total_gains:,.2f}</td><td>€{abs(ms.total_losses):,.2f}</td>"
                f"<td class='{nc}'>€{ms.net_gain_loss:,.2f}</td></tr>\n"
            )
    h.append("</table>\n")

    # Savings base / carryforward using existing engine machinery
    if isinstance(ledger_obj, SavingsLedger):
        sledger = ledger_obj
        h.append("<h2>Combined Savings Base — G/L + Div/Int (Art. 48 & 49 LIRPF)</h2>\n")
        sb_cols = [
            "Year",
            "Capital G/L",
            "Div+Interest",
            "Cross Offset",
            "Savings Base",
            "Foreign Tax (info)",
        ]
        h.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in sb_cols) + "</tr>\n")
        for r in sledger.rows:
            gp_cls = "gain" if r.gp_net >= 0 else "loss"
            h.append(
                f"<tr><td>{r.year}</td><td class='{gp_cls}'>€{r.gp_net:,.2f}</td>"
                f"<td>€{r.rcm_net:,.2f}</td>"
                f"<td>{'€' + f'{r.cross_offset:,.2f}' if r.cross_offset > 0 else '—'}</td>"
                f"<td><strong>€{r.savings_base:,.2f}</strong></td>"
                f"<td>€{r.foreign_tax_eur:,.2f}</td></tr>\n"
            )
        h.append("</table>\n")
        if sledger.gp_pending_end:
            h.append(
                "<p><strong>Pending capital losses:</strong></p><ul style='margin:6px 0 14px 20px'>\n"
            )
            for oy, rem, uby in sledger.gp_pending_end:
                h.append(f"<li class='loss'>{oy}: €{rem:,.2f} — use by {uby}</li>\n")
            h.append("</ul>\n")
    elif isinstance(ledger_obj, CarryforwardLedger):
        cf_ledger = ledger_obj
        h.append("<h2>Combined Loss Carryforward (Art. 49 LIRPF)</h2>\n")
        cf_cols = ["Year", "Net Result", "Prior Loss Applied", "Taxable After"]
        h.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cf_cols) + "</tr>\n")
        for cr in cf_ledger.rows:
            nc = "gain" if cr.net_result >= 0 else "loss"
            h.append(
                f"<tr><td>{cr.year}</td><td class='{nc}'>€{cr.net_result:,.2f}</td>"
                f"<td>€{cr.prior_losses_applied:,.2f}</td>"
                f"<td><strong>€{cr.taxable_after:,.2f}</strong></td></tr>\n"
            )
        h.append("</table>\n")
        if cf_ledger.pending_end:
            h.append("<p><strong>Pending losses:</strong></p><ul style='margin:6px 0 14px 20px'>\n")
            for oy, rem, uby in cf_ledger.pending_end:
                h.append(f"<li class='loss'>{oy}: €{rem:,.2f} — use by {uby}</li>\n")
            h.append("</ul>\n")

    # Disclaimer
    notes = [
        "⚠ Box numbers change every year — verify against the form for your filing year.",
        "⚠ Crypto is NOT subject to the 2-month wash-sale rule (DGT does not consider them homogeneous securities).",
        "⚠ This report is informational — verify results with a qualified tax advisor (Asesor Fiscal).",
    ]
    h.append("<div class='disclaimer'>" + "<br>".join(notes) + "</div>\n")
    h.append("</body></html>\n")
    return "".join(h)
