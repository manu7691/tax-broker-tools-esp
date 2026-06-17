"""
Sample data for testing the tax engine.

Provides functions to create sample stock events with and without FX rates.
"""

from datetime import date
from decimal import Decimal

from .models import EventType, SavingsIncomeYear, StockEvent


def create_sample_events_with_manual_fx() -> list[StockEvent]:
    """
    Create sample events with manually specified FX rates (from the original spreadsheet).
    This serves as a test case to verify the engine works correctly.
    """
    return [
        # 2020
        StockEvent(
            date(2020, 11, 27),
            EventType.BUY,
            Decimal("50"),
            Decimal("38.42"),
            fx_rate=Decimal("0.8388"),
            notes="ESPP Buy",
        ),
        # 2021
        StockEvent(
            date(2021, 2, 3),
            EventType.SELL,
            Decimal("50"),
            Decimal("48.85"),
            fx_rate=Decimal("0.8322"),
            notes="Manual Sell",
        ),
        StockEvent(
            date(2021, 5, 17),
            EventType.VEST,
            Decimal("30"),
            Decimal("46.68"),
            fx_rate=Decimal("0.8235"),
            notes="RSU Vest",
        ),
        StockEvent(
            date(2021, 5, 17),
            EventType.SELL,
            Decimal("25"),
            Decimal("44.82"),
            fx_rate=Decimal("0.8235"),
            notes="RSU Sell (sell-to-cover)",
        ),
        StockEvent(
            date(2021, 5, 17),
            EventType.SELL,
            Decimal("2"),
            Decimal("46.22"),
            fx_rate=Decimal("0.8235"),
            notes="RSU Sell",
        ),
        StockEvent(
            date(2021, 5, 28),
            EventType.BUY,
            Decimal("50"),
            Decimal("51.74"),
            fx_rate=Decimal("0.8236"),
            notes="ESPP Buy",
        ),
        StockEvent(
            date(2021, 6, 15),
            EventType.BUY,
            Decimal("20"),
            Decimal("55.00"),
            fx_rate=Decimal("0.8250"),
            broker="Revolut",
            notes="Direct Purchase",
        ),
        StockEvent(
            date(2021, 8, 16),
            EventType.VEST,
            Decimal("10"),
            Decimal("63.65"),
            fx_rate=Decimal("0.8495"),
            notes="RSU Vest",
        ),
        StockEvent(
            date(2021, 8, 16),
            EventType.SELL,
            Decimal("5"),
            Decimal("61.25"),
            fx_rate=Decimal("0.8495"),
            notes="RSU Sell (sell-to-cover)",
        ),
        StockEvent(
            date(2021, 9, 20),
            EventType.BUY,
            Decimal("15"),
            Decimal("62.50"),
            fx_rate=Decimal("0.8520"),
            broker="Revolut",
            notes="Direct Purchase",
        ),
        StockEvent(
            date(2021, 11, 15),
            EventType.VEST,
            Decimal("10"),
            Decimal("70.68"),
            fx_rate=Decimal("0.8738"),
            notes="RSU Vest",
        ),
        StockEvent(
            date(2021, 11, 16),
            EventType.SELL,
            Decimal("5"),
            Decimal("69.28"),
            fx_rate=Decimal("0.8797"),
            notes="RSU Sell",
        ),
        StockEvent(
            date(2021, 11, 26),
            EventType.BUY,
            Decimal("100"),
            Decimal("62.97"),
            fx_rate=Decimal("0.8857"),
            notes="ESPP Buy",
        ),
        # 2022
        StockEvent(
            date(2022, 2, 10),
            EventType.SELL,
            Decimal("25"),
            Decimal("45.00"),
            fx_rate=Decimal("0.8810"),
            broker="Revolut",
            notes="Revolut Sell",
        ),
        StockEvent(
            date(2022, 5, 27),
            EventType.BUY,
            Decimal("105"),
            Decimal("38.19"),
            fx_rate=Decimal("0.9327"),
            notes="ESPP Buy",
        ),
        StockEvent(
            date(2022, 6, 1),
            EventType.SELL,
            Decimal("205"),
            Decimal("39.15"),
            fx_rate=Decimal("0.9335"),
            notes="Manual Sell",
        ),
    ]


