"""
Revolut investment data parser (optional input).

Reads any ``input/revolut/*.csv`` file, keeps only the rows for the security
tracked by the rest of the engine, and turns them into the engine's native
:class:`StockEvent` / :class:`SavingsIncomeYear` objects so they flow into the
*same* FIFO pool as the E-Trade data (cross-broker FIFO + 2-month wash-sale, as
Spain requires for homogeneous securities).

Two export shapes are auto-detected per file:

* **Account movements** (preferred) — a full transaction log with columns
  ``Date, Ticker, Type, Quantity, Price per share, Total Amount, Currency,
  FX Rate``. This includes ``BUY``/``SELL`` trades, normalizes ``STOCK SPLIT``
  rows into post-split share terms, and ignores cash top-ups, withdrawals and
  internal transfers. It has **no ISIN**, so rows are filtered by
  **Ticker**. Crucially it carries *buys* too, so a security you bought but never
  sold on Revolut still enters the FIFO pool.

* **Realized gains/losses** — already-FIFO-matched sells with columns
  ``Date acquired, Date sold, Symbol, ISIN, ..., Cost basis, Gross proceeds``
  (with or without an ``Income from Sells`` / ``Other income & fees`` section
  header). Filtered by **ISIN** when available, else ticker. The "Other income &
  fees" section, when present, becomes dividend/withholding (RCM) income.

Currency: only USD (converted via the official ECB rate per transaction date,
like the E-Trade data) and EUR (1:1) are processed; other currencies are skipped
with a warning. Revolut's own ``FX Rate`` column is intentionally ignored —
Spanish tax law uses the ECB reference rate, which the engine fetches itself.
"""

import csv
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

from .ecb_rates import ECBRateFetcher
from .models import EventType, SavingsIncomeYear, StockEvent
from .securities import build_isin_resolver

# Section titles for the realized-gains export (each on its own line).
_SELLS_SECTION = "Income from Sells"
_OTHER_SECTION = "Other income & fees"
_SECTION_TITLES = {_SELLS_SECTION, _OTHER_SECTION}

# Columns that identify the realized-gains "sells" header row.
_SELLS_HEADER_KEYS = {"Date acquired", "Date sold", "Gross proceeds"}
# Columns that identify the account-movements header row.
_MOVEMENTS_HEADER_KEYS = {"Ticker", "Type", "Total Amount"}


def _header_cols(line: str) -> set[str]:
    return {c.strip() for c in line.split(",")}


def _first_nonempty(text: str) -> str:
    return next((ln for ln in text.splitlines() if ln.strip()), "")


def _is_movements_format(text: str) -> bool:
    """Whether a CSV is the account-movements export (vs. realized gains)."""
    return _MOVEMENTS_HEADER_KEYS.issubset(_header_cols(_first_nonempty(text)))


def _split_sections(text: str) -> dict[str, list[dict[str, str]]]:
    """Split a realized-gains CSV into its named sections.

    Handles the titled form (a ``Income from Sells`` line, a CSV header, data
    rows, then ``Other income & fees``) and the title-less form (the file is just
    the sells header followed by rows). Returns ``{title: [row_dict, ...]}``.
    """
    lines = text.splitlines()
    sections: dict[str, list[dict[str, str]]] = {}
    i, n = 0, len(lines)
    while i < n:
        title = lines[i].strip()
        if title not in _SECTION_TITLES:
            i += 1
            continue
        # Skip blank lines between the title and its header row.
        i += 1
        while i < n and not lines[i].strip():
            i += 1
        if i >= n:
            break
        header = lines[i]
        i += 1
        block: list[str] = []
        while i < n and lines[i].strip() and lines[i].strip() not in _SECTION_TITLES:
            block.append(lines[i])
            i += 1
        sections[title] = list(csv.DictReader(StringIO("\n".join([header, *block]))))

    # Title-less gains export: the whole file is the sells block.
    if _SELLS_SECTION not in sections and _SELLS_HEADER_KEYS.issubset(
        _header_cols(_first_nonempty(text))
    ):
        nonempty = [ln for ln in lines if ln.strip()]
        sections[_SELLS_SECTION] = list(csv.DictReader(StringIO("\n".join(nonempty))))
    return sections


