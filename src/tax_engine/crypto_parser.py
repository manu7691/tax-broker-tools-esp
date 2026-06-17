"""
Crypto exchange CSV parsers (Pionex, Binance) for the Spanish Tax Engine.

Spanish tax treats every disposal of a crypto-asset — including selling it for a
stablecoin or swapping it for another coin (a *permuta*) — as a capital
gain/loss event in the savings base (base del ahorro). This module normalises
the raw exchange exports into a single stream of :class:`CryptoTrade` records and
then converts them into the engine's :class:`StockEvent` objects, **one FIFO
queue per coin**.

Stablecoins (USDT/USDC/…) are treated as USD cash: their value is the quote
amount, converted to EUR at the ECB USD/EUR rate of the trade date. Only the
non-stablecoin legs (BTC, SOL, ONDO, …) therefore generate taxable gains/losses.
A trade quoted in a *non*-stablecoin (a true crypto-to-crypto swap) is flagged
and skipped with a warning — those are out of scope for this MVP.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import EventType, StockEvent

# Quote/settlement assets we treat as "USD cash". Their EUR value comes from the
# ECB USD/EUR rate, not from a separate FIFO queue.
STABLECOINS = frozenset({"USDT", "USDC", "USD", "DAI", "BUSD", "FDUSD", "TUSD", "USDP", "USDD"})

# Candidate quote assets for splitting a concatenated Binance pair like
# "SEIUSDC" -> ("SEI", "USDC"). Ordered longest-first so e.g. "FDUSD" is matched
# before "USD".
_QUOTE_ASSETS = sorted(STABLECOINS | {"BTC", "ETH", "BNB", "EUR"}, key=len, reverse=True)

# Binance exports the amount and fee as a number glued to a ticker, e.g.
# "692.7SEI" or "0.6927SEI". This splits them apart.
_AMOUNT_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([A-Za-z][A-Za-z0-9]*)\s*$")


@dataclass
class CryptoTrade:
    """A single normalised spot trade from any exchange.

    ``dt`` is stored in UTC so trades from different exchanges merge into one
    correct chronological FIFO order.
    """

    dt: datetime  # UTC
    base: str  # asset acquired on a BUY / disposed on a SELL, e.g. "BTC"
    quote: str  # settlement asset, e.g. "USDT"
    side: str  # "BUY" or "SELL"
    qty: Decimal  # base quantity
    quote_amount: Decimal  # quote (settlement) amount, e.g. USDT spent/received
    fee_qty: Decimal
    fee_coin: str
    source: str  # "Pionex" | "Binance"

    @property
    def unit_price_quote(self) -> Decimal:
        """Price of one unit of base, expressed in the quote asset."""
        return self.quote_amount / self.qty


def _split_amount(raw: str) -> tuple[Decimal, str]:
    """Split a Binance "<number><ticker>" string into (amount, ticker)."""
    m = _AMOUNT_RE.match(raw)
    if not m:
        raise ValueError(f"Cannot parse amount/ticker from {raw!r}")
    return Decimal(m.group(1)), m.group(2).upper()


def _split_pair(pair: str) -> tuple[str, str]:
    """Split a concatenated pair like "SEIUSDC" into ("SEI", "USDC")."""
    pair = pair.upper()
    for quote in _QUOTE_ASSETS:
        if pair.endswith(quote) and len(pair) > len(quote):
            return pair[: -len(quote)], quote
    raise ValueError(f"Cannot identify quote asset in pair {pair!r}")


def parse_pionex(trading_csv: Path) -> list[CryptoTrade]:
    """Parse a Pionex ``trading.csv`` export (timestamps already in UTC+0)."""
    trades: list[CryptoTrade] = []
    with open(trading_csv, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                dt = datetime.strptime(row["date(UTC+0)"].strip(), "%Y-%m-%d %H:%M:%S")
                base, quote = row["symbol"].strip().upper().split("_", 1)
                qty = Decimal(row["executed_qty"])
                amount = Decimal(row["amount"])
                fee = Decimal(row["fee"]) if row.get("fee") else Decimal("0")
            except (KeyError, ValueError, InvalidOperation) as e:
                print(f"  Warning: skipping malformed Pionex row {row!r}: {e}")
                continue
            if qty <= 0 or amount <= 0:
                continue
            trades.append(
                CryptoTrade(
                    dt=dt,
                    base=base,
                    quote=quote,
                    side=row["side"].strip().upper(),
                    qty=qty,
                    quote_amount=amount,
                    fee_qty=fee,
                    fee_coin=(row.get("fee_coin") or "").strip().upper(),
                    source="Pionex",
                )
            )
    return trades


def parse_binance(history_csv: Path, utc_offset_hours: int = 2) -> list[CryptoTrade]:
    """Parse a Binance "Spot Trade History" export.

    The export's timestamps are in the local time named in the filename
    (default UTC+2); they are shifted back to UTC so they merge correctly with
    Pionex's UTC timestamps.
    """
    trades: list[CryptoTrade] = []
    with open(history_csv, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                local_dt = datetime.strptime(row["Time"].strip(), "%y-%m-%d %H:%M:%S")
                dt = local_dt - timedelta(hours=utc_offset_hours)
                base, quote = _split_pair(row["Pair"].strip())
                qty, exec_coin = _split_amount(row["Executed"])
                amount, _quote_coin = _split_amount(row["Amount"])
                fee_qty, fee_coin = (
                    _split_amount(row["Fee"]) if row.get("Fee") else (Decimal("0"), "")
                )
            except (KeyError, ValueError, InvalidOperation) as e:
                print(f"  Warning: skipping malformed Binance row {row!r}: {e}")
                continue
            if qty <= 0 or amount <= 0:
                continue
            trades.append(
                CryptoTrade(
                    dt=dt,
                    base=base,
                    quote=quote,
                    side=row["Side"].strip().upper(),
                    qty=qty,
                    quote_amount=amount,
                    fee_qty=fee_qty,
                    fee_coin=fee_coin,
                    source="Binance",
                )
            )
    return trades


def load_crypto_trades(input_dir: Path, binance_utc_offset_hours: int = 2) -> list[CryptoTrade]:
    """Load and merge every supported export found under ``input_dir``.

    Looks for ``pionex/trading.csv`` and any Binance ``*Spot-Trade-History*.csv``
    file. Returns all trades sorted chronologically (UTC).

    ``binance_utc_offset_hours`` is the timezone the Binance export's ``Time``
    column is in (Binance exports in the account's local time, not UTC). It is
    shifted back to UTC so trades merge correctly with Pionex's UTC timestamps —
    and, importantly, so a trade near midnight lands on the right day, which
    determines its ECB rate and tax year. Set it to your export's offset
    (e.g. 0 for UTC, 1 for CET winter, 2 for CEST summer — the default).
    """
    trades: list[CryptoTrade] = []

    pionex_csv = input_dir / "pionex" / "trading.csv"
    if pionex_csv.exists():
        found = parse_pionex(pionex_csv)
        print(f"  Loaded {len(found)} Pionex trade(s).")
        trades.extend(found)

    binance_dir = input_dir / "binance"
    search_dirs = [binance_dir] if binance_dir.exists() else [input_dir]
    for d in search_dirs:
        for csv_path in sorted(d.glob("*Spot-Trade-History*.csv")):
            found = parse_binance(csv_path, utc_offset_hours=binance_utc_offset_hours)
            print(f"  Loaded {len(found)} Binance trade(s) from {csv_path.name}.")
            trades.extend(found)

    trades.sort(key=lambda t: t.dt)
    return trades


def _fee_in_quote(trade: CryptoTrade) -> Decimal:
    """Express a trade's fee in the quote (≈ USD) asset, best-effort.

    - fee paid in the quote asset (USDT/USDC) -> taken at face value;
    - fee paid in the base coin -> valued at the trade's unit price;
    - anything else (e.g. BNB) is ignored with a warning.
    """
    if trade.fee_qty <= 0 or not trade.fee_coin:
        return Decimal("0")
    if trade.fee_coin == trade.quote:
        return trade.fee_qty
    if trade.fee_coin == trade.base:
        return trade.fee_qty * trade.unit_price_quote
    print(
        f"  Warning: fee paid in {trade.fee_coin} on {trade.dt:%Y-%m-%d} "
        f"{trade.base}/{trade.quote} not valued (unsupported fee coin)."
    )
    return Decimal("0")


def trades_to_events_by_coin(
    trades: list[CryptoTrade],
    *,
    unhandled_swaps: list[CryptoTrade] | None = None,
) -> dict[str, list[StockEvent]]:
    """Convert normalised trades into per-coin :class:`StockEvent` queues.

    Each coin gets its own FIFO queue. ``price_usd`` carries the unit price in
    the (stablecoin) quote asset, which the engine multiplies by the ECB
    USD/EUR rate to obtain ``price_eur`` — so the EUR cost basis already reflects
    the exchange rate on each trade's date, as required by the Agencia
    Tributaria. Events are returned in chronological order per coin.

    Crypto-to-crypto swaps (a non-stablecoin quote) are **not handled** by this
    MVP, yet Spain taxes them as a *permuta*. They are therefore not dropped
    silently: if an ``unhandled_swaps`` list is supplied, each such trade is
    appended to it so callers can report "declare these manually" rather than
    understating the gain.
    """
    events_by_coin: dict[str, list[StockEvent]] = {}

    for t in sorted(trades, key=lambda x: x.dt):
        # Skip trades whose base IS a stablecoin (e.g. a USDC->USDT convert):
        # treated as cash, no taxable position to track.
        if t.base in STABLECOINS:
            continue
        # True crypto-to-crypto swaps (non-stable quote) are out of MVP scope,
        # but taxable in Spain — collect them so they are surfaced, not lost.
        if t.quote not in STABLECOINS:
            print(
                f"  Warning: crypto-to-crypto trade {t.base}/{t.quote} on "
                f"{t.dt:%Y-%m-%d} NOT handled (taxable permuta — declare manually)."
            )
            if unhandled_swaps is not None:
                unhandled_swaps.append(t)
            continue

        event_type = EventType.BUY if t.side == "BUY" else EventType.SELL
        # Fees are deducted from the gain by the engine on SELL events only.
        fees_usd = _fee_in_quote(t) if event_type == EventType.SELL else Decimal("0")

        event = StockEvent(
            event_date=t.dt.date(),
            event_type=event_type,
            shares=t.qty,
            price_usd=t.unit_price_quote,
            fees_usd=fees_usd,
            notes=f"{t.source} {t.side} {t.base}/{t.quote}",
        )
        events_by_coin.setdefault(t.base, []).append(event)

    return events_by_coin
