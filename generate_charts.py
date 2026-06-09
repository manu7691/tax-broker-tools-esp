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

from tax_engine import EventType, TaxEngine
from tax_engine.cli_main import (
    auto_detect_sell_to_cover,
    build_espp_purchase_map,
    load_events_from_excel,
    load_orders_from_excel,
    load_options_stock_events,
    load_rsu_events,
    load_savings_income,
    load_security_config,
)
from tax_engine.revolut_parser import load_revolut_trade_events
from tax_engine.market_data import (
    fetch_company_name,
    fetch_historical_market_data,
    fetch_peers_data,
)
from tax_engine.dashboard_helpers import (
    build_chart_data,
    build_dt_normalized_returns,
    build_fx_history,
    build_sales_decomposition,
    build_unsold_lots_and_espp_tracker,
    calculate_espp_savings,
    calculate_rsu_hold_delta,
    enrich_hist_quotes_with_avg_cost,
)


DEFAULT_PEERS = ["DDOG", "ESTC"]


def load_peers_config(input_dir: Path) -> list[str]:
    """Load peer tickers from input/peers.json; fall back to DEFAULT_PEERS."""
    peers_path = input_dir / "peers.json"
    if peers_path.exists():
        try:
            data = json.loads(peers_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                tickers = [str(t).strip().upper() for t in data if str(t).strip()]
                if tickers:
                    return tickers
        except Exception:
            pass
    return DEFAULT_PEERS


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
    args = parser.parse_args()

    input_dir = Path("input")

    # Resolve main ticker
    ticker = None
    config_company_name = None
    if args.ticker:
        ticker = args.ticker.strip().upper()
        print(f"Using ticker from command line argument: {ticker}")

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
    if args.company_name:
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

    # Load and process events
    espp_events = load_events_from_excel(input_dir) if include_espp else []
    sell_events = load_orders_from_excel(input_dir)
    
    rsu_dir = input_dir / "rsu"
    rsu_events = load_rsu_events(rsu_dir) if rsu_dir.exists() else []
    options_events = load_options_stock_events(input_dir)

    # Optional Revolut holdings of the same security (matched on ISIN, ticker as
    # fallback) so the charts reflect the same combined position as the PDF report.
    _, config_isin = load_security_config(input_dir)
    revolut_events = load_revolut_trade_events(input_dir, isin=config_isin, symbol=ticker)

    all_events = espp_events + sell_events + rsu_events + options_events + revolut_events
    if not all_events:
        print("No events found. Please make sure your Excel sheets and PDFs are in the input folder.")
        return

    # Auto-detect Sell-to-Cover vs Manual Sells
    auto_detect_sell_to_cover(all_events)

    # Process events through the engine
    engine = TaxEngine()
    engine.process_all(all_events)
    
    # Sort events by date
    all_events.sort(key=lambda x: x.event_date)
    
    # Map acquisitions by date for easy lookup
    acquisitions_by_date = {}
    for event in all_events:
        if event.event_type in (EventType.VEST, EventType.BUY, EventType.EXERCISE):
            acquisitions_by_date[event.event_date] = event

    # Fetch Yahoo Finance Trend data dynamically matched to the duration of the stock list
    first_transaction_date = all_events[0].event_date
    hist_quotes, live_p, sma50, sma200, signal, advice = fetch_historical_market_data(ticker, first_transaction_date)

    # Resolve peer tickers: CLI flag > peers.json > built-in defaults
    if args.peers:
        peer_tickers = [t.strip().upper() for t in args.peers if t.strip()]
    else:
        peer_tickers = load_peers_config(input_dir)
    print(f"Peer tickers for comparison: {', '.join(peer_tickers)}")

    # Calculate main ticker normalized returns & fetch peer data
    dt_normalized = build_dt_normalized_returns(hist_quotes, first_transaction_date)

    peer_data = {
        ticker: dt_normalized,
    }
    peers_fetched = fetch_peers_data(peer_tickers, first_transaction_date)
    peer_data.update(peers_fetched)


    # Resolve Current Price
    latest_event = all_events[-1]
    if args.current_price is not None:
        latest_price_usd = Decimal(str(args.current_price))
        print(f"Calibrating unsold shares using user-supplied price: ${latest_price_usd:.2f} USD")
        # Re-calculate signal if current price is manual
        if sma50 > 0 and sma200 > 0:
            p = float(latest_price_usd)
            if p > sma50 and p > sma200:
                signal = {
                    "en": "Bullish (Strong Growth Trend)",
                    "es": "Alcista (Fuerte tendencia de crecimiento)"
                }
                advice = {
                    "en": f"The manual price (${p:.2f}) is above both the 50-day (${sma50:.2f}) and 200-day (${sma200:.2f}) averages. Strong upward trend. Favorable window to hold or sell in increments.",
                    "es": f"El precio manual (${p:.2f}) está por encima de las medias de 50 días (${sma50:.2f}) y de 200 días (${sma200:.2f}). Fuerte tendencia alcista. Momento favorable para mantener o vender de forma incremental."
                }
            elif p < sma50 and p < sma200:
                signal = {
                    "en": "Bearish (Down Trend)",
                    "es": "Bajista (Tendencia bajista)"
                }
                advice = {
                    "en": f"The manual price (${p:.2f}) is below both the 50-day (${sma50:.2f}) and 200-day (${sma200:.2f}) averages. Trend is downward. Holding carries downside risk.",
                    "es": f"El precio manual (${p:.2f}) está por debajo de las medias de 50 días (${sma50:.2f}) y de 200 días (${sma200:.2f}). La tendencia es bajista. Mantener conlleva riesgos de caída."
                }
    else:
        if live_p > 0:
            latest_price_usd = Decimal(str(live_p))
            print(f"Fetched live market price for {ticker} from Yahoo Finance: ${latest_price_usd:.2f} USD")
        else:
            latest_price_usd = latest_event.price_usd
            print(f"Falling back to last known price from transaction history: ${latest_price_usd:.2f} USD")

    latest_fx_rate = latest_event.resolved_fx_rate
    latest_price_eur = latest_price_usd * latest_fx_rate

    # Calculate current average purchase price in USD for chart line
    if latest_fx_rate > 0:
        avg_cost_usd = float(engine.state.avg_cost_eur / latest_fx_rate)
    else:
        avg_cost_usd = 0.0

    # Calculate running average cost in USD for each historical quote
    enrich_hist_quotes_with_avg_cost(hist_quotes, engine.processed_events)

    # ESPP Exemption Calculations (only when ESPP analysis is included)
    if include_espp:
        saved_espp_discount, lost_espp_discount, total_espp_discount, espp_map = calculate_espp_savings(input_dir, engine)
    else:
        saved_espp_discount = lost_espp_discount = total_espp_discount = Decimal("0")
        espp_map = {}

    # RSU hold vs vest calculations
    rsu_decision_delta, rsu_sell_on_vest_value, rsu_hold_value = calculate_rsu_hold_delta(
        all_events, engine, float(latest_price_eur)
    )

    # Build data payloads
    sales_decomposition = build_sales_decomposition(engine, acquisitions_by_date)
    chart_data = build_chart_data(all_events)
    fx_history = build_fx_history(all_events)

    # Per-broker breakdown for the dashboard: cost basis invested (acquisitions)
    # and realized gain/loss (sells), attributed to the broker of each event. Only
    # rendered when more than one broker is present (the template hides it otherwise).
    broker_invested: dict[str, float] = {}
    broker_realized: dict[str, float] = {}
    for pe in engine.processed_events:
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

    # Unsold lots and ESPP tracker
    today_dt = date.today()
    current_reference_date = today_dt if today_dt.year >= 2026 else latest_event.event_date
    unsold_lots_data, espp_active_lots = build_unsold_lots_and_espp_tracker(engine, espp_map, current_reference_date)

    # Split the "saved" ESPP discount into what is already secured (lots held >= 3
    # years, exemption locked in) vs. still at risk (lots held but not yet 3 years
    # old, exemption only kept if held to term). Lots sold early are the "lost"
    # penalty and are excluded from both. The tracker flags at-risk lots with
    # days_left > 0 and exposes their discount as discount_at_risk.
    at_risk_espp_discount = Decimal(str(
        sum(lot["discount_at_risk"] for lot in espp_active_lots if lot["days_left"] > 0)
    ))
    secured_espp_discount = max(Decimal("0"), saved_espp_discount - at_risk_espp_discount)
    # "saved" (aggregate discount, total - early-sold) and "at risk" (summed from the
    # per-lot tracker) are computed via two different paths, so when essentially
    # everything is still at risk they can leave a sub-euro rounding residual. A real
    # secured exemption is always several euros (≥1 share of discount), so snap any
    # sub-euro residual to zero to avoid a misleading "€0.01 secured" on the scorecard.
    if secured_espp_discount < Decimal("1"):
        secured_espp_discount = Decimal("0")

    # Dividend / interest (RCM) savings income. In Spain this is taxed in the same
    # "savings base" as stock capital gains, so it matters for the tax-bracket
    # optimizer. Empty when no savings_income.json data has been recorded.
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
    current_year_si = savings_income.get(today_dt.year)
    current_year_rcm = float(current_year_si.rcm_net) if current_year_si else 0.0

    # Load HTML template
    template_path = Path(__file__).parent / "src" / "tax_engine" / "templates" / "dashboard_template.html"
    html_template = template_path.read_text(encoding="utf-8")

    # Format numbers for dashboard string injections
    rsu_delta_str = f"€{rsu_decision_delta:,.2f}"
    espp_secured_str = f"€{secured_espp_discount:,.2f}"
    espp_at_risk_str = f"€{at_risk_espp_discount:,.2f}"
    espp_lost_str = f"€{lost_espp_discount:,.2f}"

    # Replace values in the template
    html_content = html_template
    html_content = html_content.replace("__RSU_DELTA__", str(float(rsu_decision_delta)))
    html_content = html_content.replace("__RSU_DELTA_STR__", rsu_delta_str)
    html_content = html_content.replace("__ESPP_SECURED__", str(float(secured_espp_discount)))
    html_content = html_content.replace("__ESPP_SECURED_STR__", espp_secured_str)
    html_content = html_content.replace("__ESPP_AT_RISK__", str(float(at_risk_espp_discount)))
    html_content = html_content.replace("__ESPP_AT_RISK_STR__", espp_at_risk_str)
    html_content = html_content.replace("__ESPP_LOST__", str(float(lost_espp_discount)))
    html_content = html_content.replace("__ESPP_LOST_STR__", espp_lost_str)
    
    html_content = html_content.replace("__RSU_VEST_VAL__", str(float(rsu_sell_on_vest_value)))
    html_content = html_content.replace("__RSU_HOLD_VAL__", str(float(rsu_hold_value)))

    html_content = html_content.replace("__TREND_SIGNAL__", json.dumps(signal))
    html_content = html_content.replace("__TREND_ADVICE__", json.dumps(advice))

    html_content = html_content.replace("__CHART_DATA__", json.dumps(chart_data))
    html_content = html_content.replace("__FX_HISTORY__", json.dumps(fx_history))
    html_content = html_content.replace("__DECOMP_DATA__", json.dumps(sales_decomposition))
    html_content = html_content.replace("__BROKER_DATA__", json.dumps(broker_chart))
    html_content = html_content.replace("__HISTORICAL_QUOTES__", json.dumps(hist_quotes))
    html_content = html_content.replace("__ESPP_LOTS__", json.dumps(espp_active_lots))
    html_content = html_content.replace("__UNSOLD_LOTS__", json.dumps(unsold_lots_data))
    html_content = html_content.replace("__LIVE_PRICE_USD__", str(float(latest_price_usd)))
    html_content = html_content.replace("__LIVE_FX_RATE__", str(float(latest_fx_rate)))
    html_content = html_content.replace("__MY_AVG_COST_USD__", str(float(avg_cost_usd)))
    html_content = html_content.replace("__FIRST_TRANSACTION_DATE__", first_transaction_date.isoformat())
    html_content = html_content.replace("__PEER_DATA__", json.dumps(peer_data))
    html_content = html_content.replace("__PEER_TICKERS__", json.dumps(peer_tickers))
    html_content = html_content.replace("__TICKER__", ticker)
    html_content = html_content.replace("__COMPANY_NAME__", company_name)
    html_content = html_content.replace("__SAVINGS_INCOME__", json.dumps(savings_income_rows))
    html_content = html_content.replace("__CURRENT_YEAR_RCM__", str(current_year_rcm))


    output_path = Path("charts_dashboard.html")
    output_path.write_text(html_content, encoding="utf-8")
    
    print(f"\n📊 Interactive Financial & Tax Analysis Dashboard generated at: {output_path}")
    print("Open this file in your browser to view your hold delta, ESPP efficiency, and gains decomposition!")


if __name__ == "__main__":
    main()
