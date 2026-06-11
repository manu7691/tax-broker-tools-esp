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
            fees_eur = (event.fees_usd * event.resolved_fx_rate).quantize(Decimal("0.0001"), ROUND_HALF_UP)
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
        self, opening_losses: dict[int, Decimal] | None = None
    ) -> CarryforwardLedger:
        """
        Simulate the 4-year loss carryforward across the tracked years (Art. 49 LIRPF).

        A net loss generated in year Y can offset net savings-base gains of years
        Y+1 .. Y+4 only. Oldest losses are consumed first. Losses not used within
        that window expire.

        ``opening_losses`` optionally seeds the pool with pending net losses from
        years *before* the imported data window, as ``{origin_year: magnitude}``
        where magnitude is a positive Decimal.
        """
        # Pool entries are mutable [origin_year, remaining_magnitude] (remaining > 0).
        pool: list[list[Any]] = []
        for origin_year, magnitude in sorted((opening_losses or {}).items()):
            mag = abs(Decimal(str(magnitude)))
            if mag > 0:
                pool.append([origin_year, mag])

        summaries = {s.year: s for s in self.get_all_yearly_summaries()}
        ledger = CarryforwardLedger()

        for year in sorted(summaries):
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
    ) -> SavingsLedger:
        """
        Simulate the full savings base across two categories (Art. 48 & 49 LIRPF):
        capital gains/losses (G/L) and returns on movable capital (RCM = dividends
        + interest). Each year, prior-year losses offset same-category gains first,
        then a negative balance offsets up to the cross-category cap (typically 25%)
        of the other category's positive balance; remaining losses carry forward 4
        years and expire thereafter.

        ``opening_losses`` / ``opening_rcm_losses`` seed pending losses (positive
        magnitudes keyed by origin year) from before the data window.
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
        years = sorted(set(summaries) | set(savings_income))
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
                cross_offset = min(-gp_after, (rcm_after * cap).quantize(Decimal("0.01"), ROUND_HALF_UP))
                rcm_after -= cross_offset
                gp_after += cross_offset
                cross_direction = "gp->rcm"
            elif rcm_after < 0 and gp_after > 0:
                cross_offset = min(-rcm_after, (gp_after * cap).quantize(Decimal("0.01"), ROUND_HALF_UP))
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

        ledger.gp_pending_end = sorted(
            (oy, rem, oy + 4) for oy, rem in gp_pool if rem > 0
        )
        ledger.rcm_pending_end = sorted(
            (oy, rem, oy + 4) for oy, rem in rcm_pool if rem > 0
        )
        return ledger

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
            shares_str = f"{shares_sign}{TaxEngine.format_shares(e.shares)}"
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
                        f"   └─ FIFO Match: {TaxEngine.format_shares(match.shares)} shares acq on {match.acquisition_date.isoformat()} "
                        f"cost €{match.acquisition_price_eur:,.4f} -> Gain/Loss: €{match.realized_gain_loss:,.4f}"
                    )

        print("=" * 120)

    def print_tax_summary(self, opening_losses: dict[int, Decimal] | None = None, savings_income: dict[int, SavingsIncomeYear] | None = None) -> None:
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

        if savings_income:
            # Two-bucket savings base supersedes the single-bucket ledger to avoid
            # contradictory pending balances (cross-offset changes what carries forward).
            sledger = self.compute_savings_ledger(savings_income, opening_losses=opening_losses)
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
            ledger = self.compute_carryforward(opening_losses)
            print("\nLOSS CARRYFORWARD LEDGER (Art. 49 LIRPF)")
            print("-" * 95)
            if opening_losses:
                seeded = ", ".join(f"{y}: €{abs(Decimal(str(a))):,.2f}" for y, a in sorted(opening_losses.items()))
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
            if not ledger.pending_end and not ledger.expired and all(
                cf_row.prior_losses_applied == 0 for cf_row in ledger.rows
            ):
                print("  No losses to carry forward.")

        print("\n" + "=" * 95)
        print("  NOTE: Transaction Fees (Commissions, SEC Fees) ARE DEDUCTED (Wire Transfers EXCLUDED)")
        print("  from your capital gains automatically, per Spanish Tax Law (Gastos Inherentes).")
        print("  NOTE: 'Est. Tax' is an ISOLATED estimate on these stock gains only — your real")
        print("  liability depends on total savings income and prior-year loss carryforward.")
        print("=" * 95 + "\n")

    def _append_carryforward_section(
        self, html: list[str], is_es: bool, opening_losses: dict[int, Decimal] | None
    ) -> None:
        """Append the single-bucket (capital-gains only) loss-carryforward ledger."""
        ledger = self.compute_carryforward(opening_losses)
        cf_title = (
            "Loss Carryforward Ledger (Art. 49 LIRPF)"
            if not is_es
            else "Libro de Compensación de Pérdidas (Art. 49 LIRPF)"
        )
        html.append(f"<h2>{cf_title}</h2>")
        if not is_es:
            html.append(
                "<p>Net losses in a year offset savings-base gains of the following "
                "<strong>4 years</strong> (oldest losses first); unused losses then expire. "
                "This ledger simulates that across the tracked years. Cross-category offset against "
                "other savings income (dividends, interest) is still applied by your advisor at filing time.</p>"
            )
        else:
            html.append(
                "<p>Las pérdidas netas de un ejercicio compensan ganancias de la base del ahorro de los "
                "<strong>4 ejercicios siguientes</strong> (primero las más antiguas); las no utilizadas caducan. "
                "Este libro simula esa compensación entre los años analizados. La compensación con otras rentas "
                "del ahorro (dividendos, intereses) la aplica tu asesor al presentar la declaración.</p>"
            )
        if opening_losses:
            seeded = ", ".join(
                f"{y}: €{abs(Decimal(str(a))):,.2f}" for y, a in sorted(opening_losses.items())
            )
            seed_label = (
                f"Seeded with prior-year pending losses — {seeded}"
                if not is_es
                else f"Inicializado con pérdidas pendientes de años anteriores — {seeded}"
            )
            html.append(f"<p style='font-size: 11px; color: #555;'><em>{seed_label}</em></p>")

        html.append("<table>")
        if not is_es:
            html.append(
                "<tr><th>Year</th><th>Net Result</th><th>Prior-Year Loss Applied</th><th>Taxable After Carryforward</th></tr>"
            )
        else:
            html.append(
                "<tr><th>Año</th><th>Resultado Neto</th><th>Pérdida de Años Anteriores Aplicada</th><th>Base Tras Compensación</th></tr>"
            )
        for r in ledger.rows:
            net_style = "gain" if r.net_result >= 0 else "loss"
            html.append("<tr>")
            html.append(f"<td>{r.year}</td>")
            html.append(f"<td class='{net_style}'>€{r.net_result:,.2f}</td>")
            html.append(f"<td>€{r.prior_losses_applied:,.2f}</td>")
            html.append(f"<td><strong>€{r.taxable_after:,.2f}</strong></td>")
            html.append("</tr>")
        html.append("</table>")

        if ledger.pending_end:
            pend_label = (
                "Pending losses carried forward (still usable):"
                if not is_es
                else "Pérdidas pendientes de compensar (aún utilizables):"
            )
            use_by_label = "use by" if not is_es else "usar antes de"
            html.append(f"<p><strong>{pend_label}</strong></p><ul>")
            for origin_year, remaining, use_by in ledger.pending_end:
                html.append(
                    f"<li class='loss'>{origin_year}: €{remaining:,.2f} — {use_by_label} {use_by}</li>"
                )
            html.append("</ul>")
        if ledger.expired:
            exp_label = (
                "⚠️ Losses that EXPIRED unused (4-year limit passed):"
                if not is_es
                else "⚠️ Pérdidas CADUCADAS sin usar (superado el límite de 4 años):"
            )
            html.append(f"<p style='color:#cc0000;'><strong>{exp_label}</strong></p><ul>")
            for origin_year, amount in ledger.expired:
                html.append(f"<li style='color:#cc0000;'>{origin_year}: €{amount:,.2f}</li>")
            html.append("</ul>")
        if not ledger.pending_end and not ledger.expired and all(
            r.prior_losses_applied == 0 for r in ledger.rows
        ):
            no_loss = (
                "No losses to carry forward."
                if not is_es
                else "No hay pérdidas pendientes de compensar."
            )
            html.append(f"<p><em>{no_loss}</em></p>")

    def _append_savings_section(
        self,
        html: list[str],
        is_es: bool,
        savings_income: dict[int, SavingsIncomeYear],
        opening_losses: dict[int, Decimal] | None,
    ) -> None:
        """Append the two-bucket savings base (G/L + RCM) with the 25% cross-offset."""
        sledger = self.compute_savings_ledger(savings_income, opening_losses=opening_losses)
        title = (
            "Savings Base — Capital Gains + Dividends/Interest (Art. 49 LIRPF)"
            if not is_es
            else "Base del Ahorro — Ganancias + Dividendos/Intereses (Art. 49 LIRPF)"
        )
        html.append(f"<h2>{title}</h2>")
        if not is_es:
            html.append(
                "<p>Combines your stock gains/losses with dividend & interest income (RCM). "
                "A net loss in one category offsets up to the legal cap (25% from 2018) of the other "
                "category's positive balance; the rest carries forward 4 years. "
                "<strong>Foreign tax withheld is shown for reference only</strong> — the "
                "<em>deducción por doble imposición internacional</em> is applied by your advisor.</p>"
            )
        else:
            html.append(
                "<p>Combina tus ganancias/pérdidas bursátiles con los dividendos e intereses (RCM). "
                "Una pérdida neta en una categoría compensa hasta el límite legal (25% desde 2018) del saldo "
                "positivo de la otra; el resto se arrastra 4 años. "
                "<strong>La retención en origen se muestra solo a título informativo</strong> — la "
                "<em>deducción por doble imposición internacional</em> la aplica tu asesor.</p>"
            )
        html.append("<table>")
        if not is_es:
            html.append(
                "<tr><th>Year</th><th>Capital G/L (net)</th><th>Dividends + Interest</th>"
                "<th>Cross-Category Offset</th><th>Taxable Savings Base</th><th>Foreign Tax (info)</th></tr>"
            )
        else:
            html.append(
                "<tr><th>Año</th><th>Ganancia/Pérdida (neta)</th><th>Dividendos + Intereses</th>"
                "<th>Compensación entre Categorías</th><th>Base del Ahorro Imponible</th><th>Retención Origen (info)</th></tr>"
            )
        for r in sledger.rows:
            gp_style = "gain" if r.gp_net >= 0 else "loss"
            offset_txt = f"€{r.cross_offset:,.2f}" if r.cross_offset > 0 else "—"
            html.append("<tr>")
            html.append(f"<td>{r.year}</td>")
            html.append(f"<td class='{gp_style}'>€{r.gp_net:,.2f}</td>")
            html.append(f"<td>€{r.rcm_net:,.2f}</td>")
            html.append(f"<td>{offset_txt}</td>")
            html.append(f"<td><strong>€{r.savings_base:,.2f}</strong></td>")
            html.append(f"<td>€{r.foreign_tax_eur:,.2f}</td>")
            html.append("</tr>")
        html.append("</table>")

        if sledger.total_foreign_tax > 0:
            ft_label = (
                f"Total foreign tax withheld: €{sledger.total_foreign_tax:,.2f} — "
                "claim as deducción por doble imposición internacional (advisor)."
                if not is_es
                else f"Retención total en origen: €{sledger.total_foreign_tax:,.2f} — "
                "reclámala como deducción por doble imposición internacional (asesor)."
            )
            html.append(f"<p style='font-size: 11px; color: #555;'><em>{ft_label}</em></p>")

            cap_note = (
                "Spain's tax treaties cap the foreign-tax credit at the treaty rate "
                "(<strong>15%</strong> for US dividends). Withholding above that is not "
                "creditable here — reclaim the excess from the source country "
                "(e.g. a corrected W-8BEN / US 1040-NR)."
                if not is_es else
                "Los convenios de doble imposición limitan la deducción al tipo del convenio "
                "(<strong>15%</strong> para dividendos de EE. UU.). La retención por encima de "
                "ese límite no es deducible aquí — reclama el exceso al país de origen "
                "(p. ej. un W-8BEN corregido / 1040-NR de EE. UU.)."
            )
            html.append(f"<p style='font-size: 11px; color: #555;'><em>{cap_note}</em></p>")

            # Flag years whose effective withholding exceeds the 15% treaty rate.
            over = [
                (yr, (inc.foreign_tax_eur / inc.dividends_eur * 100).quantize(Decimal("0.1")))
                for yr, inc in sorted(savings_income.items())
                if inc.dividends_eur > 0
                and inc.foreign_tax_eur > inc.dividends_eur * Decimal("0.15")
            ]
            if over:
                flagged = ", ".join(f"{yr} ({rate}%)" for yr, rate in over)
                warn = (
                    f"⚠️ Effective withholding above 15% detected: {flagged}. Only 15% is "
                    "creditable in Spain; reclaim the rest at source."
                    if not is_es else
                    f"⚠️ Retención efectiva superior al 15% detectada: {flagged}. Solo el 15% es "
                    "deducible en España; reclama el resto en origen."
                )
                html.append(f"<p style='font-size: 11px; color: #cc6600;'><em>{warn}</em></p>")

        use_by = "use by" if not is_es else "usar antes de"
        if sledger.gp_pending_end:
            lbl = (
                "Pending capital losses carried forward:"
                if not is_es
                else "Pérdidas patrimoniales pendientes de compensar:"
            )
            html.append(f"<p><strong>{lbl}</strong></p><ul>")
            for oy, rem, ub in sledger.gp_pending_end:
                html.append(f"<li class='loss'>{oy}: €{rem:,.2f} — {use_by} {ub}</li>")
            html.append("</ul>")
        if sledger.rcm_pending_end:
            lbl = (
                "Pending RCM (dividend/interest) losses carried forward:"
                if not is_es
                else "Pérdidas de RCM (dividendos/intereses) pendientes de compensar:"
            )
            html.append(f"<p><strong>{lbl}</strong></p><ul>")
            for oy, rem, ub in sledger.rcm_pending_end:
                html.append(f"<li class='loss'>{oy}: €{rem:,.2f} — {use_by} {ub}</li>")
            html.append("</ul>")
        if sledger.expired:
            exp_label = (
                "⚠️ Losses that EXPIRED unused (4-year limit passed):"
                if not is_es
                else "⚠️ Pérdidas CADUCADAS sin usar (superado el límite de 4 años):"
            )
            html.append(f"<p style='color:#cc0000;'><strong>{exp_label}</strong></p><ul>")
            for bucket, oy, amount in sledger.expired:
                html.append(f"<li style='color:#cc0000;'>{bucket} {oy}: €{amount:,.2f}</li>")
            html.append("</ul>")

    def _append_modelo100_guide(self, html: list[str], is_es: bool) -> None:
        """
        Append a crosswalk from this report's figures to Modelo 100 sections.

        Box numbers ("casillas") change almost every campaign, so this guide
        anchors on the stable section names (*apartados*) and flags the casilla
        numbers as values to verify against the current year's form.
        """
        title = "Modelo 100 Filing Guide (IRPF)" if not is_es else "Guía de Cumplimentación del Modelo 100 (IRPF)"
        html.append(f"<h2>{title}</h2>")
        if not is_es:
            html.append(
                "<p style='font-size: 11px; color: #cc6600;'><strong>⚠️ Verify the casilla numbers.</strong> "
                "Modelo 100 box numbers change almost every year. The <em>section names</em> below are stable; "
                "the casilla numbers are indicative — confirm them against the form for your filing year.</p>"
            )
        else:
            html.append(
                "<p style='font-size: 11px; color: #cc6600;'><strong>⚠️ Verifica los números de casilla.</strong> "
                "Las casillas del Modelo 100 cambian casi cada año. Los <em>nombres de apartado</em> de abajo son estables; "
                "los números de casilla son orientativos — confírmalos en el modelo de tu ejercicio.</p>"
            )
        html.append("<table>")
        if not is_es:
            html.append(
                "<tr><th>Figure from this report</th><th>Modelo 100 section (apartado)</th><th>Casilla (verify)</th></tr>"
            )
            rows = [
                ("Each sale: transmission value, acquisition value, gain/loss",
                 "Ganancias y pérdidas patrimoniales derivadas de la transmisión de acciones negociadas en mercados oficiales (base del ahorro)",
                 "≈ 0328–0344"),
                ("Net balance of gains/losses (savings base)",
                 "Saldo neto de ganancias y pérdidas patrimoniales a integrar en la base imponible del ahorro",
                 "≈ 0424 / 0425"),
                ("Dividends & interest (RCM)",
                 "Rendimientos del capital mobiliario a integrar en la base imponible del ahorro (dividends, interest)",
                 "≈ 0027–0031"),
                ("Foreign tax withheld (US)",
                 "Deducción por doble imposición internacional",
                 "≈ 0588 / 0589"),
                ("Prior-year pending losses (this ledger's 'pending')",
                 "Saldos netos negativos de ejercicios anteriores pendientes de compensar (one box per origin year)",
                 "≈ 0439–0443"),
                ("Resulting savings tax base",
                 "Base imponible del ahorro",
                 "≈ 0460"),
                ("ESPP early-sale discount (if any)",
                 "Rendimientos del trabajo — via Declaración Complementaria for the purchase year",
                 "trabajo boxes"),
            ]
        else:
            html.append(
                "<tr><th>Dato de este informe</th><th>Apartado del Modelo 100</th><th>Casilla (verificar)</th></tr>"
            )
            rows = [
                ("Cada venta: valor de transmisión, de adquisición y ganancia/pérdida",
                 "Ganancias y pérdidas patrimoniales derivadas de la transmisión de acciones negociadas en mercados oficiales (base del ahorro)",
                 "≈ 0328–0344"),
                ("Saldo neto de ganancias y pérdidas (base del ahorro)",
                 "Saldo neto de ganancias y pérdidas patrimoniales a integrar en la base imponible del ahorro",
                 "≈ 0424 / 0425"),
                ("Dividendos e intereses (RCM)",
                 "Rendimientos del capital mobiliario a integrar en la base imponible del ahorro (dividendos, intereses)",
                 "≈ 0027–0031"),
                ("Retención en origen (EE. UU.)",
                 "Deducción por doble imposición internacional",
                 "≈ 0588 / 0589"),
                ("Pérdidas pendientes de años anteriores ('pendientes' de este libro)",
                 "Saldos netos negativos de ejercicios anteriores pendientes de compensar (una casilla por año de origen)",
                 "≈ 0439–0443"),
                ("Base imponible del ahorro resultante",
                 "Base imponible del ahorro",
                 "≈ 0460"),
                ("Descuento ESPP por venta anticipada (si lo hay)",
                 "Rendimientos del trabajo — mediante Declaración Complementaria del año de compra",
                 "casillas de trabajo"),
            ]
        for figure, apartado, casilla in rows:
            html.append(
                f"<tr><td>{figure}</td><td>{apartado}</td><td>{casilla}</td></tr>"
            )
        html.append("</table>")

    def _append_transmisiones_table(self, html: list[str], is_es: bool) -> None:
        """Append a per-disposal table formatted for the Modelo 100 capital-gains entry.

        One row per FIFO lot consumed by each sale, with the acquisition value, the
        transmission value, the (pro-rata) fees, and the net gain/loss — i.e. exactly
        the fields the *ganancias y pérdidas patrimoniales* section asks for. The net
        gain/loss column sums to the engine's realized result (fees included).
        """
        sells = [
            pe for pe in self.processed_events
            if pe.event.event_type == EventType.SELL and pe.fifo_matches
        ]
        if not sells:
            return

        title = (
            "Capital Gains Detail — Disposals (Modelo 100)"
            if not is_es
            else "Detalle de Ganancias Patrimoniales — Transmisiones (Modelo 100)"
        )
        html.append(f"<h2>{title}</h2>")
        if not is_es:
            html.append(
                "<p>One row per acquisition lot consumed by each sale (FIFO), with the figures "
                "the <em>ganancias y pérdidas patrimoniales</em> section needs. Fees are the "
                "pro-rata transmission costs; the <strong>Net Gain/Loss</strong> column already "
                "deducts them and sums to your total realized result.</p>"
            )
            html.append(
                "<table class='ledger'><tr><th>Security</th><th>ISIN</th>"
                "<th>Acq. date</th><th>Acq. value (EUR)</th><th>Transm. date</th>"
                "<th>Transm. value (EUR)</th><th>Fees (EUR)</th><th>Net Gain/Loss (EUR)</th></tr>"
            )
        else:
            html.append(
                "<p>Una fila por cada lote de adquisición consumido en cada venta (FIFO), con los "
                "datos que pide el apartado de <em>ganancias y pérdidas patrimoniales</em>. Los "
                "gastos son los costes de transmisión prorrateados; la columna <strong>G/P Neta"
                "</strong> ya los descuenta y suma tu resultado realizado total.</p>"
            )
            html.append(
                "<table class='ledger'><tr><th>Valor</th><th>ISIN</th>"
                "<th>Fecha adq.</th><th>Valor adq. (EUR)</th><th>Fecha transm.</th>"
                "<th>Valor transm. (EUR)</th><th>Gastos (EUR)</th><th>G/P Neta (EUR)</th></tr>"
            )

        total_net = Decimal("0")
        for pe in sells:
            e = pe.event
            sale_fees_eur = (
                (e.fees_usd * e.resolved_fx_rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
                if e.fees_usd > 0 else Decimal("0")
            )
            for m in pe.fifo_matches:
                acq_value = (m.acquisition_price_eur * m.shares).quantize(Decimal("0.01"), ROUND_HALF_UP)
                transm_value = (e.price_eur * m.shares).quantize(Decimal("0.01"), ROUND_HALF_UP)
                fee_alloc = (
                    (sale_fees_eur * m.shares / e.shares).quantize(Decimal("0.01"), ROUND_HALF_UP)
                    if e.shares > 0 else Decimal("0")
                )
                net = (m.realized_gain_loss - fee_alloc).quantize(Decimal("0.01"), ROUND_HALF_UP)
                total_net += net
                net_style = "gain" if net >= 0 else "loss"
                html.append(
                    f"<tr><td>{e.symbol or '—'}</td><td>{e.isin or '—'}</td>"
                    f"<td>{m.acquisition_date.isoformat() if not is_es else m.acquisition_date.strftime('%d/%m/%Y')}</td>"
                    f"<td>€{acq_value:,.2f}</td>"
                    f"<td>{e.event_date.isoformat() if not is_es else e.event_date.strftime('%d/%m/%Y')}</td>"
                    f"<td>€{transm_value:,.2f}</td><td>€{fee_alloc:,.2f}</td>"
                    f"<td class='{net_style}'><strong>€{net:,.2f}</strong></td></tr>"
                )
        net_style = "gain" if total_net >= 0 else "loss"
        total_label = "Total net gain/loss" if not is_es else "G/P neta total"
        html.append(
            f"<tr><td colspan='7'><strong>{total_label}</strong></td>"
            f"<td class='{net_style}'><strong>€{total_net:,.2f}</strong></td></tr>"
        )
        html.append("</table>")

    def _append_broker_breakdown(self, html: list[str], is_es: bool) -> None:
        """Append a per-broker realized gain/loss subtotal table.

        Results are attributed to the broker where the *disposal* (SELL) occurred.
        Because FIFO runs across the merged queue, a sale may consume shares
        acquired at another broker, so this is a by-selling-broker view of the
        realized result, not a by-acquisition one.
        """
        gains: dict[str, Decimal] = {}
        losses: dict[str, Decimal] = {}
        for pe in self.processed_events:
            if pe.event.event_type != EventType.SELL:
                continue
            b = pe.event.broker
            if pe.realized_gain_loss > 0:
                gains[b] = gains.get(b, Decimal("0")) + pe.realized_gain_loss
            elif pe.realized_gain_loss < 0:
                losses[b] = losses.get(b, Decimal("0")) + pe.realized_gain_loss

        brokers = sorted(set(gains) | set(losses))
        if not brokers:
            return

        title = (
            "Realized Gains/Losses by Broker"
            if not is_es
            else "Ganancias/Pérdidas Realizadas por Bróker"
        )
        html.append(f"<h2>{title}</h2>")
        if not is_es:
            html.append(
                "<p style='font-size: 11px; color: #555;'>Attributed to the broker where each "
                "sale occurred. Because FIFO is applied across all brokers, a sale may draw on "
                "shares acquired at another broker; this table splits the <em>realized result</em> "
                "by selling broker, not by acquisition.</p>"
            )
        else:
            html.append(
                "<p style='font-size: 11px; color: #555;'>Atribuidas al bróker donde se produjo "
                "cada venta. Como el FIFO se aplica entre todos los brókers, una venta puede consumir "
                "acciones adquiridas en otro bróker; esta tabla reparte el <em>resultado realizado</em> "
                "por bróker vendedor, no por adquisición.</p>"
            )
        html.append("<table>")
        if not is_es:
            html.append(
                "<tr><th>Broker</th><th>Realized Gains</th><th>Realized Losses</th>"
                "<th>Net Realized G/L</th></tr>"
            )
        else:
            html.append(
                "<tr><th>Bróker</th><th>Ganancias Realizadas</th><th>Pérdidas Realizadas</th>"
                "<th>G/P Neta Realizada</th></tr>"
            )

        total_gain = Decimal("0")
        total_loss = Decimal("0")
        for b in brokers:
            gain_amt = gains.get(b, Decimal("0"))
            loss_amt = losses.get(b, Decimal("0"))
            net = gain_amt + loss_amt
            total_gain += gain_amt
            total_loss += loss_amt
            net_style = "gain" if net >= 0 else "loss"
            html.append(
                f"<tr><td>{b}</td><td>€{gain_amt:,.2f}</td><td>€{loss_amt:,.2f}</td>"
                f"<td class='{net_style}'><strong>€{net:,.2f}</strong></td></tr>"
            )
        net_total = total_gain + total_loss
        net_style = "gain" if net_total >= 0 else "loss"
        html.append(
            f"<tr><td><strong>Total</strong></td><td><strong>€{total_gain:,.2f}</strong></td>"
            f"<td><strong>€{total_loss:,.2f}</strong></td>"
            f"<td class='{net_style}'><strong>€{net_total:,.2f}</strong></td></tr>"
        )
        html.append("</table>")

    def _append_portfolio_summary(
        self, html: list[str], is_es: bool, securities: "list[SecurityResult]"
    ) -> None:
        """Append the per-security portfolio rollup (gains / losses / net / position).

        Shown only in multi-security mode: one row per security (its own FIFO
        queue, keyed by ISIN) plus a portfolio Total row that ties to the combined
        savings base below.
        """
        title = "Portfolio Summary by Security" if not is_es else "Resumen de Cartera por Valor"
        html.append(f"<h2>{title}</h2>")
        if not is_es:
            html.append(
                "<p>Each security has its own FIFO queue (grouped by ISIN; the same ISIN "
                "across brokers is merged). The savings base, 4-year carryforward and 25% "
                "cross-category offset below operate on the <strong>portfolio total</strong>; "
                "the 2-month wash-sale rule stays <strong>per security</strong>.</p>"
            )
        else:
            html.append(
                "<p>Cada valor tiene su propia cola FIFO (agrupada por ISIN; el mismo ISIN "
                "en distintos brókers se fusiona). La base del ahorro, la compensación a 4 "
                "años y el límite del 25% entre categorías de abajo operan sobre el "
                "<strong>total de la cartera</strong>; la regla de los 2 meses se mantiene "
                "<strong>por valor</strong>.</p>"
            )
        html.append("<table>")
        if not is_es:
            html.append(
                "<tr><th>Security</th><th>ISIN</th><th>Broker(s)</th><th>Realized Gains</th>"
                "<th>Realized Losses</th><th>Net G/L</th><th>Open Position (shares)</th></tr>"
            )
        else:
            html.append(
                "<tr><th>Valor</th><th>ISIN</th><th>Bróker(es)</th><th>Ganancias Realizadas</th>"
                "<th>Pérdidas Realizadas</th><th>G/P Neta</th><th>Posición Abierta (acciones)</th></tr>"
            )
        total_gains = Decimal("0")
        total_losses = Decimal("0")
        total_net = Decimal("0")
        for r in securities:
            summaries = r.engine.get_all_yearly_summaries()
            gains = sum((s.total_gains for s in summaries), Decimal("0"))
            losses = sum((s.deductible_losses for s in summaries), Decimal("0"))
            net = r.net_gain_loss
            total_gains += gains
            total_losses += losses
            total_net += net
            brokers = ", ".join(sorted({pe.event.broker for pe in r.engine.processed_events})) or "—"
            net_style = "gain" if net >= 0 else "loss"
            html.append(
                f"<tr><td>{r.security.label}</td><td>{r.security.isin or '—'}</td>"
                f"<td>{brokers}</td><td>€{gains:,.2f}</td><td>€{losses:,.2f}</td>"
                f"<td class='{net_style}'><strong>€{net:,.2f}</strong></td>"
                f"<td>{r.engine.state.total_shares:,.4f}</td></tr>"
            )
        net_style = "gain" if total_net >= 0 else "loss"
        total_label = "Total (Portfolio)" if not is_es else "Total (Cartera)"
        html.append(
            f"<tr><td><strong>{total_label}</strong></td><td></td><td></td>"
            f"<td><strong>€{total_gains:,.2f}</strong></td>"
            f"<td><strong>€{total_losses:,.2f}</strong></td>"
            f"<td class='{net_style}'><strong>€{total_net:,.2f}</strong></td><td></td></tr>"
        )
        html.append("</table>")

    def _append_ledger_table(
        self, html: list[str], is_es: bool, events: list[ProcessedEvent]
    ) -> None:
        """Append the detailed FIFO transaction ledger table for ``events``."""
        html.append("<table class='ledger'>")
        if not is_es:
            html.append(
                "<tr><th>Date</th><th class='tipo'>Type</th><th>Broker</th><th>Shares</th><th>Price (orig.)</th><th>FX Rate</th><th>Price (EUR)</th><th>Total Value (EUR)</th><th>Portfolio Qty</th><th>Avg Cost (EUR)</th><th>Realized G/L (EUR)</th></tr>"
            )
        else:
            html.append(
                "<tr><th>Fecha</th><th class='tipo'>Tipo</th><th>Bróker</th><th>Acciones</th><th>Precio (orig.)</th><th>Tipo Cambio</th><th>Precio (EUR)</th><th>Valor Total (EUR)</th><th>Cant. Cartera</th><th>Coste Medio (EUR)</th><th>G/P Realizada (EUR)</th></tr>"
            )

        for pe in events:
            e = pe.event
            shares_sign = "+" if e.event_type != EventType.SELL else "-"
            shares_str = f"{shares_sign}{TaxEngine.format_shares(e.shares)}"

            # Translate EventType for Spanish
            event_type_str = e.event_type.value
            notes_str = e.notes
            if is_es:
                event_type_str = {
                    EventType.VEST: "Concesión",
                    EventType.SELL: "Venta",
                    EventType.BUY: "Compra",
                    EventType.EXERCISE: "Ejercicio",
                }.get(e.event_type, event_type_str)

                # Translate common notes
                notes_str = (
                    notes_str.replace("RSU Vest", "Concesión RSU")
                    .replace("ESPP Purchase", "Compra ESPP")
                    .replace("Sell-to-Cover (Auto-detected)", "Venta para Impuestos (Automático)")
                    .replace("Pending Settlement", "Pendiente de Liquidación")
                    .replace("Manual Sell", "Venta Manual")
                    .replace("Sell Order", "Orden de Venta")
                    .replace("Revolut Buy", "Compra Revolut")
                    .replace("Revolut Sell", "Venta Revolut")
                    .replace("Includes", "Incluye")
                    .replace("fees", "comisiones")
                )

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
            html.append(f"<td class='tipo'>{event_type_cell}</td>")
            html.append(f"<td>{e.broker}</td>")
            html.append(f"<td>{shares_str}</td>")
            price_cell = (
                f"${e.price_usd:,.2f}" if e.currency == "USD" else f"{e.price_usd:,.2f} {e.currency}"
            )
            html.append(f"<td>{price_cell}</td>")
            html.append(f"<td>{e.resolved_fx_rate:.4f}</td>")
            html.append(f"<td>€{e.price_eur:,.4f}</td>")
            html.append(f"<td>€{e.total_value_eur:,.2f}</td>")
            html.append(f"<td>{TaxEngine.format_shares(pe.total_shares_after)}</td>")
            html.append(f"<td>€{pe.avg_cost_eur_after:,.4f}</td>")
            html.append(f"<td>{gl_str}{wash_sale_note}</td>")
            html.append("</tr>")

            if pe.fifo_matches:
                for match in pe.fifo_matches:
                    html.append("<tr style='background-color:#fafafa; font-style:italic;'>")
                    html.append("<td></td>")
                    match_label = "FIFO Match:" if not is_es else "Cruce FIFO:"
                    # Span Type + Broker + Shares (the table gained a Broker column).
                    html.append(f"<td colspan='3' style='text-align:right;'>{match_label}</td>")
                    if not is_es:
                        match_text = f"Matched {TaxEngine.format_shares(match.shares)} shares from acquisition on {match.acquisition_date} at €{match.acquisition_price_eur:,.4f}"
                    else:
                        acq_date_str = match.acquisition_date.strftime("%d/%m/%Y")
                        match_text = f"Se cruzaron {TaxEngine.format_shares(match.shares)} acciones de la adquisición del {acq_date_str} a €{match.acquisition_price_eur:,.4f}"
                    html.append(f"<td colspan='6'>{match_text}</td>")
                    html.append(f"<td>€{match.realized_gain_loss:,.2f}</td>")
                    html.append("</tr>")
        html.append("</table>")

    def _append_calc_details(
        self, html: list[str], is_es: bool, events: list[ProcessedEvent]
    ) -> None:
        """Append the per-sale FIFO calculation breakdown for ``events``."""
        sell_events = [pe for pe in events if pe.event.event_type == EventType.SELL]
        if not sell_events:
            empty_text = "No sales transactions found." if not is_es else "No se encontraron transacciones de venta."
            html.append(f"<p><em>{empty_text}</em></p>")

        for i, pe in enumerate(sell_events, 1):
            e = pe.event
            event_date_str = e.event_date.isoformat() if not is_es else e.event_date.strftime("%d/%m/%Y")
            sale_header = f"Sale on {event_date_str}" if not is_es else f"Venta el {event_date_str}"
            html.append(f"<h3>{i}. {sale_header}</h3>")
            html.append("<ul>")
            sold_text = f"Sold: {TaxEngine.format_shares(e.shares)} shares @ €{e.price_eur:,.4f}" if not is_es else f"Vendido: {TaxEngine.format_shares(e.shares)} acciones @ €{e.price_eur:,.4f}"
            html.append(f"<li><strong>{sold_text}</strong></li>")
            matches_header = "FIFO Lot Matches" if not is_es else "Cruces de Lotes FIFO"
            html.append(f"<li><strong>{matches_header}</strong>:</li>")
            html.append("<ul>")
            for match in pe.fifo_matches:
                if not is_es:
                    match_desc = f"Matched {TaxEngine.format_shares(match.shares)} shares acquired on {match.acquisition_date} @ €{match.acquisition_price_eur:,.4f}. "
                    gl_label = "Gain/Loss"
                else:
                    acq_date_str = match.acquisition_date.strftime("%d/%m/%Y")
                    match_desc = f"Cruza {TaxEngine.format_shares(match.shares)} acciones adquiridas el {acq_date_str} @ €{match.acquisition_price_eur:,.4f}. "
                    gl_label = "Ganancia/Pérdida"
                html.append(
                    f"<li>{match_desc}"
                    f"{gl_label}: <code>({e.price_eur:,.4f} - {match.acquisition_price_eur:,.4f}) * {TaxEngine.format_shares(match.shares)} = €{match.realized_gain_loss:,.2f}</code></li>"
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

    def _append_per_security_ledgers(
        self, html: list[str], is_es: bool, securities: "list[SecurityResult]"
    ) -> None:
        """Append one ledger + FIFO-detail section per security (portfolio mode).

        Each security starts on its own page (the ``h2`` page-break rule) and
        carries its own transaction ledger and per-sale calculation breakdown.
        """
        sub = "Transactions & FIFO Detail" if not is_es else "Transacciones y Detalle FIFO"
        details_title = (
            "Calculation Details for Sales" if not is_es else "Detalles de Cálculo para Ventas"
        )
        for r in securities:
            label = r.security.label
            head = f"{label} ({r.security.isin})" if r.security.isin else label
            html.append(f"<h2>{head} — {sub}</h2>")
            self._append_ledger_table(html, is_es, r.engine.processed_events)
            html.append(f"<h3>{details_title}</h3>")
            self._append_calc_details(html, is_es, r.engine.processed_events)

    def generate_html_content(self, lang: str = "en", espp_discounts: dict[int, Decimal] | None = None, espp_early_sale_discounts: dict[int, Decimal] | None = None, opening_losses: dict[int, Decimal] | None = None, savings_income: dict[int, SavingsIncomeYear] | None = None, securities: "list[SecurityResult] | None" = None) -> str:
        """Generate HTML content for the tax report (supports English 'en' and Spanish 'es')."""
        is_es = lang.lower() == "es"

        # Distinct brokers across the processed transactions. Used to tag each
        # ledger row and, when more than one broker is present (e.g. E*TRADE +
        # Revolut), to add a per-broker realized G/L breakdown.
        brokers = sorted({pe.event.broker for pe in self.processed_events})
        multi_broker = len(brokers) > 1

        html = []
        html.append("<html><head><style>")
        html.append(
            "body { font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }"
        )
        html.append("* { box-sizing: border-box; }")
        html.append(
            "table { border-collapse: collapse; width: 100%; "
            "margin-bottom: 20px; font-size: 12px; }"
        )
        html.append("th, td { border: 1px solid #ddd; padding: 6px; text-align: left; }")
        # The Type/Tipo column holds long notes (e.g. RSU confirmation filenames),
        # so it (and only it) is allowed to wrap. The wide detailed ledger (10
        # columns) also uses a smaller font and a fixed Type width so the numeric
        # columns keep their natural size and the table fits the page without
        # squeezing the note into a tall, unreadable sliver.
        html.append("td.tipo, th.tipo { overflow-wrap: anywhere; word-break: break-word; }")
        html.append(".ledger { font-size: 9px; }")
        html.append(".ledger td.tipo, .ledger th.tipo { width: 200px; }")
        html.append(".ledger td, .ledger th { padding: 4px; }")
        html.append("th { background-color: #f2f2f2; }")
        html.append("h1, h2, h3 { color: #333; }")
        html.append(".gain { color: green; }")
        html.append(".loss { color: red; }")
        html.append("code { background-color: #f4f4f4; padding: 2px 4px; border-radius: 4px; }")
        # Print/PDF pagination: start each major section on its own page, keep the
        # first section with the title page, and avoid splitting rows or orphaning
        # a heading at the bottom of a page.
        html.append("h2 { page-break-before: always; }")
        html.append("h2:first-of-type { page-break-before: avoid; }")
        html.append("h2, h3 { page-break-after: avoid; }")
        html.append("table { page-break-inside: auto; }")
        html.append("tr, li { page-break-inside: avoid; }")
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
            if multi_broker:
                html.append(
                    "<li><strong style='color: #cc6600;'>SCOPE - Multiple Accounts</strong>: "
                    f"This report combines data from <strong>{', '.join(brokers)}</strong>, so FIFO and the "
                    "2-month wash-sale rule are applied across them together. If you also hold the same "
                    "security at <strong>yet another</strong> broker/account not imported here, repurchases "
                    "there within the 2-month window are NOT detected and some blocked losses may be "
                    "under-reported.</li>"
                )
            else:
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
            if multi_broker:
                html.append(
                    f"<li><strong>Multi-Broker Scope</strong>: Transactions from more than one "
                    f"broker were combined ({', '.join(brokers)}). Each ledger row is tagged with its "
                    "originating <strong>Broker</strong>, and FIFO matching plus the 2-month wash-sale "
                    "rule are applied across all of them — as Spain requires for homogeneous securities "
                    "(same ISIN). A per-broker realized gain/loss breakdown is shown below.</li>"
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
            if multi_broker:
                html.append(
                    "<li><strong style='color: #cc6600;'>ALCANCE - Varias Cuentas</strong>: "
                    f"Este informe combina datos de <strong>{', '.join(brokers)}</strong>, por lo que el FIFO "
                    "y la regla de los 2 meses se aplican entre ellos en conjunto. Si además tienes el mismo "
                    "valor en <strong>otra</strong> cuenta/bróker no importada aquí, las recompras allí dentro "
                    "de la ventana de 2 meses NO se detectan y algunas pérdidas bloqueadas podrían estar "
                    "infravaloradas.</li>"
                )
            else:
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
            if multi_broker:
                html.append(
                    f"<li><strong>Ámbito Multi-Bróker</strong>: Se combinaron operaciones de más de un "
                    f"bróker ({', '.join(brokers)}). Cada fila del libro indica su <strong>Bróker</strong> "
                    "de origen, y la casación FIFO junto con la regla de los 2 meses se aplican entre todos "
                    "ellos — como exige España para los valores homogéneos (mismo ISIN). Más abajo se "
                    "muestra un desglose de ganancias/pérdidas realizadas por bróker.</li>"
                )
            html.append("</ul>")

        # Portfolio rollup (per-security gains/losses/net) — multi-security mode only.
        if securities is not None:
            self._append_portfolio_summary(html, is_es, securities)

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

        # Per-broker realized G/L subtotal (only when more than one broker is present).
        if multi_broker:
            self._append_broker_breakdown(html, is_es)

        # Loss handling: the two-bucket savings ledger supersedes the single-bucket
        # carryforward ledger when dividend/interest is supplied (the cross-offset
        # changes what carries forward, so showing both would contradict).
        if savings_income:
            self._append_savings_section(html, is_es, savings_income, opening_losses)
        else:
            self._append_carryforward_section(html, is_es, opening_losses)

        # Per-disposal capital-gains detail formatted for the Modelo 100 entry.
        self._append_transmisiones_table(html, is_es)

        # Modelo 100 box guide (apartado names are stable; casillas shift yearly)
        self._append_modelo100_guide(html, is_es)

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




        # Transaction ledger + per-sale FIFO calculation details. In portfolio
        # mode these are emitted once per security (each starting on its own page);
        # otherwise once for the whole engine, exactly as before.
        if securities is not None:
            self._append_per_security_ledgers(html, is_es, securities)
        else:
            ledger_title = (
                "Detailed Transaction Ledger" if not is_es else "Libro de Transacciones Detallado"
            )
            html.append(f"<h2>{ledger_title}</h2>")
            self._append_ledger_table(html, is_es, self.processed_events)
            details_title = (
                "Calculation Details for Sales" if not is_es else "Detalles de Cálculo para Ventas"
            )
            html.append(f"<h2>{details_title}</h2>")
            self._append_calc_details(html, is_es, self.processed_events)

        html.append("</body></html>")
        return "".join(html)

    def generate_pdf_report(self, filepath: str, lang: str = "en", espp_discounts: dict[int, Decimal] | None = None, espp_early_sale_discounts: dict[int, Decimal] | None = None, opening_losses: dict[int, Decimal] | None = None, savings_income: dict[int, SavingsIncomeYear] | None = None, securities: "list[SecurityResult] | None" = None) -> None:
        """Generate a PDF tax report using Playwright (supports lang='en' or lang='es')."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("Error: Playwright is not installed. Cannot generate PDF.")
            print("Please install it with: pip install playwright && playwright install")
            return

        html_content = self.generate_html_content(lang=lang, espp_discounts=espp_discounts, espp_early_sale_discounts=espp_early_sale_discounts, opening_losses=opening_losses, savings_income=savings_income, securities=securities)

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
