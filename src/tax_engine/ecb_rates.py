"""
ECB Exchange Rate Fetcher.

Fetches USD/EUR exchange rates from the European Central Bank
Statistical Data Warehouse API.
"""

import json
import os
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import certifi

if TYPE_CHECKING:
    from .models import StockEvent

# Foreign currencies for which the ECB publishes daily EUR reference rates
# (the rates the Spanish Agencia Tributaria accepts). EUR is handled separately
# as the 1:1 base. Anything outside this set cannot be converted via the ECB.
ECB_CURRENCIES = frozenset(
    {
        "USD", "JPY", "BGN", "CZK", "DKK", "GBP", "HUF", "PLN", "RON", "SEK",
        "CHF", "ISK", "NOK", "TRY", "AUD", "BRL", "CAD", "CNY", "HKD", "IDR",
        "ILS", "INR", "KRW", "MXN", "MYR", "NZD", "PHP", "SGD", "THB", "ZAR",
    }
)


class ECBRateFetcher:
    """
    Fetches USD/EUR exchange rates from the European Central Bank.

    Uses the ECB Statistical Data Warehouse API to get official daily rates.
    These are the rates accepted by the Spanish Agencia Tributaria.

    Resolved rates are persisted to a JSON file on disk so repeated runs do
    not re-hit the ECB API and so reports can be generated offline once the
    relevant rates have been fetched at least once.
    """

    # ECB API endpoint for <currency>/EUR daily exchange rates.
    ECB_API_URL = (
        "https://data-api.ecb.europa.eu/service/data/EXR/D.{currency}.EUR.SP00.A"
        "?startPeriod={start}&endPeriod={end}&format=structurespecificdata"
    )

    # On-disk cache location. Override with the ECB_RATE_CACHE env var.
    CACHE_FILE = Path(os.environ.get("ECB_RATE_CACHE", ".ecb_rate_cache.json"))

    # Cache for rates ((currency, date) -> <currency>/EUR rate)
    _rate_cache: dict[tuple[str, date], Decimal] = {}
    _disk_cache_loaded: bool = False

    @staticmethod
    def is_supported(currency: str | None) -> bool:
        """Whether a currency can be converted to EUR (an ECB currency, or EUR)."""
        cur = (currency or "").strip().upper()
        return cur == "EUR" or cur in ECB_CURRENCIES

    @classmethod
    def _load_disk_cache(cls) -> None:
        """Load persisted rates from disk into the in-memory cache (once).

        Accepts both the current per-currency format (``{currency: {iso: rate}}``)
        and the legacy USD-only flat format (``{iso: rate}``), so old cache files
        keep working.
        """
        if cls._disk_cache_loaded:
            return
        cls._disk_cache_loaded = True
        try:
            with open(cls.CACHE_FILE, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if not isinstance(raw, dict):
            return
        for key, value in raw.items():
            if isinstance(value, dict):  # per-currency: {currency: {iso: rate}}
                currency = str(key).strip().upper()
                for iso_date, rate_str in value.items():
                    try:
                        cls._rate_cache.setdefault(
                            (currency, date.fromisoformat(iso_date)), Decimal(rate_str)
                        )
                    except (ValueError, TypeError):
                        continue
            else:  # legacy flat USD format: {iso: rate}
                try:
                    cls._rate_cache.setdefault(
                        ("USD", date.fromisoformat(str(key))), Decimal(value)
                    )
                except (ValueError, TypeError):
                    continue

    @classmethod
    def _save_disk_cache(cls) -> None:
        """Persist the in-memory cache to disk (best-effort), nested per currency."""
        try:
            nested: dict[str, dict[str, str]] = {}
            for (currency, d), rate in cls._rate_cache.items():
                nested.setdefault(currency, {})[d.isoformat()] = str(rate)
            with open(cls.CACHE_FILE, "w", encoding="utf-8") as fh:
                json.dump(nested, fh, indent=0, sort_keys=True)
        except OSError:
            pass

    @classmethod
    def _fetch_rates_for_period(
        cls, start_date: date, end_date: date, currency: str = "USD"
    ) -> dict[date, Decimal]:
        """Fetch <currency>/EUR rates from the ECB API for a date range."""
        url = cls.ECB_API_URL.format(
            currency=currency, start=start_date.isoformat(), end=end_date.isoformat()
        )

        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(url, timeout=30, context=ssl_context) as response:
                xml_data = response.read()
        except Exception as e:
            raise RuntimeError(f"Failed to fetch ECB rates: {e}") from e

        # Parse the XML response
        root = ET.fromstring(xml_data)

        # ECB uses namespaces in their XML

        rates = {}

        # Find all Obs (observation) elements
        for obs in root.iter():
            if obs.tag.endswith("}Obs") or obs.tag == "Obs":
                time_period = obs.get("TIME_PERIOD")
                obs_value = obs.get("OBS_VALUE")

                if time_period and obs_value:
                    rate_date = date.fromisoformat(time_period)
                    # ECB publishes EUR/<currency> (units of the currency per EUR);
                    # we need <currency>/EUR (EUR per unit), i.e. the inverse.
                    eur_per_unit = Decimal(obs_value)
                    unit_to_eur = (Decimal("1") / eur_per_unit).quantize(
                        Decimal("0.0001"), ROUND_HALF_UP
                    )
                    rates[rate_date] = unit_to_eur

        return rates

    @classmethod
    def get_rate(cls, target_date: date, currency: str = "USD") -> Decimal:
        """
        Get the <currency>/EUR exchange rate for a specific date.

        EUR returns 1 (no conversion). If the target date is a weekend or holiday
        (no rate published), returns the most recent available rate before it.
        """
        cur = (currency or "USD").strip().upper()
        if cur == "EUR":
            return Decimal("1")
        if cur not in ECB_CURRENCIES:
            raise ValueError(f"Currency {cur!r} is not an ECB reference currency.")

        cls._load_disk_cache()

        # Check cache first
        if (cur, target_date) in cls._rate_cache:
            return cls._rate_cache[(cur, target_date)]

        # Fetch a range around the target date to handle weekends/holidays
        # Go back 14 days to handle extended holiday periods (e.g., Christmas + New Year)
        start_date = target_date - timedelta(days=14)
        end_date = target_date

        rates = cls._fetch_rates_for_period(start_date, end_date, cur)
        cls._rate_cache.update({(cur, d): r for d, r in rates.items()})

        # Find the rate for target date or most recent before it
        if target_date in rates:
            cls._save_disk_cache()
            return rates[target_date]

        # Find the most recent rate before target date
        available_dates = sorted([d for d in rates if d <= target_date], reverse=True)
        if available_dates:
            closest_date = available_dates[0]
            # Cache this lookup for the target date too
            cls._rate_cache[(cur, target_date)] = rates[closest_date]
            cls._save_disk_cache()
            return rates[closest_date]

        raise ValueError(f"No ECB rate available for {cur} or before {target_date}")

    @classmethod
    def get_rates_bulk(
        cls, dates: list[date], currency: str = "USD"
    ) -> dict[date, Decimal]:
        """
        Fetch rates for multiple dates efficiently in a single API call.

        Returns a dict mapping each requested date to its <currency>/EUR rate.
        """
        if not dates:
            return {}

        cur = (currency or "USD").strip().upper()
        if cur == "EUR":
            return dict.fromkeys(dates, Decimal("1"))
        if cur not in ECB_CURRENCIES:
            raise ValueError(f"Currency {cur!r} is not an ECB reference currency.")

        cls._load_disk_cache()

        # Skip the network call entirely if every requested date is already cached.
        if all((cur, d) in cls._rate_cache for d in dates):
            return {d: cls._rate_cache[(cur, d)] for d in dates}

        # Find date range
        min_date = min(dates) - timedelta(days=10)  # Buffer for weekends
        max_date = max(dates)

        # Fetch all rates in range
        rates = cls._fetch_rates_for_period(min_date, max_date, cur)
        cls._rate_cache.update({(cur, d): r for d, r in rates.items()})

        # Map each requested date to its rate (or nearest previous)
        result = {}
        sorted_available = sorted(rates.keys())

        for target_date in dates:
            if target_date in rates:
                result[target_date] = rates[target_date]
            else:
                # Find nearest previous date
                for d in reversed(sorted_available):
                    if d <= target_date:
                        result[target_date] = rates[d]
                        cls._rate_cache[(cur, target_date)] = rates[d]
                        break
                else:
                    raise ValueError(f"No ECB rate available for or before {target_date}")

        cls._save_disk_cache()
        return result

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the in-memory rate cache (does not delete the disk cache)."""
        cls._rate_cache.clear()
        cls._disk_cache_loaded = False


def prefetch_ecb_rates(events: list["StockEvent"]) -> None:
    """
    Pre-fetch ECB rates for all events that don't have fx_rate specified.

    This is more efficient than fetching one at a time, as it makes one API call
    per currency for the entire date range. Events are grouped by their currency
    (defaulting to USD); EUR needs no fetch, and any currency the ECB does not
    publish is skipped with a warning (those rows will fail later if relied upon).
    """
    dates_by_currency: dict[str, list[date]] = defaultdict(list)
    for e in events:
        if e.fx_rate is None:
            dates_by_currency[(e.currency or "USD").strip().upper()].append(e.event_date)

    for currency, dates in dates_by_currency.items():
        if currency == "EUR":
            continue
        if currency not in ECB_CURRENCIES:
            print(
                f"Warning: {len(dates)} event(s) in unsupported currency {currency!r} "
                "cannot be converted via the ECB; skipping prefetch."
            )
            continue
        print(f"Fetching ECB {currency} rates for {len(dates)} dates...")
        ECBRateFetcher.get_rates_bulk(dates, currency)
    if dates_by_currency:
        print("Done.")
