#!/usr/bin/env python3
"""
Premium Interactive Financial & Tax Analysis Dashboard Generator.
Generates an interactive HTML dashboard containing advanced financial insights,
historical market trends, moving averages, active ESPP countdown clocks,
and a Real-Time Sell Simulator & Exchange Rate Sensitivity Planner.
Includes a Privacy Masking Mode and a tabbed navigation interface.
"""

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

# Add src directory to path
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.append(str(src_path))

from tax_engine import EventType, prefetch_ecb_rates, run_portfolio
from tax_engine.cli_main import (
    auto_detect_sell_to_cover,
    build_espp_purchase_map,
    load_events_from_excel,
    load_options_stock_events,
    load_orders_from_excel,
    load_rsu_events,
    load_savings_income,
    load_security_config,
)
from tax_engine.dashboard_helpers import (
    build_chart_data,
    build_dt_normalized_returns,
    build_fx_history,
    build_sales_decomposition,
    build_securities_chart,
    build_unsold_lots_and_espp_tracker,
    calculate_espp_savings,
    calculate_rsu_hold_delta,
    enrich_hist_quotes_with_avg_cost,
)
from tax_engine.market_data import (
    fetch_company_name,
    fetch_historical_market_data,
    fetch_peers_data,
)
from tax_engine.revolut_parser import (
    load_revolut_dividends_by_symbol,
    load_revolut_trade_events,
)
from tax_engine.securities import load_securities_config

DEFAULT_PEERS = ["DDOG", "ESTC"]


