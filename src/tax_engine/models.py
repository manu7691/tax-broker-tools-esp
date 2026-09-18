"""
Data models for the Spanish Tax Engine.

Contains all dataclasses and enums used throughout the application.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class EventType(Enum):
    """Types of stock events."""

    VEST = "VEST"  # RSU vesting - treated as acquisition at market price
    BUY = "BUY"  # ESPP purchase
    SELL = "SELL"  # Manual sell or sell-to-cover
    EXERCISE = "EXERCISE"  # Stock option exercise - cost basis = FMV at exercise


class LotOrigin(Enum):
    """Where a lot of shares came from.

    Spanish law taxes the *acquisition* differently depending on provenance, so
    this must survive the FIFO match: an ESPP lot sold before 36 months breaks the
    Art. 42.3.f exemption and generates rendimientos del trabajo, while an RSU or
    a plain market purchase never does. Free-text ``notes`` are for display only —
    they are unreliable as a classifier.
    """

    ESPP = "ESPP"  # Employee Stock Purchase Plan (discounted, Art. 42.3.f)
    RSU = "RSU"  # Restricted Stock Unit vesting
    EXERCISE = "EXERCISE"  # Stock option exercise
    MARKET = "MARKET"  # Ordinary purchase on the market


# Default provenance implied by an event type when the parser does not say.
# BUY stays MARKET on purpose: only a parser that *knows* it read an ESPP
# statement may claim the exemption, never a guess from the event type.
_DEFAULT_ORIGIN = {
    EventType.VEST: LotOrigin.RSU,
    EventType.EXERCISE: LotOrigin.EXERCISE,
    EventType.BUY: LotOrigin.MARKET,
    EventType.SELL: LotOrigin.MARKET,
}


@dataclass
class EsppEarlySaleReport:
    """Art. 42.3.f exemption breaches, kept strictly apart from the savings base.

    Selling ESPP shares before 36 months voids the exemption on the purchase
    discount, which is *rendimiento del trabajo* of the purchase year and needs an
    autoliquidación complementaria. It never nets against capital gains, so it
    travels in its own structure instead of being folded into a yearly summary.
    """

    taxable_by_year: dict[int, Decimal] = field(default_factory=dict)
    details: list[dict] = field(default_factory=list)  # type: ignore[type-arg]
    # ESPP disposals that could not be valued (no FMV/price on the lot). Reported
    # loudly: a missing input must never look like "no breach".
    warnings: list[str] = field(default_factory=list)


@dataclass
class ClosedYearDrift:
    """A filed tax year whose recomputed result no longer matches what was declared.

    Art. 33.5.f can legitimately change a past year (a repurchase in January blocks
    a loss sold the previous December), but the engine must never rewrite a closed
    year in silence: it reports the divergence so the taxpayer can decide between a
    complementaria and a rectificativa.
    """

    year: int
    field: str
    declared: Decimal
    computed: Decimal

    @property
    def declared_net(self) -> Decimal:
        return self.declared

    @property
    def computed_net(self) -> Decimal:
        return self.computed

    @property
    def delta(self) -> Decimal:
        return self.computed - self.declared


@dataclass
class DeferredWashSaleLoss:
    """A loss deferred by Art. 33.5.f, parked on the replacement lot that caused it.

    This is the unit of traceability the Spanish rule actually needs: the deferral
    belongs to the *replacement shares*, not to a calendar year. It is released
    when those shares are transferred, and only then does it become deductible.

    ``anchor_date`` is the moment the deferral attached — the later of the lot's
    acquisition and the loss sale. Both are past dates, so a deferral, once
    recorded, can never be altered by anything that happens later.
    """

    origin_year: int  # year of the loss sale whose loss is deferred
    anchor_date: date
    shares: Decimal  # replacement shares of the lot holding this deferral
    amount: Decimal  # total deferred loss (negative)
    released: Decimal = Decimal("0")  # part already settled: freed or rolled (negative)
    releases: list[tuple[date, Decimal]] = field(default_factory=list)
    # Part that was NOT integrated but carried over to new replacement shares
    # because the transmission was not definitive. It is settled on this claim and
    # pending on the successor claim, so it must not be double-counted as blocked.
    rolled: Decimal = Decimal("0")
    # The (date, amount) schedule behind ``rolled``, mirroring ``releases``.
    # ``released`` is freed + rolled; without dating both halves the balance still
    # deferred at a past date cannot be reconstructed once a rollover has happened.
    rollovers: list[tuple[date, Decimal]] = field(default_factory=list)
    # True when this claim is itself the successor of a deferral that rolled over.
    # Its predecessor already reported the amount as blocked in the origin year, so
    # a successor must never add to that figure again.
    is_rollover: bool = False

    @property
    def outstanding(self) -> Decimal:
        """Deferred loss still locked up (negative; zero once fully released)."""
        return self.amount - self.released


@dataclass
class ShareLot:
    """Represents a lot of acquired shares for FIFO tracking (Spain)."""

    acquisition_date: date
    shares: Decimal
    price_eur: Decimal
    remaining_shares: Decimal
    notes: str = ""
    # Provenance carried from the originating StockEvent so surviving holdings can
    # be attributed per-broker and per-security in reporting/charts. Defaults keep
    # single-security/E*TRADE callers working unchanged.
    broker: str = "E*TRADE"
    isin: str | None = None
    # Typed provenance (Rule 4). Carried from the originating StockEvent and copied
    # onto every FifoMatch, so an ESPP disposal stays identifiable after matching.
    origin: "LotOrigin" = None  # type: ignore[assignment]
    # Acquisition costs (commission, fees) for the WHOLE lot. Art. 35 LIRPF makes
    # these part of the valor de adquisición, so they must raise the cost basis
    # proportionally as the lot is consumed.
    fees_eur: Decimal = Decimal("0")
    # FX rate used to price this acquisition, kept so downstream analysis converts
    # with the lot's own rate instead of re-fetching (and possibly re-deriving) it.
    fx_rate: Decimal | None = None
    # ESPP discount data for this specific lot (FMV and price actually paid, in the
    # native currency). Per-lot rather than per-date, so two ESPP purchases on the
    # same day no longer collapse into one.
    espp_fmv_usd: Decimal | None = None
    espp_price_usd: Decimal | None = None
    # Wash-sale state carried BY THE LOT. ``deferred_claims`` holds the losses this
    # lot blocked (a lot can block more than one sale, and from more than one year);
    # ``disposals`` is the (date, shares) schedule of how FIFO consumed the lot,
    # which is what releases those deferrals.
    deferred_claims: list[DeferredWashSaleLoss] = field(default_factory=list)
    disposals: list[tuple[date, Decimal]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.origin is None:
            object.__setattr__(self, "origin", LotOrigin.MARKET)

    def cost_basis_for(self, shares: Decimal) -> Decimal:
        """Valor de adquisición of ``shares`` of this lot, acquisition costs included."""
        basis = self.price_eur * shares
        if self.fees_eur and self.shares:
            basis += self.fees_eur * shares / self.shares
        return basis.quantize(Decimal("0.0001"), ROUND_HALF_UP)

    @property
    def deferred_wash_sale_loss(self) -> Decimal:
        """Deferred loss still parked on this lot (negative; 0 when fully released)."""
        return sum((claim.outstanding for claim in self.deferred_claims), Decimal("0"))

    def shares_held_at(self, on: date) -> Decimal:
        """Shares of this lot still in the portfolio at the close of ``on``."""
        if self.acquisition_date > on:
            return Decimal("0")
        disposed = sum((sh for when, sh in self.disposals if when <= on), Decimal("0"))
        return self.shares - disposed


@dataclass
class FifoMatch:
    """Represents a match of sold shares to an acquisition lot under FIFO."""

    acquisition_date: date
    acquisition_price_eur: Decimal
    shares: Decimal
    realized_gain_loss: Decimal
    notes: str = ""
    # Provenance and per-lot ESPP data carried through the match, so Art. 42.3.f
    # analysis never has to guess from ``notes`` or look a date up in a side map.
    origin: "LotOrigin" = None  # type: ignore[assignment]
    acquisition_fx_rate: Decimal | None = None
    espp_fmv_usd: Decimal | None = None
    espp_price_usd: Decimal | None = None

    def __post_init__(self) -> None:
        if self.origin is None:
            object.__setattr__(self, "origin", LotOrigin.MARKET)


@dataclass
class StockEvent:
    """
    Represents a single stock event (vest, buy, or sell).

    Attributes:
        event_date: The date of the event
        event_type: VEST, BUY, or SELL
        shares: Number of shares (positive for buys/vests, positive for sells too)
        price_usd: Price per share in USD
        fx_rate: USD to EUR exchange rate on that day (optional - will be fetched from ECB if None)
        notes: Optional notes for the transaction
    """

    event_date: date
    event_type: EventType
    shares: Decimal
    price_usd: Decimal
    fx_rate: Decimal | None = None
    # Currency of ``price_usd``/``fees_usd`` (the field name is historical — it
    # holds the price in ``currency``, not necessarily USD). Defaults to USD so
    # existing callers are unchanged; ``resolved_fx_rate`` converts it to EUR via
    # the ECB reference rate for this currency.
    currency: str = "USD"
    fees_usd: Decimal = Decimal("0")
    shares_sold_to_cover: Decimal = Decimal("0")  # For VEST events
    notes: str = ""
    # Originating broker/account for this transaction. Defaults to E*TRADE (the
    # primary source); the optional Revolut importer sets "Revolut". Used to tag
    # rows and produce per-broker subtotals in the report.
    broker: str = "E*TRADE"
    # Canonical security identity. ``symbol`` is the display ticker; ``isin`` is the
    # FIFO grouping key when known (Spanish FIFO is per homogeneous security = per
    # ISIN). Both default empty/None so single-security callers need not set them;
    # the portfolio runner groups events by ISIN (falling back to symbol).
    symbol: str = ""
    isin: str | None = None
    # Raw E-Trade order status for SELL events: "Settled", "Executed", "Open", etc.
    # Empty when unknown (e.g. VEST/BUY events, or orders.xlsx downloaded before
    # the Status column existed). Used to tell a settled sale from one whose RSU
    # confirmation is not yet available (see auto_detect_sell_to_cover).
    order_status: str = ""
    # Typed provenance (Rule 4). ``None`` means "the parser did not say", and
    # ``__post_init__`` derives it from the event type. Only a parser that actually
    # read an ESPP statement sets ``LotOrigin.ESPP``.
    origin: "LotOrigin" = None  # type: ignore[assignment]
    # ESPP discount data in ``currency``: fair market value at purchase and the
    # price actually paid. Attached to the event (hence to the lot) so Art. 42.3.f
    # analysis never needs a by-date side lookup.
    espp_fmv_usd: Decimal | None = None
    espp_price_usd: Decimal | None = None
    _fx_rate_resolved: Decimal | None = field(default=None, init=False, repr=False)

    @property
    def resolved_fx_rate(self) -> Decimal:
        """Get the FX rate, fetching from ECB if not provided."""
        if self._fx_rate_resolved is not None:
            return self._fx_rate_resolved

        if self.fx_rate is not None:
            self._fx_rate_resolved = self.fx_rate
        else:
            # Import here to avoid circular dependency
            from .ecb_rates import ECBRateFetcher

            self._fx_rate_resolved = ECBRateFetcher.get_rate(self.event_date, self.currency)

        return self._fx_rate_resolved

    @property
    def price_eur(self) -> Decimal:
        """Calculate the price per share in EUR."""
        return (self.price_usd * self.resolved_fx_rate).quantize(Decimal("0.0001"), ROUND_HALF_UP)

    @property
    def total_value_eur(self) -> Decimal:
        """Calculate total transaction value in EUR."""
        return (self.shares * self.price_eur).quantize(Decimal("0.0001"), ROUND_HALF_UP)

    def __post_init__(self) -> None:
        """Convert numeric fields to Decimal if needed and validate."""
        object.__setattr__(self, "currency", (self.currency or "USD").strip().upper())
        if self.origin is None:
            object.__setattr__(self, "origin", _DEFAULT_ORIGIN[self.event_type])
        for money in ("espp_fmv_usd", "espp_price_usd"):
            value = getattr(self, money)
            if value is not None and not isinstance(value, Decimal):
                object.__setattr__(self, money, Decimal(str(value)))
        if not isinstance(self.shares, Decimal):
            object.__setattr__(self, "shares", Decimal(str(self.shares)))
        if not isinstance(self.price_usd, Decimal):
            object.__setattr__(self, "price_usd", Decimal(str(self.price_usd)))
        if self.fx_rate is not None and not isinstance(self.fx_rate, Decimal):
            object.__setattr__(self, "fx_rate", Decimal(str(self.fx_rate)))
        if not isinstance(self.fees_usd, Decimal):
            object.__setattr__(self, "fees_usd", Decimal(str(self.fees_usd)))
        if not isinstance(self.shares_sold_to_cover, Decimal):
            object.__setattr__(
                self, "shares_sold_to_cover", Decimal(str(self.shares_sold_to_cover))
            )

        # Validate: shares and price must be positive
        if self.shares <= 0:
            raise ValueError(f"Shares must be positive, got {self.shares}")
        if self.price_usd <= 0:
            raise ValueError(f"Price must be positive, got {self.price_usd}")
        if self.fx_rate is not None and self.fx_rate <= 0:
            raise ValueError(f"FX rate must be positive, got {self.fx_rate}")


@dataclass
class ProcessedEvent:
    """
    Result of processing a stock event through the tax engine.

    Contains the original event plus calculated values.
    """

    event: StockEvent
    total_shares_after: Decimal
    avg_cost_eur_after: Decimal
    realized_gain_loss: Decimal = Decimal("0")
    cost_change_eur: Decimal = Decimal("0")
    total_portfolio_cost_eur: Decimal = Decimal("0")
    fifo_matches: list[FifoMatch] = field(default_factory=list)


@dataclass
class YearlyTaxSummary:
    """Tax summary for a single year under Spanish law."""

    year: int
    total_gains: Decimal = Decimal("0")
    total_losses: Decimal = Decimal("0")
    # Losses realized THIS year that Art. 33.5.f still defers AT 31 DECEMBER
    # (negative) — the balance actually pending, not the gross amount ever
    # deferred. A loss deferred and released within the same year never had a
    # pending balance at year end, so it does not appear here; it is simply
    # deductible. Once written this figure is final: DGT V1547-16 and V1035-18
    # forbid going back to the year of origin when the block later breaks.
    blocked_losses: Decimal = Decimal("0")
    # Losses deferred in EARLIER years whose block broke during THIS year, because
    # the replacement shares were finally disposed of (negative). This is where the
    # deferred loss becomes deductible. Same-year releases are not counted here:
    # they are already netted out of that year's ``blocked_losses``.
    unlocked_historical_losses: Decimal = Decimal("0")
    # Breakdown of ``unlocked_historical_losses`` as {origin_year: amount}, kept so
    # the report can state which year each released loss came from.
    unlocked_losses_by_origin: dict[int, Decimal] = field(default_factory=dict)
    total_fees_eur: Decimal = Decimal("0")  # All transaction fees seen this year (EUR)
    # Art. 35 LIRPF splits fees by side: acquisition costs are capitalised into the
    # valor de adquisición of the lot (so they reduce a FUTURE gain), while disposal
    # costs reduce the valor de transmisión of the sale that incurred them. Keeping
    # them apart is what stops the report claiming a deduction that never happened.
    acquisition_fees_eur: Decimal = Decimal("0")
    disposal_fees_eur: Decimal = Decimal("0")

    @property
    def deductible_losses(self) -> Decimal:
        """Losses usable against this year's base.

        This year's losses minus the part Art. 33.5.f blocks, plus the losses
        deferred from previous years that this year unblocked.
        """
        return self.total_losses - self.blocked_losses + self.unlocked_historical_losses

    @property
    def net_gain_loss(self) -> Decimal:
        """Net gain/loss, excluding blocked losses."""
        return self.total_gains + self.deductible_losses

    @property
    def taxable_gain(self) -> Decimal:
        """Taxable gain after offsetting allowed losses."""
        return max(Decimal("0"), self.net_gain_loss)

    @property
    def tax_due(self) -> Decimal:
        """
        Calculate Spanish savings tax due (progressive scale).
        Bands:
        - Up to €6,000: 19%
        - €6,000.01 to €50,000: 21%
        - €50,000.01 to €200,000: 23%
        - €200,000.01 to €300,000: 27%
        - Over €300,000: 28%
        """
        base = self.taxable_gain
        tax = Decimal("0")

        bands = [
            (Decimal("6000"), Decimal("0.19")),
            (Decimal("44000"), Decimal("0.21")),
            (Decimal("150000"), Decimal("0.23")),
            (Decimal("100000"), Decimal("0.27")),
            (None, Decimal("0.28")),
        ]

        remaining = base
        for limit, rate in bands:
            if limit is None or remaining <= limit:
                tax += remaining * rate
                break
            else:
                tax += limit * rate
                remaining -= limit

        return tax.quantize(Decimal("0.01"), ROUND_HALF_UP)


def merge_yearly_summaries(
    *summary_dicts: "dict[int, YearlyTaxSummary] | None",
) -> dict[int, YearlyTaxSummary]:
    """Sum several per-year summary dicts into one, field by field.

    The savings base is computed on the aggregate of every source of capital
    gains — each security in portfolio mode, and stocks plus crypto in the
    combined report — so those rollups all need the same merge.

    It lives here, beside :class:`YearlyTaxSummary`, because three separate
    copies of it existed and two of them had quietly fallen behind the dataclass:
    both dropped ``unlocked_historical_losses``, so a released Art. 33.5.f
    deferral vanished from the rollup and the savings base came out overstated —
    the taxpayer would have paid tax on a deduction they were entitled to. A
    field added to the dataclass must be carried here too; the tests pin that
    structurally rather than one field at a time.

    Inputs are never mutated: every year gets a fresh summary. ``None`` entries
    are ignored, so callers can pass an optional dict straight through.
    """
    merged: dict[int, YearlyTaxSummary] = {}
    for summaries in summary_dicts:
        for year, s in (summaries or {}).items():
            agg = merged.setdefault(year, YearlyTaxSummary(year=year))
            agg.total_gains += s.total_gains
            agg.total_losses += s.total_losses
            agg.blocked_losses += s.blocked_losses
            agg.unlocked_historical_losses += s.unlocked_historical_losses
            for origin_year, amount in s.unlocked_losses_by_origin.items():
                agg.unlocked_losses_by_origin[origin_year] = (
                    agg.unlocked_losses_by_origin.get(origin_year, Decimal("0")) + amount
                )
            agg.total_fees_eur += s.total_fees_eur
            agg.acquisition_fees_eur += s.acquisition_fees_eur
            agg.disposal_fees_eur += s.disposal_fees_eur
    return merged


@dataclass
class CarryforwardYear:
    """One year's row in the 4-year loss-carryforward ledger (Art. 49 LIRPF)."""

    year: int
    net_result: Decimal  # this year's net gain/loss (negative = loss)
    prior_losses_applied: Decimal  # prior-year losses used against this year's gain
    taxable_after: Decimal  # savings base from stock after applying carryforward
    new_loss_carried: Decimal  # net loss generated this year, added to the pool


