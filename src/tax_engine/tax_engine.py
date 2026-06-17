"""
Spanish Tax Engine Core Logic.

Implements the FIFO (First In First Out) cost basis matching method
required by Spanish tax law (Agencia Tributaria) for capital gains calculations on stocks.
"""

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .portfolio import SecurityResult

from .models import (
    CarryforwardLedger,
    CarryforwardYear,
    EventType,
    FifoMatch,
    ProcessedEvent,
    SavingsIncomeYear,
    SavingsLedger,
    SavingsLedgerYear,
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

    @staticmethod
    def format_shares(val: Decimal) -> str:
        """Format decimal share counts to strip trailing zeros and show up to 6 decimals."""
        s = f"{val:,.6f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s

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
            broker=event.broker,
            isin=event.isin,
        )
        self.state.lots.append(new_lot)

        # Update running aggregates
        self.state.total_shares += shares
        self.state.total_portfolio_cost_eur += new_cost_eur

        # Informational running average cost
        if self.state.total_shares > 0:
            self.state.avg_cost_eur = (
                self.state.total_portfolio_cost_eur / self.state.total_shares
            ).quantize(Decimal("0.0001"), ROUND_HALF_UP)
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

        # Deduct any fees (Commission, SEC, Brokerage) from the total capital gain.
        # resolved_fx_rate is EUR per unit of the native currency, so fees convert
        # to EUR the same way the price does — by multiplying, not dividing.
        fees_eur = Decimal("0")
        if event.fees_usd > 0:
            fees_eur = (event.fees_usd * event.resolved_fx_rate).quantize(
                Decimal("0.0001"), ROUND_HALF_UP
            )
            total_realized_gain_loss -= fees_eur

        # Update state
        self.state.total_shares -= shares_to_sell
        self.state.total_portfolio_cost_eur -= total_cost_basis_removed

        if self.state.total_shares > 0:
            self.state.avg_cost_eur = (
                self.state.total_portfolio_cost_eur / self.state.total_shares
            ).quantize(Decimal("0.0001"), ROUND_HALF_UP)
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
                replacement_pool[key] = (
                    replacement_pool.get(key, Decimal("0")) + lot.remaining_shares
                )

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
            fees_eur = (event.fees_usd * event.resolved_fx_rate).quantize(
                Decimal("0.0001"), ROUND_HALF_UP
            )
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

    def compute_carryforward(
        self,
        opening_losses: dict[int, Decimal] | None = None,
        max_year: int | None = None,
    ) -> CarryforwardLedger:
        """
        Simulate the 4-year loss carryforward across the tracked years (Art. 49 LIRPF).

        A net loss generated in year Y can offset net savings-base gains of years
        Y+1 .. Y+4 only. Oldest losses are consumed first. Losses not used within
        that window expire.

        ``opening_losses`` optionally seeds the pool with pending net losses from
        years *before* the imported data window, as ``{origin_year: magnitude}``
        where magnitude is a positive Decimal. ``max_year`` caps the simulation at
        a complete tax year, excluding the in-progress current year from the report
        (the FIFO engine itself is unaffected — only this ledger view is bounded).
        """
        # Pool entries are mutable [origin_year, remaining_magnitude] (remaining > 0).
        pool: list[list[Any]] = []
        for origin_year, magnitude in sorted((opening_losses or {}).items()):
            mag = abs(Decimal(str(magnitude)))
            if mag > 0:
                pool.append([origin_year, mag])

        summaries = {s.year: s for s in self.get_all_yearly_summaries()}
        ledger = CarryforwardLedger()

        for year in sorted(y for y in summaries if max_year is None or y <= max_year):
            # Expire losses that can no longer be used this year (origin <= year-5).
            survivors: list[list[Any]] = []
            for origin_year, remaining in pool:
                if origin_year <= year - 5 and remaining > 0:
                    ledger.expired.append((origin_year, remaining))
                else:
                    survivors.append([origin_year, remaining])
            pool = survivors

            net = summaries[year].net_gain_loss
            applied = Decimal("0")
            new_loss = Decimal("0")
            taxable_after = max(Decimal("0"), net)

            if net > 0:
                gain_left = net
                for entry in sorted(pool):  # oldest origin year first
                    if gain_left <= 0:
                        break
                    use = min(entry[1], gain_left)
                    entry[1] -= use
                    gain_left -= use
                    applied += use
                pool = [e for e in pool if e[1] > 0]
                taxable_after = gain_left
            elif net < 0:
                new_loss = -net
                pool.append([year, new_loss])

            ledger.rows.append(
                CarryforwardYear(
                    year=year,
                    net_result=net,
                    prior_losses_applied=applied,
                    taxable_after=taxable_after,
                    new_loss_carried=new_loss,
                )
            )

        ledger.pending_end = sorted(
            (origin_year, remaining, origin_year + 4)
            for origin_year, remaining in pool
            if remaining > 0
        )
        return ledger

    @staticmethod
    def _cross_category_cap(year: int) -> Decimal:
        """
        Cross-category offset cap for a year (Art. 49 LIRPF transitional regime).

        A negative balance in one savings-base category may offset at most this
        fraction of the positive balance in the other category.
        """
        caps = {2015: "0.10", 2016: "0.15", 2017: "0.20"}
        if year <= 2015:
            return Decimal(caps.get(year, "0.10"))
        return Decimal(caps.get(year, "0.25"))  # 25% from 2018 onward

    def compute_savings_ledger(
        self,
        savings_income: dict[int, SavingsIncomeYear],
        opening_losses: dict[int, Decimal] | None = None,
        opening_rcm_losses: dict[int, Decimal] | None = None,
        max_year: int | None = None,
    ) -> SavingsLedger:
        """
        Simulate the full savings base across two categories (Art. 48 & 49 LIRPF):
        capital gains/losses (G/L) and returns on movable capital (RCM = dividends
        + interest). Each year, prior-year losses offset same-category gains first,
        then a negative balance offsets up to the cross-category cap (typically 25%)
        of the other category's positive balance; remaining losses carry forward 4
        years and expire thereafter.

        ``opening_losses`` / ``opening_rcm_losses`` seed pending losses (positive
        magnitudes keyed by origin year) from before the data window. ``max_year``
        caps the simulation at the last complete tax year so the in-progress
        current year is excluded from the report view (the FIFO engine itself is
        unaffected).
        """
        gp_pool: list[list[Any]] = [
            [y, abs(Decimal(str(m)))]
            for y, m in sorted((opening_losses or {}).items())
            if abs(Decimal(str(m))) > 0
        ]
        rcm_pool: list[list[Any]] = [
            [y, abs(Decimal(str(m)))]
            for y, m in sorted((opening_rcm_losses or {}).items())
            if abs(Decimal(str(m))) > 0
        ]

        summaries = {s.year: s for s in self.get_all_yearly_summaries()}
        years = sorted(
            y for y in (set(summaries) | set(savings_income)) if max_year is None or y <= max_year
        )
        ledger = SavingsLedger()

        def _expire(pool: list[list[Any]], bucket: str, year: int) -> list[list[Any]]:
            survivors = []
            for origin_year, remaining in pool:
                if origin_year <= year - 5 and remaining > 0:
                    ledger.expired.append((bucket, origin_year, remaining))
                else:
                    survivors.append([origin_year, remaining])
            return survivors

        def _consume(pool: list[list[Any]], gain: Decimal) -> Decimal:
            """Consume oldest pool losses against a positive gain; return amount applied."""
            applied = Decimal("0")
            left = gain
            for entry in sorted(pool):
                if left <= 0:
                    break
                use = min(entry[1], left)
                entry[1] -= use
                left -= use
                applied += use
            pool[:] = [e for e in pool if e[1] > 0]
            return applied

        for year in years:
            gp_pool = _expire(gp_pool, "G/L", year)
            rcm_pool = _expire(rcm_pool, "RCM", year)

            gp_net = summaries[year].net_gain_loss if year in summaries else Decimal("0")
            inc = savings_income.get(year)
            rcm_net = inc.rcm_net if inc else Decimal("0")
            foreign_tax = inc.foreign_tax_eur if inc else Decimal("0")

            # 1) Same-category prior-loss application against positive balances.
            gp_after = gp_net
            gp_applied = Decimal("0")
            if gp_net > 0:
                gp_applied = _consume(gp_pool, gp_net)
                gp_after = gp_net - gp_applied

            rcm_after = rcm_net
            rcm_applied = Decimal("0")
            if rcm_net > 0:
                rcm_applied = _consume(rcm_pool, rcm_net)
                rcm_after = rcm_net - rcm_applied

            # 2) Cross-category offset (capped).
            cap = self._cross_category_cap(year)
            cross_offset = Decimal("0")
            cross_direction = ""
            if gp_after < 0 and rcm_after > 0:
                cross_offset = min(
                    -gp_after, (rcm_after * cap).quantize(Decimal("0.01"), ROUND_HALF_UP)
                )
                rcm_after -= cross_offset
                gp_after += cross_offset
                cross_direction = "gp->rcm"
            elif rcm_after < 0 and gp_after > 0:
                cross_offset = min(
                    -rcm_after, (gp_after * cap).quantize(Decimal("0.01"), ROUND_HALF_UP)
                )
                gp_after -= cross_offset
                rcm_after += cross_offset
                cross_direction = "rcm->gp"

            # 3) Remaining negatives carry forward; positives form the taxable base.
            if gp_after < 0:
                gp_pool.append([year, -gp_after])
                gp_taxable = Decimal("0")
            else:
                gp_taxable = gp_after
            if rcm_after < 0:
                rcm_pool.append([year, -rcm_after])
                rcm_taxable = Decimal("0")
            else:
                rcm_taxable = rcm_after

            ledger.rows.append(
                SavingsLedgerYear(
                    year=year,
                    gp_net=gp_net,
                    rcm_net=rcm_net,
                    gp_prior_applied=gp_applied,
                    rcm_prior_applied=rcm_applied,
                    cross_offset=cross_offset,
                    cross_direction=cross_direction,
                    gp_taxable=gp_taxable,
                    rcm_taxable=rcm_taxable,
                    foreign_tax_eur=foreign_tax,
                )
            )

        ledger.gp_pending_end = sorted((oy, rem, oy + 4) for oy, rem in gp_pool if rem > 0)
        ledger.rcm_pending_end = sorted((oy, rem, oy + 4) for oy, rem in rcm_pool if rem > 0)
        return ledger

    def print_ledger(self) -> None:
        """Print the full transaction ledger in a readable format."""
        from .report import ReportRenderer

        ReportRenderer(self).print_ledger()

    def print_tax_summary(
        self,
        opening_losses: dict[int, Decimal] | None = None,
        savings_income: dict[int, SavingsIncomeYear] | None = None,
    ) -> None:
        """Print the yearly tax summary."""
        from .report import ReportRenderer

        ReportRenderer(self).print_tax_summary(
            opening_losses=opening_losses, savings_income=savings_income
        )

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
        from .report import ReportRenderer

        return ReportRenderer(self).generate_html_content(
            lang=lang,
            espp_discounts=espp_discounts,
            espp_early_sale_discounts=espp_early_sale_discounts,
            opening_losses=opening_losses,
            savings_income=savings_income,
            securities=securities,
        )

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
        """Generate a PDF tax report (supports lang='en' or lang='es')."""
        from .report import ReportRenderer

        ReportRenderer(self).generate_pdf_report(
            filepath,
            lang=lang,
            espp_discounts=espp_discounts,
            espp_early_sale_discounts=espp_early_sale_discounts,
            opening_losses=opening_losses,
            savings_income=savings_income,
            securities=securities,
        )