# Illustrative ISINs for the portfolio demo. TSLA/NVDA are the real ones; DT and
# SHEL are representative. Real runs read ISINs from your data / securities.json.
_DEMO_DT_ISIN = "US26614N1027"  # Dynatrace (illustrative)
_DEMO_TSLA_ISIN = "US88160R1014"  # Tesla
_DEMO_NVDA_ISIN = "US67066G1040"  # NVIDIA
_DEMO_ADBE_ISIN = "US00724F1012"  # Adobe
_DEMO_SHEL_ISIN = "GB00BP6MXD84"  # Shell plc (priced in GBP, London)


def create_sample_multi_security_events() -> list[StockEvent]:
    """Sample events spanning several securities and currencies (portfolio demo).

    The employer stock (DT, E*TRADE) is the single-security demo set tagged with
    its identity, plus extra securities held on Revolut: TSLA and NVDA (USD) and
    Shell (SHEL, **GBP**). This exercises per-security FIFO, same-ISIN cross-broker
    merging, the per-security report/chart, and the multi-currency (non-USD) path.
    All FX rates are supplied manually so the demo needs no network access.
    """
    # Employer stock: reuse the single-security demo, tagged as DT (E*TRADE + the
    # two Revolut "Direct Purchase" rows already in that set merge under DT's ISIN).
    events: list[StockEvent] = []
    for e in create_sample_events_with_manual_fx():
        e.symbol = "DT"
        e.isin = _DEMO_DT_ISIN
        events.append(e)

    # TSLA on Revolut (USD): bought, partly sold at a gain.
    events += [
        StockEvent(
            date(2021, 3, 10),
            EventType.BUY,
            Decimal("10"),
            Decimal("220.00"),
            fx_rate=Decimal("0.84"),
            broker="Revolut",
            symbol="TSLA",
            isin=_DEMO_TSLA_ISIN,
            notes="Revolut Buy (TSLA)",
        ),
        StockEvent(
            date(2021, 11, 1),
            EventType.SELL,
            Decimal("6"),
            Decimal("400.00"),
            fx_rate=Decimal("0.86"),
            broker="Revolut",
            symbol="TSLA",
            isin=_DEMO_TSLA_ISIN,
            notes="Revolut Sell (TSLA)",
        ),
    ]

    # NVDA on Revolut (USD): bought and held — shows an open position, no realized gain.
    events += [
        StockEvent(
            date(2021, 4, 5),
            EventType.BUY,
            Decimal("8"),
            Decimal("150.00"),
            fx_rate=Decimal("0.84"),
            broker="Revolut",
            symbol="NVDA",
            isin=_DEMO_NVDA_ISIN,
            notes="Revolut Buy (NVDA)",
        ),
    ]

    # A late DT purchase on Revolut (after the last DT sale) so DT's *remaining*
    # shares span both brokers — exercises the break-even per-broker filter.
    events.append(
        StockEvent(
            date(2022, 9, 1),
            EventType.BUY,
            Decimal("12"),
            Decimal("42.00"),
            fx_rate=Decimal("0.95"),
            broker="Revolut",
            symbol="DT",
            isin=_DEMO_DT_ISIN,
            notes="Direct Purchase (DT)",
        )
    )

    # ADBE on Revolut (USD): bought and sold at a LOSS in 2020 — an early-year net
    # loss that the loss-carryforward then applies against the 2021 gains.
    events += [
        StockEvent(
            date(2020, 9, 1),
            EventType.BUY,
            Decimal("5"),
            Decimal("480.00"),
            fx_rate=Decimal("0.85"),
            broker="Revolut",
            symbol="ADBE",
            isin=_DEMO_ADBE_ISIN,
            notes="Revolut Buy (ADBE)",
        ),
        StockEvent(
            date(2020, 12, 10),
            EventType.SELL,
            Decimal("5"),
            Decimal("408.00"),
            fx_rate=Decimal("0.85"),
            broker="Revolut",
            symbol="ADBE",
            isin=_DEMO_ADBE_ISIN,
            notes="Revolut Sell (ADBE)",
        ),
    ]

    # Shell on Revolut, priced in GBP: bought and sold — the price is converted to
    # EUR with the GBP rate (here supplied manually), exercising the currency field.
    events += [
        StockEvent(
            date(2021, 2, 1),
            EventType.BUY,
            Decimal("40"),
            Decimal("13.50"),
            fx_rate=Decimal("1.15"),
            currency="GBP",
            broker="Revolut",
            symbol="SHEL",
            isin=_DEMO_SHEL_ISIN,
            notes="Revolut Buy (SHEL)",
        ),
        StockEvent(
            date(2022, 1, 20),
            EventType.SELL,
            Decimal("30"),
            Decimal("19.00"),
            fx_rate=Decimal("1.18"),
            currency="GBP",
            broker="Revolut",
            symbol="SHEL",
            isin=_DEMO_SHEL_ISIN,
            notes="Revolut Sell (SHEL)",
        ),
    ]
    return events


