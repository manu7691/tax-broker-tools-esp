"""
Portfolio runner — one FIFO queue per security, rolled up into one savings base.

Spanish FIFO is **per homogeneous security (per ISIN)** and the taxable result is
the **aggregate** of every security's net gain/loss in the *base del ahorro*. This
module turns the single-security :class:`~tax_engine.tax_engine.TaxEngine` into a
portfolio-wide one:

1. **Group** every event by its security key (ISIN, else ticker — see
   :func:`tax_engine.securities.grouping_key`). Same ISIN from different brokers
   merges into one queue (cross-broker homogeneity); the broker stays metadata.
2. **Run the existing engine unchanged** on each group, so per-security FIFO,
   the 2-month wash-sale rule, splits, and yearly summaries are all computed by
   the audited single-security code path.
3. **Aggregate** the per-security yearly summaries into one combined engine whose
   carryforward (4y) and 25% cross-category offset operate on the **portfolio**
   net — which is correct, since those are not per-security in Spanish law.
   Wash-sale blocked losses, already baked into each per-security summary, simply
   sum; they are never recomputed across securities.

The aggregate is itself a :class:`TaxEngine`, so all existing reporting
(``print_ledger``, ``print_tax_summary``, ``generate_pdf_report``,
``compute_savings_ledger``) works on it untouched.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .models import EventType, ProcessedEvent, StockEvent, YearlyTaxSummary
from .securities import SecuritiesConfig, Security, grouping_key
from .tax_engine import TaxEngine

# Display-ordering priority mirroring TaxEngine._sort_events, so the merged
# cross-security ledger reads chronologically with acquisitions before sells.
_TYPE_PRIORITY = {
    EventType.VEST: 0,
    EventType.BUY: 1,
    EventType.EXERCISE: 1,
    EventType.SELL: 2,
}


class AmbiguousSecurityError(ValueError):
    """Raised when one ticker resolves to two different ISINs.

    Merging them would pool unrelated securities into one FIFO queue; keeping them
    apart would silently split a homogeneous one. Neither is safe to guess, so the
    run stops and asks the user to fix ``input/securities.json``.
    """


def _learn_isins_by_ticker(events: list[StockEvent]) -> dict[str, str]:
    """Map every ticker to its ISIN, using every event that states both.

    Broker exports are uneven: E*TRADE reports no ISIN at all, Revolut does. Rule 1
    requires one global FIFO queue per homogeneous security across all brokers, so
    an ISIN learned from any single event has to apply to every event for that
    ticker — otherwise the same security ends up in two queues with two cost bases.
    """
    learned: dict[str, str] = {}
    for ev in events:
        ticker = (ev.symbol or "").strip().upper()
        isin = (ev.isin or "").strip().upper()
        if not ticker or not isin:
            continue
        previous = learned.setdefault(ticker, isin)
        if previous != isin:
            raise AmbiguousSecurityError(
                f"Ticker {ticker!r} maps to two different ISINs ({previous} and {isin}). "
                f"Spanish FIFO cannot group these safely — fix the ISIN in "
                f"input/securities.json before re-running."
            )
    return learned


@dataclass
class SecurityResult:
    """One security's identity and its fully-processed single-security engine."""

    security: Security
    engine: TaxEngine

    @property
    def net_gain_loss(self) -> Decimal:
        """Net realized gain/loss across all years for this security (EUR)."""
        return sum(
            (s.net_gain_loss for s in self.engine.get_all_yearly_summaries()),
            Decimal("0"),
        )


@dataclass
class PortfolioResult:
    """Result of running the whole portfolio: per-security engines + the rollup."""

    results: list[SecurityResult]
    aggregate: TaxEngine

    @property
    def securities(self) -> list[Security]:
        return [r.security for r in self.results]


