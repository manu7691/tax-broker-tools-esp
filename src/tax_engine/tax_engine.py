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

from .dates import add_months
from .models import (
    CarryforwardLedger,
    CarryforwardYear,
    ClosedYearDrift,
    DeferredWashSaleLoss,
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


def _to_cents(value: Decimal | str | float) -> Decimal:
    """Round a monetary figure to cents, the unit a tax return is filed in."""
    return Decimal(str(value)).quantize(Decimal("0.01"), ROUND_HALF_UP)


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

    #: How a deferred wash-sale loss stops being deferred.
    #:
    #: ``"definitive"`` (default) implements the statutory rule as the DGT reads it: the loss
    #: is integrated "a medida que se transmitan los valores que permanezcan en el
    #: patrimonio" (Art. 33.5, final paragraph) — progressively, no full
    #: liquidation required — but only for transmissions that are themselves
    #: DEFINITIVE, i.e. followed by no acquisition of homogeneous securities within
    #: two months (DGT V3282-18, V0046-20, V1119-21). This is the recommended
    #: setting; the other two bracket it.
    #:
    #: ``"position_zero"`` follows the stricter reading: the loss only
    #: becomes deductible in the tax year when the whole position in the security
    #: reaches 0,00 shares AND a 2-month quarantine elapses with no new
    #: acquisitions of homogeneous securities (RSU vestings and sell-to-cover
    #: included). ``"per_lot"`` follows the literal DGT wording — the loss is freed
    #: "a medida que se transmitan los valores que permanezcan en el patrimonio",
    #: lot by lot — and is kept so both readings can be compared.
    RELEASE_POLICIES = ("definitive", "position_zero", "per_lot")

    def __init__(self, release_policy: str = "definitive") -> None:
        if release_policy not in self.RELEASE_POLICIES:
            raise ValueError(
                f"Unknown release_policy {release_policy!r}; "
                f"expected one of {self.RELEASE_POLICIES}"
            )
        self.release_policy = release_policy
        # Tax years already filed, as declared: ``{year: {"net_gain_loss": ...}}``.
        # Taxpayer state, not something derivable from the transactions, so it
        # deliberately survives :meth:`reset` and every reprocessing of events.
        self.closed_years: dict[int, dict[str, Decimal]] = {}
        self.state = TaxEngineState()
        self.processed_events: list[ProcessedEvent] = []
        # Every lot ever acquired, in chronological order. ``state.lots`` is the
        # live portfolio and gets emptied on a full liquidation; this ledger is
        # the durable record the wash-sale rule needs, because a lot that has
        # been sold is exactly the one that releases a deferred loss.
        self.lot_ledger: list[ShareLot] = []
        self.yearly_summaries: dict[int, YearlyTaxSummary] = defaultdict(
            lambda: YearlyTaxSummary(year=0)
        )

    def reset(self) -> None:
        """Reset the engine to initial state."""
        self.state = TaxEngineState()
        self.processed_events = []
        self.lot_ledger = []
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

        # Art. 35 LIRPF: "gastos y tributos inherentes a la adquisición" form part
        # of the valor de adquisición. They are capitalised onto the lot rather
        # than expensed, so they reduce the gain of whichever FUTURE sale consumes
        # these shares, in proportion to the fraction consumed.
        acquisition_fees_eur = Decimal("0")
        if event.fees_usd > 0:
            acquisition_fees_eur = (event.fees_usd * event.resolved_fx_rate).quantize(
                Decimal("0.0001"), ROUND_HALF_UP
            )
        new_cost_eur = event.total_value_eur + acquisition_fees_eur

        # Add a new lot
        new_lot = ShareLot(
            acquisition_date=event.event_date,
            shares=shares,
            price_eur=price_eur,
            remaining_shares=shares,
            notes=event.notes,
            broker=event.broker,
            isin=event.isin,
            origin=event.origin,
            fees_eur=acquisition_fees_eur,
            fx_rate=event.resolved_fx_rate,
            espp_fmv_usd=event.espp_fmv_usd,
            espp_price_usd=event.espp_price_usd,
        )
        self.state.lots.append(new_lot)
        self.lot_ledger.append(new_lot)

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
            # The lot remembers when it was consumed. This is the trigger that
            # later releases any wash-sale loss deferred onto it.
            lot.disposals.append((event.event_date, shares_from_lot))

            # Cost basis of this fraction, acquisition costs included (Art. 35).
            lot_basis = lot.cost_basis_for(shares_from_lot)
            match_gain_loss = (sell_price_eur * shares_from_lot - lot_basis).quantize(
                Decimal("0.0001"), ROUND_HALF_UP
            )
            total_realized_gain_loss += match_gain_loss
            total_cost_basis_removed += lot_basis

            fifo_matches.append(
                FifoMatch(
                    acquisition_date=lot.acquisition_date,
                    acquisition_price_eur=lot.price_eur,
                    shares=shares_from_lot,
                    realized_gain_loss=match_gain_loss,
                    notes=lot.notes,
                    origin=lot.origin,
                    acquisition_fx_rate=lot.fx_rate,
                    espp_fmv_usd=lot.espp_fmv_usd,
                    espp_price_usd=lot.espp_price_usd,
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

    def _summary_for(self, year: int) -> YearlyTaxSummary:
        """Get (creating if needed) the summary for ``year``.

        ``yearly_summaries`` is a defaultdict whose factory cannot know the key,
        so a freshly created entry carries ``year=0``; stamp the real year on it.
        """
        summary = self.yearly_summaries[year]
        if summary.year != year:
            summary.year = year
        return summary

    def detect_blocked_losses_spain(self) -> None:
        """
        Apply the Spanish 2-month wash sale rule (Art. 33.5.f LIRPF), lot by lot.

        Runs three separate phases, in order:

        1. ``_defer_losses_onto_replacement_lots`` — a loss sale that leaves the
           taxpayer holding homogeneous shares bought within 2 months parks the
           deferred loss ON THOSE LOTS.
        2. ``_release_deferred_losses`` — when FIFO later consumes such a lot, the
           loss parked on it is freed, dated to that disposal.
        3. ``_apply_wash_sale_to_summaries`` — yearly figures are *derived* by
           reading the lots. No year is ever mutated from another year's data.

        Deferral must be a pass of its own because Art. 33.5.f also counts
        repurchases in the two months AFTER the sale: at the instant of the loss
        sale, the blocking purchase may not have happened yet. What the pass never
        does is look into the future beyond that window — each deferral is fixed
        from the sale date and the replacement lot's own acquisition, both past
        events, so a closed year cannot be rewritten by anything that follows.
        """
        self._defer_losses_onto_replacement_lots()
        self._release_deferred_losses()
        self._apply_wash_sale_to_summaries()

    @staticmethod
    def _add_months(d: date, months: int) -> date:
        """Shift a date by whole months, clamping to the end of short months."""
        return add_months(d, months)

    def _defer_losses_onto_replacement_lots(self) -> None:
        """Phase 1 — park each blocked loss on the replacement lot that caused it."""
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
            start_window = self._add_months(sale_date, -2)
            end_window = self._add_months(sale_date, 2)

            targets: list[tuple[ShareLot, date, Decimal]] = []
            blocked_shares = Decimal("0")
            remaining_to_block = pe.event.shares

            for lot in self.lot_ledger:  # chronological
                if remaining_to_block <= 0:
                    break
                if not (start_window <= lot.acquisition_date <= end_window):
                    continue
                # The deferral attaches the moment both facts coexist: the loss
                # sale and the replacement holding. Measuring at the later of the
                # two dates keeps the figure built only from past events — the
                # end of the window lies in the future and would let a later sale
                # rewrite a year already reported.
                anchor = max(lot.acquisition_date, sale_date)
                # Shares of this lot that survive the sale itself, minus those
                # already pledged to an earlier loss sale: one replacement share
                # can neutralize at most one sold share, in total.
                pledged = sum((c.shares for c in lot.deferred_claims), Decimal("0"))
                available = lot.shares_held_at(anchor) - pledged
                if available <= 0:
                    continue
                used = min(available, remaining_to_block)
                targets.append((lot, anchor, used))
                blocked_shares += used
                remaining_to_block -= used

            if blocked_shares <= 0:
                continue

            blocked_loss = (pe.realized_gain_loss / pe.event.shares * blocked_shares).quantize(
                Decimal("0.0001"), ROUND_HALF_UP
            )
            pe.event.notes += f" [Wash Sale Blocked Loss: €{abs(blocked_loss):,.2f}]"

            unassigned = blocked_loss
            for position, (lot, anchor, shares) in enumerate(targets):
                if position == len(targets) - 1:
                    amount = unassigned  # last lot absorbs the rounding residual
                else:
                    amount = (blocked_loss * shares / blocked_shares).quantize(
                        Decimal("0.0001"), ROUND_HALF_UP
                    )
                unassigned -= amount
                lot.deferred_claims.append(
                    DeferredWashSaleLoss(
                        origin_year=sale_date.year,
                        anchor_date=anchor,
                        shares=shares,
                        amount=amount,
                    )
                )

    def _release_deferred_losses(self) -> None:
        """Phase 2 — free deferred losses according to the configured policy."""
        if self.release_policy == "definitive":
            self._release_per_lot(require_definitive=True)
        elif self.release_policy == "position_zero":
            self._release_on_zero_position()
        else:
            self._release_per_lot()

    def _zero_position_dates(self) -> list[date]:
        """Dates at whose close the position in this security was exactly 0,00.

        Read off the running position the ledger already tracks, taking the LAST
        event of each date so an intraday sell-then-buy is not mistaken for a
        liquidation.
        """
        position_by_date: dict[date, Decimal] = {}
        for pe in self.processed_events:
            position_by_date[pe.event.event_date] = pe.total_shares_after
        return [day for day in sorted(position_by_date) if position_by_date[day] == 0]

    def _release_on_zero_position(self) -> None:
        """Phase 2 (default) — unlock only on a clean, fully quarantined exit.

        A deferred loss becomes deductible when BOTH conditions hold:

        1. the whole position in the security reaches 0,00 shares, and
        2. the following 2 months pass with no acquisition of homogeneous
           securities — RSU vestings and sell-to-cover included, since those are
           acquisitions like any other.

        A purchase inside the quarantine voids that exit entirely; the engine then
        waits for the next zero crossing. The release is dated to the END of the
        quarantine, which is the moment the loss is finally free, so an exit in
        November unlocks in the same year but one in December unlocks in the next.
        """
        claims = [claim for lot in self.lot_ledger for claim in lot.deferred_claims]
        if not claims:
            return

        acquisition_dates = [lot.acquisition_date for lot in self.lot_ledger]

        for zero_date in self._zero_position_dates():
            quarantine_end = add_months(zero_date, 2)
            if any(zero_date < when <= quarantine_end for when in acquisition_dates):
                continue  # repurchased inside the quarantine — this exit does not count

            for claim in claims:
                # A deferral cannot be released before it attached.
                if claim.anchor_date > zero_date:
                    continue
                outstanding = claim.outstanding
                if outstanding == 0:
                    continue
                claim.released += outstanding
                claim.releases.append((quarantine_end, outstanding))

    def _is_definitive(self, sell_date: date) -> bool:
        """Whether a transmission on ``sell_date`` is definitive (Art. 33.5 doctrine).

        It is not, if homogeneous securities are acquired within the two months
        that follow it — an RSU vesting or a sell-to-cover replacement counts like
        any other purchase. A non-definitive transmission frees nothing: the same
        anti-avoidance logic that deferred the loss applies again.
        """
        window_end = add_months(sell_date, 2)
        return not any(sell_date < lot.acquisition_date <= window_end for lot in self.lot_ledger)

    def _replacement_lots_after(self, sell_date: date) -> list[ShareLot]:
        """Lots acquired in the two months after ``sell_date`` (the new replacements)."""
        window_end = add_months(sell_date, 2)
        return [lot for lot in self.lot_ledger if sell_date < lot.acquisition_date <= window_end]

    def _roll_claim(
        self, claim: DeferredWashSaleLoss, sell_date: date, shares: Decimal, amount: Decimal
    ) -> None:
        """Carry a deferral over to the shares that replaced the ones just sold.

        When the replacement securities are transmitted but the transmission is not
        definitive, the loss is not integrated — and it is not lost either. The
        newly acquired homogeneous securities take over as the replacement holding,
        so the deferral is re-parked on them and waits for a clean transmission.
        Without this the deferral would silently evaporate and the taxpayer would
        lose a deduction they are entitled to.
        """
        targets = self._replacement_lots_after(sell_date)
        if not targets:
            return
        available = sum((lot.shares for lot in targets), Decimal("0"))
        assigned = min(shares, available)
        if assigned <= 0:
            return

        unassigned = (amount * assigned / shares).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        remaining_shares = assigned
        for position, lot in enumerate(targets):
            if remaining_shares <= 0:
                break
            take = min(lot.shares, remaining_shares)
            remaining_shares -= take
            if position == len(targets) - 1 or remaining_shares <= 0:
                portion = unassigned
            else:
                portion = (amount * take / shares).quantize(Decimal("0.0001"), ROUND_HALF_UP)
            unassigned -= portion
            lot.deferred_claims.append(
                DeferredWashSaleLoss(
                    origin_year=claim.origin_year,
                    anchor_date=sell_date,
                    shares=take,
                    amount=portion,
                    is_rollover=True,
                )
            )

    def _release_per_lot(self, require_definitive: bool = False) -> None:
        """Phase 2 — selling a lot frees the loss deferred onto it, on that date.

        With ``require_definitive`` a slice is only integrated when its transmission
        passes :meth:`_is_definitive`; otherwise the slice rolls onto whatever
        homogeneous securities replaced it (see :meth:`_roll_claim`) and the pass is
        repeated, so a deferral survives any number of non-definitive round trips
        until a clean transmission finally frees it.
        """
        if require_definitive:
            # Rolling appends new claims, so iterate until the ledger stops growing.
            # Each pass can only move deferrals forward in time, and every lot is a
            # finite sink, so this terminates.
            # Rolling appends successor claims, so sweep until none are added. Each
            # claim is settled exactly once (``seen``); without that, a later sweep
            # would release an already-settled claim a second time.
            seen: set[int] = set()
            for _ in range(len(self.lot_ledger) + 1):
                before = sum(len(lot.deferred_claims) for lot in self.lot_ledger)
                self._release_pass(require_definitive=True, seen=seen)
                if sum(len(lot.deferred_claims) for lot in self.lot_ledger) == before:
                    return
            return
        self._release_pass(require_definitive=False, seen=set())

    def _release_pass(self, require_definitive: bool, seen: set[int]) -> None:
        """One sweep of the lot ledger, settling each claim not yet processed."""
        capacity: dict[date, Decimal] = {}
        for lot in list(self.lot_ledger):
            if not lot.deferred_claims:
                continue

            index = 0
            used_from_current = Decimal("0")

            # Claims were appended in sale order, so their anchors ascend.
            for claim in list(lot.deferred_claims):
                if id(claim) in seen:
                    continue
                seen.add(id(claim))
                # Disposals up to the anchor never acted as replacement shares
                # (``shares_held_at`` already netted them out), so they release
                # nothing — and, anchors ascending, never will for later claims.
                while index < len(lot.disposals) and lot.disposals[index][0] <= claim.anchor_date:
                    index += 1
                    used_from_current = Decimal("0")

                shares_left = claim.shares
                amount_left = claim.amount

                while shares_left > 0 and index < len(lot.disposals):
                    sell_date, disposed = lot.disposals[index]
                    take = min(disposed - used_from_current, shares_left)
                    if take <= 0:
                        index += 1
                        used_from_current = Decimal("0")
                        continue

                    used_from_current += take
                    shares_left -= take
                    if shares_left <= 0:
                        portion = amount_left  # last slice absorbs the residual
                    else:
                        portion = (claim.amount * take / claim.shares).quantize(
                            Decimal("0.0001"), ROUND_HALF_UP
                        )
                    amount_left -= portion
                    claim.released += portion
                    if not require_definitive:
                        claim.releases.append((sell_date, portion))
                        continue

                    # A transmission is definitive only to the extent the shares were
                    # NOT replaced within the following two months. Selling 50 and
                    # buying back 10 integrates 40 shares' worth of the deferral and
                    # rolls 10 — the replacement capacity is consumed as it is used,
                    # so one repurchased share can block only one transmitted share.
                    if sell_date not in capacity:
                        capacity[sell_date] = sum(
                            (lot.shares for lot in self._replacement_lots_after(sell_date)),
                            Decimal("0"),
                        )
                    replaced = min(take, capacity[sell_date])
                    capacity[sell_date] -= replaced
                    freed = take - replaced

                    if freed > 0:
                        freed_amount = (
                            portion
                            if replaced <= 0
                            else (portion * freed / take).quantize(Decimal("0.0001"), ROUND_HALF_UP)
                        )
                        claim.releases.append((sell_date, freed_amount))
                    else:
                        freed_amount = Decimal("0")
                    if replaced > 0:
                        rolled_amount = portion - freed_amount
                        claim.rolled += rolled_amount
                        self._roll_claim(claim, sell_date, replaced, rolled_amount)

                    if used_from_current >= disposed:
                        index += 1
                        used_from_current = Decimal("0")

    def _apply_wash_sale_to_summaries(self) -> None:
        """Phase 3 — derive the yearly figures by reading the lots, lot by lot.

        Nothing here consults another year's totals, so no year can be rewritten
        from a later one. Each year gets exactly two things:

        * ``blocked_losses`` — deferrals originated that year and STILL pending at
          31 December. A deferral released within its own year never had a pending
          balance, so it is plain deductible loss and shows up nowhere here. This
          is what guarantees a fully liquidated position reports €0.00 blocked.
        * ``unlocked_historical_losses`` — deferrals from EARLIER years released
          during this one.
        """
        for lot in self.lot_ledger:
            for claim in lot.deferred_claims:
                origin = claim.origin_year

                released_same_year = sum(
                    (amount for when, amount in claim.releases if when.year == origin),
                    Decimal("0"),
                )
                pending_at_year_end = claim.amount - released_same_year
                if claim.is_rollover:
                    pending_at_year_end = Decimal("0")  # predecessor already reported it
                if pending_at_year_end:
                    self._summary_for(origin).blocked_losses += pending_at_year_end

                for when, amount in claim.releases:
                    if when.year == origin:
                        continue  # already netted out of the origin year above
                    summary = self._summary_for(when.year)
                    summary.unlocked_historical_losses += amount
                    summary.unlocked_losses_by_origin[origin] = (
                        summary.unlocked_losses_by_origin.get(origin, Decimal("0")) + amount
                    )

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
            summary = self.yearly_summaries[year]
            summary.total_fees_eur += fees_eur
            # Which side of the trade the fee belongs to decides where it is
            # deducted: an acquisition fee is already capitalised into the lot's
            # cost basis (so it bites on a later sale), a disposal fee reduced this
            # sale's result directly.
            if event.event_type == EventType.SELL:
                summary.disposal_fees_eur += fees_eur
            else:
                summary.acquisition_fees_eur += fees_eur

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

    def _frozen_net(
        self,
        year: int,
        summary: YearlyTaxSummary,
        closed_years: dict[int, dict[str, Decimal]] | None,
    ) -> Decimal:
        """This year's net result for ledger purposes, pinned if the year is closed.

        Reporting a drift is not enough on its own: until the taxpayer files a
        rectificativa, the result they DECLARED is the one that governs what they
        may carry forward. Recomputing it away would silently destroy a pending
        loss they are still entitled to. So the ledgers read the declared figure
        for a closed year while ``yearly_summaries`` keeps reporting the
        recomputed truth (and :meth:`check_closed_years` keeps flagging the gap).

        Falls back to :attr:`closed_years` when no explicit mapping is given, so
        the CLI can set the filing state once and every view inherits it. Passing
        an empty dict explicitly disables the freeze for that call.
        """
        if closed_years is None:
            closed_years = self.closed_years
        declared = closed_years.get(year, {})
        if "net_gain_loss" in declared:
            return Decimal(str(declared["net_gain_loss"]))
        return summary.net_gain_loss

    def releases_already_deducted(self) -> dict[int, Decimal]:
        """Deferrals a closed year appears to have deducted when it was filed.

        For each year declared in :attr:`closed_years`, the amount by which the
        blocked loss the engine now computes exceeds the blocked loss actually
        reported. That excess was, in practice, taken as deductible back then — so
        integrating its later release would compute the same loss twice.

        **This is a detection, not a rule.** Art. 122.2 LGT settles an error in a
        non-prescribed year by regularising THAT year, not by netting it against a
        later one, and the administration has treated silent forward netting of
        improper negative bases as sanctionable. The normal remedy is therefore a
        complementaria for the affected year, after which the release is legitimate
        and needs no adjustment here. :meth:`apply_closed_year_forfeits` exists for
        the taxpayer who, with their advisor, decides not to regularise; it is never
        applied automatically. Read-only.
        """
        pending: dict[int, Decimal] = {}
        for year, declared in self.closed_years.items():
            if "blocked_losses" not in declared:
                continue
            summary = self.yearly_summaries.get(year)
            if summary is None:
                continue
            computed = abs(_to_cents(summary.blocked_losses))
            as_filed = abs(_to_cents(declared["blocked_losses"]))
            if computed > as_filed:
                pending[year] = computed - as_filed
        return pending

    def apply_closed_year_forfeits(self) -> dict[int, Decimal]:
        """Drop the releases identified by :meth:`releases_already_deducted`.

        **Opt-in, and without direct statutory backing.** It models the choice of
        leaving an erroneous prior year untouched and giving up the corresponding
        future deduction instead. Arithmetically the taxpayer ends up with the same
        total deduction, but the affected return stays uncorrected — which is not
        what Art. 122.2 LGT prescribes. Use it only as a deliberate decision taken
        with an advisor; the default path is to regularise the year.

        Idempotent. Returns the amount forfeited per origin year.
        """
        budget = self.releases_already_deducted()
        if not budget:
            return {}

        forfeited: dict[int, Decimal] = {}
        for summary in sorted(self.yearly_summaries.values(), key=lambda s: s.year):
            for origin in sorted(summary.unlocked_losses_by_origin):
                left = budget.get(origin, Decimal("0"))
                if left <= 0:
                    continue
                amount = summary.unlocked_losses_by_origin[origin]  # negative
                take = min(abs(amount), left)
                budget[origin] = left - take
                forfeited[origin] = forfeited.get(origin, Decimal("0")) + take
                summary.unlocked_historical_losses += take
                remaining = amount + take
                if remaining == 0:
                    del summary.unlocked_losses_by_origin[origin]
                else:
                    summary.unlocked_losses_by_origin[origin] = remaining
        return forfeited

    def check_closed_years(self, declared: dict[int, dict[str, Decimal]]) -> list[ClosedYearDrift]:
        """Compare already-filed years against what the engine now computes.

        Art. 33.5.f can legitimately move a past year's result — a repurchase in
        January blocks a loss realised the previous December — but Rule 3 forbids
        rewriting a closed year in silence. So the engine never touches those
        summaries; it reports every divergence and leaves the decision (a
        complementaria, a rectificativa, or nothing) to the taxpayer.

        ``declared`` maps a tax year to the figures as filed, e.g.
        ``{2022: {"net_gain_loss": Decimal("-5000")}}``. Any attribute or property
        of :class:`YearlyTaxSummary` can be pinned this way. This method is
        strictly read-only.
        """
        drifts: list[ClosedYearDrift] = []
        for year in sorted(declared):
            summary = self.yearly_summaries.get(year)
            for field_name, declared_value in declared[year].items():
                # A tax return is filed in cents while the engine carries four
                # decimals, so both sides are compared (and reported) at cent
                # precision. Without this every correctly-declared year would
                # raise a sub-cent drift and the alert would become noise.
                declared_value = _to_cents(declared_value)
                computed = (
                    Decimal("0.00") if summary is None else _to_cents(getattr(summary, field_name))
                )
                if computed != declared_value:
                    drifts.append(
                        ClosedYearDrift(
                            year=year,
                            field=field_name,
                            declared=declared_value,
                            computed=computed,
                        )
                    )
        return drifts

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
        closed_years: dict[int, dict[str, Decimal]] | None = None,
    ) -> CarryforwardLedger:
        """
        Simulate the 4-year loss carryforward across the tracked years (Art. 49 LIRPF).

        A net loss generated in year Y can offset net savings-base gains of years
        Y+1 .. Y+4 only. Oldest losses are consumed first. Losses not used within
        that window expire.

        ``closed_years`` pins already-filed years to the result that was declared,
        so a later repurchase cannot silently delete a loss the taxpayer is still
        carrying (see :meth:`_frozen_net`).

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

            net = self._frozen_net(year, summaries[year], closed_years)
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
        closed_years: dict[int, dict[str, Decimal]] | None = None,
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

            gp_net = (
                self._frozen_net(year, summaries[year], closed_years)
                if year in summaries
                else Decimal("0")
            )
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
        crypto_summaries: "dict[int, YearlyTaxSummary] | None" = None,
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
            crypto_summaries=crypto_summaries,
        )