def load_peers_map(input_dir: Path) -> dict[str, list[str]]:
    """Load peer tickers map from input/peers.json.
    Can be a flat list (defaults to all/primary) or a dict mapping tickers to peers."""
    peers_path = input_dir / "peers.json"
    if peers_path.exists():
        try:
            data = json.loads(peers_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                result = {}
                for k, v in data.items():
                    if isinstance(v, list):
                        result[k.upper()] = [str(t).strip().upper() for t in v if str(t).strip()]
                return result
            elif isinstance(data, list):
                tickers = [str(t).strip().upper() for t in data if str(t).strip()]
                if tickers:
                    return {"": tickers}
        except Exception:
            pass
    return {"": DEFAULT_PEERS}


def load_peers_config(input_dir: Path, ticker: str | None = None) -> list[str]:
    """Load peer tickers from input/peers.json; fall back to DEFAULT_PEERS."""
    peers_map = load_peers_map(input_dir)
    if ticker:
        return peers_map.get(ticker.upper(), peers_map.get("", DEFAULT_PEERS))
    return peers_map.get("", DEFAULT_PEERS)


def load_ticker_config(input_dir: Path) -> tuple[str | None, str | None]:
    """Load main ticker and optional company name from input/ticker.json or input/ticker.txt if they exist."""
    json_path = input_dir / "ticker.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                ticker = data.get("ticker", "").strip().upper() or None
                company_name = data.get("company_name", "").strip() or None
                return ticker, company_name
            elif isinstance(data, str):
                return data.strip().upper(), None
        except Exception:
            pass
    txt_path = input_dir / "ticker.txt"
    if txt_path.exists():
        try:
            return txt_path.read_text(encoding="utf-8").strip().upper(), None
        except Exception:
            pass
    return None, None


def detect_ticker(input_dir: Path) -> str | None:
    """Detect the stock ticker symbol from any sheet in BenefitHistory.xlsx."""
    excel_path = input_dir / "espp" / "BenefitHistory.xlsx"
    if not excel_path.exists():
        return None
    try:
        import pandas as pd
        xl = pd.ExcelFile(excel_path)
        for sheet in xl.sheet_names:
            df = xl.parse(sheet)
            if "Symbol" in df.columns:
                symbols = df["Symbol"].dropna().unique()
                if len(symbols) > 0:
                    return str(symbols[0]).strip()
    except Exception:
        pass
    return None


def prompt_include_espp() -> bool:
    """Ask whether to include ESPP analysis. Defaults to yes when non-interactive."""
    if not sys.stdin.isatty():
        return True
    response = input(
        "ESPP data detected. Include ESPP analysis in the dashboard? [Y/n]: "
    ).strip().lower()
    return response in ("", "y", "yes")


def main():
    parser = argparse.ArgumentParser(description="Generate Stock & FX Dashboard")
    parser.add_argument(
        "--current-price",
        type=float,
        default=None,
        help="Override current stock price in USD. If omitted, live price is fetched from Yahoo Finance.",
    )
    espp_group = parser.add_mutually_exclusive_group()
    espp_group.add_argument(
        "--include-espp",
        action="store_true",
        help="Include ESPP analysis without prompting (if ESPP data is present).",
    )
    espp_group.add_argument(
        "--skip-espp",
        action="store_true",
        help="Exclude ESPP analysis from the dashboard.",
    )
    parser.add_argument(
        "--peers",
        nargs="+",
        metavar="TICKER",
        help="Peer tickers for the comparison chart (e.g. --peers DDOG ESTC AAPL). "
             "Overrides input/peers.json and built-in defaults.",
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Main stock ticker symbol (e.g. DT, DDOG). Overrides config files and auto-detection.",
    )
    parser.add_argument(
        "--company-name",
        type=str,
        default=None,
        help="Main stock company name (e.g. Dynatrace, Datadog). Overrides config files and API lookup.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Generate charts & dashboard using demo (sample) data.",
    )
    parser.add_argument(
        "--all-securities",
        action="store_true",
        help="With --demo, use the multi-security sample (DT + TSLA/NVDA + a GBP "
        "position) so the per-security portfolio breakdown chart is shown.",
    )
    args = parser.parse_args()

    input_dir = Path("input")

    # Multi-security config (input/securities.json). Its `primary` designates the
    # default/employer security shown first; `include` is honored by run_portfolio.
    sec_config = load_securities_config(input_dir)

    # Resolve main ticker (CLI > securities.json primary > ticker.json > detection > DT)
    ticker = None
    config_company_name = None
    if args.demo:
        ticker = "DT"
        print("Demo mode: Using ticker 'DT'")
    elif args.ticker:
        ticker = args.ticker.strip().upper()
        print(f"Using ticker from command line argument: {ticker}")

    if not ticker and sec_config.primary:
        ticker = sec_config.primary
        print(f"Using primary security from input/securities.json: {ticker}")

    if not ticker:
        ticker, config_company_name = load_ticker_config(input_dir)
        if ticker:
            print(f"Loaded ticker from configuration file: {ticker}")

    if not ticker:
        ticker = detect_ticker(input_dir)
        if ticker:
            print(f"Detected stock ticker from data: {ticker}")

    if not ticker:
        ticker = "DT"
        print(f"Could not detect ticker from data; falling back to: {ticker}")

    # Resolve company name
    company_name = None
    if args.demo:
        company_name = "Dynatrace (Demo)"
        print("Demo mode: Using company name 'Dynatrace (Demo)'")
    elif args.company_name:
        company_name = args.company_name.strip()
        print(f"Using company name from command line argument: {company_name}")

    if not company_name and config_company_name:
        company_name = config_company_name
        print(f"Using company name from configuration file: {company_name}")

    if not company_name:
        # Try fetching company name from Yahoo Finance
        company_name = fetch_company_name(ticker)
        if company_name:
            print(f"Fetched company name from Yahoo Finance: {company_name}")

    if not company_name:
        # Hardcoded fallback list
        COMPANY_MAP = {
            "DT": "Dynatrace",
            "DDOG": "Datadog",
            "ESTC": "Elastic",
            "AAPL": "Apple",
            "MSFT": "Microsoft",
            "GOOG": "Google",
            "GOOGL": "Google",
            "AMZN": "Amazon",
            "NFLX": "Netflix",
            "META": "Meta",
            "TSLA": "Tesla",
        }
        company_name = COMPANY_MAP.get(ticker, ticker)

    if not company_name:
        company_name = ticker

    if args.demo:
        include_espp = True
        print("Demo mode: ESPP analysis enabled.")
    else:
        # Decide whether to include ESPP analysis. Only ask when ESPP data exists.
        espp_present = bool(build_espp_purchase_map(input_dir))
        if not espp_present:
            include_espp = False
            print("No ESPP data detected; skipping ESPP analysis.")
        elif args.skip_espp:
            include_espp = False
        elif args.include_espp:
            include_espp = True
        else:
            include_espp = prompt_include_espp()

    # Load and process events (sec_config was loaded above for primary resolution)
    _, config_isin = load_security_config(input_dir)

    if args.demo:
        if args.all_securities:
            from tax_engine.sample_data import create_sample_multi_security_events
            portfolio_events = create_sample_multi_security_events()
        else:
            from tax_engine.sample_data import create_sample_events_with_manual_fx
            portfolio_events = create_sample_events_with_manual_fx()
            for e in portfolio_events:
                e.symbol = e.symbol or (ticker or "")
                if config_isin:
                    e.isin = e.isin or config_isin
        print("Demo mode: Loaded sample portfolio events.")
    else:
        espp_events = load_events_from_excel(input_dir) if include_espp else []
        sell_events = load_orders_from_excel(input_dir)

        rsu_dir = input_dir / "rsu"
        rsu_events = load_rsu_events(rsu_dir) if rsu_dir.exists() else []
        options_events = load_options_stock_events(input_dir)

        etrade_events = espp_events + sell_events + rsu_events + options_events
        for e in etrade_events:
            e.symbol = e.symbol or (ticker or "")
            if config_isin:
                e.isin = e.isin or config_isin

        revolut_all_events = load_revolut_trade_events(
            input_dir, isin=config_isin, symbol=ticker,
            all_securities=True, isin_map=sec_config.isin_map,
        )

        portfolio_events = etrade_events + revolut_all_events
        if not portfolio_events:
            print("No events found. Please make sure your Excel sheets and PDFs are in the input folder.")
            return

        if revolut_all_events:
            prefetch_ecb_rates(portfolio_events)

    # Auto-detect Sell-to-Cover vs Manual Sells
    if not args.demo:
        auto_detect_sell_to_cover(portfolio_events)

    # Run the portfolio engine
    portfolio = run_portfolio(portfolio_events, config=sec_config)

    # Resolve peer tickers: CLI flag > peers.json > built-in defaults
    if args.demo:
        peer_tickers = ["DDOG", "ESTC"]
    elif args.peers:
        peer_tickers = [t.strip().upper() for t in args.peers if t.strip()]
    else:
        peer_tickers = load_peers_config(input_dir)
    print(f"Peer tickers for comparison: {', '.join(peer_tickers)}")

    # We will build multi-security data mapping for all results
    multi_security_data = {}

    # Sort results by label so primary (often DT) gets processed and we have a deterministic list
    for r in portfolio.results:
        sec_ticker = r.security.label
        sec_isin = r.security.isin or ""
        sec_engine = r.engine

        # Sort events by date
        sec_events = [pe.event for pe in sec_engine.processed_events]
        sec_events.sort(key=lambda x: x.event_date)

        if not sec_events:
            continue

        sec_first_date = sec_events[0].event_date

        if sec_ticker == ticker:
            sec_company_name = company_name
        else:
            if args.demo:
                sec_company_name = f"{sec_ticker} (Demo)"
            else:
                sec_company = fetch_company_name(sec_ticker)
                sec_company_name = sec_company if sec_company else sec_ticker

        # Fetch Yahoo Finance Trend data or simulate it
        if args.demo:
            import math
            from datetime import timedelta
            start_date_mock = date(2020, 1, 1)
            end_date_mock = date.today()
            sec_hist_quotes = []
            curr = start_date_mock
            while curr <= end_date_mock:
                days_since_start = (curr - start_date_mock).days
                if sec_ticker == "TSLA":
                    price = 50.0 + 150.0 * (days_since_start / 800.0) + 40.0 * math.sin(days_since_start / 30.0)
                elif sec_ticker == "NVDA":
                    price = 20.0 + 380.0 * ((days_since_start / 1000.0) ** 2) + 15.0 * math.sin(days_since_start / 40.0)
                elif sec_ticker == "ADBE":
                    price = 300.0 + 100.0 * math.sin(days_since_start / 100.0)
                elif sec_ticker == "SHEL":
                    price = 10.0 + 15.0 * (days_since_start / 1000.0) + 2.0 * math.sin(days_since_start / 20.0)
                else: # DT
                    if days_since_start < 700:
                        price = 35.0 + 85.0 * (days_since_start / 700.0) + 10.0 * math.sin(days_since_start / 50.0)
                    else:
                        stable_phase = days_since_start - 700
                        price = 120.0 - 80.0 * min(1.0, stable_phase / 365.0) + 5.0 * math.sin(days_since_start / 100.0)
                price = round(max(5.0, price), 2)
                sec_hist_quotes.append({"date": curr.isoformat(), "close": float(price)})
                curr += timedelta(days=1)

            # Calculate SMAs
            for idx, q in enumerate(sec_hist_quotes):
                if idx >= 49:
                    q["sma50"] = sum(x["close"] for x in sec_hist_quotes[idx-49:idx+1]) / 50.0
                else:
                    q["sma50"] = None
                if idx >= 199:
                    q["sma200"] = sum(x["close"] for x in sec_hist_quotes[idx-199:idx+1]) / 200.0
                else:
                    q["sma200"] = None

            sec_live_p = sec_hist_quotes[-1]["close"]
            sec_sma50 = sec_hist_quotes[-1]["sma50"] or 0
            sec_sma200 = sec_hist_quotes[-1]["sma200"] or 0
            sec_signal = {
                "en": "Bullish (Strong Growth Trend)" if sec_live_p > sec_sma50 else "Bearish (Down Trend)",
                "es": "Alcista (Fuerte tendencia de crecimiento)" if sec_live_p > sec_sma50 else "Bajista (Tendencia bajista)"
            }
            sec_advice = {
                "en": f"Demo mode showing simulated growth and tax calculations for {sec_ticker}.",
                "es": f"Modo demo mostrando crecimiento simulado y cálculos de impuestos para {sec_ticker}."
            }
        else:
            sec_hist_quotes, sec_live_p, sec_sma50, sec_sma200, sec_signal, sec_advice = fetch_historical_market_data(sec_ticker, sec_first_date)

        # Resolve Current Price
        if sec_ticker == ticker and args.current_price is not None:
            sec_latest_price_usd = Decimal(str(args.current_price))
            if sec_sma50 > 0 and sec_sma200 > 0:
                p = float(sec_latest_price_usd)
                if p > sec_sma50 and p > sec_sma200:
                    sec_signal = {
                        "en": "Bullish (Strong Growth Trend)",
                        "es": "Alcista (Fuerte tendencia de crecimiento)"
                    }
                    sec_advice = {
                        "en": f"The manual price (${p:.2f}) is above both the 50-day (${sec_sma50:.2f}) and 200-day (${sec_sma200:.2f}) averages. Strong upward trend.",
                        "es": f"El precio manual (${p:.2f}) está por encima de las medias de 50 días (${sec_sma50:.2f}) y de 200 días (${sec_sma200:.2f}). Fuerte tendencia alcista."
                    }
                elif p < sec_sma50 and p < sec_sma200:
                    sec_signal = {
                        "en": "Bearish (Down Trend)",
                        "es": "Bajista (Tendencia bajista)"
                    }
                    sec_advice = {
                        "en": f"The manual price (${p:.2f}) is below both the 50-day (${sec_sma50:.2f}) and 200-day (${sec_sma200:.2f}) averages. Trend is downward.",
                        "es": f"El precio manual (${p:.2f}) está por debajo de las medias de 50 días (${sec_sma50:.2f}) y de 200 días (${sec_sma200:.2f}). La tendencia es bajista."
                    }
        else:
            if sec_live_p > 0:
                sec_latest_price_usd = Decimal(str(sec_live_p))
            else:
                sec_latest_price_usd = sec_events[-1].price_usd

        sec_latest_fx_rate = sec_events[-1].resolved_fx_rate
        sec_latest_price_eur = sec_latest_price_usd * sec_latest_fx_rate

        if sec_latest_fx_rate > 0:
            sec_avg_cost_usd = float(sec_engine.state.avg_cost_eur / sec_latest_fx_rate)
        else:
            sec_avg_cost_usd = 0.0

        enrich_hist_quotes_with_avg_cost(sec_hist_quotes, sec_engine.processed_events)

        # ESPP Exemption Calculations
        sec_saved_espp_discount = sec_lost_espp_discount = sec_total_espp_discount = Decimal("0")
        sec_espp_map = {}
        if sec_ticker == ticker:
            if args.demo:
                sec_espp_map = {
                    date(2020, 11, 27): (Decimal("45.20"), Decimal("38.42")),
                    date(2021, 5, 28): (Decimal("60.87"), Decimal("51.74")),
                    date(2021, 11, 26): (Decimal("74.08"), Decimal("62.97")),
                    date(2022, 5, 27): (Decimal("44.93"), Decimal("38.19")),
                }
                from tax_engine.cli_main import detect_espp_early_sales
                from tax_engine.ecb_rates import ECBRateFetcher
                espp_discounts = {}
                for acq_date, (fmv, price) in sec_espp_map.items():
                    qty = Decimal("50") if acq_date in (date(2020, 11, 27), date(2021, 5, 28)) else (
                        Decimal("100") if acq_date == date(2021, 11, 26) else Decimal("105")
                    )
                    discount_usd = (fmv - price) * qty
                    fx_rate = ECBRateFetcher.get_rate(acq_date)
                    discount_eur = (discount_usd * fx_rate).quantize(Decimal("0.01"))
                    espp_discounts[acq_date.year] = espp_discounts.get(acq_date.year, Decimal("0")) + discount_eur

                espp_early_sales, _ = detect_espp_early_sales(sec_engine.processed_events, sec_espp_map)
                sec_total_espp_discount = sum(espp_discounts.values())
                sec_lost_espp_discount = sum(espp_early_sales.values()) if espp_early_sales else Decimal("0")
                sec_saved_espp_discount = max(Decimal("0"), sec_total_espp_discount - sec_lost_espp_discount)
            elif include_espp:
                sec_saved_espp_discount, sec_lost_espp_discount, sec_total_espp_discount, sec_espp_map = calculate_espp_savings(input_dir, sec_engine)

        # RSU hold vs vest
        sec_rsu_delta, sec_rsu_sell_on_vest_value, sec_rsu_hold_value = calculate_rsu_hold_delta(
            sec_events, sec_engine, float(sec_latest_price_eur)
        )

        # Build data payloads
        sec_acquisitions_by_date = {}
        for pe in sec_engine.processed_events:
            event = pe.event
            if event.event_type in (EventType.VEST, EventType.BUY, EventType.EXERCISE):
                sec_acquisitions_by_date[event.event_date] = event

        sec_sales_decomposition = build_sales_decomposition(sec_engine, sec_acquisitions_by_date)
        sec_chart_data = build_chart_data(sec_events)
        sec_fx_history = build_fx_history(sec_events)

        today_dt = date.today()
        sec_current_reference_date = today_dt if today_dt.year >= 2026 else sec_events[-1].event_date
        sec_unsold_lots_data, sec_espp_active_lots = build_unsold_lots_and_espp_tracker(sec_engine, sec_espp_map, sec_current_reference_date)

        sec_at_risk_espp_discount = Decimal(str(
            sum(lot["discount_at_risk"] for lot in sec_espp_active_lots if lot["days_left"] > 0)
        ))
        sec_secured_espp_discount = max(Decimal("0"), sec_saved_espp_discount - sec_at_risk_espp_discount)
        if sec_secured_espp_discount < Decimal("1"):
            sec_secured_espp_discount = Decimal("0")

        sec_rsu_delta_str = f"€{sec_rsu_delta:,.2f}"
        sec_espp_secured_str = f"€{sec_secured_espp_discount:,.2f}"
        sec_espp_at_risk_str = f"€{sec_at_risk_espp_discount:,.2f}"
        sec_espp_lost_str = f"€{sec_lost_espp_discount:,.2f}"

        # Resolve peers for this ticker
        if args.demo:
            if sec_ticker == "DT":
                sec_peer_tickers = ["DDOG", "ESTC"]
            elif sec_ticker == "TSLA":
                sec_peer_tickers = ["RIVN", "LCID"]
            elif sec_ticker == "NVDA":
                sec_peer_tickers = ["AMD", "INTC"]
            elif sec_ticker == "ADBE":
                sec_peer_tickers = ["MSFT", "CRM"]
            elif sec_ticker == "SHEL":
                sec_peer_tickers = ["BP", "XOM"]
            else:
                sec_peer_tickers = ["DDOG", "ESTC"]
        else:
            if args.peers:
                sec_peer_tickers = [t.strip().upper() for t in args.peers if t.strip()]
            else:
                sec_peer_tickers = load_peers_config(input_dir, sec_ticker)

        # Fetch peers comparison returns normalized
        sec_peer_data = {sec_ticker: build_dt_normalized_returns(sec_hist_quotes, sec_first_date)}
        if args.demo:
            # Add demo peers dynamically based on sec_peer_tickers
            dt_normalized = sec_peer_data[sec_ticker]
            for p_ticker in sec_peer_tickers:
                p_normalized = []
                seed_val = sum(ord(c) for c in p_ticker)
                factor = 0.85 + (seed_val % 4) * 0.1  # 0.85 to 1.15
                phase = (seed_val % 3) * 15.0
                period = 30.0 + (seed_val % 4) * 10.0
                for q in dt_normalized:
                    pct = q["pct"] * factor + math.sin((date.fromisoformat(q["date"]) - sec_first_date).days / period + phase) * 12.0
                    p_normalized.append({"date": q["date"], "pct": pct, "price": q["price"] * factor})
                sec_peer_data[p_ticker] = p_normalized
        else:
            try:
                peers_fetched = fetch_peers_data(sec_peer_tickers, sec_first_date)
                sec_peer_data.update(peers_fetched)
            except Exception:
                pass

        multi_security_data[sec_ticker] = {
            "ticker": sec_ticker,
            "isin": sec_isin,
            "company_name": sec_company_name,
            "live_price_usd": float(sec_latest_price_usd),
            "live_fx_rate": float(sec_latest_fx_rate),
            "my_avg_cost_usd": float(sec_avg_cost_usd),
            "first_transaction_date": sec_first_date.isoformat(),
            "rsu_delta": float(sec_rsu_delta),
            "rsu_delta_str": sec_rsu_delta_str,
            "rsu_vest_val": float(sec_rsu_sell_on_vest_value),
            "rsu_hold_val": float(sec_rsu_hold_value),
            "espp_secured": float(sec_secured_espp_discount),
            "espp_secured_str": sec_espp_secured_str,
            "espp_at_risk": float(sec_at_risk_espp_discount),
            "espp_at_risk_str": sec_espp_at_risk_str,
            "espp_lost": float(sec_lost_espp_discount),
            "espp_lost_str": sec_espp_lost_str,
            "trend_signal": sec_signal,
            "trend_advice": sec_advice,
            "chart_data": sec_chart_data,
            "fx_history": sec_fx_history,
            "decomp_data": sec_sales_decomposition,
            "historical_quotes": sec_hist_quotes,
            "espp_lots": sec_espp_active_lots,
            "unsold_lots": sec_unsold_lots_data,
            "peer_data": sec_peer_data,
            "peer_tickers": sec_peer_tickers
        }

    # Extract primary security values as the baseline for standard template replacements
    primary_data = multi_security_data.get(ticker)
    if not primary_data:
        # fallback to the first one available
        primary_data = list(multi_security_data.values())[0]

    # Per-security dividends (informational; the taxable RCM base stays
    # portfolio-level). Real data comes from the Revolut "Other income" rows.
    if args.demo:
        from tax_engine.sample_data import create_sample_dividends_by_symbol
        dividends_by_symbol = create_sample_dividends_by_symbol()
    else:
        dividends_by_symbol = load_revolut_dividends_by_symbol(input_dir)

    # Calculate portfolio wide metrics and holdings table
    holdings_table = []
    total_portfolio_value_eur = 0.0
    for r in portfolio.results:
        sec_ticker = r.security.label
        sec_isin = r.security.isin or ""
        sec_shares = float(r.engine.state.total_shares)
        sec_avg_cost_eur = float(r.engine.state.avg_cost_eur)
        sec_total_cost_eur = sec_shares * sec_avg_cost_eur

        # Live prices
        sec_data = multi_security_data.get(sec_ticker, {})
        sec_price_usd = sec_data.get("live_price_usd", 0.0)
        sec_fx_rate = sec_data.get("live_fx_rate", 1.0)
        sec_value_eur = sec_shares * sec_price_usd * sec_fx_rate
        total_portfolio_value_eur += sec_value_eur

        sec_realized_pl_eur = sum(
            float(pe.realized_gain_loss)
            for pe in r.engine.processed_events
            if pe.event.event_type == EventType.SELL
        )

        holdings_table.append({
            "ticker": sec_ticker,
            "isin": sec_isin,
            "shares": sec_shares,
            "avg_cost_eur": sec_avg_cost_eur,
            "total_cost_eur": sec_total_cost_eur,
            "price_usd": sec_price_usd,
            "fx_rate": sec_fx_rate,
            "value_eur": sec_value_eur,
            "gain_loss_eur": sec_value_eur - sec_total_cost_eur,
            "realized_pl_eur": sec_realized_pl_eur,
            "dividends_eur": float(dividends_by_symbol.get(sec_ticker, Decimal("0"))),
        })

    # Calculate allocation percentage
    for h in holdings_table:
        if total_portfolio_value_eur > 0:
            h["allocation_pct"] = (h["value_eur"] / total_portfolio_value_eur) * 100.0
        else:
            h["allocation_pct"] = 0.0

    securities_chart = build_securities_chart(portfolio.results)

    # Per-broker breakdown
    broker_invested = {}
    broker_realized = {}
    for r in portfolio.results:
        for pe in r.engine.processed_events:
            b = pe.event.broker
            if pe.event.event_type == EventType.SELL:
                broker_realized[b] = broker_realized.get(b, 0.0) + float(pe.realized_gain_loss)
            else:
                broker_invested[b] = broker_invested.get(b, 0.0) + float(pe.event.total_value_eur)
    broker_names = sorted(set(broker_invested) | set(broker_realized))
    broker_chart = {
        "brokers": broker_names,
        "invested": [round(broker_invested.get(b, 0.0), 2) for b in broker_names],
        "realized": [round(broker_realized.get(b, 0.0), 2) for b in broker_names],
    }

    # Per-year realized gain/loss + estimated savings tax (portfolio aggregate).
    yearly_tax_data = [
        {
            "year": s.year,
            "gains": round(float(s.total_gains), 2),
            "losses": round(float(s.deductible_losses), 2),
            "net": round(float(s.net_gain_loss), 2),
            "tax": round(float(s.tax_due), 2),
        }
        for s in portfolio.aggregate.get_all_yearly_summaries()
    ]

    # Currency exposure: current EUR market value grouped by each security's
    # trading currency (so multi-currency portfolios see their FX exposure).
    sec_currency = {}
    for r in portfolio.results:
        cur = "USD"
        for pe in r.engine.processed_events:
            cur = (pe.event.currency or "USD").upper()
            break
        sec_currency[r.security.label] = cur
    currency_value: dict[str, float] = {}
    for h in holdings_table:
        if h["value_eur"] > 0:
            cur = sec_currency.get(h["ticker"], "USD")
            currency_value[cur] = currency_value.get(cur, 0.0) + h["value_eur"]
    currency_exposure = {
        "labels": list(currency_value.keys()),
        "values": [round(v, 2) for v in currency_value.values()],
    }

    # Tax-loss harvesting: open positions currently at an unrealized loss that
    # could be sold to offset this year's realized gains (2-month wash-sale caveat).
    this_year = date.today().year
    yr_summary = next((s for s in portfolio.aggregate.get_all_yearly_summaries() if s.year == this_year), None)
    harvest_candidates = [
        {
            "ticker": h["ticker"],
            "isin": h["isin"],
            "shares": h["shares"],
            "unrealized_loss_eur": round(h["gain_loss_eur"], 2),
        }
        for h in holdings_table
        if h["gain_loss_eur"] < 0 and h["shares"] > 0
    ]
    harvest_candidates.sort(key=lambda c: c["unrealized_loss_eur"])
    harvest_data = {
        "year": this_year,
        "realized_gains_this_year": round(float(yr_summary.total_gains), 2) if yr_summary else 0.0,
        "total_harvestable": round(sum(c["unrealized_loss_eur"] for c in harvest_candidates), 2),
        "candidates": harvest_candidates,
    }

    # Savings income
    if args.demo:
        if args.all_securities:
            from tax_engine.sample_data import create_sample_savings_income
            savings_income = create_sample_savings_income()
        else:
            savings_income = {}
    else:
        savings_income = load_savings_income(input_dir / "savings_income.json")
    savings_income_rows = [
        {
            "year": yr,
            "dividends_eur": float(si.dividends_eur),
            "interest_eur": float(si.interest_eur),
            "total_eur": float(si.rcm_net),
        }
        for yr, si in sorted(savings_income.items())
    ]
    current_year_si = savings_income.get(date.today().year)
    current_year_rcm = float(current_year_si.rcm_net) if current_year_si else 0.0

    # Load HTML template
    template_path = Path(__file__).parent / "src" / "tax_engine" / "templates" / "dashboard_template.html"
    html_template = template_path.read_text(encoding="utf-8")

    # Replace values in the template
    html_content = html_template
    html_content = html_content.replace("__RSU_DELTA__", str(primary_data["rsu_delta"]))
    html_content = html_content.replace("__RSU_DELTA_STR__", primary_data["rsu_delta_str"])
    html_content = html_content.replace("__ESPP_SECURED__", str(primary_data["espp_secured"]))
    html_content = html_content.replace("__ESPP_SECURED_STR__", primary_data["espp_secured_str"])
    html_content = html_content.replace("__ESPP_AT_RISK__", str(primary_data["espp_at_risk"]))
    html_content = html_content.replace("__ESPP_AT_RISK_STR__", primary_data["espp_at_risk_str"])
    html_content = html_content.replace("__ESPP_LOST__", str(primary_data["espp_lost"]))
    html_content = html_content.replace("__ESPP_LOST_STR__", primary_data["espp_lost_str"])

    html_content = html_content.replace("__RSU_VEST_VAL__", str(primary_data["rsu_vest_val"]))
    html_content = html_content.replace("__RSU_HOLD_VAL__", str(primary_data["rsu_hold_val"]))

    html_content = html_content.replace("__TREND_SIGNAL__", json.dumps(primary_data["trend_signal"]))
    html_content = html_content.replace("__TREND_ADVICE__", json.dumps(primary_data["trend_advice"]))

    html_content = html_content.replace("__CHART_DATA__", json.dumps(primary_data["chart_data"]))
    html_content = html_content.replace("__FX_HISTORY__", json.dumps(primary_data["fx_history"]))
    html_content = html_content.replace("__DECOMP_DATA__", json.dumps(primary_data["decomp_data"]))
    html_content = html_content.replace("__BROKER_DATA__", json.dumps(broker_chart))
    html_content = html_content.replace("__SECURITIES_DATA__", json.dumps(securities_chart))
    html_content = html_content.replace("__YEARLY_TAX_DATA__", json.dumps(yearly_tax_data))
    html_content = html_content.replace("__CURRENCY_EXPOSURE__", json.dumps(currency_exposure))
    html_content = html_content.replace("__HARVEST_DATA__", json.dumps(harvest_data))
    html_content = html_content.replace("__HISTORICAL_QUOTES__", json.dumps(primary_data["historical_quotes"]))
    html_content = html_content.replace("__ESPP_LOTS__", json.dumps(primary_data["espp_lots"]))
    html_content = html_content.replace("__UNSOLD_LOTS__", json.dumps(primary_data["unsold_lots"]))
    html_content = html_content.replace("__LIVE_PRICE_USD__", str(primary_data["live_price_usd"]))
    html_content = html_content.replace("__LIVE_FX_RATE__", str(primary_data["live_fx_rate"]))
    html_content = html_content.replace("__MY_AVG_COST_USD__", str(primary_data["my_avg_cost_usd"]))
    html_content = html_content.replace("__FIRST_TRANSACTION_DATE__", primary_data["first_transaction_date"])
    html_content = html_content.replace("__PEER_DATA__", json.dumps(primary_data["peer_data"]))
    html_content = html_content.replace("__PEER_TICKERS__", json.dumps(primary_data["peer_tickers"]))
    html_content = html_content.replace("__TICKER__", ticker)
    html_content = html_content.replace("__COMPANY_NAME__", company_name)
    html_content = html_content.replace("__SAVINGS_INCOME__", json.dumps(savings_income_rows))
    html_content = html_content.replace("__CURRENT_YEAR_RCM__", str(current_year_rcm))

    # Multi-security payload injections
    html_content = html_content.replace("__MULTI_SECURITY_DATA__", json.dumps(multi_security_data))
    html_content = html_content.replace("__HOLDINGS_TABLE__", json.dumps(holdings_table))
    html_content = html_content.replace("__TOTAL_PORTFOLIO_VALUE__", str(total_portfolio_value_eur))

    output_filename = "charts_dashboard_demo.html" if args.demo else "charts_dashboard.html"
    output_path = Path(output_filename)
    output_path.write_text(html_content, encoding="utf-8")

    print(f"\n📊 Interactive Financial & Tax Analysis Dashboard generated at: {output_path}")
    print("Open this file in your browser to view your hold delta, ESPP efficiency, and gains decomposition!")


if __name__ == "__main__":
    main()

