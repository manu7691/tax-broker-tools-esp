"""
Dashboard data helpers.

Functions that transform engine results into JSON-serializable payloads
for the HTML template placeholders.
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tax_engine import EventType
from tax_engine.ecb_rates import ECBRateFetcher

if TYPE_CHECKING:
    from tax_engine import ProcessedEvent, StockEvent, TaxEngine
    from tax_engine.portfolio import SecurityResult


def build_securities_chart(results: "list[SecurityResult]") -> dict[str, list[Any]]:
    """Build the per-security portfolio breakdown payload for the dashboard.

    ``results`` is a list of ``portfolio.SecurityResult``. For each security it
    sums the invested cost basis (acquisitions) and the realized gain/loss (sells)
    in EUR, and records the open position. The template renders the chart only
    when more than one security is present.
    """
    chart: dict[str, list[Any]] = {"securities": [], "isin": [], "invested": [], "realized": [], "position": []}
    for r in results:
        invested = sum(
            float(pe.event.total_value_eur)
            for pe in r.engine.processed_events
            if pe.event.event_type != EventType.SELL
        )
        realized = sum(
            float(pe.realized_gain_loss)
            for pe in r.engine.processed_events
            if pe.event.event_type == EventType.SELL
        )
        chart["securities"].append(r.security.label)
        chart["isin"].append(r.security.isin or "")
        chart["invested"].append(round(invested, 2))
        chart["realized"].append(round(realized, 2))
        chart["position"].append(round(float(r.engine.state.total_shares), 4))
    return chart


def sampled_dates_fx(events: "list[StockEvent]") -> list[date]:
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


def build_sales_decomposition(engine: "TaxEngine", acquisitions_by_date: "dict[date, StockEvent]") -> list[dict[str, Any]]:
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


def build_chart_data(all_events: "list[StockEvent]") -> list[dict[str, Any]]:
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


def build_fx_history(all_events: "list[StockEvent]") -> list[dict[str, Any]]:
    """Build sampled FX rate history for the exchange rate chart."""
    fx_history = []
    for d in sampled_dates_fx(all_events):
        try:
            rate = float(ECBRateFetcher.get_rate(d))
            fx_history.append({"date": d.isoformat(), "rate": rate})
        except Exception:
            continue
    return fx_history


def calculate_rsu_hold_delta(all_events: "list[StockEvent]", engine: "TaxEngine", latest_price_eur: float) -> tuple[float, float, float]:
    """
    Calculate RSU hold vs immediate sell delta.

    Returns:
        (rsu_decision_delta, rsu_sell_on_vest_value, rsu_hold_value)
    """
    rsu_hold_value = 0.0
    rsu_sell_on_vest_value = 0.0
    rsu_lots: list[dict[str, Any]] = []

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


def calculate_espp_savings(input_dir: Path, engine: "TaxEngine") -> tuple[Decimal, Decimal, Decimal, dict[date, tuple[Decimal, Decimal]]]:
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

    total_espp_discount = sum(espp_discounts.values(), Decimal("0"))
    lost_espp_discount = sum(espp_early_sales.values(), Decimal("0"))
    saved_espp_discount = max(Decimal("0"), total_espp_discount - lost_espp_discount)

    return saved_espp_discount, lost_espp_discount, total_espp_discount, espp_map


def build_unsold_lots_and_espp_tracker(engine: "TaxEngine", espp_map: "dict[date, tuple[Decimal, Decimal]]", reference_date: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Build unsold lots data for the simulator and ESPP active lots for the countdown tracker.

    Returns:
        (unsold_lots_data, espp_active_lots)
    """
    unsold_lots_data: list[dict[str, Any]] = []
    espp_active_lots: list[dict[str, Any]] = []

    for lot in engine.state.lots:
        if lot.remaining_shares <= 0:
            continue

        unsold_lots_data.append({
            "acq_date": lot.acquisition_date.isoformat(),
            "shares": float(lot.remaining_shares),
            "price_eur": float(lot.price_eur),
            "notes": lot.notes,
            "broker": lot.broker,
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


def enrich_hist_quotes_with_avg_cost(hist_quotes: "list[dict[str, Any]]", processed_events: "list[ProcessedEvent]") -> None:
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


def build_dt_normalized_returns(hist_quotes: "list[dict[str, Any]]", first_transaction_date: date) -> list[dict[str, Any]]:
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