def create_sample_savings_income() -> dict[int, SavingsIncomeYear]:
    """Sample dividend/interest (RCM) income for the portfolio demo, in EUR.

    Paired with :func:`create_sample_multi_security_events`: 2022 has a net capital
    **loss**, so its dividend income triggers the report's 25% cross-category
    offset, and the two-bucket savings base section renders. Foreign tax is shown
    so the *deducción por doble imposición* note appears.
    """
    return {
        2021: SavingsIncomeYear(
            year=2021,
            dividends_eur=Decimal("180.00"),
            interest_eur=Decimal("20.00"),
            foreign_tax_eur=Decimal("27.00"),
        ),
        2022: SavingsIncomeYear(
            year=2022,
            dividends_eur=Decimal("240.00"),
            interest_eur=Decimal("10.00"),
            foreign_tax_eur=Decimal("36.00"),
        ),
    }


def create_sample_dividends_by_symbol() -> dict[str, Decimal]:
    """Demo gross dividends (EUR) per security symbol for the portfolio dashboard.

    Only the income-paying names in the sample pay dividends (DT and the GBP Shell
    position); the growth names (TSLA/NVDA/ADBE) pay none. Informational — feeds
    the per-security dividends column in the holdings table.
    """
    return {"DT": Decimal("300.00"), "SHEL": Decimal("150.00")}


def create_sample_espp_map() -> dict[date, tuple[Decimal, Decimal]]:
    """ESPP purchase map ``{purchase_date: (FMV_usd, purchase_price_usd)}`` for the
    DT 'ESPP Buy' dates in the sample.

    The employee pays 85% of FMV (a 15% ESPP discount). Selling within 3 years
    makes that discount taxable salary (Art. 42.3.f LIRPF), which the report's ESPP
    3-year analysis flags — the sample's early DT sells exercise that path.
    """
    fmv_by_date = {
        date(2020, 11, 27): Decimal("38.42"),
        date(2021, 5, 28): Decimal("51.74"),
        date(2021, 11, 26): Decimal("62.97"),
        date(2022, 5, 27): Decimal("38.19"),
    }
    return {
        d: (fmv, (fmv * Decimal("0.85")).quantize(Decimal("0.01")))
        for d, fmv in fmv_by_date.items()
    }


def create_sample_events_with_ecb_rates() -> list[StockEvent]:
    """
    Create sample events WITHOUT FX rates - they will be fetched from ECB automatically.
    This demonstrates the automatic rate fetching feature.
    """
    return [
        # 2020
        StockEvent(
            date(2020, 11, 27), EventType.BUY, Decimal("50"), Decimal("38.42"), notes="ESPP Buy"
        ),
        # 2021
        StockEvent(
            date(2021, 2, 3), EventType.SELL, Decimal("50"), Decimal("48.85"), notes="Manual Sell"
        ),
        StockEvent(
            date(2021, 5, 17), EventType.VEST, Decimal("30"), Decimal("46.68"), notes="RSU Vest"
        ),
        StockEvent(
            date(2021, 5, 17),
            EventType.SELL,
            Decimal("25"),
            Decimal("44.82"),
            notes="RSU Sell (sell-to-cover)",
        ),
        StockEvent(
            date(2021, 5, 17), EventType.SELL, Decimal("2"), Decimal("46.22"), notes="RSU Sell"
        ),
        StockEvent(
            date(2021, 5, 28), EventType.BUY, Decimal("50"), Decimal("51.74"), notes="ESPP Buy"
        ),
        StockEvent(
            date(2021, 6, 15),
            EventType.BUY,
            Decimal("20"),
            Decimal("55.00"),
            broker="Revolut",
            notes="Direct Purchase",
        ),
        StockEvent(
            date(2021, 8, 16), EventType.VEST, Decimal("10"), Decimal("63.65"), notes="RSU Vest"
        ),
        StockEvent(
            date(2021, 8, 16),
            EventType.SELL,
            Decimal("5"),
            Decimal("61.25"),
            notes="RSU Sell (sell-to-cover)",
        ),
        StockEvent(
            date(2021, 9, 20),
            EventType.BUY,
            Decimal("15"),
            Decimal("62.50"),
            broker="Revolut",
            notes="Direct Purchase",
        ),
        StockEvent(
            date(2021, 11, 15), EventType.VEST, Decimal("10"), Decimal("70.68"), notes="RSU Vest"
        ),
        StockEvent(
            date(2021, 11, 16), EventType.SELL, Decimal("5"), Decimal("69.28"), notes="RSU Sell"
        ),
        StockEvent(
            date(2021, 11, 26), EventType.BUY, Decimal("100"), Decimal("62.97"), notes="ESPP Buy"
        ),
        # 2022
        StockEvent(
            date(2022, 2, 10),
            EventType.SELL,
            Decimal("25"),
            Decimal("45.00"),
            broker="Revolut",
            notes="Revolut Sell",
        ),
        StockEvent(
            date(2022, 5, 27), EventType.BUY, Decimal("105"), Decimal("38.19"), notes="ESPP Buy"
        ),
        StockEvent(
            date(2022, 6, 1), EventType.SELL, Decimal("205"), Decimal("39.15"), notes="Manual Sell"
        ),
    ]


