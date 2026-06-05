"""
Spanish Tax Engine Core Logic.

Implements the FIFO (First In First Out) cost basis matching method
required by Spanish tax law (Agencia Tributaria) for capital gains calculations on stocks.
"""

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .models import (
    EventType,
    FifoMatch,
    ProcessedEvent,
    ShareLot,
    StockEvent,
    TaxEngineState,
    YearlyTaxSummary,
)


class TaxEngine:
    """
    Spanish Tax Engine using FIFO Cost Basis.

    Implements the First In First Out method required by
    Spanish tax law (IRPF) for calculating capital gains on stocks.
    """

    def __init__(self) -> None:
        self.state = TaxEngineState()
        self.processed_events: list[ProcessedEvent] = []
        self.yearly_summaries: dict[int, YearlyTaxSummary] = defaultdict(
            lambda: YearlyTaxSummary(year=0)
        )

    def reset(self) -> None:
        """Reset the engine to initial state."""
        self.state = TaxEngineState()
        self.processed_events = []
        self.yearly_summaries = defaultdict(lambda: YearlyTaxSummary(year=0))

    def _sort_events(self, events: list[StockEvent]) -> list[StockEvent]:
        """
        Sort events by date, with acquisitions before sells on same day.

        This is critical for correct processing - if a VEST and SELL happen
        on the same day (common with sell-to-cover), the VEST must be
        processed first.
        """

        def sort_key(event: StockEvent) -> tuple[date, int]:
            # Primary: date
            # Secondary: event type priority (VEST=0, BUY=1, EXERCISE=1, SELL=2)
            type_priority = {
                EventType.VEST: 0,
                EventType.BUY: 1,
                EventType.EXERCISE: 1,
                EventType.SELL: 2,
            }
            return (event.event_date, type_priority[event.event_type])

        return sorted(events, key=sort_key)

    def _process_acquisition(self, event: StockEvent) -> ProcessedEvent:
        """
        Process a BUY or VEST event.

        Records the shares as a new share lot under Spanish FIFO rules.
        """
        shares = event.shares
        price_eur = event.price_eur
        new_cost_eur = event.total_value_eur

        # Add a new lot
        new_lot = ShareLot(
            acquisition_date=event.event_date,
            shares=shares,
            price_eur=price_eur,
            remaining_shares=shares,
            notes=event.notes,
        )
        self.state.lots.append(new_lot)

        # Update running aggregates
        self.state.total_shares += shares
        self.state.total_portfolio_cost_eur += new_cost_eur

        # Informational running average cost
        if self.state.total_shares > 0:
            self.state.avg_cost_eur = (self.state.total_portfolio_cost_eur / self.state.total_shares).quantize(
                Decimal("0.0001"), ROUND_HALF_UP
            )
        else:
            self.state.avg_cost_eur = Decimal("0")

        return ProcessedEvent(
            event=event,
            total_shares_after=self.state.total_shares,
            avg_cost_eur_after=self.state.avg_cost_eur,
            realized_gain_loss=Decimal("0"),
            cost_change_eur=new_cost_eur,
            total_portfolio_cost_eur=self.state.total_portfolio_cost_eur,
        )

    def _process_sell(self, event: StockEvent) -> ProcessedEvent:
        """
        Process a SELL event.

        Matches sold shares against the oldest available share lots (FIFO).
        """
        shares_to_sell = event.shares
        sell_price_eur = event.price_eur

        if shares_to_sell > self.state.total_shares:
            raise ValueError(
                f"Cannot sell {shares_to_sell} shares on {event.event_date}. "
                f"Only {self.state.total_shares} shares held. "
                f"Check for timing issues with sell-to-cover transactions."
            )

        fifo_matches: list[FifoMatch] = []
        total_realized_gain_loss = Decimal("0")
        total_cost_basis_removed = Decimal("0")

        remaining_to_match = shares_to_sell
        for lot in self.state.lots:
            if remaining_to_match <= 0:
                break
            if lot.remaining_shares <= 0:
                continue

            shares_from_lot = min(lot.remaining_shares, remaining_to_match)
            lot.remaining_shares -= shares_from_lot
            remaining_to_match -= shares_from_lot

            match_gain_loss = ((sell_price_eur - lot.price_eur) * shares_from_lot).quantize(
                Decimal("0.0001"), ROUND_HALF_UP
            )
            total_realized_gain_loss += match_gain_loss
            total_cost_basis_removed += (lot.price_eur * shares_from_lot).quantize(
                Decimal("0.0001"), ROUND_HALF_UP
            )

            fifo_matches.append(
                FifoMatch(
                    acquisition_date=lot.acquisition_date,
                    acquisition_price_eur=lot.price_eur,
                    shares=shares_from_lot,
                    realized_gain_loss=match_gain_loss,
                    notes=lot.notes,
                )
            )

        # Deduct any fees (Commission, SEC, Brokerage) from the total capital gain
        fees_eur = Decimal("0")
        if event.fees_usd > 0:
            fees_eur = (event.fees_usd / event.resolved_fx_rate).quantize(
                Decimal("0.0001"), ROUND_HALF_UP
            )
            total_realized_gain_loss -= fees_eur
            
        # Update state
        self.state.total_shares -= shares_to_sell
        self.state.total_portfolio_cost_eur -= total_cost_basis_removed

        if self.state.total_shares > 0:
            self.state.avg_cost_eur = (self.state.total_portfolio_cost_eur / self.state.total_shares).quantize(
                Decimal("0.0001"), ROUND_HALF_UP
            )
        else:
            self.state.avg_cost_eur = Decimal("0")
            self.state.total_portfolio_cost_eur = Decimal("0")
            self.state.lots = []

        return ProcessedEvent(
            event=event,
            total_shares_after=self.state.total_shares,
            avg_cost_eur_after=self.state.avg_cost_eur,
            realized_gain_loss=total_realized_gain_loss,
            cost_change_eur=-total_cost_basis_removed,
            total_portfolio_cost_eur=self.state.total_portfolio_cost_eur,
            fifo_matches=fifo_matches,
        )

    def detect_blocked_losses_spain(self) -> None:
        """
        Detect and compute blocked losses under the Spanish 2-month wash sale rule.

        Under Art. 33.5.f LIRPF, a loss from selling shares is blocked (deferred) if
        the taxpayer still holds homogeneous shares that were acquired within 2 months
        before or after the sale date.

        The key distinction: lots that were fully consumed by the sell itself are NOT
        counted as triggering acquisitions — only shares that remain in the portfolio
        (i.e., "replacement" shares) trigger the blocking rule.
        """
        import calendar

        def _add_months(d: date, months: int) -> date:
            month = d.month - 1 + months
            year = d.year + month // 12
            month = month % 12 + 1
            day = min(d.day, calendar.monthrange(year, month)[1])
            return date(year, month, day)

        # Build a consumable pool of "replacement" shares: lots that still hold
        # shares after all processing is complete (self.state.lots reflects the
        # final state). Each surviving share can block at most ONE sold share —
        # so the pool is decremented as it is claimed. Without this, a single
        # replacement lot sitting in the 2-month window of several loss sales
        # would block each of them independently, over-deferring the losses.
        replacement_pool: dict[tuple[date, Decimal], Decimal] = {}
        for lot in self.state.lots:
            if lot.remaining_shares > 0:
                key = (lot.acquisition_date, lot.price_eur)
                replacement_pool[key] = replacement_pool.get(key, Decimal("0")) + lot.remaining_shares

        # Process loss-making sells chronologically so earlier sales claim
        # replacement shares first (a deterministic allocation when windows overlap).
        loss_sells = sorted(
            (
                pe
                for pe in self.processed_events
                if pe.event.event_type == EventType.SELL and pe.realized_gain_loss < 0
            ),
            key=lambda pe: pe.event.event_date,
        )

        for pe in loss_sells:
            sale_date = pe.event.event_date
            start_window = _add_months(sale_date, -2)
            end_window = _add_months(sale_date, 2)

            # Claim replacement shares acquired within the 2-month window, consuming
            # them from the pool so they cannot block another sale's loss as well.
            blocked_shares = Decimal("0")
            remaining_to_block = pe.event.shares
            for key in sorted(replacement_pool.keys()):
                if remaining_to_block <= 0:
                    break
                acq_date, _acq_price = key
                if not (start_window <= acq_date <= end_window):
                    continue
                available = replacement_pool[key]
                if available <= 0:
                    continue
                used = min(available, remaining_to_block)
                replacement_pool[key] -= used
                blocked_shares += used
                remaining_to_block -= used

            if blocked_shares > 0:
                blocked_loss = (pe.realized_gain_loss / pe.event.shares * blocked_shares).quantize(
                    Decimal("0.0001"), ROUND_HALF_UP
                )

                pe.event.notes += f" [Wash Sale Blocked Loss: €{abs(blocked_loss):,.2f}]"
                year = sale_date.year
                self.yearly_summaries[year].blocked_losses += blocked_loss

    def process_event(self, event: StockEvent) -> ProcessedEvent:
        """Process a single stock event."""
        if event.event_type in (EventType.VEST, EventType.BUY, EventType.EXERCISE):
            result = self._process_acquisition(event)
        elif event.event_type == EventType.SELL:
            result = self._process_sell(event)
        else:
            raise ValueError(f"Unknown event type: {event.event_type}")

        # Track for yearly summary
        year = event.event_date.year
        if year not in self.yearly_summaries:
            self.yearly_summaries[year] = YearlyTaxSummary(year=year)

        if result.realized_gain_loss > 0:
            self.yearly_summaries[year].total_gains += result.realized_gain_loss
        elif result.realized_gain_loss < 0:
            self.yearly_summaries[year].total_losses += result.realized_gain_loss

        if event.fees_usd > 0 and event.resolved_fx_rate:
            fees_eur = (event.fees_usd / event.resolved_fx_rate).quantize(Decimal("0.0001"), ROUND_HALF_UP)
            self.yearly_summaries[year].total_fees_eur += fees_eur

        self.processed_events.append(result)
        return result

    def process_all(self, events: list[StockEvent]) -> list[ProcessedEvent]:
        """
        Process all events in chronological order.
        """
        self.reset()
        sorted_events = self._sort_events(events)

        for event in sorted_events:
            self.process_event(event)

        self.detect_blocked_losses_spain()

        return self.processed_events

    def get_yearly_summary(self, year: int) -> YearlyTaxSummary | None:
        """Get the tax summary for a specific year."""
        return self.yearly_summaries.get(year)

    def get_all_yearly_summaries(self) -> list[YearlyTaxSummary]:
        """Get all yearly tax summaries, sorted by year."""
        return sorted(self.yearly_summaries.values(), key=lambda s: s.year)

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

        for pe in self.processed_events:
            e = pe.event
            shares_sign = "+" if e.event_type != EventType.SELL else "-"
            shares_str = f"{shares_sign}{e.shares:,.0f}"
            gain_str = f"€{pe.realized_gain_loss:,.4f}" if pe.realized_gain_loss != 0 else ""

            wash_sale_info = ""
            if pe.event.event_type == EventType.SELL and pe.realized_gain_loss < 0:
                if "Wash Sale Blocked Loss" in pe.event.notes:
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
                        f"   └─ FIFO Match: {match.shares:,.0f} shares acq on {match.acquisition_date.isoformat()} "
                        f"cost €{match.acquisition_price_eur:,.4f} -> Gain/Loss: €{match.realized_gain_loss:,.4f}"
                    )

        print("=" * 120)

    def print_tax_summary(self) -> None:
        """Print the yearly tax summary."""
        print("\n" + "=" * 95)
        print("YEARLY TAX SUMMARY (Spain)")
        print("=" * 95)
        print(
            f"{'Year':<8} {'Total Gains':>15} {'Total Losses':>15} {'Blocked Loss':>15} "
            f"{'Taxable Base':>15} {'Est. Tax':>15}"
        )
        print("-" * 95)

        for summary in self.get_all_yearly_summaries():
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
        for summary in self.get_all_yearly_summaries():
            print(f"  Year {summary.year}:")
            print(f"    Total Realized Gains:      €{summary.total_gains:>12,.2f}")
            print(f"    Total Realized Losses:     €{summary.total_losses:>12,.2f}")
            print(f"    Blocked Losses (2-month):  €{summary.blocked_losses:>12,.2f}")
            print(f"    Total Fees Deducted:       €{summary.total_fees_eur:>12,.2f}")
            print(f"    Net Taxable Capital Gains: €{summary.taxable_gain:>12,.2f}")
            print(f"    Estimated Tax Due:         €{summary.tax_due:>12,.2f}")
            print()

        # 4-Year Loss Carryforward (Art. 49 LIRPF)
        carryforward_rows = [s for s in self.get_all_yearly_summaries() if s.net_gain_loss < 0]
        print("\nLOSS CARRYFORWARD WORKSHEET (Art. 49 LIRPF)")
        print("-" * 95)
        if carryforward_rows:
            print("Net losses below can be offset against savings-base gains of the next 4 years:")
            print()
            for s in carryforward_rows:
                print(f"  Year {s.year}: Net Loss €{s.net_gain_loss:>12,.2f}  ->  deductible through {s.year + 4}")
        else:
            print("  No years with a net loss to carry forward.")

        print("\n" + "=" * 95)
        print("  NOTE: Transaction Fees (Commissions, SEC Fees) ARE DEDUCTED (Wire Transfers EXCLUDED)")
        print("  from your capital gains automatically, per Spanish Tax Law (Gastos Inherentes).")
        print("  NOTE: 'Est. Tax' is an ISOLATED estimate on these stock gains only — your real")
        print("  liability depends on total savings income and prior-year loss carryforward.")
        print("=" * 95 + "\n")

    def generate_html_content(self, lang: str = "en", espp_discounts: dict[int, Decimal] | None = None, espp_early_sale_discounts: dict[int, Decimal] | None = None) -> str:
        """Generate HTML content for the tax report (supports English 'en' and Spanish 'es')."""
        is_es = lang.lower() == "es"
        
        html = []
        html.append("<html><head><style>")
        html.append(
            "body { font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }"
        )
        html.append(
            "table { border-collapse: collapse; width: 100%; margin-bottom: 20px; font-size: 12px; }"
        )
        html.append("th, td { border: 1px solid #ddd; padding: 6px; text-align: left; }")
        html.append("th { background-color: #f2f2f2; }")
        html.append("h1, h2, h3 { color: #333; }")
        html.append(".gain { color: green; }")
        html.append(".loss { color: red; }")
        html.append("code { background-color: #f4f4f4; padding: 2px 4px; border-radius: 4px; }")
        html.append("</style></head><body>")

        title = "Spanish Tax Report" if not is_es else "Informe Fiscal de España (FIFO)"
        gen_on = "Generated on" if not is_es else "Generado el"
        html.append(f"<h1>{title}</h1>")
        today_str = date.today().isoformat() if not is_es else date.today().strftime("%d/%m/%Y")
        html.append(f"<p>{gen_on}: {today_str}</p>")

        # Methodology
        methodology_title = "Methodology" if not is_es else "Metodología"
        html.append(f"<h2>{methodology_title}</h2>")
        if not is_es:
            html.append(
                "<p>This report calculates capital gains using the <strong>FIFO (First In First Out)</strong> method as required by Spanish tax law (Agencia Tributaria).</p>"
            )
            html.append("<h3>Key Rules:</h3>")
            html.append("<ul>")
            html.append(
                "<li><strong>FIFO Matching</strong>: Shares of the same security are sold in the order they were acquired. Sells are matched against the oldest available purchase lots.</li>"
            )
            html.append(
                "<li><strong>Spanish Tax Savings Scale</strong>: Progressive tax scale is applied: 19% up to €6,000; 21% up to €50,000; 23% up to €200,000; 27% up to €300,000; 28% over €300,000.</li>"
            )
            html.append(
                "<li><strong>2-Month Wash Sale Rule</strong>: Losses from sales are blocked (deferred) if homogenous shares are acquired within 2 months before or after the sale date.</li>"
            )
            html.append(
                "<li><strong style='color: #cc6600;'>SCOPE - Single Account</strong>: "
                "The wash-sale rule applies per security across <strong>all</strong> your brokers/accounts, "
                "but this report only sees data from this account. If you bought the same security elsewhere "
                "(e.g. another broker) within the 2-month window, those repurchases are NOT detected here and "
                "some blocked losses may be under-reported.</li>"
            )
            html.append(
                "<li><strong style='color: green;'>NOTE - Fees Included</strong>: "
                "This report <strong>INCLUDES and DEDUCTS</strong> transaction commissions and SEC fees "
                "from your capital gains, applying the official ECB USD/EUR exchange rate of the transaction date. "
                "<strong>NOTE: Wire transfer fees are EXCLUDED per user preference.</strong></li>"
            )
            html.append("</ul>")
        else:
            html.append(
                "<p>Este informe calcula las ganancias y pérdidas patrimoniales utilizando el método <strong>FIFO (First In First Out)</strong>, tal como exige la normativa fiscal española (Agencia Tributaria).</p>"
            )
            html.append("<h3>Reglas Clave:</h3>")
            html.append("<ul>")
            html.append(
                "<li><strong>Casación FIFO</strong>: Las acciones de un mismo valor se consideran vendidas en el orden de su adquisición. Las ventas se casan con los lotes de compra más antiguos disponibles.</li>"
            )
            html.append(
                "<li><strong>Escala de Gravamen del Ahorro</strong>: Se aplica la escala progresiva española: 19% hasta €6.000; 21% hasta €50.000; 23% hasta €200.000; 27% hasta €300.000; y 28% para importes superiores a €300.000.</li>"
            )
            html.append(
                "<li><strong>Regla de los 2 Meses (Wash Sale)</strong>: Las pérdidas patrimoniales quedan bloqueadas (diferidas) si se adquieren acciones homogéneas dentro de los 2 meses anteriores o posteriores a la fecha de la venta.</li>"
            )
            html.append(
                "<li><strong style='color: #cc6600;'>ALCANCE - Una Sola Cuenta</strong>: "
                "La regla de los 2 meses se aplica por valor en <strong>todos</strong> tus brókers/cuentas, "
                "pero este informe solo ve los datos de esta cuenta. Si compraste el mismo valor en otro lugar "
                "(p. ej. otro bróker) dentro de la ventana de 2 meses, esas recompras NO se detectan aquí y "
                "algunas pérdidas bloqueadas podrían estar infravaloradas.</li>"
            )
            html.append(
                "<li><strong style='color: green;'>NOTA - Comisiones Incluidas</strong>: "
                "Este informe <strong>INCLUYE y DEDUCE</strong> las comisiones de transacción y tasas SEC "
                "como gastos inherentes a la transmisión, utilizando el tipo de cambio oficial del BCE de la fecha exacta de la transacción. "
                "<strong>NOTA: Las tarifas de transferencia bancaria (Wire fees) se EXCLUYEN por preferencia del usuario.</strong></li>"
            )
            html.append("</ul>")

        # Yearly Tax Summary Table (Modelo 100 - Savings Base)
        summary_title = (
            "Yearly Tax Summary (Modelo 100 - Savings Base)"
            if not is_es
            else "Resumen Fiscal Anual (Modelo 100 - Base Imponible del Ahorro)"
        )
        html.append(f"<h2>{summary_title}</h2>")
        html.append("<table>")
        if not is_es:
            html.append(
                "<tr><th>Year</th><th>Total Gains</th><th>Total Losses</th><th>Blocked Losses (2-month rule)</th><th>Deductible Losses</th><th>Fees Deducted</th><th>Net Taxable Savings Base</th><th>Estimated Tax (Isolated)*</th></tr>"
            )
        else:
            html.append(
                "<tr><th>Año</th><th>Ganancias Totales</th><th>Pérdidas Totales</th><th>Pérdidas Bloqueadas (Regla 2 meses)</th><th>Pérdidas Deducibles</th><th>Gastos Deducidos</th><th>Base Imponible del Ahorro</th><th>Impuesto Estimado (Aislado)*</th></tr>"
            )

        for summary in self.get_all_yearly_summaries():
            net_style = "gain" if summary.taxable_gain >= 0 else "loss"
            html.append("<tr>")
            html.append(f"<td>{summary.year}</td>")
            html.append(f"<td>€{summary.total_gains:,.2f}</td>")
            html.append(f"<td>€{summary.total_losses:,.2f}</td>")
            html.append(f"<td>€{summary.blocked_losses:,.2f}</td>")
            html.append(f"<td>€{summary.deductible_losses:,.2f}</td>")
            html.append(f"<td>€{summary.total_fees_eur:,.2f}</td>")
            html.append(
                f"<td class='{net_style}'><strong>€{summary.taxable_gain:,.2f}</strong></td>"
            )
            html.append(f"<td><strong>€{summary.tax_due:,.2f}</strong></td>")
            html.append("</tr>")
        html.append("</table>")

        if not is_es:
            html.append(
                "<p style='font-size: 11px; color: #555;'>"
                "<strong>* Isolated estimate.</strong> The tax column applies the savings-base scale to these "
                "stock gains <em>alone</em>. Your actual liability depends on your <strong>total</strong> savings "
                "income for the year (dividends, interest, gains from other assets) and any losses carried forward "
                "from prior years (4-year limit, Art. 49 LIRPF). Use this figure as a guide, not as the final tax due.</p>"
            )
        else:
            html.append(
                "<p style='font-size: 11px; color: #555;'>"
                "<strong>* Estimación aislada.</strong> La columna de impuesto aplica la escala del ahorro a estas "
                "ganancias bursátiles de forma <em>aislada</em>. Tu cuota real depende de tu base del ahorro "
                "<strong>total</strong> del ejercicio (dividendos, intereses, ganancias de otros activos) y de las "
                "pérdidas pendientes de compensar de años anteriores (límite de 4 años, Art. 49 LIRPF). "
                "Usa esta cifra como orientación, no como la cuota definitiva.</p>"
            )

        # 4-Year Loss Carryforward Worksheet (Art. 49 LIRPF)
        carryforward_rows = [
            s for s in self.get_all_yearly_summaries() if s.net_gain_loss < 0
        ]
        cf_title = (
            "Loss Carryforward Worksheet (Art. 49 LIRPF)"
            if not is_es
            else "Hoja de Compensación de Pérdidas (Art. 49 LIRPF)"
        )
        html.append(f"<h2>{cf_title}</h2>")
        if not is_es:
            html.append(
                "<p>Net losses in a year can be offset against savings-base gains of the following "
                "<strong>4 years</strong>. This engine reports each year in isolation — your advisor must "
                "apply the carryforward across years (and against other savings income) at filing time.</p>"
            )
        else:
            html.append(
                "<p>Las pérdidas netas de un ejercicio pueden compensarse con ganancias de la base del ahorro "
                "de los <strong>4 ejercicios siguientes</strong>. Este motor informa cada año de forma aislada — "
                "tu asesor debe aplicar la compensación entre años (y contra otras rentas del ahorro) al presentar la declaración.</p>"
            )
        if carryforward_rows:
            html.append("<table>")
            if not is_es:
                html.append(
                    "<tr><th>Year of Loss</th><th>Net Loss</th><th>Deductible Through (incl.)</th></tr>"
                )
            else:
                html.append(
                    "<tr><th>Año de la Pérdida</th><th>Pérdida Neta</th><th>Compensable Hasta (incl.)</th></tr>"
                )
            for s in carryforward_rows:
                html.append("<tr>")
                html.append(f"<td>{s.year}</td>")
                html.append(f"<td class='loss'>€{s.net_gain_loss:,.2f}</td>")
                html.append(f"<td>{s.year + 4}</td>")
                html.append("</tr>")
            html.append("</table>")
        else:
            no_loss = (
                "No years with a net loss to carry forward."
                if not is_es
                else "No hay ejercicios con pérdida neta pendiente de compensar."
            )
            html.append(f"<p><em>{no_loss}</em></p>")

        # ESPP 3-Year Holding Period Analysis
        if espp_early_sale_discounts or espp_discounts:
            espp_section_title = "ESPP 3-Year Holding Period Analysis (Art. 42.3.f LIRPF)" if not is_es else "Análisis del Período de Retención ESPP de 3 Años (Art. 42.3.f LIRPF)"
            html.append(f"<h2>{espp_section_title}</h2>")

            if espp_early_sale_discounts:
                # Early sales warning
                if not is_es:
                    html.append(
                        "<p style='color: #cc0000;'><strong>⚠️ The following ESPP discounts are TAXABLE as salary income.</strong><br>"
                        "Because specific ESPP shares were sold (according to FIFO rules) before reaching the 3-year holding period, "
                        "they lose their tax exemption. You must file a Complementary Return (Declaración Complementaria) "
                        "for the year they were originally purchased.</p>"
                    )
                else:
                    html.append(
                        "<p style='color: #cc0000;'><strong>⚠️ Los siguientes descuentos ESPP son IMPONIBLES como rendimiento del trabajo.</strong><br>"
                        "Debido a que ciertas acciones ESPP fueron vendidas (según reglas FIFO) antes de alcanzar los 3 años de retención, "
                        "pierden su exención fiscal. Debe presentar una Declaración Complementaria por el año en que fueron adquiridas originalmente.</p>"
                    )
                html.append("<table>")
                if not is_es:
                    html.append("<tr><th>Purchase Year (Complementary Return)</th><th>Taxable ESPP Discount (Salary Income)</th></tr>")
                else:
                    html.append("<tr><th>Año de Compra (Declaración Complementaria)</th><th>Descuento ESPP Imponible (Rendimiento del Trabajo)</th></tr>")
                for year, amount in sorted(espp_early_sale_discounts.items()):
                    html.append("<tr>")
                    html.append(f"<td>{year}</td>")
                    html.append(f"<td><strong style='color: #cc0000;'>€{amount:,.2f}</strong></td>")
                    html.append("</tr>")
                html.append("</table>")
            elif espp_discounts:
                # All clear — no early sales
                if not is_es:
                    html.append(
                        "<p style='color: green;'><strong>✅ No ESPP shares were sold before the 3-year holding period.</strong> "
                        "All ESPP discounts may be exempt under Art. 42.3.f LIRPF (subject to €12,000/year limit).</p>"
                    )
                else:
                    html.append(
                        "<p style='color: green;'><strong>✅ No se vendieron acciones ESPP antes del período de retención de 3 años.</strong> "
                        "Todos los descuentos ESPP pueden estar exentos según Art. 42.3.f LIRPF (sujeto al límite de €12.000/año).</p>"
                    )

            # Company sign-off reminder (always shown)
            if not is_es:
                html.append(
                    "<p style='background-color: #fff3cd; padding: 10px; border-left: 4px solid #ffc107;'>"
                    "<strong>⚠️ IMPORTANT:</strong> Employee must have signed the ESPP enrollment/agreement "
                    "document from the company for each offering period to qualify for the tax exemption.</p>"
                )
            else:
                html.append(
                    "<p style='background-color: #fff3cd; padding: 10px; border-left: 4px solid #ffc107;'>"
                    "<strong>⚠️ IMPORTANTE:</strong> El empleado debe haber firmado el documento de inscripción/acuerdo ESPP "
                    "de la empresa para cada período de oferta para poder optar a la exención fiscal.</p>"
                )




        # Transaction Ledger
        ledger_title = "Detailed Transaction Ledger" if not is_es else "Libro de Transacciones Detallado"
        html.append(f"<h2>{ledger_title}</h2>")
        html.append("<table>")
        if not is_es:
            html.append(
                "<tr><th>Date</th><th>Type</th><th>Shares</th><th>Price (USD)</th><th>FX Rate</th><th>Price (EUR)</th><th>Total Value (EUR)</th><th>Portfolio Qty</th><th>Avg Cost (EUR)</th><th>Realized G/L (EUR)</th></tr>"
            )
        else:
            html.append(
                "<tr><th>Fecha</th><th>Tipo</th><th>Acciones</th><th>Precio (USD)</th><th>Tipo Cambio</th><th>Precio (EUR)</th><th>Valor Total (EUR)</th><th>Cant. Cartera</th><th>Coste Medio (EUR)</th><th>G/P Realizada (EUR)</th></tr>"
            )

        for pe in self.processed_events:
            e = pe.event
            shares_sign = "+" if e.event_type != EventType.SELL else "-"
            shares_str = f"{shares_sign}{e.shares:,.0f}"

            # Translate EventType for Spanish
            event_type_str = e.event_type.value
            notes_str = e.notes
            if is_es:
                if e.event_type == EventType.VEST: event_type_str = "Concesión"
                elif e.event_type == EventType.SELL: event_type_str = "Venta"
                elif e.event_type == EventType.BUY: event_type_str = "Compra"
                elif e.event_type == EventType.EXERCISE: event_type_str = "Ejercicio"
                
                # Translate common notes
                if "RSU Vest" in notes_str: notes_str = notes_str.replace("RSU Vest", "Concesión RSU")
                if "ESPP Purchase" in notes_str: notes_str = notes_str.replace("ESPP Purchase", "Compra ESPP")
                if "Sell-to-Cover" in notes_str: notes_str = notes_str.replace("Sell-to-Cover (Auto-detected)", "Venta para Impuestos (Automático)")
                if "Manual Sell" in notes_str: notes_str = notes_str.replace("Manual Sell", "Venta Manual")
                if "Sell Order" in notes_str: notes_str = notes_str.replace("Sell Order", "Orden de Venta")
                if "Includes" in notes_str: notes_str = notes_str.replace("Includes", "Incluye").replace("fees", "comisiones")

            event_type_cell = f"{event_type_str}<br><small style='color:gray;'>{notes_str}</small>"
            event_date_str = e.event_date.isoformat() if not is_es else e.event_date.strftime("%d/%m/%Y")

            gl_str = ""
            wash_sale_note = ""
            if pe.realized_gain_loss != 0:
                gl_str = f"<strong>€{pe.realized_gain_loss:,.2f}</strong>"
                if pe.event.event_type == EventType.SELL and pe.realized_gain_loss < 0 and "Wash Sale Blocked" in pe.event.notes:
                    note_text = "Wash Sale Blocked" if not is_es else "Bloqueado Regla 2 Meses"
                    wash_sale_note = f"<br><small style='color:red;'>{note_text}</small>"
            elif e.event_type == EventType.SELL:
                gl_str = "€0.00"

            html.append("<tr>")
            html.append(f"<td>{event_date_str}</td>")
            html.append(f"<td>{event_type_cell}</td>")
            html.append(f"<td>{shares_str}</td>")
            html.append(f"<td>${e.price_usd:,.2f}</td>")
            html.append(f"<td>{e.resolved_fx_rate:.4f}</td>")
            html.append(f"<td>€{e.price_eur:,.4f}</td>")
            html.append(f"<td>€{e.total_value_eur:,.2f}</td>")
            html.append(f"<td>{pe.total_shares_after:,.0f}</td>")
            html.append(f"<td>€{pe.avg_cost_eur_after:,.4f}</td>")
            html.append(f"<td>{gl_str}{wash_sale_note}</td>")
            html.append("</tr>")

            if pe.fifo_matches:
                for match in pe.fifo_matches:
                    html.append("<tr style='background-color:#fafafa; font-style:italic;'>")
                    html.append("<td></td>")
                    match_label = "FIFO Match:" if not is_es else "Cruce FIFO:"
                    html.append(f"<td colspan='2' style='text-align:right;'>{match_label}</td>")
                    if not is_es:
                        match_text = f"Matched {match.shares:,.0f} shares from acquisition on {match.acquisition_date} at €{match.acquisition_price_eur:,.4f}"
                    else:
                        acq_date_str = match.acquisition_date.strftime("%d/%m/%Y")
                        match_text = f"Se cruzaron {match.shares:,.0f} acciones de la adquisición del {acq_date_str} a €{match.acquisition_price_eur:,.4f}"
                    html.append(f"<td colspan='6'>{match_text}</td>")
                    html.append(f"<td>€{match.realized_gain_loss:,.2f}</td>")
                    html.append("</tr>")
        html.append("</table>")

        # Calculation Details
        details_title = "Calculation Details for Sales" if not is_es else "Detalles de Cálculo para Ventas"
        html.append(f"<h2>{details_title}</h2>")
        sell_events = [pe for pe in self.processed_events if pe.event.event_type == EventType.SELL]
        if not sell_events:
            empty_text = "No sales transactions found." if not is_es else "No se encontraron transacciones de venta."
            html.append(f"<p><em>{empty_text}</em></p>")

        for i, pe in enumerate(sell_events, 1):
            e = pe.event
            event_date_str = e.event_date.isoformat() if not is_es else e.event_date.strftime("%d/%m/%Y")
            sale_header = f"Sale on {event_date_str}" if not is_es else f"Venta el {event_date_str}"
            html.append(f"<h3>{i}. {sale_header}</h3>")
            html.append("<ul>")
            sold_text = f"Sold: {e.shares:,.0f} shares @ €{e.price_eur:,.4f}" if not is_es else f"Vendido: {e.shares:,.0f} acciones @ €{e.price_eur:,.4f}"
            html.append(f"<li><strong>{sold_text}</strong></li>")
            matches_header = "FIFO Lot Matches" if not is_es else "Cruces de Lotes FIFO"
            html.append(f"<li><strong>{matches_header}</strong>:</li>")
            html.append("<ul>")
            for match in pe.fifo_matches:
                if not is_es:
                    match_desc = f"Matched {match.shares:,.0f} shares acquired on {match.acquisition_date} @ €{match.acquisition_price_eur:,.4f}. "
                    gl_label = "Gain/Loss"
                else:
                    acq_date_str = match.acquisition_date.strftime("%d/%m/%Y")
                    match_desc = f"Cruza {match.shares:,.0f} acciones adquiridas el {acq_date_str} @ €{match.acquisition_price_eur:,.4f}. "
                    gl_label = "Ganancia/Pérdida"
                html.append(
                    f"<li>{match_desc}"
                    f"{gl_label}: <code>({e.price_eur:,.4f} - {match.acquisition_price_eur:,.4f}) * {match.shares:,.0f} = €{match.realized_gain_loss:,.2f}</code></li>"
                )
            html.append("</ul>")
            
            tot_label = "Total Realized Gain/Loss" if not is_es else "Total Ganancia/Pérdida Realizada"
            html.append(
                f"<li><strong>{tot_label}</strong>: <strong>€{pe.realized_gain_loss:,.2f}</strong></li>"
            )
            if "Wash Sale Blocked" in pe.event.notes:
                warn_text = (
                    "Wash Sale Warning: This loss is blocked under Spanish rules due to a purchase of homogenous shares within 2 months."
                    if not is_es else
                    "Aviso de Regla de 2 Meses: Esta pérdida está bloqueada según la ley española debido a la compra de acciones homogéneas en un plazo de 2 meses."
                )
                html.append(f"<li><strong style='color:red;'>{warn_text}</strong></li>")
            html.append("</ul>")

        html.append("</body></html>")
        return "".join(html)

    def generate_pdf_report(self, filepath: str, lang: str = "en", espp_discounts: dict[int, Decimal] | None = None, espp_early_sale_discounts: dict[int, Decimal] | None = None) -> None:
        """Generate a PDF tax report using Playwright (supports lang='en' or lang='es')."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("Error: Playwright is not installed. Cannot generate PDF.")
            print("Please install it with: pip install playwright && playwright install")
            return

        html_content = self.generate_html_content(lang=lang, espp_discounts=espp_discounts, espp_early_sale_discounts=espp_early_sale_discounts)

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