@dataclass
class CarryforwardLedger:
    """Result of running the loss-carryforward simulation across all years."""

    rows: list[CarryforwardYear] = field(default_factory=list)
    expired: list[tuple[int, Decimal]] = field(default_factory=list)  # (origin_year, amount)
    pending_end: list[tuple[int, Decimal, int]] = field(
        default_factory=list
    )  # (origin_year, remaining, use_by_year)


@dataclass
class SavingsIncomeYear:
    """Dividend/interest (RCM) data for a single year, in EUR."""

    year: int
    dividends_eur: Decimal = Decimal("0")
    interest_eur: Decimal = Decimal("0")
    foreign_tax_eur: Decimal = Decimal("0")  # US tax withheld (informational)

    @property
    def rcm_net(self) -> Decimal:
        """Net returns on movable capital for the year."""
        return self.dividends_eur + self.interest_eur


@dataclass
class SavingsLedgerYear:
    """One year's row of the two-bucket savings-base ledger (Art. 49 LIRPF)."""

    year: int
    gp_net: Decimal  # capital gains/losses net this year
    rcm_net: Decimal  # dividends + interest net this year
    gp_prior_applied: Decimal  # prior-year G/L losses used against this year's G/L gain
    rcm_prior_applied: Decimal  # prior-year RCM losses used against this year's RCM
    cross_offset: Decimal  # amount offset across categories (25% cap)
    cross_direction: str  # "gp->rcm", "rcm->gp", or ""
    gp_taxable: Decimal  # capital-gains contribution to the savings base (>= 0)
    rcm_taxable: Decimal  # RCM contribution to the savings base (>= 0)
    foreign_tax_eur: Decimal

    @property
    def savings_base(self) -> Decimal:
        """Total savings base for the year (both buckets, >= 0)."""
        return self.gp_taxable + self.rcm_taxable