def _parse_decimal(raw: str | None) -> Decimal:
    """Parse a money/quantity cell, tolerating ``$``, thousands separators, spaces."""
    cleaned = str(raw or "").replace("$", "").replace(",", "").strip()
    if cleaned in ("", "--", "N/A"):
        raise InvalidOperation(f"empty numeric cell: {raw!r}")
    return Decimal(cleaned)


def _parse_amount(raw: str | None) -> Decimal:
    """Parse a possibly currency-prefixed amount, e.g. ``"USD 1208.60"`` -> 1208.60.

    The currency code (and any thousands separators) are stripped; the trailing
    numeric token is returned, preserving a leading minus (``"USD -272.38"``).
    """
    cleaned = str(raw or "").replace(",", "").strip()
    if cleaned in ("", "--", "N/A"):
        raise InvalidOperation(f"empty amount cell: {raw!r}")
    return Decimal(cleaned.split()[-1])


def _row_matches(row: dict[str, str], isin: str | None, symbol: str | None) -> bool:
    """Whether a realized-gains row belongs to the tracked security.

    ISIN is authoritative when supplied (it defines "homogeneous"); the ticker
    is used only as a fallback when no ISIN is configured.
    """
    if isin:
        return (row.get("ISIN") or "").strip().upper() == isin.strip().upper()
    if symbol:
        return (row.get("Symbol") or "").strip().upper() == symbol.strip().upper()
    return False


def _learn_isins_from_text(text: str) -> dict[str, str]:
    """Harvest ``{ticker: ISIN}`` pairs from an ISIN-bearing (gains) export.

    The realized-gains export carries a Symbol + ISIN per row; collecting them
    lets the ISIN-less movements export resolve each ticker to its ISIN without
    any network call or user config. Returns ``{}`` for a movements-only file.
    """
    learned: dict[str, str] = {}
    for rows in _split_sections(text).values():
        for row in rows:
            sym = (row.get("Symbol") or "").strip().upper()
            isin = (row.get("ISIN") or "").strip().upper()
            if sym and isin:
                learned.setdefault(sym, isin)
    return learned


def _normalize_currency(currency: str | None) -> str | None:
    """Normalize a row's currency, or ``None`` if the ECB cannot convert it.

    Returns the upper-cased currency code for EUR and any ECB reference currency
    (USD, GBP, CHF, …); returns ``None`` for currencies the ECB does not publish,
    signalling the caller to skip the row.
    """
    cur = (currency or "USD").strip().upper()
    return cur if ECBRateFetcher.is_supported(cur) else None


def _fx_rate_for(currency: str) -> Decimal | None:
    """The fx_rate to stamp on an event in ``currency``: 1 for EUR, else ``None``.

    A ``None`` rate defers conversion to the ECB fetcher (which uses the event's
    ``currency`` and date); EUR rows are already in euros, so they carry a 1:1 rate.
    """
    return Decimal("1") if currency == "EUR" else None


