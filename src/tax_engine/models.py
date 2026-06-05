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
class ShareLot:
    """Represents a lot of acquired shares for FIFO tracking (Spain)."""

    acquisition_date: date
    shares: Decimal
    price_eur: Decimal
    remaining_shares: Decimal
    notes: str = ""


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
    fees_usd: Decimal = Decimal("0")
    shares_sold_to_cover: Decimal = Decimal("0")  # For VEST events
    notes: str = ""
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

            self._fx_rate_resolved = ECBRateFetcher.get_rate(self.event_date)

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
        if not isinstance(self.shares, Decimal):
            object.__setattr__(self, "shares", Decimal(str(self.shares)))
        if not isinstance(self.price_usd, Decimal):
            object.__setattr__(self, "price_usd", Decimal(str(self.price_usd)))
        if self.fx_rate is not None and not isinstance(self.fx_rate, Decimal):
            object.__setattr__(self, "fx_rate", Decimal(str(self.fx_rate)))

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
    blocked_losses: Decimal = Decimal("0")
    total_fees_eur: Decimal = Decimal("0")  # Total transaction fees deducted (EUR)

    @property
    def deductible_losses(self) -> Decimal:
        """Deductible losses for Spain (after excluding blocked losses)."""
        return self.total_losses - self.blocked_losses

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
                )
                for lot in self.lots
            ],
        )
