"""
Sample data for testing the tax engine.

Provides functions to create sample stock events with and without FX rates.
"""

from datetime import date
from decimal import Decimal

from .models import EventType, StockEvent


def create_sample_events_with_manual_fx() -> list[StockEvent]:
    """
    Create sample events with manually specified FX rates (from the original spreadsheet).
    This serves as a test case to verify the engine works correctly.
    """
    return [
        # 2020
        StockEvent(date(2020, 11, 27), EventType.BUY, Decimal("50"), Decimal("38.42"),
                   fx_rate=Decimal("0.8388"), notes="ESPP Buy"),
        # 2021
        StockEvent(date(2021, 2, 3), EventType.SELL, Decimal("50"), Decimal("48.85"),
                   fx_rate=Decimal("0.8322"), notes="Manual Sell"),
        StockEvent(date(2021, 5, 17), EventType.VEST, Decimal("30"), Decimal("46.68"),
                   fx_rate=Decimal("0.8235"), notes="RSU Vest"),
        StockEvent(date(2021, 5, 17), EventType.SELL, Decimal("25"), Decimal("44.82"),
                   fx_rate=Decimal("0.8235"), notes="RSU Sell (sell-to-cover)"),
        StockEvent(date(2021, 5, 17), EventType.SELL, Decimal("2"), Decimal("46.22"),
                   fx_rate=Decimal("0.8235"), notes="RSU Sell"),
        StockEvent(date(2021, 5, 28), EventType.BUY, Decimal("50"), Decimal("51.74"),
                   fx_rate=Decimal("0.8236"), notes="ESPP Buy"),
        StockEvent(date(2021, 8, 16), EventType.VEST, Decimal("10"), Decimal("63.65"),
                   fx_rate=Decimal("0.8495"), notes="RSU Vest"),
        StockEvent(date(2021, 8, 16), EventType.SELL, Decimal("5"), Decimal("61.25"),
                   fx_rate=Decimal("0.8495"), notes="RSU Sell (sell-to-cover)"),
        StockEvent(date(2021, 11, 15), EventType.VEST, Decimal("10"), Decimal("70.68"),
                   fx_rate=Decimal("0.8738"), notes="RSU Vest"),
        StockEvent(date(2021, 11, 16), EventType.SELL, Decimal("5"), Decimal("69.28"),
                   fx_rate=Decimal("0.8797"), notes="RSU Sell"),
        StockEvent(date(2021, 11, 26), EventType.BUY, Decimal("100"), Decimal("62.97"),
                   fx_rate=Decimal("0.8857"), notes="ESPP Buy"),
        # 2022
        StockEvent(date(2022, 5, 27), EventType.BUY, Decimal("105"), Decimal("38.19"),
                   fx_rate=Decimal("0.9327"), notes="ESPP Buy"),
        StockEvent(date(2022, 6, 1), EventType.SELL, Decimal("205"), Decimal("39.15"),
                   fx_rate=Decimal("0.9335"), notes="Manual Sell"),
    ]


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
            date(2022, 5, 27), EventType.BUY, Decimal("105"), Decimal("38.19"), notes="ESPP Buy"
        ),
        StockEvent(
            date(2022, 6, 1), EventType.SELL, Decimal("205"), Decimal("39.15"), notes="Manual Sell"
        ),
    ]