def _movements_trade_events(
    text: str,
    symbol: str | None,
    source: str,
    all_securities: bool = False,
    resolver: "Callable[[str | None], str | None] | None" = None,
) -> list[StockEvent]:
    """Build BUY/SELL events from an account-movements CSV.

    Filtered to ``symbol`` by default; with ``all_securities`` every ticker in the
    file is emitted (each ticker tracked independently). The movements export has
    no ISIN column, so each event's ISIN is resolved from the ticker via
    ``resolver`` (user map → gains-export learned map → cache); unresolved tickers
    keep ``isin=None`` and group by ticker.

    Stock splits are normalized away rather than handed to the engine: when a
    ``STOCK SPLIT`` row is reached, every trade already collected **for that
    ticker** (which is in pre-split share terms) is rescaled to post-split terms —
    shares ×ratio, price ÷ratio — so the stream ends up in current-share terms,
    consistent with later rows and with the E-Trade data. The split ratio is
    derived from the added shares against the running holding at that point. This
    keeps cost basis unchanged and means FIFO never sees more shares sold than
    were acquired.
    """
    want = (symbol or "").strip().upper()
    # Per-ticker state so splits rescale only their own security's prior trades.
    trades_by_ticker: dict[str, list[dict[str, object]]] = defaultdict(list)
    running_by_ticker: dict[str, Decimal] = defaultdict(Decimal)

    for idx, row in enumerate(csv.DictReader(StringIO(text)), start=1):
        ticker = (row.get("Ticker") or "").strip().upper()
        if not ticker or (not all_securities and ticker != want):
            continue

        ttype = (row.get("Type") or "").strip().upper()
        is_split = "SPLIT" in ttype
        if ttype.startswith("BUY"):
            event_type, label, sign = EventType.BUY, "Buy", Decimal("1")
        elif ttype.startswith("SELL"):
            event_type, label, sign = EventType.SELL, "Sell", Decimal("-1")
        elif ttype.startswith("TRANSFER"):
            continue  # internal entity move; net-zero to the holding
        elif not is_split:
            continue  # cash top-up/withdrawal or other non-trade rows

        trades = trades_by_ticker[ticker]
        running = running_by_ticker[ticker]

        if is_split:
            try:
                added = _parse_decimal(row.get("Quantity"))
            except InvalidOperation:
                added = Decimal("0")
            if running <= 0 or added <= 0:
                print(
                    f"Warning: {source} row {idx}: {ticker} stock split could not be applied "
                    f"(added={row.get('Quantity')!r}, holding={running}); leaving shares as-is."
                )
                continue
            ratio = (running + added) / running
            for t in trades:  # rescale prior trades into post-split terms
                t["shares"] = t["shares"] * ratio  # type: ignore[operator]
                t["price"] = t["price"] / ratio  # type: ignore[operator]
            running_by_ticker[ticker] = running + added
            continue

        cur = _normalize_currency(row.get("Currency"))
        if cur is None:
            print(
                f"Warning: {source} row {idx} ({ticker}) in unsupported currency "
                f"{row.get('Currency')!r}; skipping (not an ECB reference currency)."
            )
            continue

        try:
            event_date = date.fromisoformat(str(row["Date"]).split("T")[0].strip())
            qty = _parse_decimal(row.get("Quantity"))
            price = _parse_amount(row.get("Price per share"))
        except (KeyError, ValueError, InvalidOperation) as e:
            print(f"Warning: skipping malformed Revolut movement row {idx} in {source}: {e}")
            continue

        if qty <= 0 or price <= 0:
            print(
                f"Warning: skipping Revolut movement row {idx} in {source}: "
                f"non-positive quantity/price ({qty}/{price})."
            )
            continue

        trades.append(
            {
                "event_date": event_date,
                "event_type": event_type,
                "shares": qty,
                "price": price,
                "fx_rate": _fx_rate_for(cur),
                "currency": cur,
                "notes": f"Revolut {label} ({ticker})",
            }
        )
        running_by_ticker[ticker] = running + sign * qty

    return [
        StockEvent(
            event_date=t["event_date"],  # type: ignore[arg-type]
            event_type=t["event_type"],  # type: ignore[arg-type]
            shares=t["shares"],  # type: ignore[arg-type]
            price_usd=t["price"],  # type: ignore[arg-type]
            fx_rate=t["fx_rate"],  # type: ignore[arg-type]
            currency=t["currency"],  # type: ignore[arg-type]
            notes=t["notes"],  # type: ignore[arg-type]
            broker="Revolut",
            symbol=ticker,  # movements export has no ISIN; tag the ticker
            isin=resolver(ticker) if resolver else None,
        )
        for ticker, trades in trades_by_ticker.items()
        for t in trades
    ]