def create_sample_crypto_events() -> dict[str, list["StockEvent"]]:
    """Sample crypto trades, one FIFO queue per coin (crypto demo).

    Mirrors the per-coin layout produced by ``trades_to_events_by_coin``: each
    ``StockEvent`` holds the unit price in the (stablecoin ≈ USD) quote asset via
    ``price_usd`` and a manual ``fx_rate`` so the demo runs **fully offline** — no
    ECB network call. The set exercises the engine's main paths:

    * **BTC** — a buy, a partial sell at a gain, then the rest sold next year.
    * **ETH** — bought high, sold the following year at a loss (so the combined
      savings base shows offsetting against the gains).
    * **SOL** — bought on two exchanges (Binance cheap, Pionex dearer); a partial
      sell shows FIFO consuming the cheapest lot first and leaves an open lot.

    ``notes`` follow the ``"<source> <SIDE> <BASE>/<QUOTE>"`` convention the real
    parser emits, and SELL events carry a small fee in the quote asset.
    """
    return {
        "BTC": [
            StockEvent(
                date(2024, 2, 12),
                EventType.BUY,
                Decimal("0.50"),
                Decimal("48000"),
                fx_rate=Decimal("0.93"),
                notes="Binance BUY BTC/USDT",
            ),
            StockEvent(
                date(2024, 11, 8),
                EventType.SELL,
                Decimal("0.20"),
                Decimal("72000"),
                fx_rate=Decimal("0.92"),
                fees_usd=Decimal("14.40"),
                notes="Binance SELL BTC/USDT",
            ),
            StockEvent(
                date(2025, 3, 3),
                EventType.SELL,
                Decimal("0.30"),
                Decimal("84000"),
                fx_rate=Decimal("0.91"),
                fees_usd=Decimal("25.20"),
                notes="Binance SELL BTC/USDT",
            ),
        ],
        "ETH": [
            StockEvent(
                date(2024, 3, 20),
                EventType.BUY,
                Decimal("4"),
                Decimal("3600"),
                fx_rate=Decimal("0.92"),
                notes="Pionex BUY ETH/USDT",
            ),
            StockEvent(
                date(2025, 6, 10),
                EventType.SELL,
                Decimal("4"),
                Decimal("2500"),
                fx_rate=Decimal("0.88"),
                fees_usd=Decimal("10.00"),
                notes="Pionex SELL ETH/USDT",
            ),
        ],
        "SOL": [
            StockEvent(
                date(2024, 1, 15),
                EventType.BUY,
                Decimal("50"),
                Decimal("95"),
                fx_rate=Decimal("0.91"),
                notes="Binance BUY SOL/USDC",
            ),
            StockEvent(
                date(2024, 9, 1),
                EventType.BUY,
                Decimal("50"),
                Decimal("145"),
                fx_rate=Decimal("0.90"),
                notes="Pionex BUY SOL/USDT",
            ),
            StockEvent(
                date(2025, 4, 22),
                EventType.SELL,
                Decimal("60"),
                Decimal("170"),
                fx_rate=Decimal("0.89"),
                fees_usd=Decimal("10.20"),
                notes="Binance SELL SOL/USDT",
            ),
        ],
    }
