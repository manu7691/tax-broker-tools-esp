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
    released: Decimal = Decimal("0")  # part already freed (negative)
    releases: list[tuple[date, Decimal]] = field(default_factory=list)

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
    # Wash-sale state carried BY THE LOT. ``deferred_claims`` holds the losses this
    # lot blocked (a lot can block more than one sale, and from more than one year);
    # ``disposals`` is the (date, shares) schedule of how FIFO consumed the lot,
    # which is what releases those deferrals.
    deferred_claims: list[DeferredWashSaleLoss] = field(default_factory=list)
    disposals: list[tuple[date, Decimal]] = field(default_factory=list)

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
    total_fees_eur: Decimal = Decimal("0")  # Total transaction fees deducted (EUR)

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
                )
                for lot in self.lots
            ],
        )