def _sells_trade_events(
    text: str,
    isin: str | None,
    symbol: str | None,
    source: str,
    all_securities: bool = False,
) -> list[StockEvent]:
    """Build BUY+SELL events from a realized-gains CSV's "Income from Sells" rows.

    When ``all_securities`` is set, the per-security filter is bypassed and *every*
    ticker in the export is emitted (the gains export carries an ISIN per row, so
    each event gets its own ``symbol``/``isin`` and lands in its own FIFO queue).
    Otherwise rows are filtered to the tracked security (ISIN preferred).
    """
    events: list[StockEvent] = []
    for idx, row in enumerate(_split_sections(text).get(_SELLS_SECTION, []), start=1):
        if not all_securities and not _row_matches(row, isin, symbol):
            continue

        cur = _normalize_currency(row.get("Currency"))
        sym = (row.get("Symbol") or "").strip().upper()
        row_isin = (row.get("ISIN") or "").strip().upper() or None
        if cur is None:
            print(
                f"Warning: {source} sell row {idx} ({sym}) in unsupported currency "
                f"{row.get('Currency')!r}; skipping (not an ECB reference currency)."
            )
            continue
        fx_rate = _fx_rate_for(cur)

        try:
            acq_date = date.fromisoformat(str(row["Date acquired"]).strip())
            sold_date = date.fromisoformat(str(row["Date sold"]).strip())
            qty = _parse_decimal(row.get("Quantity"))
            cost_basis = _parse_decimal(row.get("Cost basis"))
            proceeds = _parse_decimal(row.get("Gross proceeds"))
        except (KeyError, ValueError, InvalidOperation) as e:
            print(f"Warning: skipping malformed Revolut sell row {idx} in {source}: {e}")
            continue

        if qty <= 0 or cost_basis <= 0 or proceeds <= 0:
            print(
                f"Warning: skipping Revolut sell row {idx} in {source}: "
                f"non-positive qty/cost/proceeds ({qty}/{cost_basis}/{proceeds})."
            )
            continue

        buy_price = (cost_basis / qty).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        sell_price = (proceeds / qty).quantize(Decimal("0.0001"), ROUND_HALF_UP)

        events.append(
            StockEvent(
                event_date=acq_date,
                event_type=EventType.BUY,
                shares=qty,
                price_usd=buy_price,
                fx_rate=fx_rate,
                currency=cur,
                notes=f"Revolut Buy ({sym})",
                broker="Revolut",
                symbol=sym,
                isin=row_isin,
            )
        )
        events.append(
            StockEvent(
                event_date=sold_date,
                event_type=EventType.SELL,
                shares=qty,
                price_usd=sell_price,
                fx_rate=fx_rate,
                currency=cur,
                notes=f"Revolut Sell ({sym})",
                broker="Revolut",
                symbol=sym,
                isin=row_isin,
            )
        )
    return events


