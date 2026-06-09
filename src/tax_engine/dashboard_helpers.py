"""
Dashboard data helpers.

Functions that transform engine results into JSON-serializable payloads
for the HTML template placeholders.
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from tax_engine import EventType
from tax_engine.ecb_rates import ECBRateFetcher


def sampled_dates_fx(events: list) -> list:
    """Helper to sample dates for FX rates drawing."""
    start_date = events[0].event_date
    end_date = events[-1].event_date
    date_list = []
    curr = start_date
    while curr <= end_date:
        date_list.append(curr)
        curr += timedelta(days=1)
        
    step = max(1, len(date_list) // 500)
    sampled_dates = date_list[::step]
    if end_date not in sampled_dates:
        sampled_dates.append(end_date)
    return sampled_dates


def build_sales_decomposition(engine, acquisitions_by_date: dict) -> list[dict]:
    """Build the gain/loss decomposition for each sale (stock vs FX contribution)."""
    sales_decomposition = []
    for pe in engine.processed_events:
        if pe.event.event_type != EventType.SELL:
            continue
        
        sell_event = pe.event
        sell_price_usd = sell_event.price_usd
        sell_fx_rate = sell_event.resolved_fx_rate
        
        for match in pe.fifo_matches:
            acq_event = acquisitions_by_date.get(match.acquisition_date)
            if not acq_event:
                continue
            acq_price_usd = acq_event.price_usd
            acq_fx_rate = acq_event.resolved_fx_rate
            
            stock_contribution_per_share = (sell_price_usd - acq_price_usd) * acq_fx_rate
            fx_contribution_per_share = sell_price_usd * (sell_fx_rate - acq_fx_rate)
            
            shares = match.shares
            sales_decomposition.append({
                "date": sell_event.event_date.isoformat(),
                "shares": float(shares),
                "stock_gain": float(stock_contribution_per_share * shares),
                "fx_gain": float(fx_contribution_per_share * shares),
                "total_gain": float(match.realized_gain_loss),
                "notes": sell_event.notes,
                # Whether the sold shares came from an ESPP lot, so the dashboard
                # can keep "exclude ESPP" filtering consistent between current
                # holdings and past realized gains/losses.
                "is_espp": "ESPP" in acq_event.notes,
            })
    return sales_decomposition


def build_chart_data(all_events: list) -> list[dict]:
    """Build event timeline data for the transaction scatter chart."""
    chart_data = []
    for event in all_events:
        chart_data.append({
            "date": event.event_date.isoformat(),
            "type": event.event_type.value,
            "shares": float(event.shares),
            "price_usd": float(event.price_usd),
            "price_eur": float(event.price_eur),
            "fx_rate": float(event.resolved_fx_rate),
            "notes": event.notes
        })
    return chart_data


def build_fx_history(all_events: list) -> list[dict]:
    """Build sampled FX rate history for the exchange rate chart."""
    fx_history = []
    for d in sampled_dates_fx(all_events):
        try:
            rate = float(ECBRateFetcher.get_rate(d))
            fx_history.append({"date": d.isoformat(), "rate": rate})
        except Exception:
            continue
    return fx_history


def calculate_rsu_hold_delta(all_events: list, engine, latest_price_eur: float) -> tuple[float, float, float]:
    """
    Calculate RSU hold vs immediate sell delta.
    
    Returns:
        (rsu_decision_delta, rsu_sell_on_vest_value, rsu_hold_value)
    """
    rsu_hold_value = 0.0
    rsu_sell_on_vest_value = 0.0
    rsu_lots = []
    
    for event in all_events:
        if event.event_type == EventType.VEST:
            rsu_lots.append({
                "date": event.event_date,
                "vest_price_eur": float(event.price_eur),
                "total_shares": float(event.shares),
                "remaining_shares": float(event.shares),
                "realized_sales_value": 0.0,
            })
            
    for pe in engine.processed_events:
        if pe.event.event_type == EventType.SELL:
            for match in pe.fifo_matches:
                for lot in rsu_lots:
                    if lot["date"] == match.acquisition_date:
                        shares_sold = float(match.shares)
                        lot["remaining_shares"] -= shares_sold
                        sale_price_eur = float(match.realized_gain_loss / match.shares) + lot["vest_price_eur"]
                        lot["realized_sales_value"] += shares_sold * sale_price_eur
                        break

    for lot in rsu_lots:
        rsu_sell_on_vest_value += lot["total_shares"] * lot["vest_price_eur"]
        current_value = lot["remaining_shares"] * latest_price_eur
        rsu_hold_value += lot["realized_sales_value"] + current_value

    rsu_decision_delta = rsu_hold_value - rsu_sell_on_vest_value
    return rsu_decision_delta, rsu_sell_on_vest_value, rsu_hold_value


def calculate_espp_savings(input_dir: Path, engine) -> tuple[Decimal, Decimal, Decimal, dict]:
    """
    Calculate ESPP tax exemption savings, losses, and build the purchase map.
    
    Returns:
        (saved_espp_discount, lost_espp_discount, total_espp_discount, espp_map)
    """
    from tax_engine.cli_main import (
        build_espp_purchase_map,
        calculate_espp_discounts,
        detect_espp_early_sales,
    )
    
    espp_discounts = calculate_espp_discounts(input_dir)
    espp_map = build_espp_purchase_map(input_dir)
    espp_early_sales, _ = detect_espp_early_sales(engine.processed_events, espp_map)
    
    total_espp_discount = sum(espp_discounts.values()) if espp_discounts else Decimal("0")
    lost_espp_discount = sum(espp_early_sales.values()) if espp_early_sales else Decimal("0")
    saved_espp_discount = max(Decimal("0"), total_espp_discount - lost_espp_discount)
    
    return saved_espp_discount, lost_espp_discount, total_espp_discount, espp_map


def build_unsold_lots_and_espp_tracker(engine, espp_map: dict, reference_date: date) -> tuple[list[dict], list[dict]]:
    """
    Build unsold lots data for the simulator and ESPP active lots for the countdown tracker.
    
    Returns:
        (unsold_lots_data, espp_active_lots)
    """
    unsold_lots_data = []
    espp_active_lots = []

    for lot in engine.state.lots:
        if lot.remaining_shares <= 0:
            continue
        
        unsold_lots_data.append({
            "acq_date": lot.acquisition_date.isoformat(),
            "shares": float(lot.remaining_shares),
            "price_eur": float(lot.price_eur),
            "notes": lot.notes
        })

        if "ESPP" in lot.notes:
            try:
                unlock_date = lot.acquisition_date.replace(year=lot.acquisition_date.year + 3)
            except ValueError:  # Leap year
                unlock_date = lot.acquisition_date.replace(year=lot.acquisition_date.year + 3, day=28)
            
            days_left = (unlock_date - reference_date).days
            
            # Resolve original discount at risk
            espp_info = espp_map.get(lot.acquisition_date)
            discount_at_risk_eur = 0.0
            if espp_info:
                fmv_usd, purchase_price_usd = espp_info
                disc_per_share_usd = fmv_usd - purchase_price_usd
                fx_rate = ECBRateFetcher.get_rate(lot.acquisition_date)
                discount_at_risk_eur = float(disc_per_share_usd * lot.remaining_shares * fx_rate)

            status = "🔓 Exemption Secured" if days_left <= 0 else "🔒 Locked"
            advice_str = "Safe to Sell (No tax penalty)" if days_left <= 0 else f"HOLD to avoid paying tax on €{discount_at_risk_eur:,.2f}"

            espp_active_lots.append({
                "acq_date": lot.acquisition_date.isoformat(),
                "shares": float(lot.remaining_shares),
                "price_eur": float(lot.price_eur),
                "unlock_date": unlock_date.isoformat(),
                "days_left": max(0, days_left),
                "status": status,
                "advice": advice_str,
                "discount_at_risk": discount_at_risk_eur
            })

    espp_active_lots.sort(key=lambda x: x["acq_date"])
    return unsold_lots_data, espp_active_lots


def enrich_hist_quotes_with_avg_cost(hist_quotes: list[dict], processed_events: list) -> None:
    """Add running average cost (USD) to each historical quote for the trend chart overlay."""
    processed_sorted = sorted(processed_events, key=lambda x: x.event.event_date)
    for q in hist_quotes:
        q_date = date.fromisoformat(q["date"])
        last_pe = None
        for pe in processed_sorted:
            if pe.event.event_date <= q_date:
                last_pe = pe
            else:
                break
        if last_pe and last_pe.total_shares_after > 0:
            # Convert running EUR cost to USD using the event's resolved FX rate
            fx_rate = last_pe.event.resolved_fx_rate
            if fx_rate > 0:
                q["avg_cost_usd"] = float(last_pe.avg_cost_eur_after / fx_rate)
            else:
                q["avg_cost_usd"] = None
        else:
            q["avg_cost_usd"] = None


def build_dt_normalized_returns(hist_quotes: list[dict], first_transaction_date: date) -> list[dict]:
    """Calculate normalized DT returns starting from the first transaction date."""
    dt_normalized = []
    if hist_quotes:
        start_dt_str = first_transaction_date.isoformat()
        dt_quotes_filtered = [q for q in hist_quotes if q["date"] >= start_dt_str]
        if dt_quotes_filtered:
            first_dt_close = dt_quotes_filtered[0]["close"]
            for q in dt_quotes_filtered:
                pct = ((q["close"] - first_dt_close) / first_dt_close) * 100.0
                dt_normalized.append({"date": q["date"], "pct": pct, "price": q["close"]})
    return dt_normalized