@dataclass
class SavingsLedger:
    """Result of the two-bucket savings-base simulation across all years."""

    rows: list[SavingsLedgerYear] = field(default_factory=list)
    # (bucket, origin_year, amount) where bucket is "G/L" or "RCM"
    expired: list[tuple[str, int, Decimal]] = field(default_factory=list)
    # (origin_year, remaining, use_by_year)
    gp_pending_end: list[tuple[int, Decimal, int]] = field(default_factory=list)
    rcm_pending_end: list[tuple[int, Decimal, int]] = field(default_factory=list)

    @property
    def total_foreign_tax(self) -> Decimal:
        return sum((r.foreign_tax_eur for r in self.rows), Decimal("0"))


@dataclass
class TaxEngineState:
    """
    Current state of the tax engine.

    Tracks the portfolio position, FIFO cost basis, and share lots.
    """

    total_shares: Decimal = Decimal("0")
    avg_cost_eur: Decimal = Decimal("0")
    total_portfolio_cost_eur: Decimal = Decimal("0")
    lots: list[ShareLot] = field(default_factory=list)

    def clone(self) -> "TaxEngineState":
        """Create a copy of the current state."""
        return TaxEngineState(
            total_shares=self.total_shares,
            avg_cost_eur=self.avg_cost_eur,
            total_portfolio_cost_eur=self.total_portfolio_cost_eur,
            lots=[
                ShareLot(
                    acquisition_date=lot.acquisition_date,
                    shares=lot.shares,
                    price_eur=lot.price_eur,
                    remaining_shares=lot.remaining_shares,
                    notes=lot.notes,
                    broker=lot.broker,
                    isin=lot.isin,
                    origin=lot.origin,
                    fees_eur=lot.fees_eur,
                    fx_rate=lot.fx_rate,
                    espp_fmv_usd=lot.espp_fmv_usd,
                    espp_price_usd=lot.espp_price_usd,
                )
                for lot in self.lots
            ],
        )