def load_revolut_trade_events(
    input_dir: Path,
    isin: str | None = None,
    symbol: str | None = None,
    all_securities: bool = False,
    isin_map: dict[str, str] | None = None,
) -> list[StockEvent]:
    """Build trade (BUY/SELL) events from every ``input/revolut/*.csv``.

    Each file is auto-detected as the account-movements export (filtered by
    ticker) or the realized-gains export (filtered by ISIN, else ticker). When
    ``all_securities`` is set, *both* exports emit every ticker they carry: the
    gains export tags each event with its own ISIN, and the ISIN-less movements
    export resolves each ticker to an ISIN via, in order, the user ``isin_map``,
    the ISINs learned from any gains export in this same folder, and the persistent
    cache (see :func:`tax_engine.securities.build_isin_resolver`). Tickers that stay
    unresolved keep ``isin=None`` and group by ticker (flagged with a caveat).
    Returns an empty list when the folder, files, or matching rows are absent.
    """
    revolut_dir = input_dir / "revolut"
    if not revolut_dir.is_dir():
        return []
    if not (isin or symbol or all_securities):
        print(
            "Warning: Revolut CSV found but no ISIN/ticker configured to filter it; "
            "skipping. Set 'ticker' (and optionally 'isin') in input/ticker.json."
        )
        return []

    files: list[tuple[str, str]] = []  # (filename, text)
    for csv_path in sorted(revolut_dir.glob("*.csv")):
        try:
            files.append((csv_path.name, csv_path.read_text(encoding="utf-8-sig")))
        except OSError as e:
            print(f"Warning: could not read Revolut file {csv_path}: {e}")

    # Build the ticker→ISIN resolver once, seeded with the ISINs the gains
    # export(s) carry, so the movements export can attach them without a network call.
    resolver = None
    if all_securities:
        learned: dict[str, str] = {}
        for _name, text in files:
            if not _is_movements_format(text):
                learned.update(_learn_isins_from_text(text))
        resolver = build_isin_resolver(isin_map or {}, learned=learned)

    events: list[StockEvent] = []
    for name, text in files:
        if _is_movements_format(text):
            if all_securities:
                events += _movements_trade_events(
                    text, None, name, all_securities=True, resolver=resolver
                )
            elif not symbol:
                print(
                    f"Warning: {name} is an account-movements export but no ticker "
                    "is configured to filter it (it has no ISIN); skipping. Set 'ticker' "
                    "in input/ticker.json."
                )
            else:
                events += _movements_trade_events(text, symbol, name)
        else:
            events += _sells_trade_events(
                text, isin, symbol, name, all_securities=all_securities
            )

    if all_securities:
        unresolved = sorted({e.symbol for e in events if e.symbol and not e.isin})
        if unresolved:
            print(
                f"Note: {len(unresolved)} Revolut ticker(s) had no ISIN and are grouped by "
                f"ticker — {', '.join(unresolved)}. Safe if the ticker never changed ISIN; "
                "add them to input/securities.json 'isin_map' to merge across brokers reliably."
            )

    if events:
        n_buy = sum(1 for e in events if e.event_type == EventType.BUY)
        n_sell = sum(1 for e in events if e.event_type == EventType.SELL)
        scope = "all securities" if all_securities else (symbol or isin)
        print(
            f"Loaded {len(events)} Revolut trade event(s) for {scope} "
            f"({n_buy} buy, {n_sell} sell)."
        )
    return events