def group_events_by_security(
    events: list[StockEvent],
) -> dict[str, tuple[Security, list[StockEvent]]]:
    """Bucket events into ``{key: (Security, events)}``, one bucket per security.

    Events whose ISIN is missing but whose ticker is known from another event are
    backfilled first (Rule 1: one global queue per homogeneous security, across
    every broker and account); a ticker claiming two ISINs raises
    :class:`AmbiguousSecurityError` rather than guessing.

    The key is the ISIN when known, else ``@TICKER`` (see
    :func:`~tax_engine.securities.grouping_key`), so the same ISIN reported by
    different brokers merges into a single queue. Within a bucket the ISIN is
    consistent by construction (it is part of the key); the representative
    :class:`Security` takes the first non-empty ISIN/ticker seen.
    """
    # Backfill ISINs before bucketing, so a security reported with an ISIN by one
    # broker and without one by another still lands in a single FIFO queue.
    learned = _learn_isins_by_ticker(events)
    for ev in events:
        if not ev.isin:
            ticker = (ev.symbol or "").strip().upper()
            if ticker in learned:
                ev.isin = learned[ticker]

    buckets: dict[str, list[StockEvent]] = {}
    isin_by_key: dict[str, str | None] = {}
    ticker_by_key: dict[str, str | None] = {}

    for ev in events:
        key = grouping_key(ev.isin, ev.symbol)
        buckets.setdefault(key, []).append(ev)
        if not isin_by_key.get(key) and ev.isin:
            isin_by_key[key] = ev.isin
        if not ticker_by_key.get(key) and ev.symbol:
            ticker_by_key[key] = ev.symbol

    return {
        key: (
            Security(isin=isin_by_key.get(key), ticker=ticker_by_key.get(key)),
            evs,
        )
        for key, evs in buckets.items()
    }


def _build_aggregate(results: list[SecurityResult]) -> TaxEngine:
    """Fold per-security engines into one combined engine for the savings base.

    Sums each security's yearly summaries (gains, losses, already-computed blocked
    losses, losses unblocked this year, fees) and concatenates their processed
    events, their surviving lots and their full lot ledgers, so
    the carryforward/savings-ledger and all reporting run on the portfolio total.
    The wash-sale detection is deliberately **not** re-run — doing so across
    securities would be wrong, and each per-security summary already carries it.
    """
    aggregate = TaxEngine()
    summaries: dict[int, YearlyTaxSummary] = {}
    processed: list[ProcessedEvent] = []

    for r in results:
        eng = r.engine
        for year, s in eng.yearly_summaries.items():
            tgt = summaries.setdefault(year, YearlyTaxSummary(year=year))
            tgt.total_gains += s.total_gains
            tgt.total_losses += s.total_losses
            tgt.blocked_losses += s.blocked_losses
            tgt.unlocked_historical_losses += s.unlocked_historical_losses
            for origin_year, amount in s.unlocked_losses_by_origin.items():
                tgt.unlocked_losses_by_origin[origin_year] = (
                    tgt.unlocked_losses_by_origin.get(origin_year, Decimal("0")) + amount
                )
            tgt.total_fees_eur += s.total_fees_eur
        processed.extend(eng.processed_events)
        aggregate.state.total_shares += eng.state.total_shares
        aggregate.state.total_portfolio_cost_eur += eng.state.total_portfolio_cost_eur
        aggregate.state.lots.extend(eng.state.lots)
        # The durable record of every lot ever acquired, not just the surviving
        # ones: provenance analysis (ESPP breaches, per-origin holdings) reads it,
        # and a fully liquidated security leaves nothing in ``state.lots``.
        aggregate.lot_ledger.extend(eng.lot_ledger)

    # Plain dict (not the engine's defaultdict): the aggregate is read-only from
    # here on — get_all_yearly_summaries/compute_* only iterate its values.
    aggregate.yearly_summaries = summaries
    aggregate.processed_events = sorted(
        processed, key=lambda pe: (pe.event.event_date, _TYPE_PRIORITY[pe.event.event_type])
    )

    if aggregate.state.total_shares > 0:
        aggregate.state.avg_cost_eur = (
            aggregate.state.total_portfolio_cost_eur / aggregate.state.total_shares
        ).quantize(Decimal("0.0001"), ROUND_HALF_UP)

    return aggregate


def run_portfolio(
    events: list[StockEvent],
    config: SecuritiesConfig | None = None,
    release_policy: str = "definitive",
) -> PortfolioResult:
    """Run a per-security FIFO engine for every security and build the rollup.

    ``release_policy`` is forwarded to every per-security engine, since Art. 33.5.f
    is applied per homogeneous security and all of them must use the same reading.

    ``config`` optionally filters which securities are kept (the ``include``
    allow-list from ``input/securities.json``); with no config, every detected
    security is processed. Securities are returned sorted by display label.
    """
    grouped = group_events_by_security(events)

    results: list[SecurityResult] = []
    for security, group_events in grouped.values():
        if config is not None and not config.is_included(security.isin, security.ticker):
            continue
        engine = TaxEngine(release_policy=release_policy)
        engine.process_all(group_events)
        results.append(SecurityResult(security=security, engine=engine))

    results.sort(key=lambda r: r.security.label)
    return PortfolioResult(results=results, aggregate=_build_aggregate(results))
