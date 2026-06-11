"""
Market data fetching module.

Handles all Yahoo Finance API interactions: live prices, historical quotes,
SMA calculations, and peer group normalized returns.
"""

import datetime
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import requests


def fetch_latest_price(ticker: str) -> Decimal | None:
    """Fetch the latest stock price from Yahoo Finance public API."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            return Decimal(str(price))
    except Exception as e:
        print(f"Warning: Failed to fetch live price for {ticker} from Yahoo Finance: {e}")
    return None


def fetch_historical_market_data(
    ticker: str, start_date: date
) -> tuple[list[dict[str, Any]], float, float, float, dict[str, str], dict[str, str]]:
    """Fetch daily historical stock prices starting 300 days before the first transaction date to today, calculating 50 & 200 SMA."""
    quotes: list[dict[str, Any]] = []
    current_price = 0.0
    sma50_val = 0.0
    sma200_val = 0.0
    signal = {"en": "Neutral", "es": "Neutral"}
    advice = {
        "en": "No market trend data available.",
        "es": "No hay datos de tendencia de mercado disponibles.",
    }

    try:
        # Fetch starting 300 days before the first transaction to allow SMA200 calculation on day 1
        fetch_start = start_date - timedelta(days=300)
        period1 = int(
            datetime.datetime.combine(fetch_start, datetime.time.min)
            .replace(tzinfo=datetime.timezone.utc)
            .timestamp()
        )
        period2 = int(
            datetime.datetime.combine(date.today() + timedelta(days=1), datetime.time.min)
            .replace(tzinfo=datetime.timezone.utc)
            .timestamp()
        )

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={period1}&period2={period2}&interval=1d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r.status_code != 200:
            # Fallback to standard 5y range
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5y&interval=1d"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)

        if r.status_code == 200:
            result = r.json()["chart"]["result"][0]
            timestamps = result["timestamp"]
            closes = result["indicators"]["quote"][0]["close"]

            raw_quotes: list[dict[str, Any]] = []
            for t, c in zip(timestamps, closes, strict=False):
                if c is not None:
                    dt_str = (
                        datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc)
                        .date()
                        .isoformat()
                    )
                    raw_quotes.append({"date": dt_str, "close": float(c)})

            # Calculate SMAs
            for idx, q in enumerate(raw_quotes):
                close_val = q["close"]

                # 50 SMA
                if idx >= 49:
                    sma50 = sum(x["close"] for x in raw_quotes[idx - 49 : idx + 1]) / 50.0
                else:
                    sma50 = None

                # 200 SMA
                if idx >= 199:
                    sma200 = sum(x["close"] for x in raw_quotes[idx - 199 : idx + 1]) / 200.0
                else:
                    sma200 = None

                quotes.append(
                    {"date": q["date"], "close": close_val, "sma50": sma50, "sma200": sma200}
                )

            if quotes:
                latest_quote = quotes[-1]
                # Try to get the actual real-time price first, otherwise fallback to the last close
                live_price = fetch_latest_price(ticker)
                current_price = (
                    float(live_price) if live_price is not None else latest_quote["close"]
                )

                # Determine signal
                if latest_quote["sma50"] and latest_quote["sma200"]:
                    s50 = latest_quote["sma50"]
                    s200 = latest_quote["sma200"]
                    sma50_val = s50
                    sma200_val = s200

                    if current_price > s50 and current_price > s200:
                        signal = {
                            "en": "Bullish (Strong Growth Trend)",
                            "es": "Alcista (Fuerte tendencia de crecimiento)",
                        }
                        advice = {
                            "en": f"The stock is performing strongly, trading above both the 50-day (${s50:.2f}) and 200-day (${s200:.2f}) market averages. The overall market momentum is upward. Holding is a solid option, but this is also a favorable window to sell in small increments to lock in gains.",
                            "es": f"La acción está teniendo un gran rendimiento, cotizando por encima de las medias de 50 días (${s50:.2f}) y de 200 días (${s200:.2f}). El impulso general es alcista. Mantener es una opción sólida, pero también es una ventana favorable para vender de forma incremental y asegurar ganancias.",
                        }
                    elif current_price < s50 and current_price < s200:
                        signal = {"en": "Bearish (Down Trend)", "es": "Bajista (Tendencia bajista)"}
                        advice = {
                            "en": f"The stock price is below both the 50-day (${s50:.2f}) and 200-day (${s200:.2f}) averages, indicating a clear downward trend. Holding single stock during a downtrend increases downside risk. Consider diversifying by selling some shares.",
                            "es": f"El precio de la acción está por debajo de las medias de 50 días (${s50:.2f}) y de 200 días (${s200:.2f}), indicando una clara tendencia bajista. Mantener una sola acción durante una tendencia bajista aumenta los riesgos. Considere diversificar vendiendo algunas acciones.",
                        }
                    else:
                        signal = {
                            "en": "Consolidating (Ranging Trend)",
                            "es": "Consolidando (Tendencia lateral)",
                        }
                        advice = {
                            "en": "The stock is currently trading between its short-term and long-term averages. Price is consolidating. Monitor closely for a breakout.",
                            "es": "La acción cotiza actualmente entre sus medias a corto y largo plazo. El precio se está consolidando. Monitoree de cerca para detectar una ruptura.",
                        }
    except Exception as e:
        print(f"Warning: Failed to fetch historical data for {ticker}: {e}")

    return quotes, current_price, sma50_val, sma200_val, signal, advice


def fetch_peers_data(peers: list[str], start_date: date) -> dict[str, list[dict[str, Any]]]:
    """Fetch daily quotes for peers and return normalized performance starting from first date."""
    peers_quotes: dict[str, list[dict[str, Any]]] = {}

    # We query the exact range since start_date to today
    period1 = int(
        datetime.datetime.combine(start_date, datetime.time.min)
        .replace(tzinfo=datetime.timezone.utc)
        .timestamp()
    )
    period2 = int(
        datetime.datetime.combine(date.today() + timedelta(days=1), datetime.time.min)
        .replace(tzinfo=datetime.timezone.utc)
        .timestamp()
    )

    for peer in peers:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{peer}?period1={period1}&period2={period2}&interval=1d"
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if r.status_code == 200:
                result = r.json()["chart"]["result"][0]
                timestamps = result.get("timestamp", [])
                closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])

                raw: list[dict[str, Any]] = []
                for t, c in zip(timestamps, closes, strict=False):
                    if c is not None:
                        dt_str = (
                            datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc)
                            .date()
                            .isoformat()
                        )
                        raw.append({"date": dt_str, "close": float(c)})

                if raw:
                    first_close = raw[0]["close"]
                    # Calculate percentage return relative to first day
                    normalized: list[dict[str, Any]] = []
                    for q in raw:
                        pct = ((q["close"] - first_close) / first_close) * 100.0
                        normalized.append({"date": q["date"], "pct": pct, "price": q["close"]})
                    peers_quotes[peer] = normalized
        except Exception as e:
            print(f"Warning: Failed to fetch peer comparison for {peer}: {e}")

    return peers_quotes


def fetch_company_name(ticker: str) -> str | None:
    """Fetch the company name for a given ticker from Yahoo Finance search API."""
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}&quotesCount=1"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            quotes = data.get("quotes", [])
            if quotes:
                # Try longname, then shortname, then symbol
                name = quotes[0].get("longname") or quotes[0].get("shortname")
                if name:
                    return str(name).strip()
    except Exception as e:
        print(f"Warning: Failed to fetch company name for {ticker}: {e}")
    return None