def load_revolut_savings_income(
    input_dir: Path,
    isin: str | None = None,
    symbol: str | None = None,
    all_securities: bool = False,
) -> dict[int, SavingsIncomeYear]:
    """Build per-year RCM income from any "Other income & fees" section.

    Gross amounts become dividends; withholding tax becomes foreign tax (both in
    EUR, converted at the ECB rate on the payment date for USD rows). Filtered to
    the tracked security, consistent with the trades — unless ``all_securities``
    is set, in which case every security's dividends are summed into the portfolio
    RCM. The account-movements export has no dividend section, so this returns
    ``{}`` for those files. Returns ``{}`` when absent.
    """
    revolut_dir = input_dir / "revolut"
    if not revolut_dir.is_dir() or not (isin or symbol or all_securities):
        return {}

    result: dict[int, SavingsIncomeYear] = {}
    count = 0
    for csv_path in sorted(revolut_dir.glob("*.csv")):
        try:
            text = csv_path.read_text(encoding="utf-8-sig")
        except OSError as e:
            print(f"Warning: could not read Revolut file {csv_path}: {e}")
            continue
        if _is_movements_format(text):
            continue

        rows = _split_sections(text).get(_OTHER_SECTION, [])
        for idx, row in enumerate(rows, start=1):
            if not all_securities and not _row_matches(row, isin, symbol):
                continue

            cur = _normalize_currency(row.get("Currency"))
            if cur is None:
                print(
                    f"Warning: {csv_path.name} income row {idx} in unsupported currency "
                    f"{row.get('Currency')!r}; skipping (not an ECB reference currency)."
                )
                continue

            try:
                pay_date = date.fromisoformat(str(row["Date"]).strip())
                gross = _parse_decimal(row.get("Gross amount"))
                withholding = _parse_decimal(row.get("Withholding tax") or "0")
            except (KeyError, ValueError, InvalidOperation) as e:
                print(f"Warning: skipping malformed Revolut income row {idx} in {csv_path.name}: {e}")
                continue

            rate = Decimal("1") if cur == "EUR" else ECBRateFetcher.get_rate(pay_date, cur)
            gross_eur = (gross * rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
            wht_eur = (withholding * rate).quantize(Decimal("0.01"), ROUND_HALF_UP)

            entry = result.setdefault(pay_date.year, SavingsIncomeYear(year=pay_date.year))
            # Rows tied to a security (Symbol) are dividends; symbol-less rows are
            # cash interest. Both are RCM (same savings base) but different Modelo
            # 100 casillas, so we keep them apart.
            if (row.get("Symbol") or "").strip():
                entry.dividends_eur += gross_eur
            else:
                entry.interest_eur += gross_eur
            entry.foreign_tax_eur += wht_eur
            count += 1

    if count:
        print(f"Loaded {count} Revolut dividend/fee row(s) across {len(result)} year(s).")
    return result


def load_revolut_dividends_by_symbol(input_dir: Path) -> dict[str, Decimal]:
    """Total gross dividend/interest (RCM) income in EUR **per security symbol**.

    Parses the Revolut "Other income & fees" rows (which carry a Symbol) and sums
    the gross amounts per symbol, converting each at the ECB rate on the payment
    date. This is **informational** — the taxable RCM base stays portfolio-level
    (Spanish law) — and is used to show a per-security dividends column in the
    dashboard. Returns ``{}`` when the folder/rows are absent.
    """
    revolut_dir = input_dir / "revolut"
    if not revolut_dir.is_dir():
        return {}

    by_symbol: dict[str, Decimal] = {}
    for csv_path in sorted(revolut_dir.glob("*.csv")):
        try:
            text = csv_path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        if _is_movements_format(text):
            continue
        for row in _split_sections(text).get(_OTHER_SECTION, []):
            sym = (row.get("Symbol") or "").strip().upper()
            cur = _normalize_currency(row.get("Currency"))
            if not sym or cur is None:
                continue
            try:
                pay_date = date.fromisoformat(str(row["Date"]).strip())
                gross = _parse_decimal(row.get("Gross amount"))
            except (KeyError, ValueError, InvalidOperation):
                continue
            rate = Decimal("1") if cur == "EUR" else ECBRateFetcher.get_rate(pay_date, cur)
            gross_eur = (gross * rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
            by_symbol[sym] = by_symbol.get(sym, Decimal("0")) + gross_eur
    return by_symbol


def merge_savings_income(
    base: dict[int, SavingsIncomeYear], extra: dict[int, SavingsIncomeYear]
) -> dict[int, SavingsIncomeYear]:
    """Sum two per-year RCM maps into a new dict (does not mutate the inputs)."""
    merged: dict[int, SavingsIncomeYear] = {
        y: SavingsIncomeYear(
            year=y,
            dividends_eur=e.dividends_eur,
            interest_eur=e.interest_eur,
            foreign_tax_eur=e.foreign_tax_eur,
        )
        for y, e in base.items()
    }
    for y, e in extra.items():
        tgt = merged.setdefault(y, SavingsIncomeYear(year=y))
        tgt.dividends_eur += e.dividends_eur
        tgt.interest_eur += e.interest_eur
        tgt.foreign_tax_eur += e.foreign_tax_eur
    return merged


def load_revolut_events(
    input_dir: Path,
    isin: str | None = None,
    symbol: str | None = None,
    all_securities: bool = False,
    isin_map: dict[str, str] | None = None,
) -> tuple[list[StockEvent], dict[int, SavingsIncomeYear]]:
    """Convenience wrapper returning both the trade events and the RCM income.

    With ``all_securities`` both Revolut exports are loaded unfiltered (every
    ticker); the movements export's tickers are resolved to ISINs via ``isin_map``
    plus the ISINs learned from any gains export (see ``load_revolut_trade_events``).
    """
    return (
        load_revolut_trade_events(
            input_dir, isin=isin, symbol=symbol, all_securities=all_securities, isin_map=isin_map
        ),
        load_revolut_savings_income(input_dir, isin=isin, symbol=symbol, all_securities=all_securities),
    )
